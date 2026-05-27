'use client';

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Cpu, Mail, Lock, ShieldAlert, Loader2, Sparkles } from 'lucide-react';
import { apiPost } from '@/lib/api';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError(null);

    try {
      const data = await apiPost('/auth/login', { email, password });
      
      // Store auth session
      localStorage.setItem('token', data.access_token);
      localStorage.setItem('user', JSON.stringify(data.user));

      // Redirect to main command center
      router.push('/app');
    } catch (err: any) {
      setError(err.message || 'Login failed. Please check your credentials.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="relative min-h-screen w-full flex items-center justify-center bg-[#070709] overflow-hidden px-4 select-none">
      
      {/* Dynamic Background Glows */}
      <div className="absolute top-1/4 left-1/4 w-[400px] h-[400px] rounded-full bg-fuchsia-500/10 blur-[120px] pointer-events-none animate-pulse"></div>
      <div className="absolute bottom-1/4 right-1/4 w-[450px] h-[450px] rounded-full bg-violet-600/10 blur-[130px] pointer-events-none animate-pulse duration-5000"></div>

      {/* Main Glass Card container */}
      <div className="w-full max-w-md bg-white/[0.02] border border-white/10 backdrop-blur-xl rounded-2xl shadow-2xl p-8 relative overflow-hidden transition-all duration-300 hover:border-white/15">
        
        {/* Subtle top glow line */}
        <div className="absolute top-0 left-0 w-full h-[2px] bg-gradient-to-r from-transparent via-fuchsia-500/50 to-transparent"></div>

        {/* Branding header */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-12 h-12 rounded-xl bg-gradient-to-tr from-fuchsia-600/20 to-violet-600/20 border border-fuchsia-500/25 flex items-center justify-center mb-4 transition-all duration-500 hover:rotate-[360deg] shadow-lg shadow-fuchsia-500/5">
            <Cpu size={24} className="text-fuchsia-400" strokeWidth={2.5} />
          </div>
          
          <div className="flex items-center gap-1.5 text-zinc-400 text-xs font-semibold tracking-widest uppercase mb-1">
            <Sparkles size={12} className="text-fuchsia-400" />
            Neural De-Noiser
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white text-center">SemanticOS Platform</h1>
          <p className="text-xs text-zinc-500 mt-1 text-center">Log ingestion, anomaly mapping & Drift Engine</p>
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
            <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider block">Email Address</label>
            <div className="relative flex items-center">
              <Mail size={16} className="absolute left-3.5 text-zinc-500 pointer-events-none" />
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="operator@semanticos.io"
                className="w-full h-11 bg-white/[0.03] border border-white/10 rounded-xl pl-11 pr-4 text-sm text-white placeholder-zinc-600 outline-none transition-all focus:border-fuchsia-500/40 focus:bg-white/[0.05] focus:ring-1 focus:ring-fuchsia-500/20"
              />
            </div>
          </div>

          {/* Password input field */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider block">Password</label>
            </div>
            <div className="relative flex items-center">
              <Lock size={16} className="absolute left-3.5 text-zinc-500 pointer-events-none" />
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••••••"
                className="w-full h-11 bg-white/[0.03] border border-white/10 rounded-xl pl-11 pr-4 text-sm text-white placeholder-zinc-600 outline-none transition-all focus:border-fuchsia-500/40 focus:bg-white/[0.05] focus:ring-1 focus:ring-fuchsia-500/20"
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

        <div className="mt-8 text-center border-t border-white/5 pt-6">
          <p className="text-[10px] text-zinc-500 font-mono tracking-wider uppercase">
            Protected by ECDSA JWT Session Engine
          </p>
        </div>
      </div>
    </div>
  );
}
