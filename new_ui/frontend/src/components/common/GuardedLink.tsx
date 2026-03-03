/**
 * Guarded Link Component
 *
 * A Link component that respects the navigation guard.
 * Shows confirmation dialog when trying to navigate away during a running task.
 */

import { Link, LinkProps, useLocation } from 'react-router-dom';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useState } from 'react';
import { ConfirmDialog } from './ConfirmDialog';

interface GuardedLinkProps extends Omit<LinkProps, 'onClick'> {
  children: React.ReactNode;
}

export function GuardedLink({ to, children, ...props }: GuardedLinkProps) {
  const { status } = useWorkflowStore();
  const location = useLocation();
  const [showDialog, setShowDialog] = useState(false);

  const shouldBlock = status === 'running';
  const targetPath = typeof to === 'string' ? to : to.pathname;
  const isSamePage = targetPath === location.pathname;

  const handleClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (shouldBlock && !isSamePage) {
      e.preventDefault();
      setShowDialog(true);
    }
  };

  const handleConfirm = () => {
    setShowDialog(false);
    // Navigate by setting window.location to trigger actual navigation
    window.location.href = typeof to === 'string' ? to : to.pathname || '/';
  };

  return (
    <>
      <Link to={to} onClick={handleClick} {...props}>
        {children}
      </Link>

      <ConfirmDialog
        isOpen={showDialog}
        title="Task is still running"
        message="A task is currently running. If you leave this page, the task will continue in the background, but you may lose track of its progress."
        confirmLabel="Leave anyway"
        cancelLabel="Stay here"
        variant="warning"
        onConfirm={handleConfirm}
        onCancel={() => setShowDialog(false)}
      />
    </>
  );
}
