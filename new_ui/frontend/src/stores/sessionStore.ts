import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { Message } from '../types/common';

interface SessionState {
  // Session
  sessionId: string | null;

  // Conversation history
  conversationHistory: Message[];

  // User preferences
  preferences: {
    llmProvider: string;
    enableIndexing: boolean;
    theme: 'light' | 'dark';
  };

  // Recent projects
  recentProjects: {
    id: string;
    name: string;
    type: string;
    timestamp: string;
  }[];

  // Actions
  setSessionId: (id: string | null) => void;
  addMessage: (message: Omit<Message, 'id' | 'timestamp'>) => void;
  clearHistory: () => void;
  updatePreferences: (prefs: Partial<SessionState['preferences']>) => void;
  addRecentProject: (project: Omit<SessionState['recentProjects'][0], 'timestamp'>) => void;
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set, get) => ({
      sessionId: null,
      conversationHistory: [],
      preferences: {
        llmProvider: 'google',
        enableIndexing: false,
        theme: 'light',
      },
      recentProjects: [],

      setSessionId: (id) => set({ sessionId: id }),

      addMessage: (message) => {
        const newMessage: Message = {
          ...message,
          id: crypto.randomUUID(),
          timestamp: new Date().toISOString(),
        };
        set((state) => ({
          conversationHistory: [...state.conversationHistory, newMessage],
        }));
      },

      clearHistory: () => set({ conversationHistory: [] }),

      updatePreferences: (prefs) =>
        set((state) => ({
          preferences: { ...state.preferences, ...prefs },
        })),

      addRecentProject: (project) => {
        const newProject = {
          ...project,
          timestamp: new Date().toISOString(),
        };
        set((state) => ({
          recentProjects: [newProject, ...state.recentProjects.slice(0, 9)],
        }));
      },
    }),
    {
      name: 'deepcode-session',
      partialize: (state) => ({
        preferences: state.preferences,
        recentProjects: state.recentProjects,
      }),
    }
  )
);
