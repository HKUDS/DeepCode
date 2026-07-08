/**
 * Composer — the message input. Enter sends, Shift+Enter newlines; the send
 * button becomes an interrupt while a turn is running.
 */
import { useState } from 'react'
import { Send, StopCircle } from 'lucide-react'

interface Props {
  disabled: boolean
  running: boolean
  onSend: (text: string) => void
  onInterrupt: () => void
  placeholder?: string
}

export default function Composer({
  disabled,
  running,
  onSend,
  onInterrupt,
  placeholder,
}: Props) {
  const [value, setValue] = useState('')

  const submit = () => {
    const text = value.trim()
    if (!text || disabled || running) return
    onSend(text)
    setValue('')
  }

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
    </div>
  )
}
