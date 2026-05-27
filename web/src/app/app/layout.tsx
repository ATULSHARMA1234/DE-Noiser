'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { LayoutGrid, Terminal, ShieldAlert, History, Database, Settings, Search, Play, Cpu, X, FileText, Loader2, Network, Users, Bell, Sun, Moon, Menu } from 'lucide-react';
import { apiFetch, runAnalysis } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import { useTheme } from '@/context/ThemeContext';
import { useToast } from '@/context/ToastContext';
import { OnboardingWizard } from '@/components/OnboardingWizard';

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading, logout, hasRole } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const { toast } = useToast();
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const pathname = usePathname();
  const router = useRouter();
  const [showRunModal, setShowRunModal] = useState(false);
  const [sources, setSources] = useState<any[]>([]);
  const [selectedSource, setSelectedSource] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  // Dynamically append User Directory if user is ADMIN
  const navItems = [
    { name: 'Command Center', path: '/app', icon: LayoutGrid },
    { name: 'Live Pulse', path: '/app/live', icon: Terminal },
    { name: 'Service Topology', path: '/app/topology', icon: Network },
    { name: 'Incident Memory', path: '/app/incidents', icon: ShieldAlert },
    { name: 'Analysis Runs', path: '/app/runs', icon: History },
    { name: 'Sources', path: '/app/sources', icon: Database },
    { name: 'Alerts', path: '/app/alerts', icon: Bell },
    { name: 'Settings', path: '/app/settings', icon: Settings },
  ];

  if (user?.role === 'ADMIN') {
    navItems.push({ name: 'User Directory', path: '/app/users', icon: Users });
  }

  const [showCommandPalette, setShowCommandPalette] = useState(false);
  const [paletteSearch, setPaletteSearch] = useState('');

  React.useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Escape closes modals
      if (e.key === 'Escape') {
        setShowRunModal(false);
        setShowCommandPalette(false);
      }

      // Command+K / Ctrl+K toggles Command Palette
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setShowCommandPalette(prev => !prev);
      }

      // Command+R / Ctrl+R triggers analysis
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'r') {
        e.preventDefault();
        fetchSources();
        setShowRunModal(true);
      }

      // Command+L / Ctrl+L routes to live stream
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'l') {
        e.preventDefault();
        router.push('/app/live');
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [router]);

  const commandPaletteItems = [
    { name: 'Go to Command Center', action: () => { router.push('/app'); setShowCommandPalette(false); }, icon: LayoutGrid },
    { name: 'Go to Live Pulse', action: () => { router.push('/app/live'); setShowCommandPalette(false); }, icon: Terminal },
    { name: 'Go to Service Topology', action: () => { router.push('/app/topology'); setShowCommandPalette(false); }, icon: Network },
    { name: 'Go to Incident Memory', action: () => { router.push('/app/incidents'); setShowCommandPalette(false); }, icon: ShieldAlert },
    { name: 'Go to Analysis Runs', action: () => { router.push('/app/runs'); setShowCommandPalette(false); }, icon: History },
    { name: 'Go to Sources', action: () => { router.push('/app/sources'); setShowCommandPalette(false); }, icon: Database },
    { name: 'Go to Alerts Panel', action: () => { router.push('/app/alerts'); setShowCommandPalette(false); }, icon: Bell },
    { name: 'Go to Settings', action: () => { router.push('/app/settings'); setShowCommandPalette(false); }, icon: Settings },
  ];

  if (user?.role === 'ADMIN') {
    commandPaletteItems.push({ name: 'Go to User Directory', action: () => { router.push('/app/users'); setShowCommandPalette(false); }, icon: Users });
    commandPaletteItems.push({ name: 'Go to Security Audit Logs', action: () => { router.push('/app/audit'); setShowCommandPalette(false); }, icon: ShieldAlert });
  }

  commandPaletteItems.push(
    { name: 'Toggle Light/Dark Theme', action: () => { toggleTheme(); setShowCommandPalette(false); }, icon: Sun },
    { name: 'Trigger New Analysis Modal', action: () => { fetchSources(); setShowRunModal(true); setShowCommandPalette(false); }, icon: Play },
    { name: 'Sign Out / Log Out', action: () => { logout(); setShowCommandPalette(false); }, icon: X }
  );

  const filteredCommands = commandPaletteItems.filter(cmd =>
    cmd.name.toLowerCase().includes(paletteSearch.toLowerCase())
  );

  const fetchSources = async () => {
    try {
      const data = await apiFetch('/sources');
      setSources(data);
      if (data.length > 0 && !selectedSource) {
        setSelectedSource(data[0].path);
      }
    } catch (e) {
      console.error('Failed to fetch sources:', e);
    }
  };

  const handleRunAnalysis = async () => {
    if (!selectedSource) return;
    setIsRunning(true);
    try {
      await runAnalysis({ source: selectedSource, intelligence: true });
      setShowRunModal(false);
      // Navigate to command center to see results
      router.push('/app');
      // Force refresh
      window.location.reload();
    } catch (e: any) {
      toast.error(`Analysis failed: ${e.message}`);
    } finally {
      setIsRunning(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen w-full flex items-center justify-center bg-[#070709] overflow-hidden select-none">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[300px] h-[300px] rounded-full bg-fuchsia-500/10 blur-[100px] pointer-events-none"></div>
        <div className="flex flex-col items-center gap-4 z-10">
          <Cpu size={40} className="text-fuchsia-500 animate-spin duration-3000" />
          <div className="text-xs font-semibold uppercase tracking-wider text-zinc-400">Booting Neural Core...</div>
        </div>
      </div>
    );
  }

  if (!user) {
    return null;
  }

  return (
    <div className="flex h-screen bg-[#0a0a0c] text-white font-sans overflow-hidden">
      
      {/* Translucent background overlay on mobile when sidebar drawer is active */}
      {mobileSidebarOpen && (
        <div 
          className="fixed inset-0 bg-black/60 backdrop-blur-xs z-45 md:hidden" 
          onClick={() => setMobileSidebarOpen(false)} 
        />
      )}
      
      {/* SIDEBAR */}
      <aside className={`fixed inset-y-0 left-0 w-64 border-r border-white/5 bg-[#0a0a0c] flex flex-col z-50 shrink-0 transform md:relative md:translate-x-0 transition-transform duration-300 ${
        mobileSidebarOpen ? 'translate-x-0' : '-translate-x-full'
      }`}>
        
        {/* Logo Area */}
        <div className="h-16 flex items-center justify-between px-6 border-b border-transparent shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-6 h-6 bg-transparent flex items-center justify-center">
               <Cpu size={24} className="text-fuchsia-500" strokeWidth={2.5} />
            </div>
            <h1 className="text-base font-bold tracking-tight text-white">SemanticOS</h1>
          </div>
          {/* Close button on mobile */}
          <button 
            onClick={() => setMobileSidebarOpen(false)}
            className="p-1 text-zinc-500 hover:text-white md:hidden cursor-pointer bg-transparent border-none"
          >
            <X size={16} />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 flex flex-col gap-1 w-full px-3 py-6">
          {navItems.map((item) => {
            const isActive = pathname === item.path || (pathname?.startsWith(item.path) && item.path !== '/app');
            const Icon = item.icon;
            
            return (
              <Link 
                key={item.name} 
                href={item.path} 
                className={`flex items-center gap-3 px-4 py-2.5 rounded-full transition-all text-sm font-medium ${
                  isActive 
                    ? 'text-fuchsia-400 bg-fuchsia-900/30 border border-fuchsia-500/10' 
                    : 'text-zinc-400 hover:text-zinc-200 hover:bg-white/5 border border-transparent'
                }`}
              >
                <Icon size={18} strokeWidth={isActive ? 2.5 : 2} className={isActive ? "text-fuchsia-400" : "text-zinc-400"} />
                {item.name}
              </Link>
            );
          })}
        </nav>

        {/* Footer Status */}
        <div className="p-6">
          <div className="flex items-center gap-2 text-xs font-medium text-zinc-400">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            Local Engine Online
          </div>
        </div>
      </aside>

      {/* MAIN CONTENT AREA */}
      <main className="flex-1 flex flex-col overflow-hidden relative bg-[#0a0a0c]">
        
        {/* TOP HEADER */}
        <header className="h-16 border-b border-white/5 bg-[#0a0a0c] flex items-center justify-between px-8 shrink-0 z-40">
           
           {/* Mobile hamburger menu */}
           <button 
             onClick={() => setMobileSidebarOpen(true)} 
             className="p-2 -ml-2 mr-3 text-zinc-400 hover:text-white md:hidden cursor-pointer bg-transparent border-none"
           >
             <Menu size={20} />
           </button>

           {/* Search Bar */}
           <div className="flex-1 max-w-md">
             <div className="relative flex items-center w-full h-9 rounded-md bg-zinc-900 border border-white/10 px-3 overflow-hidden">
               <Search size={14} className="text-zinc-500" />
               <input 
                 type="text" 
                 placeholder="Search clusters, incidents..." 
                 value={searchQuery}
                 onChange={(e) => setSearchQuery(e.target.value)}
                 onKeyDown={(e) => {
                   if (e.key === 'Enter' && searchQuery.trim()) {
                     router.push(`/app/incidents?q=${encodeURIComponent(searchQuery)}`);
                   }
                 }}
                 className="bg-transparent border-none outline-none text-sm text-zinc-300 ml-2 w-full placeholder-zinc-600"
               />
             </div>
           </div>

           {/* Actions */}
           <div className="flex items-center gap-4">
              <button
                onClick={() => {
                  fetchSources();
                  setShowRunModal(true);
                }}
                className="bg-fuchsia-600 hover:bg-fuchsia-500 text-white font-bold rounded-md px-4 py-1.5 h-9 text-xs border-none flex items-center gap-2 cursor-pointer transition-colors"
              >
                <Play size={14} fill="currentColor" /> Run Analysis
              </button>
              <button
                 onClick={toggleTheme}
                 className="p-2 rounded-lg bg-white/[0.02] hover:bg-white/5 border border-white/10 text-zinc-400 hover:text-white cursor-pointer transition-all flex items-center justify-center h-9 w-9"
                 title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
               >
                 {theme === 'dark' ? <Sun size={15} /> : <Moon size={15} />}
               </button>
              <div className="flex items-center gap-3 bg-white/[0.02] border border-white/10 px-3 py-1.5 rounded-full">
                <div className="flex flex-col text-right hidden md:flex">
                  <span className="text-[10px] font-bold text-white max-w-[120px] truncate">{user.email}</span>
                  <span className="text-[9px] font-semibold text-fuchsia-400 tracking-wider uppercase">{user.role}</span>
                </div>
                <button 
                  onClick={logout} 
                  className="bg-zinc-800 hover:bg-red-950/40 hover:text-red-400 text-zinc-300 text-[10px] px-2.5 py-1 rounded-full cursor-pointer transition-all border border-white/5 hover:border-red-500/20 font-bold uppercase tracking-wider"
                >
                  Logout
                </button>
              </div>
           </div>
        </header>

        {/* PAGE CONTENT */}
        <div className="flex-1 overflow-y-auto bg-[#0a0a0c] p-8">
           {children}
        </div>
      </main>

      {/* ═══ RUN ANALYSIS MODAL ═══ */}
      {showRunModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-[100] flex items-center justify-center">
          <div className="bg-[#141416] border border-white/10 rounded-2xl w-[520px] max-h-[80vh] overflow-hidden shadow-2xl">
            
            {/* Modal Header */}
            <div className="flex items-center justify-between px-6 py-5 border-b border-white/5">
              <div>
                <h2 className="text-base font-bold text-white">Run New Analysis</h2>
                <p className="text-xs text-zinc-500 mt-0.5">Select a log source to analyze with the Neural Engine</p>
              </div>
              <button onClick={() => setShowRunModal(false)} className="text-zinc-500 hover:text-white transition-colors cursor-pointer">
                <X size={18} />
              </button>
            </div>

            {/* Source List */}
            <div className="p-6 max-h-[50vh] overflow-y-auto space-y-2">
              {sources.length === 0 ? (
                <div className="text-center py-8 text-zinc-500 text-sm">
                  No log files found in <code className="text-fuchsia-400">data/</code> directory.
                  <br/>Upload a file or add logs to analyze.
                </div>
              ) : (
                sources.map((src) => (
                  <label
                    key={src.path}
                    className={`flex items-center gap-4 p-4 rounded-xl cursor-pointer transition-all border ${
                      selectedSource === src.path
                        ? 'bg-fuchsia-500/10 border-fuchsia-500/30'
                        : 'bg-white/[0.02] border-white/5 hover:bg-white/5'
                    }`}
                  >
                    <input
                      type="radio"
                      name="source"
                      value={src.path}
                      checked={selectedSource === src.path}
                      onChange={() => setSelectedSource(src.path)}
                      className="accent-fuchsia-500"
                    />
                    <FileText size={18} className="text-zinc-400 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-white truncate">{src.name}</p>
                      <p className="text-[10px] text-zinc-500 mt-0.5">
                        {src.size_human} · ~{src.lines_estimate?.toLocaleString()} lines
                      </p>
                    </div>
                  </label>
                ))
              )}
            </div>

            {/* Modal Footer */}
            <div className="px-6 py-4 border-t border-white/5 flex items-center justify-between">
              <button
                onClick={() => setShowRunModal(false)}
                className="text-sm text-zinc-400 hover:text-white transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                onClick={handleRunAnalysis}
                disabled={isRunning || !selectedSource}
                className="bg-fuchsia-600 hover:bg-fuchsia-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold rounded-lg px-6 py-2.5 text-sm flex items-center gap-2 transition-colors cursor-pointer"
              >
                {isRunning ? (
                  <>
                    <Loader2 size={16} className="animate-spin" /> Analyzing...
                  </>
                ) : (
                  <>
                    <Play size={14} fill="currentColor" /> Start Analysis
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
      {/* ═══ COMMAND PALETTE MODAL ═══ */}
      {showCommandPalette && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-[200] flex items-start justify-center pt-[15vh] px-4">
          <div className="bg-[#0f0f13] border border-white/10 rounded-2xl w-[580px] overflow-hidden shadow-2xl animate-in fade-in zoom-in-95 duration-200">
            <div className="p-4 border-b border-white/5 flex items-center gap-3">
              <Search size={18} className="text-zinc-500 shrink-0" />
              <input
                type="text"
                autoFocus
                placeholder="Search commands, destinations, actions... (e.g. 'Topology')"
                value={paletteSearch}
                onChange={(e) => setPaletteSearch(e.target.value)}
                className="bg-transparent border-none outline-none text-sm text-zinc-200 w-full placeholder-zinc-600"
              />
              <span className="text-[9px] font-bold text-zinc-500 bg-white/5 border border-white/10 px-2 py-1 rounded">ESC</span>
            </div>
            
            <div className="max-h-[300px] overflow-y-auto p-2">
              {filteredCommands.length === 0 ? (
                <div className="text-center py-8 text-zinc-500 text-xs">No matching actions found</div>
              ) : (
                filteredCommands.map((cmd, idx) => {
                  const Icon = cmd.icon;
                  return (
                    <button
                      key={idx}
                      onClick={cmd.action}
                      className="w-full text-left flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-semibold text-zinc-400 hover:text-white hover:bg-white/[0.04] transition-all cursor-pointer border-none bg-transparent"
                    >
                      <Icon size={14} className="text-fuchsia-400 shrink-0" />
                      <span>{cmd.name}</span>
                    </button>
                  );
                })
              )}
            </div>
          </div>
        </div>
      )}
      {/* ═══ ONBOARDING WIZARD ═══ */}
      <OnboardingWizard />
    </div>
  );
}
