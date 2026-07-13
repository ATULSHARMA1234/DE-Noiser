'use client';

import React, { createContext, useContext, useState, useRef, useEffect, ReactNode } from 'react';
import { useToast } from './ToastContext';

export type TaskStatus = 'running' | 'success' | 'error';

export interface BackgroundTask {
  id: string;
  title: string;
  status: TaskStatus;
  result?: any;
  error?: string;
  startedAt: number;
}

interface TaskContextType {
  tasks: BackgroundTask[];
  executeTask: (id: string, title: string, promise: Promise<any>) => void;
  removeTask: (id: string) => void;
  getTask: (id: string) => BackgroundTask | undefined;
  isTaskRunning: (id: string) => boolean;
}

const TaskContext = createContext<TaskContextType | undefined>(undefined);

export function TaskProvider({ children }: { children: ReactNode }) {
  const [tasks, setTasks] = useState<BackgroundTask[]>([]);
  const { toast } = useToast();
  // Keep a ref so the async callbacks always see the latest tasks array.
  // Written in an effect rather than during render: mutating a ref while
  // rendering is not allowed (and can leave the ref stale on a bailed render).
  const tasksRef = useRef<BackgroundTask[]>([]);
  useEffect(() => {
    tasksRef.current = tasks;
  }, [tasks]);

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

    toast.success(`${title} started…`);

    // Fire-and-forget: the promise runs in the background
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
    <TaskContext.Provider value={{ tasks, executeTask, removeTask, getTask, isTaskRunning }}>
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
