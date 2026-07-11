'use client';

import React, { useState } from 'react';
import { Play, Plus, Trash2, Edit3, Type, Database, Save, ChevronDown, AlignLeft } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { useToast } from '@/context/ToastContext';

interface Cell {
 id: string;
 type: 'markdown' | 'query';
 content: string;
 result?: any;
 error?: string;
 isEditing?: boolean;
}

export default function NotebooksPage() {
 const { toast } = useToast();
 const [title, setTitle] = useState('Untitled Investigation Notebook');
 const [cells, setCells] = useState<Cell[]>([
 {
 id: 'c1',
 type: 'markdown',
 content: '# Incident Investigation: Elevated API Latency\n\nThis notebook analyzes the recent spike in API latency across the payment and auth services.',
 isEditing: false
 },
 {
 id: 'c2',
 type: 'query',
 content: 'status:ERROR AND service:payment',
 isEditing: false
 }
 ]);
 const [isRunning, setIsRunning] = useState<Record<string, boolean>>({});

 const updateCell = (id: string, updates: Partial<Cell>) => {
 setCells(cells.map(c => c.id === id ? { ...c, ...updates } : c));
 };

 const addCell = (type: 'markdown' | 'query') => {
 const newCell: Cell = {
 id: `c_${Date.now()}`,
 type,
 content: type === 'markdown' ? 'Double click to edit markdown' : '',
 isEditing: type === 'markdown'
 };
 setCells([...cells, newCell]);
 };

 const removeCell = (id: string) => {
 setCells(cells.filter(c => c.id !== id));
 };

 const runQuery = async (id: string, query: string) => {
 setIsRunning(prev => ({ ...prev, [id]: true }));
 updateCell(id, { error: undefined, result: undefined });
 
 try {
 const response = await apiFetch('/query', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify({ query, limit: 50 })
 });
 updateCell(id, { result: response.logs || [] });
 } catch (e: any) {
 const msg = typeof e?.message === 'string' ? e.message : JSON.stringify(e?.detail || e);
 updateCell(id, { error: msg || 'Query failed' });
 } finally {
 setIsRunning(prev => ({ ...prev, [id]: false }));
 }
 };

 const renderCellContent = (cell: Cell) => {
 if (cell.type === 'markdown') {
 if (cell.isEditing) {
 return (
 <div className="flex flex-col gap-2">
 <textarea
 className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded p-3 text-sm text-[var(--text-primary)] font-mono resize-y min-h-[100px] outline-none focus:border-[var(--primary)] transition-colors"
 value={cell.content}
 onChange={e => updateCell(cell.id, { content: e.target.value })}
 placeholder="Enter markdown..."
 autoFocus
 />
 <div className="flex justify-end">
 <button 
 onClick={() => updateCell(cell.id, { isEditing: false })}
 className="bg-[var(--primary)] hover:bg-[var(--primary)] text-white px-4 py-1.5 rounded text-xs font-medium"
 >
 Done
 </button>
 </div>
 </div>
 );
 }
 return (
 <div 
 className="prose prose-invert prose-sm max-w-none prose-p:text-[var(--text-secondary)] prose-headings:text-[var(--text-primary)] prose-a:text-[var(--primary)] cursor-text"
 onDoubleClick={() => updateCell(cell.id, { isEditing: true })}
 >
 {cell.content.split('\\n').map((line, i) => {
 if (line.startsWith('# ')) return <h1 key={i} className="text-xl font-bold mt-4 mb-2">{line.substring(2)}</h1>;
 if (line.startsWith('## ')) return <h2 key={i} className="text-lg font-bold mt-3 mb-2">{line.substring(3)}</h2>;
 return <p key={i} className="mb-2">{line}</p>;
 })}
 </div>
 );
 }

 if (cell.type === 'query') {
 return (
 <div className="flex flex-col gap-3">
 <div className="flex rounded-md border border-[var(--border)] overflow-hidden bg-[var(--bg-input)] focus-within:border-[var(--primary)] transition-colors">
 <div className="bg-[var(--bg-surface)] px-3 py-2 border-r border-[var(--border)] text-[var(--text-muted)] font-mono text-xs flex items-center select-none">
 LQL
 </div>
 <input
 type="text"
 value={cell.content}
 onChange={e => updateCell(cell.id, { content: e.target.value })}
 onKeyDown={e => {
 if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
 runQuery(cell.id, cell.content);
 }
 }}
 className="flex-1 bg-transparent px-3 py-2 font-mono text-sm text-[var(--text-primary)] outline-none"
 placeholder="e.g. status:ERROR"
 />
 <button
 onClick={() => runQuery(cell.id, cell.content)}
 disabled={isRunning[cell.id] || !cell.content}
 className="bg-[var(--primary)] hover:bg-[var(--primary)] disabled:opacity-50 text-white px-4 flex items-center justify-center transition-colors"
 title="Run query (Cmd+Enter)"
 >
 {isRunning[cell.id] ? <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <Play size={14} fill="currentColor" />}
 </button>
 </div>

 {cell.error && (
 <div className="text-red-400 text-xs font-mono bg-red-500/10 border border-red-500/20 p-3 rounded">
 {cell.error}
 </div>
 )}

 {cell.result && (
 <div className="border border-[var(--border)] rounded bg-[var(--bg-app)] overflow-hidden">
 <div className="bg-[var(--bg-surface)] px-3 py-1.5 text-[10px] font-bold text-[var(--text-secondary)] uppercase tracking-wider border-b border-[var(--border)]">
 {cell.result.length === 0 ? 'No results found' : `${cell.result.length} results`}
 </div>
 {cell.result.length > 0 && (
 <div className="max-h-60 overflow-y-auto">
 <table className="w-full text-left text-xs">
 <thead className="bg-[var(--bg-surface)] sticky top-0">
 <tr>
 <th className="px-3 py-1.5 text-[var(--text-muted)] font-semibold whitespace-nowrap">Time</th>
 <th className="px-3 py-1.5 text-[var(--text-muted)] font-semibold">Level</th>
 <th className="px-3 py-1.5 text-[var(--text-muted)] font-semibold">Source</th>
 <th className="px-3 py-1.5 text-[var(--text-muted)] font-semibold">Message</th>
 </tr>
 </thead>
 <tbody className="divide-y divide-[var(--border)]">
 {cell.result.map((row: any, i: number) => {
 const ts = row.timestamp;
 const date = ts ? new Date(typeof ts === 'number' && ts < 1e12 ? ts * 1000 : ts) : null;
 const level = row.level || 'INFO';
 const levelColor = level === 'ERROR' || level === 'FATAL' ? 'text-red-400' : level === 'WARN' ? 'text-yellow-400' : 'text-blue-400';
 return (
 <tr key={i} className="hover:bg-[var(--bg-surface-hover)]">
 <td className="px-3 py-2 text-[var(--text-secondary)] whitespace-nowrap align-top font-mono">{date ? date.toLocaleTimeString() : '—'}</td>
 <td className={`px-3 py-2 font-bold align-top whitespace-nowrap ${levelColor}`}>{level}</td>
 <td className="px-3 py-2 text-[var(--text-secondary)] align-top whitespace-nowrap truncate max-w-[100px]">{row.source || row.service || '—'}</td>
 <td className="px-3 py-2 font-mono text-[var(--text-primary)] break-all">{row.message || JSON.stringify(row)}</td>
 </tr>
 );
 })}
 </tbody>
 </table>
 </div>
 )}
 </div>
 )}
 </div>
 );
 }
 };

 return (
 <div className="max-w-[1000px] mx-auto pb-20">
 <div className="flex items-center justify-between mb-8 sticky top-0 bg-[var(--bg-app)]/90 backdrop-blur-md py-4 z-10">
 <div className="flex items-center gap-3 w-full max-w-2xl">
 <input
 type="text"
 value={title}
 onChange={e => setTitle(e.target.value)}
 className="text-2xl font-bold text-[var(--text-primary)] bg-transparent border-none outline-none w-full hover:bg-[var(--bg-surface)] focus:bg-[var(--bg-surface)] px-2 py-1 rounded -ml-2 transition-colors"
 />
 </div>
 <div className="flex items-center gap-3">
 <button className="flex items-center gap-2 text-[var(--text-secondary)] hover:text-[var(--text-primary)] px-3 py-1.5 rounded border border-[var(--border)] hover:bg-[var(--bg-surface)] transition-colors text-sm font-medium">
 <Save size={14} /> Save
 </button>
 </div>
 </div>

 <div className="space-y-6">
 {cells.map((cell, idx) => (
 <div key={cell.id} className="group relative flex gap-4">
 {/* Action Bar (Left) */}
 <div className="flex flex-col items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity pt-2">
 <span className="text-[10px] font-mono text-[var(--text-muted)] font-bold">[{idx + 1}]</span>
 <button onClick={() => removeCell(cell.id)} className="text-[var(--text-secondary)] hover:text-red-400" title="Delete Cell">
 <Trash2 size={14} />
 </button>
 </div>

 {/* Cell Content */}
 <div className={`flex-1 border rounded-lg p-5 transition-colors ${
 cell.type === 'markdown' 
 ? cell.isEditing ? 'border-[var(--primary)] bg-[var(--bg-surface)] shadow-md' : 'border-transparent hover:border-[var(--border)] hover:bg-[var(--bg-surface)]' 
 : 'border-[var(--border)] bg-[var(--bg-card)]'
 }`}>
 {renderCellContent(cell)}
 </div>
 </div>
 ))}
 </div>

 {/* Add Cell Buttons */}
 <div className="mt-8 flex items-center gap-4 pl-10 border-t border-[var(--border)] pt-8">
 <span className="text-sm font-semibold text-[var(--text-secondary)]">Add Cell:</span>
 <button onClick={() => addCell('markdown')} className="flex items-center gap-2 text-sm text-[var(--text-primary)] bg-[var(--bg-card)] hover:bg-[var(--bg-surface-hover)] border border-[var(--border)] px-4 py-2 rounded-lg transition-colors shadow-sm">
 <AlignLeft size={16} className="text-blue-400" /> Markdown
 </button>
 <button onClick={() => addCell('query')} className="flex items-center gap-2 text-sm text-[var(--text-primary)] bg-[var(--bg-card)] hover:bg-[var(--bg-surface-hover)] border border-[var(--border)] px-4 py-2 rounded-lg transition-colors shadow-sm">
 <Database size={16} className="text-[var(--primary)]" /> Query (LQL)
 </button>
 </div>
 </div>
 );
}
