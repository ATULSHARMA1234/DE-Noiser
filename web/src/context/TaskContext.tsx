'use client';

import React, { createContext, useContext, useState, useRef, useEffect, ReactNode } from 'react';
import { useToast } from './ToastContext';
import { pollTask } from '@/lib/api';

export type TaskStatus = 'running' | 'success' | 'error';

export interface BackgroundTask {
  id: string;
  title: string;
  status: TaskStatus;
  result?: any;
  error?: string;
  startedAt: number;
  /** Server-side Celery id, when the work is running on a worker. */
  remoteTaskId?: string;
}

interface TaskContextType {
  tasks: BackgroundTask[];
  executeTask: (id: string, title: string, promise: Promise<any>) => void;
  attachRemoteTask: (id: string, remoteTaskId: string) => void;
  removeTask: (id: string) => void;
  getTask: (id: string) => BackgroundTask | undefined;
  isTaskRunning: (id: string) => boolean;
}

const TaskContext = createContext<TaskContextType | undefined>(undefined);

// Running tasks are mirrored here so a hard reload can reattach to work that is
// still executing on the worker. Client-side navigation never needed this — the
// provider outlives it — but a reload dropped the poll loop and the job ran to
// completion with nothing watching it.
const STORAGE_KEY = 'semanticos.running_tasks';
// Beyond this age a persisted task is assumed dead rather than resumed; the
// backend's own poll budget is five minutes.
const MAX_RESUME_AGE_MS = 10 * 60 * 1000;

interface PersistedTask {
  id: string;
  title: string;
  startedAt: number;
  remoteTaskId: string;
}

function loadPersisted(): PersistedTask[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const now = Date.now();
    return parsed.filter(
      (t: any) => t?.id && t?.remoteTaskId && now - (t.startedAt ?? 0) < MAX_RESUME_AGE_MS
    );
  } catch {
    return [];
  }
}

function persist(tasks: BackgroundTask[]) {
  if (typeof window === 'undefined') return;
  try {
    const running = tasks
      .filter(t => t.status === 'running' && t.remoteTaskId)
      .map(({ id, title, startedAt, remoteTaskId }) => ({ id, title, startedAt, remoteTaskId }));
    if (running.length === 0) {
      window.localStorage.removeItem(STORAGE_KEY);
    } else {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(running));
    }
  } catch {
    // A full or unavailable localStorage must not break task execution.
  }
}

export function TaskProvider({ children }: { children: ReactNode }) {
  const [tasks, setTasks] = useState<BackgroundTask[]>([]);
  const { toast } = useToast();
  // Keep a ref so the async callbacks always see the latest tasks array.
  // Written in an effect rather than during render: mutating a ref while
  // rendering is not allowed (and can leave the ref stale on a bailed render).
  const tasksRef = useRef<BackgroundTask[]>([]);
  useEffect(() => {
    tasksRef.current = tasks;
    persist(tasks);
  }, [tasks]);

  // Watch a promise and settle the matching task. Shared by fresh submissions
  // and resumed ones so both report identically.
  const watch = (id: string, title: string, promise: Promise<any>, announce: boolean) => {
    promise
      .then((result) => {
        setTasks(prev =>
          prev.map(t => t.id === id ? { ...t, status: 'success', result } : t)
        );
        toast.success(`${title} completed successfully!`);
      })
      .catch((error: any) => {
        setTasks(prev =>
          prev.map(t => t.id === id ? { ...t, status: 'error', error: error.message || String(error) } : t)
        );
        toast.error(`${title} failed: ${error.message || 'Unknown error'}`);
      });

    if (announce) toast.success(`${title} started…`);
  };

  // Reattach to anything that was still running when the page was reloaded.
  const resumed = useRef(false);
  useEffect(() => {
    if (resumed.current) return;
    resumed.current = true;

    const pending = loadPersisted();
    if (pending.length === 0) return;

    setTasks(prev => {
      const known = new Set(prev.map(t => t.id));
      const restored = pending
        .filter(p => !known.has(p.id))
        .map<BackgroundTask>(p => ({
          id: p.id,
          title: p.title,
          status: 'running',
          startedAt: p.startedAt,
          remoteTaskId: p.remoteTaskId,
        }));
      return restored.length ? [...prev, ...restored] : prev;
    });

    for (const p of pending) {
      // startedAt is the original submission time, so the elapsed timer and the
      // poll budget both continue rather than restarting.
      watch(p.id, p.title, pollTask(p.remoteTaskId, p.startedAt), false);
    }

    toast.success(
      pending.length === 1
        ? `Reattached to ${pending[0].title}, still running…`
        : `Reattached to ${pending.length} running tasks…`
    );
  }, []);

  const executeTask = (id: string, title: string, promise: Promise<any>) => {
    // Prevent duplicate: if a task with this ID is already running, skip
    if (tasksRef.current.some(t => t.id === id && t.status === 'running')) {
      return;
    }

    // Add or replace task in state
    setTasks(prev => {
      const filtered = prev.filter(t => t.id !== id);
      return [...filtered, { id, title, status: 'running', startedAt: Date.now() }];
    });

    // Fire-and-forget: the promise runs in the background
    watch(id, title, promise, true);
  };

  /** Record the server-side id for a running task so a reload can resume it. */
  const attachRemoteTask = (id: string, remoteTaskId: string) => {
    setTasks(prev => prev.map(t => (t.id === id ? { ...t, remoteTaskId } : t)));
  };

  const removeTask = (id: string) => {
    setTasks(prev => prev.filter(t => t.id !== id));
  };

  const getTask = (id: string) => {
    return tasks.find(t => t.id === id);
  };

  const isTaskRunning = (id: string) => {
    return tasks.some(t => t.id === id && t.status === 'running');
  };

  return (
    <TaskContext.Provider value={{ tasks, executeTask, attachRemoteTask, removeTask, getTask, isTaskRunning }}>
      {children}
    </TaskContext.Provider>
  );
}

export function useTasks() {
  const context = useContext(TaskContext);
  if (context === undefined) {
    throw new Error('useTasks must be used within a TaskProvider');
  }
  return context;
}
