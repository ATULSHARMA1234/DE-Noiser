'use client';

import React, { useState, useEffect } from 'react';
import { useAuth } from '@/context/AuthContext';
import { apiFetch, apiPost, apiDelete } from '@/lib/api';
import { Users, UserPlus, Trash2, Shield, ShieldCheck, ShieldAlert, Loader2, Sparkles, X } from 'lucide-react';

import { useToast } from '@/context/ToastContext';

type ManagedUser = {
  id: number;
  email: string;
  role: string;
};

export default function UserDirectoryPage() {
  const { user: currentUser } = useAuth();
  const { toast } = useToast();
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('VIEWER');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchUsers = async () => {
    setIsLoading(true);
    try {
      const data = await apiFetch('/users');
      setUsers(data);
    } catch (e: any) {
      console.error('Failed to load users:', e);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (currentUser?.role === 'ADMIN') {
      fetchUsers();
    }
  }, [currentUser]);

  const handleAddUser = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setError(null);
    try {
      await apiPost('/users', { email, password, role });
      setEmail('');
      setPassword('');
      setRole('VIEWER');
      setShowAddModal(false);
      await fetchUsers();
      toast.success('New operator provisioned successfully.');
    } catch (e: any) {
      setError(e.message || 'Failed to provision user.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteUser = async (userId: number) => {
    if (!confirm('Are you sure you want to delete this user?')) return;
    try {
      await apiDelete(`/users/${userId}`);
      await fetchUsers();
      toast.success('Operator deleted successfully.');
    } catch (e: any) {
      toast.error(`Delete failed: ${e.message}`);
    }
  };

  if (currentUser?.role !== 'ADMIN') {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] text-center">
        <ShieldAlert size={48} className="text-red-500 mb-4 animate-bounce" />
        <h2 className="text-xl font-bold text-[var(--text-primary)]">Access Restricted</h2>
        <p className="text-sm text-[var(--text-muted)] mt-2 max-w-md">
          Only users with the <code className="text-fuchsia-400">ADMIN</code> role can access the User Provisioning directory.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-[1600px] mx-auto pb-10">
      
      {/* Header Panel */}
      <div className="flex items-center justify-between mb-8 border-b border-[var(--border-subtle)] pb-5">
        <div>
          <div className="flex items-center gap-2 text-fuchsia-400 text-xs font-bold tracking-widest uppercase mb-1">
            <Sparkles size={12} />
            Platform Control
          </div>
          <h1 className="text-xl font-bold text-[var(--text-primary)] flex items-center gap-2">
            <Users size={22} className="text-fuchsia-500" />
            User Provisioning Directory
          </h1>
          <p className="text-xs text-[var(--text-muted)] mt-1">Manage platform credentials, roles, and administrative scopes</p>
        </div>

        <button
          onClick={() => {
            setError(null);
            setShowAddModal(true);
          }}
          className="bg-fuchsia-600 hover:bg-fuchsia-500 text-white font-bold rounded-lg px-4 py-2 text-xs flex items-center gap-2 cursor-pointer transition-colors border-none"
        >
          <UserPlus size={14} /> Provision Operator
        </button>
      </div>

      {/* Main Grid content */}
      {isLoading ? (
        <div className="flex flex-col items-center justify-center py-20 gap-3">
          <Loader2 size={32} className="animate-spin text-fuchsia-500" />
          <p className="text-xs text-[var(--text-muted)]">Retrieving security contexts...</p>
        </div>
      ) : users.length === 0 ? (
        <div className="border border-dashed border-[var(--border)] rounded-2xl p-16 text-center">
          <Users size={40} className="text-[var(--text-dimmed)] mx-auto mb-4" />
          <p className="text-sm text-[var(--text-secondary)] font-medium">No operators provisioned</p>
          <p className="text-xs text-[var(--text-dimmed)] mt-1">Create platform accounts to delegate VIEWER or ANALYST privileges.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {users.map((u) => {
            const isSelf = u.email === currentUser?.email;
            
            return (
              <div 
                key={u.id}
                className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] hover:border-[var(--border)] rounded-2xl p-5 relative overflow-hidden transition-all duration-300 flex flex-col justify-between"
              >
                {/* Visual glow indicator */}
                <div className={`absolute top-0 left-0 w-full h-[3px] bg-gradient-to-r ${
                  u.role === 'ADMIN' ? 'from-fuchsia-600 to-violet-600' :
                  u.role === 'ANALYST' ? 'from-cyan-600 to-blue-600' :
                  'from-zinc-600 to-zinc-500'
                }`} />

                <div>
                  <div className="flex items-start justify-between mb-4">
                    <div className="w-10 h-10 rounded-xl bg-[var(--bg-surface-hover)] border border-[var(--border-subtle)] flex items-center justify-center">
                      {u.role === 'ADMIN' ? <ShieldCheck size={20} className="text-fuchsia-400" /> :
                       u.role === 'ANALYST' ? <Shield size={20} className="text-cyan-400" /> :
                       <Users size={20} className="text-zinc-400" />}
                    </div>

                    <div className="flex items-center gap-1.5">
                      {isSelf && (
                        <span className="text-[9px] font-bold tracking-wider bg-[var(--bg-surface-hover)] border border-[var(--border)] px-2 py-0.5 rounded text-[var(--text-secondary)] uppercase">
                          You
                        </span>
                      )}
                      <span className={`text-[10px] font-black tracking-wider px-2 py-0.5 rounded border uppercase ${
                        u.role === 'ADMIN' ? 'bg-fuchsia-500/10 border-fuchsia-500/20 text-fuchsia-400' :
                        u.role === 'ANALYST' ? 'bg-cyan-500/10 border-cyan-500/20 text-cyan-400' :
                        'bg-zinc-500/10 border-zinc-500/20 text-zinc-400'
                      }`}>
                        {u.role}
                      </span>
                    </div>
                  </div>

                  <p className="text-sm font-bold text-[var(--text-primary)] truncate mb-1">{u.email}</p>
                  <p className="text-[10px] text-[var(--text-muted)] font-mono">Operator context ID: #{u.id}</p>
                </div>

                <div className="mt-6 pt-4 border-t border-[var(--border-subtle)] flex justify-end">
                  <button
                    onClick={() => handleDeleteUser(u.id)}
                    disabled={isSelf}
                    className="p-2 rounded-lg text-[var(--text-muted)] hover:text-red-500 dark:hover:text-red-400 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-red-500/5 transition-colors cursor-pointer border-none"
                    title={isSelf ? "Cannot delete your own account" : "Delete operator context"}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ═══ ADD USER MODAL ═══ */}
      {showAddModal && (
        <div className="fixed inset-0 bg-black/75 backdrop-blur-md z-[100] flex items-center justify-center p-4">
          <div className="bg-[var(--bg-modal)] border border-[var(--border)] rounded-2xl w-full max-w-md overflow-hidden shadow-2xl relative">
            <div className="absolute top-0 left-0 w-full h-[2px] bg-gradient-to-r from-transparent via-fuchsia-500 to-transparent"></div>
            
            {/* Modal Header */}
            <div className="flex items-center justify-between px-6 py-5 border-b border-[var(--border-subtle)]">
              <div>
                <h2 className="text-base font-bold text-[var(--text-primary)]">Provision Operator</h2>
                <p className="text-xs text-[var(--text-muted)] mt-0.5">Register a new secure credential identity</p>
              </div>
              <button 
                onClick={() => setShowAddModal(false)}
                className="text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors cursor-pointer border-none bg-transparent"
              >
                <X size={18} />
              </button>
            </div>

            {/* Modal Body / Form */}
            <form onSubmit={handleAddUser}>
              <div className="p-6 space-y-4">
                {error && (
                  <div className="p-3.5 rounded-xl bg-red-500/10 border border-red-500/20 flex items-start gap-2.5">
                    <ShieldAlert size={16} className="text-red-400 shrink-0 mt-0.5" />
                    <span className="text-xs text-red-300/90 leading-relaxed">{error}</span>
                  </div>
                )}

                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-[var(--text-secondary)] uppercase tracking-wide">Operator Email</label>
                  <input
                    type="email"
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="analyst.name@semanticos.io"
                    className="w-full h-10 bg-[var(--bg-surface)] border border-[var(--border)] rounded-xl px-3.5 text-xs text-[var(--text-primary)] placeholder-[var(--text-dimmed)] outline-none focus:border-fuchsia-500/40 focus:bg-[var(--bg-surface-hover)]"
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-[var(--text-secondary)] uppercase tracking-wide">Secure Password</label>
                  <input
                    type="password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••••••"
                    className="w-full h-10 bg-[var(--bg-surface)] border border-[var(--border)] rounded-xl px-3.5 text-xs text-[var(--text-primary)] placeholder-[var(--text-dimmed)] outline-none focus:border-fuchsia-500/40 focus:bg-[var(--bg-surface-hover)]"
                  />
                </div>

                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-[var(--text-secondary)] uppercase tracking-wide">Platform Privilege Role</label>
                  <select
                    value={role}
                    onChange={(e) => setRole(e.target.value)}
                    className="w-full h-10 bg-[var(--bg-modal)] border border-[var(--border)] rounded-xl px-3 text-xs text-[var(--text-input)] outline-none focus:border-fuchsia-500/40"
                  >
                    <option value="VIEWER">VIEWER (Audits, viewing runs & alerts)</option>
                    <option value="ANALYST">ANALYST (Submit analyses, resolve incidents)</option>
                    <option value="ADMIN">ADMIN (Full management scopes, user provisioning)</option>
                  </select>
                </div>
              </div>

              {/* Modal Footer */}
              <div className="px-6 py-4 border-t border-[var(--border-subtle)] flex items-center justify-between">
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  className="text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors cursor-pointer border-none bg-transparent"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="bg-fuchsia-600 hover:bg-fuchsia-500 disabled:opacity-50 text-white font-bold rounded-lg px-5 py-2 text-xs flex items-center gap-1.5 transition-colors cursor-pointer border-none"
                >
                  {isSubmitting ? (
                    <>
                      <Loader2 size={14} className="animate-spin" /> Provisioning...
                    </>
                  ) : (
                    'Provision Operator'
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
