'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Pause, Play, TerminalSquare, Trash2, Download } from 'lucide-react';
import { WS_BASE } from '@/lib/api';

type LogEntry = {
 id: string;
 level: string;
 service: string;
 message: string;
 timestamp?: number;
 highlight?: boolean;
 levelColor?: string;
};

const LEVEL_COLORS: Record<string, string> = {
 INFO: 'text-blue-400',
 WARN: 'text-yellow-400',
 ERROR: 'text-red-500',
 ANOMALY: 'text-[var(--primary)]',
};

const LEVEL_FILTERS = ['ALL', 'INFO', 'WARN', 'ERROR', 'ANOMALY'];

export default function LivePulsePage() {
 const [logs, setLogs] = useState<LogEntry[]>([]);
 const [isPaused, setIsPaused] = useState(false);
 const [autoScroll, setAutoScroll] = useState(true);
 const [levelFilter, setLevelFilter] = useState('ALL');
 const [serviceFilter, setServiceFilter] = useState('');
 const [totalReceived, setTotalReceived] = useState(0);
 const [connected, setConnected] = useState(false);
 const scrollRef = useRef<HTMLDivElement>(null);
 const wsRef = useRef<WebSocket | null>(null);

 useEffect(() => {
 if (isPaused) {
 wsRef.current?.close();
 setConnected(false);
 return;
 }

 const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
 const url = token ? `${WS_BASE}/stream?token=${encodeURIComponent(token)}` : `${WS_BASE}/stream`;
 const ws = new WebSocket(url);
 wsRef.current = ws;

 ws.onopen = () => {
 setConnected(true);
 // Tell the backend we want to tail the live ingestion stream
 // If the file doesn't exist yet, it will safely fall back to the demo stream
 ws.send(JSON.stringify({ file: 'data/live_stream.log' }));
 };

 ws.onmessage = (event) => {
 let log: LogEntry;
 try {
 log = JSON.parse(event.data);
 } catch {
 return; // ignore malformed frames
 }
 log.levelColor = LEVEL_COLORS[log.level] || 'text-[var(--text-muted)]';
 setTotalReceived(prev => prev + 1);
 setLogs(prev => [...prev, log].slice(-500)); // Keep last 500 logs
 };

 ws.onerror = () => {
 // Silently handle — might not have websockets installed
 setConnected(false);
 };

 ws.onclose = () => {
 setConnected(false);
 };

 return () => ws.close();
 }, [isPaused]);

 // Auto-scroll effect
 useEffect(() => {
 if (autoScroll && scrollRef.current) {
 scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
 }
 }, [logs, autoScroll]);

 const filteredLogs = logs.filter(log => {
 if (levelFilter !== 'ALL' && log.level !== levelFilter) return false;
 if (serviceFilter && !log.service.toLowerCase().includes(serviceFilter.toLowerCase())) return false;
 return true;
 });

 const clearLogs = () => {
 setLogs([]);
 setTotalReceived(0);
 };

 const exportLogs = () => {
 const text = filteredLogs.map(l => `[${l.level}] [${l.service}] ${l.message}`).join('\n');
 const blob = new Blob([text], { type: 'text/plain' });
 const url = URL.createObjectURL(blob);
 const a = document.createElement('a');
 a.href = url;
 a.download = `semanticos_live_${Date.now()}.log`;
 a.click();
 };

 const services = [...new Set(logs.map(l => l.service))];

 return (
 <div className="max-w-[1600px] mx-auto pb-10 flex flex-col h-full">
 
 {/* Header */}
 <div className="flex items-center justify-between mb-6 border-b border-[var(--border-subtle)] pb-4 shrink-0">
 <div className="flex items-center gap-4">
 <h1 className="text-lg font-bold text-[var(--text-primary)] flex items-center gap-2">
 <TerminalSquare size={20} className="text-[var(--primary)]" />
 &gt;_ Live Pulse Stream
 </h1>
 {isPaused ? (
 <span className="text-[10px] font-bold uppercase tracking-wider text-orange-400 bg-orange-400/10 px-2 py-1 rounded border border-orange-500/20 flex items-center gap-1">
 <span className="w-1.5 h-1.5 rounded-full bg-orange-500"></span> PAUSED
 </span>
 ) : connected ? (
 <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-400 bg-emerald-400/10 px-2 py-1 rounded border border-emerald-500/20 flex items-center gap-1">
 <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span> STREAMING
 </span>
 ) : (
 <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)] bg-[var(--bg-surface)] px-2 py-1 rounded border border-[var(--border-subtle)] flex items-center gap-1">
 <span className="w-1.5 h-1.5 rounded-full bg-[var(--text-muted)] animate-pulse"></span> CONNECTING
 </span>
 )}
 <span className="text-[10px] text-[var(--text-muted)] font-mono">{totalReceived} events received</span>
 </div>
 
 <div className="flex items-center gap-4 text-xs font-medium">
 {/* Level Filter */}
 <div className="flex items-center gap-1">
 {LEVEL_FILTERS.map(level => (
 <button
 key={level}
 onClick={() => setLevelFilter(level)}
 className={`px-2 py-1 rounded text-[10px] font-bold uppercase tracking-wider transition-colors cursor-pointer border-none ${
 levelFilter === level
 ? level === 'ERROR' ? 'bg-red-500/20 text-red-400'
 : level === 'WARN' ? 'bg-yellow-500/20 text-yellow-400'
 : level === 'ANOMALY' ? 'bg-[var(--primary)] text-[var(--primary)]'
 : 'bg-blue-500/20 text-blue-400'
 : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] bg-transparent'
 }`}
 >
 {level}
 </button>
 ))}
 </div>

 {/* Service Filter */}
 <select
 value={serviceFilter}
 onChange={(e) => setServiceFilter(e.target.value)}
 className="bg-[var(--bg-modal)] border border-[var(--border)] text-[var(--text-input)] text-xs rounded px-2 py-1 outline-none cursor-pointer"
 >
 <option value="">All Services</option>
 {services.map(s => <option key={s} value={s}>{s}</option>)}
 </select>

 <label className="flex items-center gap-2 text-[var(--text-secondary)] cursor-pointer">
 <input 
 type="checkbox" 
 checked={autoScroll} 
 onChange={() => setAutoScroll(!autoScroll)}
 className="accent-[var(--primary)]"
 />
 Auto-scroll
 </label>

 <button 
 onClick={exportLogs}
 className="flex items-center gap-1.5 text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors cursor-pointer bg-transparent border-none"
 >
 <Download size={14} /> Export
 </button>

 <button 
 onClick={clearLogs}
 className="flex items-center gap-1.5 text-[var(--text-secondary)] hover:text-red-400 transition-colors cursor-pointer bg-transparent border-none"
 >
 <Trash2 size={14} /> Clear
 </button>

 <button 
 onClick={() => setIsPaused(!isPaused)}
 className="flex items-center gap-1.5 bg-[var(--bg-surface)] hover:bg-[var(--bg-surface-hover)] text-[var(--text-primary)] px-3 py-1.5 rounded-md transition-colors border border-[var(--border-subtle)] cursor-pointer"
 >
 {isPaused ? <><Play size={14} /> Resume</> : <><Pause size={14} /> Pause</>}
 </button>
 </div>
 </div>

 {/* Terminal View */}
 <div 
 ref={scrollRef}
 className="font-mono text-xs w-full flex-1 overflow-y-auto bg-[var(--bg-inset)] rounded-xl border border-[var(--border-subtle)] p-2"
 >
 {filteredLogs.length === 0 ? (
 <div className="flex items-center justify-center h-full text-[var(--text-muted)]">
 {isPaused ? 'Stream paused. Click Resume to continue.' : 'Waiting for log events...'}
 </div>
 ) : (
 filteredLogs.map((log, i) => (
 <div 
 key={i} 
 className={`flex items-start gap-6 py-1.5 px-4 rounded-sm ${
 log.highlight ? 'bg-[var(--primary)] border-l-2 border-[var(--primary)]' : 'hover:bg-[var(--bg-surface-hover)]'
 }`}
 >
 <span className="text-[var(--text-dimmed)] w-10 shrink-0 text-right font-mono">{log.id}</span>
 <span className={`font-bold text-[10px] w-16 shrink-0 ${log.levelColor}`}>
 {log.level}
 </span>
 <span className="text-[var(--text-secondary)] w-28 shrink-0 truncate">{log.service}</span>
 <span className={`${log.highlight ? 'text-[var(--primary)] font-medium' : log.level === 'WARN' ? 'text-yellow-500/80' : 'text-[var(--text-input)]'}`}>
 {log.message}
 </span>
 </div>
 ))
 )}
 </div>

 </div>
 );
}

