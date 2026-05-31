'use client';

import React, { createContext, useContext, useState, useCallback } from 'react';
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react';

type ToastType = 'success' | 'error' | 'info';

type Toast = {
  id: string;
  type: ToastType;
  message: string;
};

type ToastOptions = {
  title?: string;
  description?: string;
  type?: ToastType;
};

interface ToastFunction {
  (options: ToastOptions): void;
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
}

type ToastContextType = {
  toast: ToastFunction;
};

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timersRef = React.useRef<{ [key: string]: NodeJS.Timeout }>({});

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    if (timersRef.current[id]) {
      clearTimeout(timersRef.current[id]);
      delete timersRef.current[id];
    }
  }, []);

  const addToast = useCallback((type: ToastType, message: string) => {
    const id = Math.random().toString(36).substring(2, 9);
    setToasts((prev) => {
      // Limit to max 5 toasts to prevent DOM crashes
      const next = [...prev, { id, type, message }];
      if (next.length > 5) {
        return next.slice(next.length - 5);
      }
      return next;
    });
    
    // Auto-remove after 4 seconds
    timersRef.current[id] = setTimeout(() => {
      removeToast(id);
    }, 4000);
  }, [removeToast]);

  // Cleanup all timeouts on unmount
  React.useEffect(() => {
    const timers = timersRef.current;
    return () => {
      Object.values(timers).forEach(clearTimeout);
    };
  }, []);

  const toastFn = useCallback((options: ToastOptions) => {
    const msg = options.description ? `${options.title}: ${options.description}` : (options.title || '');
    addToast(options.type || 'info', msg);
  }, [addToast]);

  const toast = React.useMemo(() => {
    const fn: any = (options: ToastOptions) => toastFn(options);
    // eslint-disable-next-line react-hooks/immutability
    fn.success = (msg: string) => addToast('success', msg);
    // eslint-disable-next-line react-hooks/immutability
    fn.error = (msg: string) => addToast('error', msg);
    // eslint-disable-next-line react-hooks/immutability
    fn.info = (msg: string) => addToast('info', msg);
    return fn as ToastFunction;
  }, [toastFn, addToast]);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      
      {/* Floating toast notifications container */}
      <div className="fixed top-6 right-6 z-[300] flex flex-col gap-3 w-full max-w-sm pointer-events-none">
        {toasts.map((t) => {
          const isSuccess = t.type === 'success';
          const isError = t.type === 'error';
          
          return (
            <div
              key={t.id}
              className={`p-4 rounded-2xl border backdrop-blur-xl shadow-2xl flex items-start gap-3 pointer-events-auto transition-all duration-300 animate-in slide-in-from-right-5 fade-in ${
                isSuccess
                  ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
                  : isError
                  ? 'bg-red-500/10 border-red-500/20 text-red-400'
                  : 'bg-fuchsia-500/10 border-fuchsia-500/20 text-fuchsia-400'
              }`}
            >
              {isSuccess && <CheckCircle2 size={16} className="shrink-0 mt-0.5" />}
              {isError && <AlertCircle size={16} className="shrink-0 mt-0.5" />}
              {!isSuccess && !isError && <Info size={16} className="shrink-0 mt-0.5" />}
              
              <div className="text-xs font-semibold leading-relaxed flex-1">{t.message}</div>
              
              <button
                onClick={() => removeToast(t.id)}
                className="text-zinc-500 hover:text-zinc-300 transition-colors border-none bg-transparent cursor-pointer shrink-0"
              >
                <X size={14} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (context === undefined) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
}
