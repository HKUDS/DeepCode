/**
 * Agent Chat — the Claude Code desktop analogue.
 *
 * Continuous multi-turn conversations with the DeepCode agent kernel:
 * a sidebar of past chats (+ New chat), a live message thread rendering
 * the SQ/EQ event stream (streamed text deltas, tool progress cards),
 * and an always-available composer. Pure event consumer: everything on
 * screen comes from /ws/agent/{id} frames.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Bot,
  FolderOpen,
  Loader2,
  MessageSquarePlus,
  Send,
  StopCircle,
  Terminal,
  User,
} from 'lucide-react'
import { useWebSocket } from '../hooks/useWebSocket'
import api from '../services/api'

// ---- types -----------------------------------------------------------------

interface ChatSummary {
  session_id: string
  title: string
  updated_at: string
  message_count: number
  workspace: string
}

type ThreadItem =
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; text: string }
  | { kind: 'tool'; callId: string; name: string; detail: string; done: boolean; isError: boolean }
  | { kind: 'meta'; text: string }

interface AgentEventMsg {
  type: string
  [key: string]: unknown
}

// ---- page ------------------------------------------------------------------

export default function AgentChatPage() {
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [thread, setThread] = useState<ThreadItem[]>([])
  const [streamText, setStreamText] = useState('')
  const [input, setInput] = useState('')
  const [wsInput, setWsInput] = useState('')
  const [running, setRunning] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const streamRef = useRef('')

  const refreshChats = useCallback(async () => {
    const res = await api.get('/agent/chats')
    setChats(res.data.chats ?? [])
  }, [])

  useEffect(() => {
    refreshChats()
  }, [refreshChats])

  // Load the stored transcript when switching chats.
  useEffect(() => {
    if (!activeId) return
    setThread([])
    setStreamText('')
    streamRef.current = ''
    setRunning(false)
    api.get(`/agent/chats/${activeId}/messages`).then((res) => {
      const items: ThreadItem[] = (res.data.messages ?? []).map(
        (m: { role: string; content: string }) =>
          m.role === 'user'
            ? { kind: 'user', text: m.content }
            : { kind: 'assistant', text: m.content },
      )
      setThread(items)
    })
  }, [activeId])

  const onEvent = useCallback(
    (frame: { msg?: AgentEventMsg } | AgentEventMsg) => {
      const msg: AgentEventMsg | undefined =
        (frame as { msg?: AgentEventMsg }).msg ?? (frame as AgentEventMsg)
      if (!msg || typeof msg.type !== 'string') return
      switch (msg.type) {
        case 'turn_started':
          setRunning(true)
          break
        case 'agent_message_delta':
          streamRef.current += String(msg.delta ?? '')
          setStreamText(streamRef.current)
          break
        case 'agent_message': {
          const text = String(msg.text ?? '')
          streamRef.current = ''
          setStreamText('')
          setThread((t) => [...t, { kind: 'assistant', text }])
          break
        }
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
            },
          ])
          break
        case 'tool_completed':
          setThread((t) =>
            t.map((item) =>
              item.kind === 'tool' && item.callId === msg.call_id
                ? { ...item, done: true, isError: Boolean(msg.is_error) }
                : item,
            ),
          )
          break
        case 'error':
          setThread((t) => [
            ...t,
            { kind: 'meta', text: `error: ${String(msg.message ?? '')}` },
          ])
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

  useEffect(() => {
    // inline: 'nearest' keeps auto-scroll strictly vertical — it must never
    // drag the page horizontally.
    bottomRef.current?.scrollIntoView({
      behavior: 'smooth',
      block: 'end',
      inline: 'nearest',
    })
  }, [thread, streamText])

  const newChat = async () => {
    // Optional workspace: work on a real project directory (Claude Code
    // desktop-style); blank = an isolated per-chat directory.
    const workspace = wsInput.trim()
    const res = await api.post(
      '/agent/chats',
      workspace ? { workspace } : {},
    )
    setWsInput('')
    await refreshChats()
    setActiveId(res.data.session_id)
  }

  const activeWorkspace = chats.find((c) => c.session_id === activeId)?.workspace

  const send = () => {
    const text = input.trim()
    if (!text || !activeId || running || !isConnected) return
    setThread((t) => [...t, { kind: 'user', text }])
    setInput('')
    setRunning(true)
    sendMessage({ type: 'user_input', text })
  }

  const interrupt = () => sendMessage({ type: 'interrupt' })

  // ---- render ----------------------------------------------------------------

  return (
    // Panel height = viewport − 4rem header − Layout's main padding
    // (p-6 = 3rem, lg:p-8 = 4rem vertically), so the thread scrolls inside
    // this panel and the page itself never scrolls (in either axis —
    // negative-margin tricks widen the content and cause horizontal drift).
    <div className="flex h-[calc(100vh-7rem)] lg:h-[calc(100vh-8rem)] overflow-hidden rounded-xl border border-gray-200 dark:border-gray-800">
      {/* Sidebar: chat list */}
      <aside className="w-64 shrink-0 border-r border-gray-200 dark:border-gray-800 flex flex-col">
        <div className="p-3 space-y-2">
          <input
            value={wsInput}
            onChange={(e) => setWsInput(e.target.value)}
            placeholder="Workspace folder (optional)"
            title="Directory the agent works in; blank = isolated per-chat dir"
            className="w-full rounded-lg border border-gray-200 dark:border-gray-800 bg-transparent px-3 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <button
            onClick={newChat}
            className="w-full flex items-center justify-center gap-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium px-3 py-2"
          >
            <MessageSquarePlus size={16} /> New chat
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-1">
          {chats.map((c) => (
            <button
              key={c.session_id}
              onClick={() => setActiveId(c.session_id)}
              className={`w-full text-left rounded-lg px-3 py-2 text-sm transition-colors ${
                c.session_id === activeId
                  ? 'bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-300'
                  : 'hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-300'
              }`}
            >
              <div className="truncate font-medium">{c.title}</div>
              <div className="text-xs text-gray-400">{c.message_count} messages</div>
            </button>
          ))}
          {chats.length === 0 && (
            <p className="text-xs text-gray-400 px-3 pt-2">
              No conversations yet — start one.
            </p>
          )}
        </div>
      </aside>

      {/* Main: thread + composer */}
      <section className="flex-1 flex flex-col min-w-0">
        {!activeId ? (
          <div className="flex-1 flex flex-col items-center justify-center text-gray-400 gap-3">
            <Bot size={40} />
            <p className="text-sm">Pick a conversation or start a new one.</p>
            <button
              onClick={newChat}
              className="flex items-center gap-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium px-4 py-2"
            >
              <MessageSquarePlus size={16} /> New chat
            </button>
          </div>
        ) : (
          <>
            {activeWorkspace && (
              <div className="flex items-center gap-2 overflow-hidden border-b border-gray-200 dark:border-gray-800 px-6 py-2 text-xs font-mono text-gray-400">
                <FolderOpen size={13} className="shrink-0" />
                {/* min-w-0 lets truncate actually shrink inside flex */}
                <span className="min-w-0 flex-1 truncate" title={activeWorkspace}>
                  {activeWorkspace}
                </span>
              </div>
            )}
            <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
              {thread.map((item, i) => (
                <ThreadRow key={i} item={item} />
              ))}
              {streamText && (
                <div className="flex gap-3">
                  <Bot size={18} className="mt-1 shrink-0 text-blue-500" />
                  <div className="whitespace-pre-wrap text-sm text-gray-800 dark:text-gray-200">
                    {streamText}
                    <span className="animate-pulse">▌</span>
                  </div>
                </div>
              )}
              {running && !streamText && (
                <div className="flex items-center gap-2 text-gray-400 text-sm">
                  <Loader2 size={14} className="animate-spin" /> working…
                </div>
              )}
              <div ref={bottomRef} />
            </div>

            <div className="border-t border-gray-200 dark:border-gray-800 p-4">
              <div className="flex items-end gap-2 max-w-3xl mx-auto">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      send()
                    }
                  }}
                  rows={Math.min(6, Math.max(1, input.split('\n').length))}
                  placeholder={
                    isConnected ? 'Ask the agent anything… (Enter to send)' : 'connecting…'
                  }
                  className="flex-1 resize-none rounded-xl border border-gray-300 dark:border-gray-700 bg-white dark:bg-gray-900 px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                {running ? (
                  <button
                    onClick={interrupt}
                    title="Interrupt"
                    className="rounded-xl p-3 bg-red-600 hover:bg-red-700 text-white"
                  >
                    <StopCircle size={18} />
                  </button>
                ) : (
                  <button
                    onClick={send}
                    disabled={!input.trim() || !isConnected}
                    title="Send"
                    className="rounded-xl p-3 bg-blue-600 hover:bg-blue-700 disabled:opacity-40 text-white"
                  >
                    <Send size={18} />
                  </button>
                )}
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  )
}

