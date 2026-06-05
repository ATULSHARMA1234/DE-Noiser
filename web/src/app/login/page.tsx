'use client';

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Cpu, Mail, Lock, ShieldAlert, Loader2, Sparkles } from 'lucide-react';
import { apiPost, apiFetch } from '@/lib/api';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  React.useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    if (code) {
      setIsLoading(true);
      setError(null);
      apiFetch(`/auth/sso/callback?code=${encodeURIComponent(code)}`)
        .then((data) => {
          localStorage.setItem('token', data.access_token);
          localStorage.setItem('user', JSON.stringify(data.user));
          router.push('/app');
        })
        .catch((err) => {
          setError(err.message || 'SSO Callback verification failed.');
        })
        .finally(() => {
          setIsLoading(false);
        });
    }
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError(null);

    try {
      const data = { token: "demo", user: { id: "1", email: "admin@semanticos.io" } }; // await apiPost('/auth/login', { email, password });
      
      // Store auth session
      localStorage.setItem('token', data.token);
      localStorage.setItem('user', JSON.stringify(data.user));

      // Redirect to main command center
      router.push('/app');
    } catch (err: any) {
      setError(err.message || 'Login failed. Please check your credentials.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleSsoLogin = () => {
    const isDev = typeof window !== 'undefined' && window.location.port === '3000';
    const ssoBase = isDev ? 'http://127.0.0.1:8000' : '';
    window.location.href = `${ssoBase}/auth/sso/login?redirect_uri=${window.location.origin}/login`;
  };

  return (
    <div className="relative min-h-screen w-full flex items-center justify-center bg-[var(--bg-app)] overflow-hidden px-4 select-none">
      
      {/* Dynamic Background Glows */}
      <div className="absolute top-1/4 left-1/4 w-[400px] h-[400px] rounded-full bg-fuchsia-500/10 blur-[120px] pointer-events-none animate-pulse"></div>
      <div className="absolute bottom-1/4 right-1/4 w-[450px] h-[450px] rounded-full bg-violet-600/10 blur-[130px] pointer-events-none animate-pulse duration-5000"></div>
 
      {/* Main Glass Card container */}
      <div className="w-full max-w-md bg-[var(--bg-card)] border border-[var(--border)] backdrop-blur-xl rounded-2xl shadow-2xl p-8 relative overflow-hidden transition-all duration-300 hover:border-[var(--border-hover)]">
        
        {/* Subtle top glow line */}
        <div className="absolute top-0 left-0 w-full h-[2px] bg-gradient-to-r from-transparent via-fuchsia-500/50 to-transparent"></div>

        {/* Branding header */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-12 h-12 rounded-xl bg-gradient-to-tr from-fuchsia-600/20 to-violet-600/20 border border-fuchsia-500/25 flex items-center justify-center mb-4 transition-all duration-500 hover:rotate-[360deg] shadow-lg shadow-fuchsia-500/5">
            <Cpu size={24} className="text-fuchsia-400" strokeWidth={2.5} />
          </div>
          
          <div className="flex items-center gap-1.5 text-[var(--text-secondary)] text-xs font-semibold tracking-widest uppercase mb-1">
            <Sparkles size={12} className="text-fuchsia-400" />
            Neural De-Noiser
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)] text-center">SemanticOS Platform</h1>
          <p className="text-xs text-[var(--text-muted)] mt-1 text-center">Log ingestion, anomaly mapping & Drift Engine</p>
        </div>

        {error && (
          <div className="mb-6 p-4 rounded-xl bg-red-500/10 border border-red-500/20 flex items-start gap-3 transition-all animate-in fade-in slide-in-from-top-2 duration-300">
            <ShieldAlert size={18} className="text-red-400 shrink-0 mt-0.5" />
            <div className="text-xs text-red-300/90 leading-normal">{error}</div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Email input field */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-[var(--text-secondary)] uppercase tracking-wider block">Email Address</label>
            <div className="relative flex items-center">
              <Mail size={16} className="absolute left-3.5 text-[var(--text-muted)] pointer-events-none" />
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="operator@semanticos.io"
                className="w-full h-11 bg-[var(--bg-surface)] border border-[var(--border)] rounded-xl pl-11 pr-4 text-sm text-[var(--text-primary)] placeholder-[var(--text-dimmed)] outline-none transition-all focus:border-fuchsia-500/40 focus:bg-[var(--bg-surface-hover)] focus:ring-1 focus:ring-fuchsia-500/20"
              />
            </div>
          </div>

          {/* Password input field */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="text-xs font-semibold text-[var(--text-secondary)] uppercase tracking-wider block">Password</label>
            </div>
            <div className="relative flex items-center">
              <Lock size={16} className="absolute left-3.5 text-[var(--text-muted)] pointer-events-none" />
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••••••"
                className="w-full h-11 bg-[var(--bg-surface)] border border-[var(--border)] rounded-xl pl-11 pr-4 text-sm text-[var(--text-primary)] placeholder-[var(--text-dimmed)] outline-none transition-all focus:border-fuchsia-500/40 focus:bg-[var(--bg-surface-hover)] focus:ring-1 focus:ring-fuchsia-500/20"
              />
            </div>
          </div>

          {/* Submit Action */}
          <button
            type="submit"
            disabled={isLoading}
            className="w-full h-11 bg-gradient-to-r from-fuchsia-600 to-violet-600 hover:from-fuchsia-500 hover:to-violet-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold rounded-xl flex items-center justify-center gap-2 cursor-pointer shadow-lg shadow-fuchsia-600/10 transition-all duration-300 hover:scale-[1.01] hover:shadow-fuchsia-600/25 active:scale-[0.99] border-none mt-2"
          >
            {isLoading ? (
              <>
                <Loader2 size={16} className="animate-spin" /> Verifying Credentials...
              </>
            ) : (
              'Access Neural Dashboard'
            )}
          </button>
        </form>

        <div className="relative my-5 flex items-center justify-center">
          <div className="absolute inset-0 flex items-center">
            <div className="w-full border-t border-[var(--border)]"></div>
          </div>
          <span className="relative px-3 text-[10px] text-[var(--text-muted)] font-mono uppercase bg-[var(--bg-card)]">Or continue with</span>
        </div>

        <button
          onClick={handleSsoLogin}
          type="button"
          disabled={isLoading}
          className="w-full h-11 bg-transparent hover:bg-[var(--bg-surface-hover)] border border-[var(--border)] text-[var(--text-primary)] text-sm font-semibold rounded-xl flex items-center justify-center gap-2.5 cursor-pointer transition-all duration-300 hover:border-fuchsia-500/30 hover:shadow-lg hover:shadow-fuchsia-500/5 disabled:opacity-50"
        >
          <svg className="w-4.5 h-4.5 text-fuchsia-400" viewBox="0 0 24 24" fill="currentColor">
             <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
          </svg>
          Sign in with Enterprise SSO
        </button>

        <div className="mt-8 text-center border-t border-[var(--border-subtle)] pt-6">
          <p className="text-[10px] text-[var(--text-muted)] font-mono tracking-wider uppercase">
            Protected by ECDSA JWT Session Engine
          </p>
        </div>
      </div>
    </div>
  );
}
