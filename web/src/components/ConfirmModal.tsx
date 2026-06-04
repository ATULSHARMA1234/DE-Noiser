'use client';

import React from 'react';
import { X, AlertTriangle } from 'lucide-react';

interface ConfirmModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
}

export function ConfirmModal({
  isOpen,
  onClose,
  onConfirm,
  title,
  message,
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  danger = true,
}: ConfirmModalProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[250] p-4">
      <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-2xl shadow-2xl w-full max-w-md overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        <div className="p-4 border-b border-[var(--border)] flex justify-between items-center bg-[var(--bg-app)]">
          <div className="flex items-center gap-2">
            <AlertTriangle className={danger ? 'text-red-500' : 'text-fuchsia-500'} size={18} />
            <h2 className="font-bold text-[var(--text-primary)]">{title}</h2>
          </div>
          <button onClick={onClose} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors border-none bg-transparent cursor-pointer">
            <X size={20} />
          </button>
        </div>
        <div className="p-6">
          <p className="text-sm text-[var(--text-secondary)] leading-relaxed">{message}</p>
          <div className="pt-6 flex justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl text-sm font-medium border border-[var(--border)] hover:bg-[var(--bg-app)] transition-colors text-[var(--text-primary)] bg-transparent cursor-pointer"
            >
              {cancelText}
            </button>
            <button
              type="button"
              onClick={() => {
                onConfirm();
                onClose();
              }}
              className={`text-white px-4 py-2 rounded-xl text-sm font-medium transition-colors border-none cursor-pointer ${
                danger ? 'bg-red-600 hover:bg-red-700' : 'bg-fuchsia-600 hover:bg-fuchsia-700'
              }`}
            >
              {confirmText}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
