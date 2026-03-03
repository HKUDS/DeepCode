import { ReactNode, useState } from 'react';
import Header from './Header';
import Sidebar from './Sidebar';
import { TaskRecoveryBanner } from '../common/TaskRecoveryBanner';
import { ConfirmDialog } from '../common/ConfirmDialog';
import { useTaskRecovery } from '../../hooks/useTaskRecovery';
import { useNavigationGuard } from '../../hooks/useNavigationGuard';

interface LayoutProps {
  children: ReactNode;
}

export default function Layout({ children }: LayoutProps) {
  const { isRecovering, recoveredTaskId } = useTaskRecovery();
  const [showBanner, setShowBanner] = useState(true);

  const {
    showConfirmDialog,
    confirmNavigation,
    cancelNavigation,
  } = useNavigationGuard();

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Task Recovery Banner */}
      {showBanner && (
        <TaskRecoveryBanner
          isRecovering={isRecovering}
          recoveredTaskId={recoveredTaskId}
          onDismiss={() => setShowBanner(false)}
        />
      )}

      {/* Navigation Confirmation Dialog */}
      <ConfirmDialog
        isOpen={showConfirmDialog}
        title="Task is still running"
        message="A task is currently running. If you leave this page, the task will continue in the background, but you may lose track of its progress. Are you sure you want to leave?"
        confirmLabel="Leave"
        cancelLabel="Stay"
        variant="warning"
        onConfirm={confirmNavigation}
        onCancel={cancelNavigation}
      />

      <Header />
      <div className="flex">
        <Sidebar />
        <main className="flex-1 p-6 lg:p-8">
          <div className="mx-auto max-w-7xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
