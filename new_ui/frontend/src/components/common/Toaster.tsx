import { useEffect, useState } from 'react';
import { X, CheckCircle, AlertCircle, Info, AlertTriangle } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

interface Toast {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  title: string;
  description?: string;
}

// Global toast state
let toasts: Toast[] = [];
let listeners: ((toasts: Toast[]) => void)[] = [];

const notify = () => {
  listeners.forEach((listener) => listener([...toasts]));
};

export const toast = {
  success: (title: string, description?: string) => {
    const id = crypto.randomUUID();
    toasts = [...toasts, { id, type: 'success', title, description }];
    notify();
    setTimeout(() => toast.dismiss(id), 5000);
  },
  error: (title: string, description?: string) => {
    const id = crypto.randomUUID();
    toasts = [...toasts, { id, type: 'error', title, description }];
    notify();
    setTimeout(() => toast.dismiss(id), 8000);
  },
  warning: (title: string, description?: string) => {
    const id = crypto.randomUUID();
    toasts = [...toasts, { id, type: 'warning', title, description }];
    notify();
    setTimeout(() => toast.dismiss(id), 6000);
  },
  info: (title: string, description?: string) => {
    const id = crypto.randomUUID();
    toasts = [...toasts, { id, type: 'info', title, description }];
    notify();
    setTimeout(() => toast.dismiss(id), 5000);
  },
  dismiss: (id: string) => {
    toasts = toasts.filter((t) => t.id !== id);
    notify();
  },
};

const icons = {
  success: CheckCircle,
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
};

const colors = {
  success: 'bg-green-50 border-green-200 text-green-800',
  error: 'bg-red-50 border-red-200 text-red-800',
  warning: 'bg-yellow-50 border-yellow-200 text-yellow-800',
  info: 'bg-blue-50 border-blue-200 text-blue-800',
};

const iconColors = {
  success: 'text-green-500',
  error: 'text-red-500',
  warning: 'text-yellow-500',
  info: 'text-blue-500',
};

export function Toaster() {
  const [currentToasts, setCurrentToasts] = useState<Toast[]>([]);

  useEffect(() => {
    listeners.push(setCurrentToasts);
    return () => {
      listeners = listeners.filter((l) => l !== setCurrentToasts);
    };
  }, []);

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      <AnimatePresence>
        {currentToasts.map((t) => {
          const Icon = icons[t.type];
          return (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, y: 20, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -10, scale: 0.95 }}
              className={`flex items-start gap-3 p-4 rounded-lg border shadow-lg max-w-sm ${colors[t.type]}`}
            >
              <Icon className={`h-5 w-5 mt-0.5 ${iconColors[t.type]}`} />
              <div className="flex-1 min-w-0">
                <p className="font-medium text-sm">{t.title}</p>
                {t.description && (
                  <p className="text-sm opacity-80 mt-0.5">{t.description}</p>
                )}
              </div>
              <button
                onClick={() => toast.dismiss(t.id)}
                className="p-1 rounded hover:bg-black/5 transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
