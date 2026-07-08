/**
 * ChatSidebar — the list of conversations + New chat.
 *
 * Each row reveals rename (inline edit) and delete (with confirm) on hover.
 * "New chat" starts a draft; the workspace picker sits above it. Empty rows
 * never appear here because sessions are created lazily on first message.
 */
import { useState } from 'react'
import { Check, MessageSquarePlus, Pencil, Trash2, X } from 'lucide-react'
import type { ChatSummary } from '../../hooks/useAgentChat'
import { relativeTime } from '../../utils/time'

interface Props {
  chats: ChatSummary[]
  activeId: string | null
  isDraft: boolean
  onSelect: (id: string) => void
  onNew: () => void
  onRename: (id: string, title: string) => void
  onDelete: (id: string) => void
  workspaceSlot?: React.ReactNode
}

export default function ChatSidebar({
  chats,
  activeId,
  isDraft,
  onSelect,
  onNew,
  onRename,
  onDelete,
  workspaceSlot,
}: Props) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [confirmId, setConfirmId] = useState<string | null>(null)

  const startEdit = (c: ChatSummary) => {
    setEditingId(c.session_id)
    setEditValue(c.title === '(untitled)' ? '' : c.title)
  }
  const commitEdit = (id: string) => {
    const v = editValue.trim()
    if (v) onRename(id, v)
    setEditingId(null)
  }

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-gray-200 dark:border-gray-800">
      <div className="space-y-2 p-3">
        {workspaceSlot}
        <button
          onClick={onNew}
          className={`flex w-full items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium ${
            isDraft
              ? 'bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300'
              : 'bg-blue-600 text-white hover:bg-blue-700'
          }`}
        >
          <MessageSquarePlus size={16} /> New chat
        </button>
      </div>

      <div className="flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {chats.map((c) => {
          const active = c.session_id === activeId
          const editing = editingId === c.session_id
          return (
            <div
              key={c.session_id}
              className={`group relative rounded-lg transition-colors ${
                active
                  ? 'bg-blue-50 dark:bg-blue-950'
                  : 'hover:bg-gray-100 dark:hover:bg-gray-800'
              }`}
            >
              {editing ? (
                <div className="flex items-center gap-1 px-2 py-1.5">
                  <input
                    autoFocus
                    value={editValue}
                    onChange={(e) => setEditValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') commitEdit(c.session_id)
                      if (e.key === 'Escape') setEditingId(null)
                    }}
                    className="min-w-0 flex-1 rounded border border-blue-300 bg-white px-2 py-1 text-sm dark:bg-gray-900"
                  />
                  <button onClick={() => commitEdit(c.session_id)} title="Save">
                    <Check size={14} className="text-green-600" />
                  </button>
                  <button onClick={() => setEditingId(null)} title="Cancel">
                    <X size={14} className="text-gray-400" />
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => onSelect(c.session_id)}
                  className="w-full px-3 py-2 pr-14 text-left"
                >
                  <div
                    className={`truncate text-sm font-medium ${
                      active
                        ? 'text-blue-700 dark:text-blue-300'
                        : 'text-gray-700 dark:text-gray-300'
                    }`}
                  >
                    {c.title || '(untitled)'}
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 text-xs text-gray-400">
                    <span>{c.message_count} msgs</span>
                    <span>·</span>
                    <span>{relativeTime(c.updated_at)}</span>
                  </div>
                </button>
              )}

              {!editing && confirmId !== c.session_id && (
                <div className="absolute right-1 top-1.5 flex gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                  <IconBtn title="Rename" onClick={() => startEdit(c)}>
                    <Pencil size={13} />
                  </IconBtn>
                  <IconBtn title="Delete" onClick={() => setConfirmId(c.session_id)}>
                    <Trash2 size={13} />
                  </IconBtn>
                </div>
              )}

              {confirmId === c.session_id && (
                <div className="absolute right-1 top-1 flex items-center gap-1 rounded-md bg-white px-1.5 py-1 text-xs shadow dark:bg-gray-900">
                  <span className="text-gray-500">Delete?</span>
                  <button
                    onClick={() => {
                      onDelete(c.session_id)
                      setConfirmId(null)
                    }}
                    className="font-semibold text-red-600"
                  >
                    Yes
                  </button>
                  <button onClick={() => setConfirmId(null)} className="text-gray-400">
                    No
                  </button>
                </div>
              )}
            </div>
          )
        })}

        {chats.length === 0 && !isDraft && (
          <p className="px-3 pt-2 text-xs text-gray-400">
            No conversations yet — start one.
          </p>
        )}
      </div>
    </aside>
  )
}

function IconBtn({
  children,
  title,
  onClick,
}: {
  children: React.ReactNode
  title: string
  onClick: () => void
}) {
  return (
    <button
      title={title}
      onClick={onClick}
      className="rounded p-1 text-gray-400 hover:bg-gray-200 hover:text-gray-700 dark:hover:bg-gray-700 dark:hover:text-gray-200"
    >
      {children}
    </button>
  )
}
