/**
 * Composer — the message input. Enter sends, Shift+Enter newlines; the send
 * button becomes an interrupt while a turn is running.
 */
import { useState } from 'react'
import { Send, StopCircle, Cpu } from 'lucide-react'
import type { ModelInfo } from '../../hooks/useAgentChat'

interface Props {
  disabled: boolean
  running: boolean
  onSend: (text: string) => void
  onInterrupt: () => void
  placeholder?: string
  modelInfo?: ModelInfo | null
}

export default function Composer({
  disabled,
  running,
  onSend,
  onInterrupt,
  placeholder,
  modelInfo,
}: Props) {
  const [value, setValue] = useState('')

  const submit = () => {
    const text = value.trim()
    if (!text || disabled || running) return
    onSend(text)
    setValue('')
  }

  const tierBadge = modelInfo
    ? modelInfo.tier === 'free'
      ? 'bg-green-100 text-green-700 border-green-300'
      : modelInfo.tier === 'paid'
        ? 'bg-amber-100 text-amber-700 border-amber-300'
        : 'bg-gray-100 text-gray-500 border-gray-300'
    : 'bg-gray-100 text-gray-400 border-gray-200'

  return (
    <div className="border-t border-gray-200 dark:border-gray-800 p-4">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
          rows={Math.min(6, Math.max(1, value.split('\n').length))}
          placeholder={placeholder ?? 'Ask the agent anything… (Enter to send, Shift+Enter for newline)'}
          className="flex-1 resize-none rounded-xl border border-gray-300 bg-white px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-700 dark:bg-gray-900"
        />
        {running ? (
          <button
            onClick={onInterrupt}
            title="Interrupt"
            className="rounded-xl bg-red-600 p-3 text-white hover:bg-red-700"
          >
            <StopCircle size={18} />
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={!value.trim() || disabled}
            title="Send"
            className="rounded-xl bg-blue-600 p-3 text-white hover:bg-blue-700 disabled:opacity-40"
          >
            <Send size={18} />
          </button>
        )}
      </div>

      {/* Model status bar */}
      {modelInfo && (
        <div className="mx-auto mt-2 flex max-w-3xl items-center gap-2">
          <span
            className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${tierBadge}`}
            title={`Current model: ${modelInfo.model}`}
          >
            <Cpu size={11} />
            <span className="max-w-[160px] truncate">{modelInfo.model}</span>
            <span className="ml-0.5 rounded-full bg-white/60 px-1 text-[10px] font-bold">
              {modelInfo.tier === 'free' ? '免费' : modelInfo.tier === 'paid' ? '付费' : '?'}
            </span>
          </span>
        </div>
      )}
    </div>
  )
}
