/**
 * Navigation Guard Hook
 *
 * Prevents accidental navigation away from a page when a task is running.
 * - Shows browser warning on refresh/close (beforeunload)
 * - Shows confirmation dialog on in-app navigation
 */

import { useEffect, useCallback, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useWorkflowStore } from '../stores/workflowStore';

interface NavigationGuardState {
  isBlocking: boolean;
  pendingPath: string | null;
  showConfirmDialog: boolean;
}

export function useNavigationGuard() {
  const { status } = useWorkflowStore();
  const navigate = useNavigate();
  const location = useLocation();

  const [guardState, setGuardState] = useState<NavigationGuardState>({
    isBlocking: false,
    pendingPath: null,
    showConfirmDialog: false,
  });

  // Determine if we should block navigation
  const shouldBlock = status === 'running';

  // Handle browser beforeunload event (refresh, close tab, close browser)
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (shouldBlock) {
        e.preventDefault();
        // Chrome requires returnValue to be set
        e.returnValue = 'A task is still running. Are you sure you want to leave?';
        return e.returnValue;
      }
    };

    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [shouldBlock]);

  // Update blocking state
  useEffect(() => {
    setGuardState(prev => ({ ...prev, isBlocking: shouldBlock }));
  }, [shouldBlock]);

  // Function to attempt navigation (called by NavLink wrapper)
  const attemptNavigation = useCallback((path: string) => {
    if (shouldBlock && path !== location.pathname) {
      setGuardState({
        isBlocking: true,
        pendingPath: path,
        showConfirmDialog: true,
      });
      return false; // Block navigation
    }
    return true; // Allow navigation
  }, [shouldBlock, location.pathname]);

  // Confirm navigation (user clicked "Leave" in dialog)
  const confirmNavigation = useCallback(() => {
    const { pendingPath } = guardState;
    setGuardState({
      isBlocking: false,
      pendingPath: null,
      showConfirmDialog: false,
    });
    if (pendingPath) {
      navigate(pendingPath);
    }
  }, [guardState.pendingPath, navigate]);

  // Cancel navigation (user clicked "Stay" in dialog)
  const cancelNavigation = useCallback(() => {
    setGuardState(prev => ({
      ...prev,
      pendingPath: null,
      showConfirmDialog: false,
    }));
  }, []);

  return {
    isBlocking: guardState.isBlocking,
    showConfirmDialog: guardState.showConfirmDialog,
    pendingPath: guardState.pendingPath,
    attemptNavigation,
    confirmNavigation,
    cancelNavigation,
  };
}
