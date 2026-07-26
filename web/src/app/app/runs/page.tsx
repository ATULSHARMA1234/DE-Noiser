'use client';

import React, { useState, useEffect } from 'react';
import { CheckCircle2, Clock, XCircle, Trash2, RefreshCw, GitCompare, FileText } from 'lucide-react';
import { apiFetch, apiDelete } from '@/lib/api';
import { useRouter } from 'next/navigation';
import { useToast } from '@/context/ToastContext';
import { ConfirmModal } from '@/components/ConfirmModal';

export default function AnalysisRunsPage() {
 const router = useRouter();
 const { toast } = useToast();
 const [runs, setRuns] = useState<any[]>([]);
 const [isLoading, setIsLoading] = useState(true);
 const [error, setError] = useState<string | null>(null);
 const [selectedRuns, setSelectedRuns] = useState<string[]>([]);

 const [confirmOpen, setConfirmOpen] = useState(false);
 const [confirmTitle, setConfirmTitle] = useState('');
 const [confirmMessage, setConfirmMessage] = useState('');
 const [confirmCallback, setConfirmCallback] = useState<(() => void) | null>(null);

 const fetchRuns = () => {
 setIsLoading(true);
 setError(null);
 apiFetch('/runs')
 .then(data => {
 setRuns(Array.isArray(data) ? data : []);
 setSelectedRuns([]);
 setIsLoading(false);
 })
 .catch(e => {
 console.error(e);
 setError(e.message || 'Failed to load analysis runs.');
 setIsLoading(false);
 });
 };

 useEffect(() => { fetchRuns(); }, []);

 const handleDelete = (runId: string) => {
 setConfirmTitle('Delete Analysis Run');
 setConfirmMessage('Delete this analysis run?');
 setConfirmCallback(() => async () => {
 try {
 await apiDelete(`/runs/${runId}`);
 fetchRuns();
 toast.success('Analysis run deleted successfully.');
 } catch (e: any) {
 toast.error(`Delete failed: ${e.message}`);
 }
 });
 setConfirmOpen(true);
 };

 const toggleSelection = (runId: string) => {
 setSelectedRuns(prev => {
 if (prev.includes(runId)) return prev.filter(id => id !== runId);
 if (prev.length >= 2) return [prev[1], runId]; // keep max 2 selected
 return [...prev, runId];
 });
 };

 const handleCompare = () => {
 if (selectedRuns.length !== 2) return;
 // ensure chronological order (older = run_a, newer = run_b)
 const r1 = runs.find(r => r.id === selectedRuns[0]);
 const r2 = runs.find(r => r.id === selectedRuns[1]);
 const d1 = new Date(r1.created_at).getTime();
 const d2 = new Date(r2.created_at).getTime();
 
 let runA = selectedRuns[0];
 let runB = selectedRuns[1];
 if (d1 > d2) {
 runA = selectedRuns[1];
 runB = selectedRuns[0];
 }
 
 router.push(`/app/runs/compare?run_a=${runA}&run_b=${runB}`);
 };

 return (
 <div className="max-w-[1600px] mx-auto pb-10">
 <div className="flex items-center justify-between mb-8">
 <div>
 <h1 className="text-xl font-bold text-[var(--text-primary)] mb-1">Analysis Runs</h1>
 <p className="text-xs text-[var(--text-muted)]">History of batch processing runs, HDBSCAN clustering, and snapshots. <span className="text-[var(--text-dimmed)]">{runs.length} total</span></p>
 </div>
 <div className="flex gap-3">
 <button 
 onClick={fetchRuns}
 className="bg-[var(--bg-surface)] hover:bg-[var(--bg-surface-hover)] text-[var(--text-primary)] font-medium rounded-md px-4 py-2 text-xs border border-[var(--border-subtle)] flex items-center gap-2 cursor-pointer transition-colors"
 >
 <RefreshCw size={14} /> Refresh
 </button>
 <button 
 onClick={handleCompare}
 disabled={selectedRuns.length !== 2}
 className="bg-[var(--primary)] disabled:bg-[var(--bg-track)] disabled:text-[var(--text-dimmed)] hover:bg-[var(--primary)] text-white font-medium rounded-md px-4 py-2 text-xs flex items-center gap-2 cursor-pointer transition-colors border-none"
 >
 <GitCompare size={14} /> Compare Selected ({selectedRuns.length}/2)
 </button>
 </div>
 </div>

 {runs.length > 0 && (
 <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4">
 <p className="text-[10px] text-[var(--text-muted)] font-medium mb-1">Total Runs</p>
 <p className="text-2xl font-bold text-[var(--text-primary)]">{runs.length}</p>
 </div>
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4">
 <p className="text-[10px] text-[var(--text-muted)] font-medium mb-1">Total Logs Processed</p>
 <p className="text-2xl font-bold text-[var(--text-primary)]">{runs.reduce((acc, r) => acc + (r.raw_lines || 0), 0).toLocaleString()}</p>
 </div>
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4">
 <p className="text-[10px] text-[var(--text-muted)] font-medium mb-1">Avg Reduction</p>
 <p className="text-2xl font-bold text-emerald-400">
 {(runs.reduce((acc, r) => acc + (r.reduction_ratio || 0), 0) / runs.length * 100).toFixed(1)}%
 </p>
 </div>
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4">
 <p className="text-[10px] text-[var(--text-muted)] font-medium mb-1">Avg Duration</p>
 <p className="text-2xl font-bold text-[var(--primary)]">
 {(runs.reduce((acc, r) => acc + (r.duration_sec || 0), 0) / runs.length).toFixed(1)}s
 </p>
 </div>
 </div>
 )}

 <div className="bg-[var(--bg-card)] border-none rounded-xl overflow-hidden shadow-sm">
 <table className="w-full text-left text-xs">
 <thead className="text-[10px] font-bold text-[var(--text-dimmed)] uppercase tracking-wider border-b border-transparent bg-transparent">
 <tr>
 <th className="p-5 font-medium w-10">Select</th>
 <th className="p-5 font-medium">Status</th>
 <th className="p-5 font-medium">Run ID</th>
 <th className="p-5 font-medium">Source</th>
 <th className="p-5 font-medium">Stats</th>
 <th className="p-5 font-medium">Time</th>
 <th className="p-5 font-medium text-right">Actions</th>
 </tr>
 </thead>
 <tbody className="divide-y divide-transparent">
 {isLoading ? (
 Array.from({ length: 5 }).map((_, idx) => (
 <tr key={idx}>
 <td className="p-5 text-center"><div className="shimmer-bg h-4 w-4 rounded mx-auto" /></td>
 <td className="p-5"><div className="shimmer-bg h-6 w-16 rounded-full" /></td>
 <td className="p-5"><div className="shimmer-bg h-4 w-28 rounded" /></td>
 <td className="p-5"><div className="shimmer-bg h-4 w-40 rounded" /></td>
 <td className="p-5">
 <div className="shimmer-bg h-4 w-32 rounded mb-1.5" />
 <div className="shimmer-bg h-3 w-20 rounded" />
 </td>
 <td className="p-5"><div className="shimmer-bg h-4 w-28 rounded" /></td>
 <td className="p-5 text-right"><div className="shimmer-bg h-4 w-8 rounded ml-auto" /></td>
 </tr>
 ))
 ) : error ? (
 <tr>
 <td colSpan={7} className="p-8 text-center text-red-400">
 {error}
 </td>
 </tr>
 ) : runs.length === 0 ? (
 <tr>
 <td colSpan={7} className="p-8 text-center text-[var(--text-muted)]">
 No analysis runs yet. Use the Run Analysis button to start one.
 </td>
 </tr>
 ) : runs.map((run: any) => (
 <tr key={run.id} className={`hover:bg-[var(--bg-surface-hover)] transition-colors rounded-lg ${selectedRuns.includes(run.id) ? 'bg-[var(--primary)]' : ''}`}>
 <td className="p-5 text-center">
 <input 
 type="checkbox" 
 checked={selectedRuns.includes(run.id)} 
 onChange={() => toggleSelection(run.id)}
 className="w-4 h-4 rounded bg-[var(--bg-input)] border-[var(--border)] accent-[var(--primary)] cursor-pointer"
 />
 </td>
 <td className="p-5">
 {run.status === "Completed" ? (
 <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full border border-emerald-500/20 bg-emerald-500/10 text-[9px] font-bold text-emerald-400 uppercase tracking-wider">
 <CheckCircle2 size={12} /> Completed
 </span>
 ) : (
 <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full border border-red-500/20 bg-red-500/10 text-[9px] font-bold text-red-400 uppercase tracking-wider">
 <XCircle size={12} /> {run.status}
 </span>
 )}
 </td>
 <td className="p-5">
 <span className="text-[var(--text-secondary)] font-medium font-mono">{run.id}</span>
 </td>
 <td className="p-5">
 <span className="inline-flex items-center px-2 py-1 rounded bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[10px] text-[var(--text-secondary)] font-mono truncate max-w-[200px]">
 {run.source}
 </span>
 </td>
 <td className="p-5">
 <div className="flex flex-col gap-0.5">
 <span className="text-[var(--text-secondary)]"><strong className="text-[var(--text-primary)]">{run.raw_lines?.toLocaleString()}</strong> raw lines</span>
 <span className="text-[var(--text-secondary)]"><strong className="text-[var(--primary)]">{run.cluster_count}</strong> clusters</span>
 <span className="text-emerald-400 font-medium text-[10px]">{(run.reduction_ratio * 100).toFixed(2)}% reduced</span>
 </div>
 </td>
 <td className="p-5">
 <div className="text-[var(--text-secondary)] flex items-center gap-1.5 mb-1">
 <Clock size={12} /> {run.created_at ? new Date(run.created_at).toLocaleString() : '—'}
 </div>
 <div className="text-[var(--text-muted)] text-[10px]">Duration: {run.duration_sec?.toFixed(2)}s</div>
 </td>
 <td className="p-5 text-right">
 <div className="flex justify-end items-center gap-3">
 <button
 onClick={() => router.push(`/app/runs/${run.id}`)}
 className="inline-flex items-center gap-1.5 text-[var(--primary)] hover:underline text-xs font-semibold cursor-pointer bg-transparent border-none p-0"
 >
 <FileText size={13} /> Report
 </button>
 <button
 onClick={() => handleDelete(run.id)}
 className="text-[var(--text-muted)] hover:text-red-400 transition-colors cursor-pointer bg-transparent border-none"
 >
 <Trash2 size={14} />
 </button>
 </div>
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 <ConfirmModal
 isOpen={confirmOpen}
 onClose={() => setConfirmOpen(false)}
 onConfirm={confirmCallback || (() => {})}
 title={confirmTitle}
 message={confirmMessage}
 />
 </div>
 );
}

