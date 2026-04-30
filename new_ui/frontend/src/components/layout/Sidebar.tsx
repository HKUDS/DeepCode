import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  FileText,
  MessageSquare,
  GitBranch,
  Clock,
  Loader2,
  Plus,
  Trash2,
} from 'lucide-react';
import { useState } from 'react';
import { useSessionStore } from '../../stores/sessionStore';
import { ConfirmDialog } from '../common/ConfirmDialog';
import type { SessionSummary } from '../../types/api';

export default function Sidebar() {
  const location = useLocation();
  const navigate = useNavigate();
  const [sessionToDelete, setSessionToDelete] = useState<SessionSummary | null>(null);
  const {
    activeSessionId,
    sessions,
    isLoading,
    createSession,
    deleteSession,
    selectSession,
  } = useSessionStore();

  const menuItems = [
    {
      path: '/paper-to-code',
      icon: FileText,
      label: 'Paper to Code',
      description: 'Convert research papers',
    },
    {
      path: '/chat',
      icon: MessageSquare,
      label: 'Chat Planning',
      description: 'Describe your project',
    },
    {
      path: '/workflow',
      icon: GitBranch,
      label: 'Workflow Editor',
      description: 'Visual workflow design',
    },
  ];

  const getSessionTitle = (session: SessionSummary) =>
    session.title || `Session ${session.session_id}`;

  const formatRelativeTime = (value: string) => {
    const time = new Date(value).getTime();
    const diff = Date.now() - time;
    const minutes = Math.max(1, Math.floor(diff / 60000));
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  };

  const handleNewSession = async () => {
    const session = await createSession('New session');
    if (session) {
      navigate('/chat');
    }
  };

  const handleSelectSession = async (sessionId: string) => {
    const session = await selectSession(sessionId);
    const latestTask = session?.tasks?.[session.tasks.length - 1];
    if (latestTask?.task_kind === 'chat' || latestTask?.task_kind === 'requirement') {
      navigate('/chat');
    } else if (latestTask?.task_kind === 'paper' || latestTask?.task_kind === 'url') {
      navigate('/paper-to-code');
    }
  };

  const handleConfirmDelete = async () => {
    if (!sessionToDelete) return;
    await deleteSession(sessionToDelete.session_id);
    setSessionToDelete(null);
  };

  return (
    <aside className="hidden lg:flex flex-col w-72 min-h-[calc(100vh-4rem)] border-r border-gray-200 bg-white">
      <div className="flex-1 p-4">
        {/* Quick Actions */}
        <div className="mb-6">
          <h3 className="px-3 text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            Quick Actions
          </h3>
          <nav className="space-y-1">
            {menuItems.map((item) => {
              const Icon = item.icon;
              const isActive = location.pathname === item.path;

              return (
                <Link
                  key={item.path}
                  to={item.path}
                  className={`flex items-start space-x-3 px-3 py-2.5 rounded-lg transition-colors ${
                    isActive
                      ? 'bg-primary-50 text-primary-700'
                      : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                  }`}
                >
                  <Icon
                    className={`h-5 w-5 mt-0.5 ${
                      isActive ? 'text-primary-600' : 'text-gray-400'
                    }`}
                  />
                  <div>
                    <div className="font-medium text-sm">{item.label}</div>
                    <div
                      className={`text-xs ${
                        isActive ? 'text-primary-600/70' : 'text-gray-400'
                      }`}
                    >
                      {item.description}
                    </div>
                  </div>
                </Link>
              );
            })}
          </nav>
        </div>

        {/* Sessions */}
        <div>
          <div className="px-3 mb-2 flex items-center justify-between">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider flex items-center">
              <Clock className="h-3 w-3 mr-1.5" />
              Sessions
            </h3>
            <button
              onClick={handleNewSession}
              className="p-1 rounded text-gray-400 hover:text-primary-600 hover:bg-primary-50"
              title="New session"
            >
              <Plus className="h-4 w-4" />
            </button>
          </div>
          {isLoading && sessions.length === 0 ? (
            <div className="px-3 py-3 text-sm text-gray-400 flex items-center">
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              Loading sessions...
            </div>
          ) : sessions.length === 0 ? (
            <div className="px-3 py-3 text-sm text-gray-400">
              No sessions yet. Start a new task or create a session.
            </div>
          ) : (
            <div className="space-y-1 max-h-[32rem] overflow-y-auto pr-1">
              {sessions.slice(0, 30).map((session) => {
                const isActive = activeSessionId === session.session_id;
                return (
                  <div
                    key={session.session_id}
                    className={`group rounded-lg transition-colors ${
                      isActive
                        ? 'bg-primary-50 text-primary-700'
                        : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                    }`}
                  >
                    <button
                      onClick={() => handleSelectSession(session.session_id)}
                      className="w-full px-3 py-2 text-left"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="text-sm font-medium truncate">
                            {getSessionTitle(session)}
                          </div>
                          <div className="text-xs text-gray-400 truncate">
                            {session.message_count} msg · {session.task_count} task
                            {session.task_count === 1 ? '' : 's'} ·{' '}
                            {formatRelativeTime(session.updated_at)}
                          </div>
                        </div>
                        <span className="text-[10px] font-mono text-gray-400">
                          {session.session_id}
                        </span>
                      </div>
                    </button>
                    <div className="hidden group-hover:flex px-3 pb-2 justify-end">
                      <button
                        onClick={() => setSessionToDelete(session)}
                        className="inline-flex items-center text-xs text-red-500 hover:text-red-700"
                      >
                        <Trash2 className="h-3 w-3 mr-1" />
                        Delete
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="p-4 border-t border-gray-100">
        <div className="flex items-center justify-center space-x-2 text-xs text-gray-400">
          <img
            src="https://github.com/Zongwei9888/Experiment_Images/raw/43c585dca3d21b8e4b6390d835cdd34dc4b4b23d/DeepCode_images/title_logo.svg"
            alt="DeepCode"
            className="h-4 w-4"
          />
          <span>DeepCode v1.0.0</span>
        </div>
      </div>
      <ConfirmDialog
        isOpen={sessionToDelete !== null}
        title="Delete Session?"
        message={`Delete "${sessionToDelete ? getSessionTitle(sessionToDelete) : ''}"? This removes the persisted conversation and task history for this session.`}
        confirmLabel="Delete"
        cancelLabel="Cancel"
        variant="danger"
        onConfirm={handleConfirmDelete}
        onCancel={() => setSessionToDelete(null)}
      />
    </aside>
  );
}
