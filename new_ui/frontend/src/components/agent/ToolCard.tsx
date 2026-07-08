/**
 * ToolCard — one tool call as a compact, expandable card.
 *
 * Collapsed: `● name(args)` with a live spinner or ✓ / ✗. Click to expand
 * the result preview the kernel streamed on tool_completed. Interactive only
 * when there is something to reveal.
 */
import { useState } from 'react'
import { ChevronRight, Loader2, Terminal } from 'lucide-react'

export interface ToolItem {
  name: string
  detail: string
  done: boolean
  isError: boolean
  result: string
}

export default function ToolCard({ item }: { item: ToolItem }) {
  const [open, setOpen] = useState(false)
  const expandable = item.done && Boolean(item.result)

  const tone = item.isError
    ? 'border-red-300 text-red-600 dark:border-red-900 dark:text-red-400'
    : item.done
      ? 'border-gray-200 text-gray-500 dark:border-gray-800 dark:text-gray-400'
      : 'border-blue-300 text-blue-600 dark:border-blue-900 dark:text-blue-400'

  return (
    <div className="pl-8">
      <button
        type="button"
        onClick={() => expandable && setOpen((v) => !v)}
        className={`flex max-w-full items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-mono ${tone} ${
          expandable ? 'cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-900' : 'cursor-default'
        }`}
      >
        {item.done ? (
          <Terminal size={12} className="shrink-0" />
        ) : (
          <Loader2 size={12} className="shrink-0 animate-spin" />
        )}
        <span className="font-semibold shrink-0">{item.name}</span>
        {item.detail && (
          <span className="min-w-0 truncate opacity-70">({item.detail})</span>
        )}
        {item.done && !item.isError && <span className="shrink-0 text-green-500">✓</span>}
        {item.isError && <span className="shrink-0">✗</span>}
        {expandable && (
          <ChevronRight
            size={12}
            className={`shrink-0 transition-transform ${open ? 'rotate-90' : ''}`}
          />
        )}
      </button>
      {open && (
        <pre className="mt-1 max-h-48 overflow-auto rounded-lg bg-gray-900 dark:bg-black/60 px-3 py-2 text-[11px] leading-relaxed text-gray-200 whitespace-pre-wrap break-words">
          {item.result}
        </pre>
      )}
    </div>
  )
}
