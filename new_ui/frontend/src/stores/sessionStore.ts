import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import { sessionsApi } from '../services/api';
import type { SessionDetail, SessionSummary } from '../types/api';

interface SessionState {
  activeSessionId: string | null;
  sessions: SessionSummary[];
  activeSession: SessionDetail | null;
  isLoading: boolean;
  error: string | null;
  lastLoadedAt: string | null;
  _requestSeq: number;

  // User preferences
  preferences: {
    llmProvider: string;
    enableIndexing: boolean;
    theme: 'light' | 'dark';
  };

  // Actions
  setActiveSessionId: (id: string | null) => void;
  loadSessions: () => Promise<void>;
  selectSession: (id: string) => Promise<SessionDetail | null>;
  createSession: (title?: string) => Promise<SessionDetail | null>;
  deleteSession: (id: string) => Promise<void>;
  branchSession: (
    id: string,
    fromMessageIndex: number,
    title?: string
  ) => Promise<SessionDetail | null>;
  refreshActiveSession: () => Promise<void>;
  updatePreferences: (prefs: Partial<SessionState['preferences']>) => void;
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set, get) => ({
      activeSessionId: null,
      sessions: [],
      activeSession: null,
      isLoading: false,
      error: null,
      lastLoadedAt: null,
      _requestSeq: 0,
      preferences: {
        llmProvider: 'google',
        enableIndexing: false,
        theme: 'light',
      },

      setActiveSessionId: (id) => {
        set({ activeSessionId: id });
      },

      loadSessions: async () => {
        const seq = get()._requestSeq + 1;
        set({ isLoading: true, error: null, _requestSeq: seq });
        try {
          const data = await sessionsApi.list();
          if (get()._requestSeq !== seq) return;
          set({
            sessions: data.sessions,
            isLoading: false,
            lastLoadedAt: new Date().toISOString(),
          });
        } catch (error) {
          if (get()._requestSeq !== seq) return;
          set({
            error:
              error instanceof Error
                ? error.message
                : 'Failed to load sessions',
            isLoading: false,
          });
        }
      },

      selectSession: async (id) => {
        const seq = get()._requestSeq + 1;
        set({
          activeSessionId: id,
          isLoading: true,
          error: null,
          _requestSeq: seq,
        });
        try {
          const session = await sessionsApi.get(id);
          if (get()._requestSeq !== seq) return null;
          set({
            activeSession: session,
            activeSessionId: session.session_id,
            isLoading: false,
            lastLoadedAt: new Date().toISOString(),
          });
          return session;
        } catch (error) {
          if (get()._requestSeq !== seq) return null;
          set({
            error:
              error instanceof Error
                ? error.message
                : 'Failed to load session',
            isLoading: false,
          });
          return null;
        }
      },

      createSession: async (title = '') => {
        set({ isLoading: true, error: null });
        try {
          const session = await sessionsApi.create(title);
          set((state) => ({
            activeSession: session,
            activeSessionId: session.session_id,
            sessions: [
              {
                session_id: session.session_id,
                title: session.title,
                created_at: session.created_at,
                updated_at: session.updated_at,
                message_count: session.messages.length,
                task_count: session.tasks.length,
              },
              ...state.sessions.filter(
                (s) => s.session_id !== session.session_id
              ),
            ],
            isLoading: false,
            lastLoadedAt: new Date().toISOString(),
          }));
          return session;
        } catch (error) {
          set({
            error:
              error instanceof Error
                ? error.message
                : 'Failed to create session',
            isLoading: false,
          });
          return null;
        }
      },

      deleteSession: async (id) => {
        await sessionsApi.delete(id);
        set((state) => ({
          sessions: state.sessions.filter((s) => s.session_id !== id),
          activeSessionId:
            state.activeSessionId === id ? null : state.activeSessionId,
          activeSession:
            state.activeSession?.session_id === id ? null : state.activeSession,
        }));
      },

      branchSession: async (id, fromMessageIndex, title) => {
        set({ isLoading: true, error: null });
        try {
          const session = await sessionsApi.branch(id, fromMessageIndex, title);
          set((state) => ({
            activeSession: session,
            activeSessionId: session.session_id,
            sessions: [
              {
                session_id: session.session_id,
                title: session.title,
                created_at: session.created_at,
                updated_at: session.updated_at,
                message_count: session.messages.length,
                task_count: session.tasks.length,
              },
              ...state.sessions,
            ],
            isLoading: false,
          }));
          return session;
        } catch (error) {
          set({
            error:
              error instanceof Error
                ? error.message
                : 'Failed to branch session',
            isLoading: false,
          });
          return null;
        }
      },

      refreshActiveSession: async () => {
        const { activeSessionId } = get();
        if (!activeSessionId) return;
        await get().selectSession(activeSessionId);
        await get().loadSessions();
      },

      updatePreferences: (prefs) =>
        set((state) => ({
          preferences: { ...state.preferences, ...prefs },
        })),
    }),
    {
      name: 'deepcode-session',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        activeSessionId: state.activeSessionId,
        preferences: state.preferences,
      }),
    }
  )
);
