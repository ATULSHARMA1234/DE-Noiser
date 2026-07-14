'use client';

import React, { useState, useEffect } from 'react';
import { ShieldAlert, Database, Clock, CheckCircle2, X, Zap, AlertTriangle, Trash2, Search } from 'lucide-react';
import { apiFetch, apiPut, apiDelete } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import { ConfirmModal } from '@/components/ConfirmModal';

export default function IncidentMemoryPage() {
 const { toast } = useToast();
 const [incidents, setIncidents] = useState<any[]>([]);
 const [isLoading, setIsLoading] = useState(true);
 const [error, setError] = useState<string | null>(null);
 const [selectedIncident, setSelectedIncident] = useState<any>(null);
 const [domainFilter, setDomainFilter] = useState('All');
 const [statusFilter, setStatusFilter] = useState('All');
 const [search, setSearch] = useState('');

 const [confirmOpen, setConfirmOpen] = useState(false);
 const [confirmTitle, setConfirmTitle] = useState('');
 const [confirmMessage, setConfirmMessage] = useState('');
 const [confirmCallback, setConfirmCallback] = useState<(() => void) | null>(null);

 const fetchIncidents = () => {
 setIsLoading(true);
 setError(null);
 apiFetch('/incidents')
 .then(data => {
 setIncidents(Array.isArray(data) ? data : []);
 setIsLoading(false);
 })
 .catch(e => {
 console.error(e);
 setError(e.message || 'Failed to load incidents.');
 setIsLoading(false);
 });
 };

 useEffect(() => { fetchIncidents(); }, []);

 const resolveIncident = async (id: number, resolve: boolean) => {
 try {
 await apiPut(`/incidents/${id}/resolve`, { resolved: resolve });
 fetchIncidents();
 if (selectedIncident?.id === id) {
 setSelectedIncident((prev: any) => ({...prev, status: resolve ? 'RESOLVED' : 'OPEN' }));
 }
 toast.success(resolve ? 'Incident marked as resolved.' : 'Incident reopened.');
 } catch (e: any) {
 toast.error(`Failed to resolve: ${e.message}`);
 }
 };

 const deleteIncident = (id: number) => {
 setConfirmTitle('Delete Incident');
 setConfirmMessage('Delete this incident permanently?');
 setConfirmCallback(() => async () => {
 try {
 await apiDelete(`/incidents/${id}`);
 fetchIncidents();
 if (selectedIncident?.id === id) setSelectedIncident(null);
 toast.success('Incident deleted successfully.');
 } catch (e: any) {
 toast.error(`Delete failed: ${e.message}`);
 }
 });
 setConfirmOpen(true);
 };

 const domains = ['All', ...new Set(incidents.map(i => i.domain).filter(Boolean))];

 const filteredIncidents = incidents.filter(inc => {
 if (domainFilter !== 'All' && inc.domain !== domainFilter) return false;
 if (statusFilter !== 'All' && inc.status !== statusFilter) return false;
 if (search.trim()) {
 const q = search.toLowerCase();
 const haystack = `${inc.title ?? ''} ${inc.domain ?? ''} ${inc.summary ?? ''} inc_${inc.id}`.toLowerCase();
 if (!haystack.includes(q)) return false;
 }
 return true;
 });

 const openCount = incidents.filter(i => i.status === 'OPEN').length;

 return (
 <div className="max-w-[1600px] mx-auto pb-10">
 {/* Header */}
 <div className="flex items-end justify-between mb-4 gap-4 flex-wrap">
 <div>
 <div className="eyebrow mb-1">
 {incidents.length} total · <span style={{ color: openCount ? 'var(--signal-crit)' : 'var(--signal-ok)' }}>{openCount} open</span>
 </div>
 <h1 className="text-[18px] font-semibold text-[var(--text-primary)]">Incident Memory</h1>
 </div>
 <div className="flex gap-2">
 <div className="flex items-center gap-2 bg-[var(--bg-inset)] border border-[var(--border)] rounded-[3px] px-2.5 h-8 focus-within:border-[var(--primary-line)]">
 <Search size={13} className="text-[var(--text-muted)]" />
 <input
 value={search}
 onChange={(e) => setSearch(e.target.value)}
 placeholder="Search incidents…"
 className="bg-transparent border-none outline-none text-[12px] text-[var(--text-primary)] w-52"
 />
 {search && (
 <button onClick={() => setSearch('')} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] bg-transparent border-none cursor-pointer">
 <X size={12} />
 </button>
 )}
 </div>
 <select
 value={domainFilter}
 onChange={(e) => setDomainFilter(e.target.value)}
 className="bg-[var(--bg-inset)] border border-[var(--border)] text-[var(--text-secondary)] text-[12px] rounded-[3px] px-2.5 h-8 outline-none cursor-pointer"
 >
 {domains.map(d => <option key={d}>{d}</option>)}
 </select>
 <select
 value={statusFilter}
 onChange={(e) => setStatusFilter(e.target.value)}
 className="bg-[var(--bg-inset)] border border-[var(--border)] text-[var(--text-secondary)] text-[12px] rounded-[3px] px-2.5 h-8 outline-none cursor-pointer"
 >
 <option>All</option>
 <option>OPEN</option>
 <option>RESOLVED</option>
 </select>
 </div>
 </div>

 {/* Table */}
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-[3px] overflow-hidden">
 <table className="w-full text-left text-xs">
 <thead className="border-b border-[var(--border-subtle)]">
 <tr>
 <th className="px-4 py-2.5 eyebrow font-normal text-left">Status</th>
 <th className="px-4 py-2.5 eyebrow font-normal text-left">Incident Title</th>
 <th className="px-4 py-2.5 eyebrow font-normal text-left">Domain</th>
 <th className="px-4 py-2.5 eyebrow font-normal text-left">Impact</th>
 <th className="px-4 py-2.5 eyebrow font-normal text-left">Time</th>
 <th className="px-4 py-2.5 eyebrow font-normal text-left">Actions</th>
 </tr>
 </thead>
 <tbody className="divide-y divide-[var(--border-subtle)]">
 {isLoading ? (
 Array.from({ length: 5 }).map((_, idx) => (
 <tr key={idx}>
 <td className="px-4 py-3"><div className="shimmer-bg h-6 w-16 rounded-full" /></td>
 <td className="px-4 py-3">
 <div className="shimmer-bg h-4 w-40 rounded mb-1.5" />
 <div className="shimmer-bg h-3 w-20 rounded" />
 </td>
 <td className="px-4 py-3"><div className="shimmer-bg h-6 w-24 rounded" /></td>
 <td className="px-4 py-3"><div className="shimmer-bg h-2.5 w-20 rounded" /></td>
 <td className="px-4 py-3"><div className="shimmer-bg h-4 w-28 rounded" /></td>
 <td className="px-4 py-3"><div className="shimmer-bg h-4 w-12 rounded" /></td>
 </tr>
 ))
 ) : error ? (
 <tr>
 <td colSpan={6} className="p-8 text-center text-[var(--signal-crit)]">
 {error}
 </td>
 </tr>
 ) : filteredIncidents.length === 0 ? (
 <tr>
 <td colSpan={6} className="p-8 text-center text-[var(--text-muted)]">
 {incidents.length === 0 ? 'No incidents recorded yet. Run an analysis to generate incidents.' : 'No incidents match filters.'}
 </td>
 </tr>
 ) : filteredIncidents.map((inc: any) => (
 <tr 
 key={inc.id} 
 className="hover:bg-[var(--bg-surface-hover)] transition-colors cursor-pointer group rounded-lg"
 onClick={() => setSelectedIncident(inc)}
 >
 <td className="px-4 py-3">
 {inc.status === 'OPEN' ? (
 <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-[2px] mono text-[9px] uppercase tracking-wider" style={{ color: "var(--signal-crit)", background: "var(--signal-crit-dim)" }}>
 <ShieldAlert size={10} /> Open
 </span>
 ) : (
 <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-[2px] mono text-[9px] uppercase tracking-wider" style={{ color: "var(--signal-ok)", background: "var(--signal-ok-dim)" }}>
 <CheckCircle2 size={10} /> Resolved
 </span>
 )}
 </td>
 <td className="px-4 py-3">
 <p className="text-[13px] text-[var(--text-primary)] mb-0.5">{inc.title}</p>
 <p className="mono text-[10px] text-[var(--text-dimmed)]">inc_{inc.id}</p>
 </td>
 <td className="px-4 py-3">
 <span className="mono text-[11px] text-[var(--text-secondary)]">{inc.domain}</span>
 </td>
 <td className="px-4 py-3">
 <div className="flex items-center gap-3">
 <div className="w-16 h-1 bg-[var(--bg-track)] rounded-[1px] overflow-hidden">
 <div className="h-full rounded-[1px]" style={{ width: `${inc.impact_score * 100}%`, background: inc.impact_score > 0.7 ? 'var(--signal-crit)' : inc.impact_score > 0.4 ? 'var(--signal-warn)' : 'var(--signal-info)' }}></div>
 </div>
 <span className="mono tnum text-[11px] text-[var(--text-secondary)]">{inc.impact_score?.toFixed(2)}</span>
 </div>
 </td>
 <td className="px-4 py-3">
 <div className="flex items-center gap-1.5 text-[var(--text-secondary)]">
 <Clock size={12} /> {inc.created_at ? new Date(inc.created_at).toLocaleString() : '—'}
 </div>
 </td>
 <td className="px-4 py-3">
 <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
 {inc.status === 'OPEN' ? (
 <button 
 onClick={() => resolveIncident(inc.id, true)}
 className="mono text-[10px] uppercase tracking-wider text-[var(--signal-ok)] hover:opacity-80 transition-opacity cursor-pointer bg-transparent border-none"
 >
 Resolve
 </button>
 ) : (
 <button 
 onClick={() => resolveIncident(inc.id, false)}
 className="mono text-[10px] uppercase tracking-wider text-[var(--signal-warn)] hover:opacity-80 transition-opacity cursor-pointer bg-transparent border-none"
 >
 Reopen
 </button>
 )}
 <button 
 onClick={() => deleteIncident(inc.id)}
 className="text-[var(--text-muted)] hover:text-[var(--signal-crit)] transition-colors cursor-pointer bg-transparent border-none"
 >
 <Trash2 size={12} />
 </button>
 </div>
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>

 {/* ═══ DRILL-DOWN MODAL ═══ */}
 {selectedIncident && (
 <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-[100] flex items-center justify-center">
 <div className="bg-[var(--bg-modal)] border border-[var(--border)] rounded-[4px] w-[640px] max-h-[85vh] overflow-hidden">
 
 {/* Modal Header */}
 <div className="flex items-center justify-between px-6 py-5 border-b border-[var(--border-subtle)]">
 <div className="flex items-center gap-3">
 <AlertTriangle size={20} style={{ color: selectedIncident.status === 'OPEN' ? 'var(--signal-crit)' : 'var(--signal-ok)' }} />
 <div>
 <h2 className="text-base font-bold text-[var(--text-primary)]">{selectedIncident.title}</h2>
 <p className="text-[10px] text-[var(--text-muted)] font-mono mt-0.5">inc_{selectedIncident.id} · {selectedIncident.domain}</p>
 </div>
 </div>
 <button onClick={() => setSelectedIncident(null)} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors cursor-pointer bg-transparent border-none">
 <X size={18} />
 </button>
 </div>

 {/* Modal Body */}
 <div className="p-6 overflow-y-auto max-h-[60vh] space-y-6">
 
 {/* Status + Impact */}
 <div className="grid grid-cols-2 gap-4">
 <div className="bg-[var(--bg-inset)] border border-[var(--border-subtle)] rounded-[3px] p-3">
 <p className="eyebrow mb-1.5">Status</p>
 <p className="mono text-[13px]" style={{ color: selectedIncident.status === 'OPEN' ? 'var(--signal-crit)' : 'var(--signal-ok)' }}>
 {selectedIncident.status}
 </p>
 </div>
 <div className="bg-[var(--bg-inset)] border border-[var(--border-subtle)] rounded-[3px] p-3">
 <p className="eyebrow mb-1.5">Impact Score</p>
 <p className="text-sm font-bold text-[var(--text-primary)]">{selectedIncident.impact_score?.toFixed(2)}</p>
 </div>
 </div>

 {/* Context */}
 {(selectedIncident.source || selectedIncident.total_logs) && (
 <div className="grid grid-cols-2 gap-4">
 <div className="bg-[var(--bg-inset)] border border-[var(--border-subtle)] rounded-[3px] p-3">
 <p className="eyebrow mb-1.5">Source File</p>
 <p className="text-xs text-[var(--text-secondary)] font-mono truncate">{selectedIncident.source || '—'}</p>
 </div>
 <div className="bg-[var(--bg-inset)] border border-[var(--border-subtle)] rounded-[3px] p-3">
 <p className="eyebrow mb-1.5">Logs Analyzed</p>
 <p className="text-xs text-[var(--text-secondary)] font-mono">{selectedIncident.total_logs?.toLocaleString() || '—'} → {selectedIncident.cluster_count || '—'} clusters</p>
 </div>
 </div>
 )}

 {/* Summary */}
 <div>
 <p className="text-[10px] font-bold text-[var(--text-muted)] uppercase tracking-widest mb-3">AI Incident Summary</p>
 <div className="text-sm text-[var(--text-secondary)] leading-relaxed bg-[var(--bg-inset)] rounded-lg p-4">
 {Array.isArray(selectedIncident.summary) 
 ? selectedIncident.summary.map((s: string, i: number) => <p key={i} className="mb-2">{s}</p>)
 : (selectedIncident.summary || 'No summary available.')
 }
 </div>
 </div>

 {/* Remediation */}
 {selectedIncident.remediation_hints && (
 <div>
 <p className="text-[10px] font-bold text-[var(--text-muted)] uppercase tracking-widest mb-3">Remediation Hints</p>
 <ul className="space-y-2">
 {(Array.isArray(selectedIncident.remediation_hints) ? selectedIncident.remediation_hints : [selectedIncident.remediation_hints]).map((hint: string, i: number) => (
 <li key={i} className="flex items-start gap-2 text-[13px] bg-[var(--bg-inset)] border border-[var(--border-subtle)] rounded-[3px] p-2.5" style={{ color: "var(--signal-ok)" }}>
 <Zap size={13} className="shrink-0 mt-0.5" style={{ color: "var(--signal-ok)" }} />
 {hint}
 </li>
 ))}
 </ul>
 </div>
 )}
 </div>

 {/* Modal Footer */}
 <div className="px-6 py-4 border-t border-[var(--border-subtle)] flex items-center justify-between">
 <span className="text-[10px] text-[var(--text-dimmed)]">
 Created: {selectedIncident.created_at ? new Date(selectedIncident.created_at).toLocaleString() : '—'}
 </span>
 <div className="flex gap-3">
 {selectedIncident.status === 'OPEN' ? (
 <button 
 onClick={() => { resolveIncident(selectedIncident.id, true); setSelectedIncident((p: any) => ({...p, status: 'RESOLVED'})); }}
 className="rounded-[3px] px-3 h-8 text-[12px] flex items-center gap-1.5 transition-opacity hover:opacity-90 cursor-pointer border-none text-black" style={{ background: "var(--signal-ok)" }}
 >
 <CheckCircle2 size={14} /> Mark Resolved
 </button>
 ) : (
 <button 
 onClick={() => { resolveIncident(selectedIncident.id, false); setSelectedIncident((p: any) => ({...p, status: 'OPEN'})); }}
 className="rounded-[3px] px-3 h-8 text-[12px] flex items-center gap-1.5 transition-opacity hover:opacity-90 cursor-pointer border-none text-black" style={{ background: "var(--signal-warn)" }}
 >
 <AlertTriangle size={14} /> Reopen
 </button>
 )}
 </div>
 </div>
 </div>
 </div>
 )}

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