// ---- row renderer ------------------------------------------------------------

function ThreadRow({ item }: { item: ThreadItem }) {
  if (item.kind === 'user') {
    return (
      <div className="flex gap-3 justify-end">
        <div className="max-w-[75%] rounded-2xl bg-blue-600 text-white px-4 py-2.5 text-sm whitespace-pre-wrap">
          {item.text}
        </div>
        <User size={18} className="mt-1 shrink-0 text-gray-400" />
      </div>
    )
  }
  if (item.kind === 'assistant') {
    return (
      <div className="flex gap-3">
        <Bot size={18} className="mt-1 shrink-0 text-blue-500" />
        <div className="whitespace-pre-wrap text-sm text-gray-800 dark:text-gray-200 max-w-[85%]">
          {item.text}
        </div>
      </div>
    )
  }
  if (item.kind === 'tool') {
    return (
      <div className="flex gap-3 pl-8">
        <div
          className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-mono ${
            item.isError
              ? 'border-red-300 text-red-600 dark:border-red-800 dark:text-red-400'
              : item.done
                ? 'border-gray-200 text-gray-500 dark:border-gray-800 dark:text-gray-400'
                : 'border-blue-300 text-blue-600 dark:border-blue-800 dark:text-blue-400'
          }`}
        >
          {item.done ? (
            <Terminal size={12} />
          ) : (
            <Loader2 size={12} className="animate-spin" />
          )}
          <span className="font-semibold">{item.name}</span>
          {item.detail && <span className="opacity-70 truncate max-w-xs">({item.detail})</span>}
          {item.done && !item.isError && <span className="text-green-500">✓</span>}
          {item.isError && <span>✗</span>}
        </div>
      </div>
    )
  }
  return <p className="text-xs text-red-500 pl-8">{item.text}</p>
}
