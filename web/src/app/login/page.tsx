'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Cpu, Mail, Lock, ShieldAlert, Loader2 } from 'lucide-react';
import { apiPost, apiFetch, setSessionToken } from '@/lib/api';

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
 // The session itself is in httpOnly cookies set by the server; the token
 // is held in memory only, as a fallback for split-origin dev where the
 // browser will not send the cookie. Never persisted.
 setSessionToken(data.access_token ?? null);
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
 const data = await apiPost('/auth/login', { email, password });

 // The server set httpOnly session cookies on this response. Only the
 // non-sensitive user profile is persisted, so there is nothing in
 // localStorage for an XSS to steal.
 setSessionToken(data.access_token ?? null);
 localStorage.setItem('user', JSON.stringify(data.user));

 // Redirect to main command center
 router.push('/app');
 } catch (err: any) {
 setError(err.message || 'Login failed. Please check your credentials.');
 } finally {
 setIsLoading(false);
 }
 };

 // Which sign-in flows this deployment offers. The page used to show one
 // hardcoded button wired to OIDC, so a SAML-only deployment had a working SAML
 // endpoint no user could reach, and an unconfigured one offered a button that
 // could only fail.
 const [providers, setProviders] = useState<any>(null);

 useEffect(() => {
 apiFetch('/auth/sso/providers')
 .then(setProviders)
 .catch(() => setProviders(null));
 }, []);

 const startSso = (startUrl: string) => {
 const isDev = typeof window !== 'undefined' && window.location.port === '3000';
 const ssoBase = isDev ? 'http://127.0.0.1:8000' : '';
 window.location.href = `${ssoBase}${startUrl}?redirect_uri=${window.location.origin}/login`;
 };

 const enabledProviders = providers
 ? ['oidc', 'saml', 'mock'].map(k => ({ key: k, ...providers[k] })).filter(p => p?.enabled)
 : [];

 return (
 <div className="relative min-h-screen w-full flex items-center justify-center bg-[var(--bg-app)] overflow-hidden px-4 select-none">
 
 {/* Subtle background accent */}
 <div className="absolute top-1/3 left-1/2 -translate-x-1/2 w-[500px] h-[500px] rounded-full bg-[var(--primary)]/5 blur-[150px] pointer-events-none"></div>

 {/* Main Card */}
 <div className="w-full max-w-[400px] bg-[var(--bg-card)] border border-[var(--border)] rounded-lg shadow-xl p-8 relative overflow-hidden transition-all duration-200">
 
 {/* Top accent bar */}
 <div className="absolute top-0 left-0 w-full h-[3px] bg-[var(--primary)]"></div>

 {/* Branding header */}
 <div className="flex flex-col items-center mb-7">
 <div className="w-10 h-10 rounded-lg bg-[var(--primary)]/15 border border-[var(--primary)]/25 flex items-center justify-center mb-4">
 <Cpu size={22} className="text-[var(--primary)]" strokeWidth={2.2} />
 </div>
 
 <h1 className="text-xl font-bold tracking-tight text-[var(--text-primary)] text-center">SemanticOS</h1>
 <p className="text-xs text-[var(--text-muted)] mt-1 text-center">Enterprise Log Intelligence Platform</p>
 </div>

 {error && (
 <div className="mb-5 p-3 rounded bg-[var(--status-red)]/10 border border-[var(--status-red)]/20 flex items-start gap-2.5">
 <ShieldAlert size={16} className="text-[var(--status-red)] shrink-0 mt-0.5" />
 <div className="text-xs text-[var(--status-red)] leading-normal">{error}</div>
 </div>
 )}

 <form onSubmit={handleSubmit} className="space-y-4">
 {/* Email input field */}
 <div className="space-y-1.5">
 <label className="text-[11px] font-semibold text-[var(--text-secondary)] uppercase tracking-wider block">Email Address</label>
 <div className="relative flex items-center">
 <Mail size={15} className="absolute left-3 text-[var(--text-muted)] pointer-events-none" />
 <input
 type="email"
 required
 value={email}
 onChange={(e) => setEmail(e.target.value)}
 placeholder="operator@semanticos.io"
 className="w-full h-10 bg-[var(--bg-input)] border border-[var(--border)] rounded pl-10 pr-4 text-[13px] text-[var(--text-primary)] placeholder-[var(--text-dimmed)] outline-none transition-all focus:border-[var(--primary)]/50 focus:ring-1 focus:ring-[var(--primary)]/20"
 />
 </div>
 </div>

 {/* Password input field */}
 <div className="space-y-1.5">
 <label className="text-[11px] font-semibold text-[var(--text-secondary)] uppercase tracking-wider block">Password</label>
 <div className="relative flex items-center">
 <Lock size={15} className="absolute left-3 text-[var(--text-muted)] pointer-events-none" />
 <input
 type="password"
 required
 value={password}
 onChange={(e) => setPassword(e.target.value)}
 placeholder="••••••••••••"
 className="w-full h-10 bg-[var(--bg-input)] border border-[var(--border)] rounded pl-10 pr-4 text-[13px] text-[var(--text-primary)] placeholder-[var(--text-dimmed)] outline-none transition-all focus:border-[var(--primary)]/50 focus:ring-1 focus:ring-[var(--primary)]/20"
 />
 </div>
 </div>

 {/* Submit Action */}
 <button
 type="submit"
 disabled={isLoading}
 className="w-full h-10 bg-[var(--primary)] hover:bg-[var(--primary-hover)] disabled:opacity-50 disabled:cursor-not-allowed text-white text-[13px] font-semibold rounded flex items-center justify-center gap-2 cursor-pointer transition-all duration-200 border-none mt-1"
 >
 {isLoading ? (
 <>
 <Loader2 size={15} className="animate-spin" /> Verifying...
 </>
 ) : (
 'Sign In'
 )}
 </button>
 </form>

 {enabledProviders.length > 0 && (
 <>
 <div className="relative my-5 flex items-center justify-center">
 <div className="absolute inset-0 flex items-center">
 <div className="w-full border-t border-[var(--border)]"></div>
 </div>
 <span className="relative px-3 text-[10px] text-[var(--text-muted)] font-mono uppercase bg-[var(--bg-card)]">Or continue with</span>
 </div>

 <div className="space-y-2">
 {enabledProviders.map(provider => (
 <button
 key={provider.key}
 onClick={() => startSso(provider.start_url)}
 type="button"
 disabled={isLoading}
 className="w-full h-10 bg-transparent hover:bg-[var(--bg-surface-hover)] border border-[var(--border)] text-[var(--text-primary)] text-[13px] font-medium rounded flex items-center justify-center gap-2.5 cursor-pointer transition-all duration-200 hover:border-[var(--primary)]/30 disabled:opacity-50"
 >
 <svg className="w-4 h-4 text-[var(--primary)]" viewBox="0 0 24 24" fill="currentColor">
 <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
 </svg>
 {provider.label}
 </button>
 ))}
 </div>
 </>
 )}

 <div className="mt-6 text-center border-t border-[var(--border-subtle)] pt-5">
 <p className="text-[10px] text-[var(--text-dimmed)] font-mono tracking-wider uppercase">
 Protected by JWT Session Engine
 </p>
 </div>
 </div>
 </div>
 );
}
