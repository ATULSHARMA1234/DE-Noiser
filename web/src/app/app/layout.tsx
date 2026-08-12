'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { LayoutGrid, Terminal, ShieldAlert, History, Database, Settings, Search, Play, Cpu, X, FileText, Loader2, Network, Users, Bell, Sun, Moon, Menu, Activity, Zap, Plug, BookOpen, Bug } from 'lucide-react';
import { apiFetch, runAnalysis } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import { useTheme } from 'next-themes';
import { useToast } from '@/context/ToastContext';
import { OnboardingWizard } from '@/components/OnboardingWizard';
import { TimeRangeProvider } from '@/context/TimeRangeContext';
import { TimeRangePicker } from '@/components/TimeRangePicker';
import { useTasks } from '@/context/TaskContext';

export default function AppLayout({ children }: { children: React.ReactNode }) {
 const { user, loading, logout, hasRole } = useAuth();
 const { theme, setTheme } = useTheme();
 const { toast } = useToast();
 const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
 const pathname = usePathname();
 const router = useRouter();
 const [showRunModal, setShowRunModal] = useState(false);
 const [sources, setSources] = useState<any[]>([]);
 const [selectedSource, setSelectedSource] = useState('');
 const [searchQuery, setSearchQuery] = useState('');
 const { tasks, executeTask, attachRemoteTask } = useTasks();
 const runningTasksCount = tasks.filter(t => t.status === 'running').length;

 // Grouped by the job each tool does, so a long list stays scannable.
 // Three destinations are folded under the thing they belong to rather than
 // sitting at top level: Analysis Runs is the plumbing behind Incidents, and
 // Integrations / User Directory are both Settings. They stay one click away.
 type NavChild = { name: string; path: string };
 type NavItem = { name: string; path: string; icon: any; children?: NavChild[] };
 const navSections: { label: string; items: NavItem[] }[] = [
 {
 label: 'Overview',
 items: [
 { name: 'Command Center', path: '/app', icon: LayoutGrid },
 { name: 'Dashboards', path: '/app/dashboards', icon: LayoutGrid },
 ],
 },
 {
 label: 'Investigate',
 items: [
 { name: 'Explore', path: '/app/explore', icon: Search },
 { name: 'Live Stream', path: '/app/live', icon: Terminal },
 { name: 'Traces', path: '/app/traces', icon: Activity },
 { name: 'Notebooks', path: '/app/notebooks', icon: BookOpen },
 ],
 },
 {
 label: 'Analyze',
 items: [
 { name: 'Issues', path: '/app/issues', icon: Bug },
 {
 name: 'Incidents', path: '/app/incidents', icon: ShieldAlert,
 children: [{ name: 'Analysis Runs', path: '/app/runs' }],
 },
 { name: 'Metrics', path: '/app/metrics', icon: Activity },
 { name: 'Topology', path: '/app/topology', icon: Network },
 ],
 },
 {
 label: 'Reliability',
 items: [
 { name: 'SLOs', path: '/app/slos', icon: Zap },
 { name: 'Monitors', path: '/app/monitors', icon: Bell },
 { name: 'Alerts', path: '/app/alerts', icon: Bell },
 { name: 'Runbooks', path: '/app/runbooks', icon: Play },
 ],
 },
 {
 label: 'Configure',
 items: [
 { name: 'Data Sources', path: '/app/sources', icon: Database },
 {
 name: 'Settings', path: '/app/settings', icon: Settings,
 children: [
 { name: 'Integrations', path: '/app/integrations' },
 ...(user?.role === 'ADMIN'
 ? [{ name: 'User Directory', path: '/app/users' }]
 : []),
 ],
 },
 ],
 },
 ];

 // A parent stays lit while one of its folded children is the active route.
 const isItemActive = (item: NavItem) =>
 pathname === item.path ||
 (!!pathname?.startsWith(item.path + '/') && item.path !== '/app') ||
 !!item.children?.some((c) => pathname === c.path);

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

 // Command+Shift+R / Ctrl+Shift+R triggers analysis (Shift avoids browser reload)
 if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'r') {
 e.preventDefault();
 // eslint-disable-next-line react-hooks/immutability
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
 { name: 'Toggle Light/Dark Theme', action: () => { setTheme(theme === 'dark' ? 'light' : 'dark'); setShowCommandPalette(false); }, icon: Sun },
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

 const handleRunAnalysis = () => {
 if (!selectedSource) return;
 setShowRunModal(false);
 
 const sourceName = sources.find(s => s.path === selectedSource)?.name || 'Source';
 const taskId = `analysis:${sourceName}`;
 
 executeTask(taskId, `Analyzing ${sourceName}`, runAnalysis(
 { source: selectedSource, intelligence: true },
 (remoteId) => attachRemoteTask(taskId, remoteId),
 ));
 
 router.push('/app');
 };

 if (loading) {
 return (
 <div className="min-h-screen w-full flex items-center justify-center bg-[var(--bg-app)] overflow-hidden select-none">
 <div className="flex flex-col items-center gap-4 z-10">
 <Cpu size={36} className="text-[var(--primary)] animate-spin" />
 <div className="text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">Loading SemanticOS...</div>
 </div>
 </div>
 );
 }

 if (!user) {
 return null;
 }

 return (
 <TimeRangeProvider>
 <div className="flex h-screen bg-[var(--bg-app)] text-[var(--text-primary)] font-sans overflow-hidden">
 
 {/* Translucent background overlay on mobile when sidebar drawer is active */}
 {mobileSidebarOpen && (
 <div 
 className="fixed inset-0 bg-black/60 backdrop-blur-xs z-45 md:hidden" 
 onClick={() => setMobileSidebarOpen(false)} 
 />
 )}
 
 {/* SIDEBAR */}
 <aside className={`fixed inset-y-0 left-0 w-60 border-r border-[var(--border-subtle)] bg-[var(--bg-app)] flex flex-col z-50 shrink-0 transform md:relative md:translate-x-0 transition-transform duration-300 ${
 mobileSidebarOpen ? 'translate-x-0' : '-translate-x-full'
 }`}>
 
 {/* Logo Area */}
 <div className="h-14 flex items-center justify-between px-4 border-b border-[var(--border-subtle)] shrink-0">
 <Link href="/app" className="flex items-center gap-2.5">
 <span className="w-6 h-6 rounded-[3px] border border-[var(--primary-line)] bg-[var(--primary-dim)] flex items-center justify-center">
 <span className="mono text-[13px] font-bold text-[var(--primary)] leading-none">S</span>
 </span>
 <h1 className="text-[14px] font-semibold tracking-tight text-[var(--text-primary)]">SemanticOS</h1>
 </Link>
 {/* Close button on mobile */}
 <button
 onClick={() => setMobileSidebarOpen(false)}
 className="p-1 text-[var(--text-muted)] hover:text-[var(--text-primary)] md:hidden cursor-pointer bg-transparent border-none"
 >
 <X size={16} />
 </button>
 </div>

 {/* Navigation */}
 <nav className="flex-1 flex flex-col gap-4 w-full px-2.5 py-3 overflow-y-auto">
 {navSections.map((section) => (
 <div key={section.label}>
 <div className="eyebrow px-2 mb-1.5">{section.label}</div>
 <div className="flex flex-col gap-0.5">
 {section.items.map((item) => {
 const isActive = isItemActive(item);
 const Icon = item.icon;
 return (
 <div key={item.name}>
 <Link
 href={item.path}
 className={`flex items-center gap-2.5 px-2 h-8 rounded-[3px] transition-colors text-[13px] border-l-2 ${
 isActive
 ? 'text-[var(--primary)] bg-[var(--primary-dim)] border-[var(--primary)]'
 : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)] border-transparent'
 }`}
 >
 <Icon size={15} strokeWidth={2} className={isActive ? 'text-[var(--primary)]' : ''} />
 {item.name}
 </Link>
 {/* Folded children reveal once you're anywhere in the parent's area. */}
 {item.children && item.children.length > 0 && isActive && (
 <div className="ml-4 mt-0.5 flex flex-col border-l border-[var(--border-subtle)]">
 {item.children.map((c) => (
 <Link
 key={c.path}
 href={c.path}
 className={`pl-3 h-7 flex items-center text-[12px] transition-colors ${
 pathname === c.path
 ? 'text-[var(--primary)]'
 : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
 }`}
 >
 {c.name}
 </Link>
 ))}
 </div>
 )}
 </div>
 );
 })}
 </div>
 </div>
 ))}
 </nav>

 {/* Footer Status */}
 <div className="px-4 py-3 border-t border-[var(--border-subtle)] shrink-0">
 <div className="flex items-center gap-2 eyebrow">
 <span className="w-1.5 h-1.5 rounded-full bg-[var(--status-green)]" />
 Engine online · local
 </div>
 </div>
 </aside>

 {/* MAIN CONTENT AREA */}
 <main className="flex-1 flex flex-col overflow-hidden relative bg-[var(--bg-app)]">
 
 {/* TOP HEADER */}
 <header className="h-14 border-b border-[var(--border-subtle)] bg-[var(--bg-app)] flex items-center justify-between px-6 shrink-0 z-40">
 
 {/* Mobile hamburger menu */}
 <button 
 onClick={() => setMobileSidebarOpen(true)} 
 className="p-2 -ml-2 mr-3 text-[var(--text-secondary)] hover:text-[var(--text-primary)] md:hidden cursor-pointer bg-transparent border-none"
 >
 <Menu size={20} />
 </button>

 {/* Search Bar */}
 <div className="flex-1 max-w-md">
 <div className="relative flex items-center w-full h-8 rounded bg-[var(--bg-input)] border border-[var(--border)] px-3 overflow-hidden">
 <Search size={14} className="text-[var(--text-muted)]" />
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
 className="bg-transparent border-none outline-none text-[13px] text-[var(--text-input)] ml-2 w-full placeholder-[var(--text-dimmed)]"
 />
 </div>
 </div>

 {/* Actions */}
 <div className="flex items-center gap-3">
 {runningTasksCount > 0 && (
 <div className="flex items-center gap-2 px-3 py-1.5 bg-[var(--bg-surface)] border border-[var(--primary)]/30 rounded text-xs font-medium text-[var(--primary)]">
 <Loader2 size={14} className="animate-spin" />
 {runningTasksCount} Active {runningTasksCount === 1 ? 'Task' : 'Tasks'}
 </div>
 )}
 <TimeRangePicker />
 <button
 onClick={() => {
 fetchSources();
 setShowRunModal(true);
 }}
 className="bg-[var(--primary)] hover:bg-[var(--primary-hover)] text-white font-semibold rounded px-4 py-1.5 h-8 text-xs border-none flex items-center gap-2 cursor-pointer transition-colors"
 >
 <Play size={13} fill="currentColor" /> Run Analysis
 </button>
 <button
 onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
 className="p-1.5 rounded bg-[var(--bg-surface)] hover:bg-[var(--bg-surface-hover)] border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer transition-all flex items-center justify-center h-8 w-8"
 title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
 >
 {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
 </button>
 <div className="flex items-center gap-2.5 bg-[var(--bg-surface)] border border-[var(--border)] px-3 py-1 rounded">
 <div className="flex flex-col text-right hidden md:flex">
 <span className="text-[11px] font-semibold text-[var(--text-primary)] max-w-[120px] truncate">{user.email}</span>
 <span className="text-[9px] font-medium text-[var(--primary)] tracking-wider uppercase">{user.role}</span>
 </div>
 <button 
 onClick={logout} 
 className="bg-[var(--bg-card)] hover:bg-[var(--status-red)]/10 hover:text-[var(--status-red)] text-[var(--text-muted)] text-[10px] px-2 py-0.5 rounded cursor-pointer transition-all border border-[var(--border-subtle)] hover:border-[var(--status-red)]/20 font-semibold uppercase tracking-wider"
 >
 Logout
 </button>
 </div>
 </div>
 </header>

 {/* PAGE CONTENT */}
 <div className="flex-1 overflow-y-auto bg-[var(--bg-app)] p-8">
 {children}
 </div>
 </main>

 {/* ═══ RUN ANALYSIS MODAL ═══ */}
 {showRunModal && (
 <div className="fixed inset-0 bg-black/60 z-[100] flex items-center justify-center">
 <div className="bg-[var(--bg-modal)] border border-[var(--border)] rounded-lg w-[520px] max-h-[80vh] overflow-hidden shadow-2xl">
 
 {/* Modal Header */}
 <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-subtle)]">
 <div>
 <h2 className="text-sm font-bold text-[var(--text-primary)]">Run New Analysis</h2>
 <p className="text-xs text-[var(--text-muted)] mt-0.5">Select a log source to analyze</p>
 </div>
 <button onClick={() => setShowRunModal(false)} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors cursor-pointer bg-transparent border-none">
 <X size={16} />
 </button>
 </div>

 {/* Source List */}
 <div className="p-5 max-h-[50vh] overflow-y-auto space-y-1.5">
 {sources.length === 0 ? (
 <div className="text-center py-8 text-[var(--text-muted)] text-sm">
 No log files found in <code className="text-[var(--primary)]">data/</code> directory.
 <br/>Upload a file or add logs to analyze.
 </div>
 ) : (
 sources.map((src) => (
 <label
 key={src.path}
 className={`flex items-center gap-4 p-3 rounded cursor-pointer transition-all border ${
 selectedSource === src.path
 ? 'bg-[var(--primary)]/10 border-[var(--primary)]/30 text-[var(--text-primary)]'
 : 'bg-[var(--bg-surface)] border-[var(--border-subtle)] hover:bg-[var(--bg-surface-hover)]'
 }`}
 >
 <input
 type="radio"
 name="source"
 value={src.path}
 checked={selectedSource === src.path}
 onChange={() => setSelectedSource(src.path)}
 className="accent-[var(--primary)]"
 />
 <FileText size={16} className="text-[var(--text-secondary)] shrink-0" />
 <div className="flex-1 min-w-0">
 <p className="text-[13px] font-medium text-[var(--text-primary)] truncate">{src.name}</p>
 <p className="text-[10px] text-[var(--text-muted)] mt-0.5">
 {src.size_human} · ~{src.lines_estimate?.toLocaleString()} lines
 </p>
 </div>
 </label>
 ))
 )}
 </div>

 {/* Modal Footer */}
 <div className="px-6 py-3.5 border-t border-[var(--border-subtle)] flex items-center justify-between">
 <button
 onClick={() => setShowRunModal(false)}
 className="text-[13px] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors cursor-pointer bg-transparent border-none"
 >
 Cancel
 </button>
 <button
 onClick={handleRunAnalysis}
 disabled={!selectedSource}
 className="bg-[var(--primary)] hover:bg-[var(--primary-hover)] disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold rounded px-5 py-2 text-[13px] flex items-center gap-2 transition-colors cursor-pointer border-none"
 >
 <Play size={13} fill="currentColor" /> Start Analysis
 </button>
 </div>
 </div>
 </div>
 )}
 {/* ═══ COMMAND PALETTE MODAL ═══ */}
 {showCommandPalette && (
 <div className="fixed inset-0 bg-black/50 z-[200] flex items-start justify-center pt-[15vh] px-4">
 <div className="bg-[var(--bg-elevated)] border border-[var(--border)] rounded-lg w-[560px] overflow-hidden shadow-2xl">
 <div className="p-3.5 border-b border-[var(--border-subtle)] flex items-center gap-3">
 <Search size={16} className="text-[var(--text-muted)] shrink-0" />
 <input
 type="text"
 autoFocus
 placeholder="Search commands, destinations, actions..."
 value={paletteSearch}
 onChange={(e) => setPaletteSearch(e.target.value)}
 className="bg-transparent border-none outline-none text-[13px] text-[var(--text-primary)] w-full placeholder-[var(--text-dimmed)]"
 />
 <span className="text-[9px] font-semibold text-[var(--text-muted)] bg-[var(--bg-surface-hover)] border border-[var(--border)] px-1.5 py-0.5 rounded text-nowrap">ESC</span>
 </div>
 
 <div className="max-h-[300px] overflow-y-auto p-1.5">
 {filteredCommands.length === 0 ? (
 <div className="text-center py-8 text-[var(--text-muted)] text-xs">No matching actions found</div>
 ) : (
 filteredCommands.map((cmd, idx) => {
 const Icon = cmd.icon;
 return (
 <button
 key={idx}
 onClick={cmd.action}
 className="w-full text-left flex items-center gap-3 px-3 py-2 rounded text-[13px] font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)] transition-all cursor-pointer border-none bg-transparent"
 >
 <Icon size={14} className="text-[var(--primary)] shrink-0" />
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
 </TimeRangeProvider>
 );
}
