/**
 * MessageList — the conversation thread.
 *
 * User turns as bubbles; assistant turns as markdown; tool calls as
 * expandable cards; errors in an error style (not a plain assistant bubble).
 * The live streaming delta renders with a caret until the authoritative
 * agent_message arrives.
 */
import { useEffect, useRef } from 'react'
import { AlertTriangle, Bot, Loader2, User } from 'lucide-react'
import type { ThreadItem } from '../../hooks/useAgentChat'
import Markdown from './Markdown'
import ToolCard from './ToolCard'

interface Props {
  thread: ThreadItem[]
  streamText: string
  running: boolean
}

export default function MessageList({ thread, streamText, running }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end', inline: 'nearest' })
  }, [thread, streamText])

  return (
    <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
      {thread.map((item, i) => (
        <Row key={i} item={item} />
      ))}

      {streamText && (
        <div className="flex gap-3">
          <Bot size={18} className="mt-0.5 shrink-0 text-blue-500" />
          <div className="min-w-0 max-w-[85%]">
            <Markdown>{streamText}</Markdown>
            <span className="ml-0.5 inline-block animate-pulse">▌</span>
          </div>
        </div>
      )}

      {running && !streamText && (
        <div className="flex items-center gap-2 pl-8 text-sm text-gray-400">
          <Loader2 size={14} className="animate-spin" /> working…
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  )
}

function Row({ item }: { item: ThreadItem }) {
  switch (item.kind) {
    case 'user':
      return (
        <div className="flex justify-end gap-3">
          <div className="max-w-[75%] whitespace-pre-wrap break-words rounded-2xl bg-blue-600 px-4 py-2.5 text-sm text-white">
            {item.text}
          </div>
          <User size={18} className="mt-1 shrink-0 text-gray-400" />
        </div>
      )
    case 'assistant':
      return (
        <div className="flex gap-3">
          <Bot size={18} className="mt-0.5 shrink-0 text-blue-500" />
          <div className="min-w-0 max-w-[85%]">
            <Markdown>{item.text}</Markdown>
          </div>
        </div>
      )
    case 'tool':
      return <ToolCard item={item} />
    case 'error':
      return (
        <div className="flex gap-3">
          <AlertTriangle size={18} className="mt-0.5 shrink-0 text-red-500" />
          <div className="min-w-0 max-w-[85%] rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
            {item.text}
          </div>
        </div>
      )
  }
}
