import { Link, useLocation, useNavigate } from 'react-router-dom';
import {
  Settings,
  Menu,
  Loader2,
  Clock,
  Plus,
  ChevronDown,
  Trash2,
} from 'lucide-react';
import { useState, type MouseEvent } from 'react';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useSessionStore } from '../../stores/sessionStore';

export default function Header() {
  const location = useLocation();
  const navigate = useNavigate();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isSessionMenuOpen, setIsSessionMenuOpen] = useState(false);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(
    null
  );
  const [sessionDeleteError, setSessionDeleteError] = useState<string | null>(
    null
  );

  const { status, workflowType, progress } = useWorkflowStore();
  const {
    activeSessionId,
    activeSession,
    sessions,
    isLoading,
    createSession,
    selectSession,
    deleteSession,
  } = useSessionStore();
  const isRunning = status === 'running' || status === 'waiting_for_input';

  const navItems = [
    { path: '/', label: 'Home' },
    { path: '/paper-to-code', label: 'Paper to Code' },
    { path: '/chat', label: 'Chat Planning' },
    { path: '/workflow', label: 'Workflow' },
  ];

  const getSessionTitle = () =>
    activeSession?.title || activeSessionId || 'Sessions';

  const routeForSession = (session: Awaited<ReturnType<typeof selectSession>>) => {
    const latestTask = session?.tasks?.[session.tasks.length - 1];
    if (latestTask?.task_kind === 'chat' || latestTask?.task_kind === 'requirement') {
      navigate('/chat');
    } else if (
      latestTask?.task_kind === 'paper' ||
      latestTask?.task_kind === 'paper2code' ||
      latestTask?.task_kind === 'url'
    ) {
      navigate('/paper-to-code');
    }
  };

  const handleSelectSession = async (sessionId: string) => {
    const session = await selectSession(sessionId);
    setIsSessionMenuOpen(false);
    routeForSession(session);
  };

  const handleNewSession = async () => {
    const session = await createSession('New session');
    setIsSessionMenuOpen(false);
    if (session) navigate('/chat');
  };

  const handleDeleteSession = async (
    event: MouseEvent<HTMLButtonElement>,
    sessionId: string
  ) => {
    event.stopPropagation();
    setSessionDeleteError(null);
    const confirmed = window.confirm(
      'Delete this session? This removes the session history, task workspaces, generated files, and logs for this session. Uploaded source files are kept.'
    );
    if (!confirmed) return;

    setDeletingSessionId(sessionId);
    try {
      await deleteSession(sessionId);
    } catch (error) {
      setSessionDeleteError(
        error instanceof Error ? error.message : 'Failed to delete session'
      );
    } finally {
      setDeletingSessionId(null);
    }
  };

  return (
    <header className="sticky top-0 z-50 border-b border-gray-200 bg-white/80 backdrop-blur-sm">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="flex h-16 items-center justify-between">
          {/* Logo */}
          <Link to="/" className="flex items-center space-x-2">
            <img
              src="https://github.com/Zongwei9888/Experiment_Images/raw/43c585dca3d21b8e4b6390d835cdd34dc4b4b23d/DeepCode_images/title_logo.svg"
              alt="DeepCode Logo"
              className="h-8 w-8"
            />
            <span className="text-xl font-semibold text-gray-900">
              DeepCode
            </span>
          </Link>

          {/* Desktop Navigation */}
          <nav className="hidden md:flex items-center space-x-1">
            {navItems.map((item) => (
              <Link
                key={item.path}
                to={item.path}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                  location.pathname === item.path
                    ? 'bg-primary-50 text-primary-600'
                    : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
                }`}
              >
                {item.label}
              </Link>
            ))}
          </nav>

          {/* Right Side */}
          <div className="flex items-center space-x-3">
            <div className="relative">
              <button
                onClick={() => setIsSessionMenuOpen((open) => !open)}
                className="flex max-w-[12rem] items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:border-primary-200 hover:bg-primary-50 hover:text-primary-700 sm:max-w-[16rem]"
                title="Select session"
              >
                <Clock className="h-4 w-4 flex-shrink-0 text-primary-500" />
                <span className="truncate">{getSessionTitle()}</span>
                <ChevronDown className="h-4 w-4 flex-shrink-0 text-gray-400" />
              </button>

              {isSessionMenuOpen && (
                <div className="absolute right-0 mt-2 w-[calc(100vw-2rem)] max-w-sm overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl">
                  <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
                    <div>
                      <div className="text-sm font-semibold text-gray-900">Sessions</div>
                      <div className="text-xs text-gray-400">
                        Resume or start a workspace
                      </div>
                    </div>
                    <button
                      onClick={handleNewSession}
                      className="inline-flex items-center gap-1 rounded-lg bg-primary-50 px-2.5 py-1.5 text-xs font-medium text-primary-700 hover:bg-primary-100"
                    >
                      <Plus className="h-3.5 w-3.5" />
                      New
                    </button>
                  </div>

                  <div className="max-h-80 overflow-y-auto p-2">
                    {sessionDeleteError && (
                      <div className="mb-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
                        {sessionDeleteError}
                      </div>
                    )}
                    {isLoading && sessions.length === 0 ? (
                      <div className="flex items-center px-3 py-4 text-sm text-gray-400">
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Loading sessions...
                      </div>
                    ) : sessions.length === 0 ? (
                      <div className="px-3 py-4 text-sm text-gray-400">
                        No sessions yet. Start a task or create one.
                      </div>
                    ) : (
                      sessions.slice(0, 12).map((session) => {
                        const isActive = activeSessionId === session.session_id;
                        return (
                          <div
                            key={session.session_id}
                            className={`group flex w-full items-start gap-2 rounded-lg px-3 py-2 text-left transition-colors ${
                              isActive
                                ? 'bg-primary-50 text-primary-700'
                                : 'text-gray-700 hover:bg-gray-50'
                            }`}
                          >
                            <button
                              onClick={() => handleSelectSession(session.session_id)}
                              className="min-w-0 flex-1 text-left"
                            >
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                  <div className="truncate text-sm font-medium">
                                    {session.title || `Session ${session.session_id}`}
                                  </div>
                                  <div className="text-xs text-gray-400">
                                    {session.message_count} msg · {session.task_count} task
                                    {session.task_count === 1 ? '' : 's'}
                                  </div>
                                </div>
                                <span className="mt-0.5 flex-shrink-0 font-mono text-[10px] text-gray-400">
                                  {session.session_id}
                                </span>
                              </div>
                            </button>
                            <button
                              onClick={(event) =>
                                handleDeleteSession(event, session.session_id)
                              }
                              disabled={deletingSessionId === session.session_id}
                              className="mt-0.5 rounded-md p-1.5 text-gray-300 transition-colors hover:bg-red-50 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-60 group-hover:text-gray-500"
                              title="Delete session"
                              aria-label={`Delete session ${session.session_id}`}
                            >
                              {deletingSessionId === session.session_id ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                              ) : (
                                <Trash2 className="h-4 w-4" />
                              )}
                            </button>
                          </div>
                        );
                      })
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Running Task Indicator */}
            {isRunning && (
              <button
                onClick={() => {
                  if (workflowType === 'chat-planning') {
                    navigate('/chat');
                  } else if (workflowType === 'paper-to-code') {
                    navigate('/paper-to-code');
                  }
                }}
                className="flex items-center space-x-2 px-3 py-1.5 bg-blue-50 border border-blue-200 rounded-full text-sm font-medium text-blue-700 hover:bg-blue-100 transition-colors"
              >
                <Loader2 className="h-4 w-4 animate-spin" />
                <span className="hidden sm:inline">Task Running</span>
                <span className="text-blue-500">{progress}%</span>
              </button>
            )}

            <Link
              to="/settings"
              className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-700 transition-colors"
            >
              <Settings className="h-5 w-5" />
            </Link>

            {/* Mobile menu button */}
            <button
              className="md:hidden p-2 rounded-lg text-gray-500 hover:bg-gray-100"
              onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
            >
              <Menu className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Mobile Navigation */}
        {isMobileMenuOpen && (
          <nav className="md:hidden py-4 border-t border-gray-100">
            {navItems.map((item) => (
              <Link
                key={item.path}
                to={item.path}
                className={`block px-4 py-2 rounded-lg text-sm font-medium ${
                  location.pathname === item.path
                    ? 'bg-primary-50 text-primary-600'
                    : 'text-gray-600 hover:bg-gray-100'
                }`}
                onClick={() => setIsMobileMenuOpen(false)}
              >
                {item.label}
              </Link>
            ))}
          </nav>
        )}
      </div>
    </header>
  );
}
