/**
 * Agent Chat — the Claude Code desktop analogue.
 *
 * Continuous multi-turn conversations with the DeepCode agent kernel. A
 * sidebar of past chats (rename / delete / New chat), a thread that renders
 * the live SQ/EQ event stream (streamed markdown, expandable tool cards),
 * and an always-available composer. All state lives in useAgentChat; this
 * page is composition only.
 */
import { useState } from 'react'
import { Bot, FolderOpen, FolderSearch, MessageSquarePlus } from 'lucide-react'
import { useAgentChat } from '../hooks/useAgentChat'
import ChatSidebar from '../components/agent/ChatSidebar'
import MessageList from '../components/agent/MessageList'
import Composer from '../components/agent/Composer'
import WorkspacePicker from '../components/agent/WorkspacePicker'

export default function AgentChatPage() {
  const chat = useAgentChat()
  const [wsInput, setWsInput] = useState('')
  const [pickerOpen, setPickerOpen] = useState(false)

  const startNew = () => {
    chat.startDraft(wsInput.trim())
  }

  const isDraft = chat.draft !== null
  const showThread = chat.activeId !== null || isDraft
  const composerDisabled = isDraft ? false : chat.activeId === null || !chat.isConnected

  const workspaceSlot = (
    <div className="flex items-center gap-1">
      <input
        value={wsInput}
        onChange={(e) => setWsInput(e.target.value)}
        placeholder="Workspace folder (optional)"
        title="Directory the agent works in for the next new chat; blank = an isolated per-chat dir"
        className="min-w-0 flex-1 rounded-lg border border-gray-200 bg-transparent px-3 py-1.5 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-gray-800"
      />
      <button
        onClick={() => setPickerOpen(true)}
        title="Browse folders"
        className="shrink-0 rounded-lg border border-gray-200 p-1.5 text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:border-gray-800 dark:hover:bg-gray-800"
      >
        <FolderSearch size={15} />
      </button>
    </div>
  )

  return (
    <div className="flex h-[calc(100vh-7rem)] overflow-hidden rounded-xl border border-gray-200 dark:border-gray-800 lg:h-[calc(100vh-8rem)]">
      <ChatSidebar
        chats={chat.chats}
        activeId={chat.activeId}
        isDraft={isDraft}
        onSelect={chat.selectChat}
        onNew={startNew}
        onRename={chat.renameChat}
        onDelete={chat.deleteChat}
        workspaceSlot={workspaceSlot}
      />

      <section className="flex min-w-0 flex-1 flex-col">
        {!showThread ? (
          <EmptyState onNew={startNew} />
        ) : (
          <>
            <WorkspaceHeader workspace={chat.activeWorkspace} isDraft={isDraft} />
            <MessageList
              thread={chat.thread}
              streamText={chat.streamText}
              running={chat.running}
            />
            <Composer
              disabled={composerDisabled}
              running={chat.running}
              onSend={chat.send}
              onInterrupt={chat.interrupt}
              placeholder={
                isDraft
                  ? 'Describe your first task to start this conversation…'
                  : chat.isConnected
                    ? undefined
                    : 'connecting…'
              }
            />
          </>
        )}
      </section>

      {pickerOpen && (
        <WorkspacePicker
          onSelect={(path) => {
            setWsInput(path)
            setPickerOpen(false)
          }}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  )
}

function WorkspaceHeader({
  workspace,
  isDraft,
}: {
  workspace: string
  isDraft: boolean
}) {
  return (
    <div className="flex items-center gap-2 overflow-hidden border-b border-gray-200 px-6 py-2 text-xs font-mono text-gray-400 dark:border-gray-800">
      <FolderOpen size={13} className="shrink-0" />
      <span className="min-w-0 flex-1 truncate" title={workspace}>
        {workspace || (isDraft ? 'isolated per-chat directory' : '')}
      </span>
      {isDraft && <span className="shrink-0 text-blue-500">draft</span>}
    </div>
  )
}

function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 text-gray-400">
      <Bot size={40} />
      <p className="text-sm">Pick a conversation or start a new one.</p>
      <button
        onClick={onNew}
        className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
      >
        <MessageSquarePlus size={16} /> New chat
      </button>
    </div>
  )
}
