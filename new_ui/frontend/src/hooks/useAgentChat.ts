/**
 * useAgentChat — all Agent Chat state, WebSocket, and REST wiring in one place.
 *
 * The page and its components stay presentational; every behavior (draft/lazy
 * session creation, live event reduction, delete/rename, workspace selection)
 * lives here. The chat is a pure consumer of the /ws/agent/{id} event stream.
 *
 * Lazy creation: "New chat" enters a *draft* (no server session yet, so the
 * sidebar never fills with empty "(untitled)" rows). The session is created
 * only when the first message is sent; the pending text is flushed once the
 * new WebSocket connects.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useWebSocket } from './useWebSocket'
import api from '../services/api'

export interface ChatSummary {
  session_id: string
  title: string
  updated_at: string
  message_count: number
  workspace: string
}

export type ThreadItem =
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; text: string }
  | {
      kind: 'tool'
      callId: string
      name: string
      detail: string
      done: boolean
      isError: boolean
      result: string
    }
  | { kind: 'error'; text: string }

interface AgentEventMsg {
  type: string
  [key: string]: unknown
}

export interface Draft {
  workspace: string
}

export function useAgentChat() {
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [thread, setThread] = useState<ThreadItem[]>([])
  const [streamText, setStreamText] = useState('')
  const [running, setRunning] = useState(false)

  const streamRef = useRef('')
  const pendingSendRef = useRef<string | null>(null)

  const refreshChats = useCallback(async () => {
    const res = await api.get('/agent/chats')
    setChats(res.data.chats ?? [])
  }, [])

  useEffect(() => {
    refreshChats()
  }, [refreshChats])

  // Load a stored transcript when switching into an existing chat.
  useEffect(() => {
    if (!activeId) return
    setThread([])
    setStreamText('')
    streamRef.current = ''
    setRunning(false)
    api.get(`/agent/chats/${activeId}/messages`).then((res) => {
      setThread(
        (res.data.messages ?? []).map(
          (m: { role: string; content: string }): ThreadItem =>
            m.role === 'user'
              ? { kind: 'user', text: m.content }
              : { kind: 'assistant', text: m.content },
        ),
      )
    })
  }, [activeId])

  const onEvent = useCallback(
    (frame: { msg?: AgentEventMsg } | AgentEventMsg) => {
      const msg = (frame as { msg?: AgentEventMsg }).msg ?? (frame as AgentEventMsg)
      if (!msg || typeof msg.type !== 'string') return
      switch (msg.type) {
        case 'turn_started':
          setRunning(true)
          break
        case 'agent_message_delta':
          streamRef.current += String(msg.delta ?? '')
          setStreamText(streamRef.current)
          break
        case 'agent_message':
          streamRef.current = ''
          setStreamText('')
          setThread((t) => [...t, { kind: 'assistant', text: String(msg.text ?? '') }])
          break
        case 'tool_started':
          setThread((t) => [
            ...t,
            {
              kind: 'tool',
              callId: String(msg.call_id ?? ''),
              name: String(msg.name ?? ''),
              detail: String(msg.detail ?? ''),
              done: false,
              isError: false,
              result: '',
            },
          ])
          break
        case 'tool_completed':
          setThread((t) =>
            t.map((item) =>
              item.kind === 'tool' && item.callId === msg.call_id
                ? {
                    ...item,
                    done: true,
                    isError: Boolean(msg.is_error),
                    result: String(msg.result_preview ?? ''),
                  }
                : item,
            ),
          )
          break
        case 'error':
          setThread((t) => [...t, { kind: 'error', text: String(msg.message ?? '') }])
          break
        case 'task_complete':
          setRunning(false)
          streamRef.current = ''
          setStreamText('')
          refreshChats()
          break
      }
    },
    [refreshChats],
  )

  const { sendMessage, isConnected } = useWebSocket(
    activeId ? `/ws/agent/${activeId}` : null,
    { onMessage: onEvent as (m: unknown) => void },
  )

  // Flush a queued first message once the fresh session's socket is live.
  useEffect(() => {
    if (isConnected && activeId && pendingSendRef.current) {
      sendMessage({ type: 'user_input', text: pendingSendRef.current })
      pendingSendRef.current = null
    }
  }, [isConnected, activeId, sendMessage])

  // -- actions ---------------------------------------------------------------

  const selectChat = useCallback((id: string) => {
    setDraft(null)
    setActiveId(id)
  }, [])

  const startDraft = useCallback((workspace = '') => {
    setActiveId(null)
    setThread([])
    setStreamText('')
    streamRef.current = ''
    setRunning(false)
    setDraft({ workspace })
  }, [])

  const send = useCallback(
    async (text: string) => {
      const body = text.trim()
      if (!body || running) return
      if (draft) {
        // Materialize the session now, then flush this message on connect.
        setThread([{ kind: 'user', text: body }])
        setRunning(true)
        pendingSendRef.current = body
        const res = await api.post(
          '/agent/chats',
          draft.workspace ? { workspace: draft.workspace } : {},
        )
        setDraft(null)
        setActiveId(res.data.session_id)
        await refreshChats()
        return
      }
      if (!activeId || !isConnected) return
      setThread((t) => [...t, { kind: 'user', text: body }])
      setRunning(true)
      sendMessage({ type: 'user_input', text: body })
    },
    [draft, activeId, isConnected, running, sendMessage, refreshChats],
  )

  const interrupt = useCallback(() => {
    sendMessage({ type: 'interrupt' })
  }, [sendMessage])

  const renameChat = useCallback(
    async (id: string, title: string) => {
      await api.patch(`/agent/chats/${id}`, { title })
      await refreshChats()
    },
    [refreshChats],
  )

  const deleteChat = useCallback(
    async (id: string) => {
      await api.delete(`/agent/chats/${id}`)
      if (activeId === id) setActiveId(null)
      await refreshChats()
    },
    [activeId, refreshChats],
  )

  const activeWorkspace =
    draft?.workspace ||
    chats.find((c) => c.session_id === activeId)?.workspace ||
    ''

  return {
    chats,
    activeId,
    draft,
    thread,
    streamText,
    running,
    isConnected,
    activeWorkspace,
    selectChat,
    startDraft,
    send,
    interrupt,
    renameChat,
    deleteChat,
  }
}
