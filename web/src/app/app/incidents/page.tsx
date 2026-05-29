'use client';

import React, { useState, useEffect } from 'react';
import { ShieldAlert, Database, Clock, CheckCircle2, X, Zap, AlertTriangle, Trash2 } from 'lucide-react';
import { apiFetch, apiPut, apiDelete } from '@/lib/api';
import { useToast } from '@/context/ToastContext';

export default function IncidentMemoryPage() {
  const { toast } = useToast();
  const [incidents, setIncidents] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedIncident, setSelectedIncident] = useState<any>(null);
  const [domainFilter, setDomainFilter] = useState('All');
  const [statusFilter, setStatusFilter] = useState('All');

  const fetchIncidents = () => {
    setIsLoading(true);
    apiFetch('/incidents')
      .then(data => {
        setIncidents(data);
        setIsLoading(false);
      })
      .catch(e => {
        console.error(e);
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

  const deleteIncident = async (id: number) => {
    if (!confirm('Delete this incident permanently?')) return;
    try {
      await apiDelete(`/incidents/${id}`);
      fetchIncidents();
      if (selectedIncident?.id === id) setSelectedIncident(null);
      toast.success('Incident deleted successfully.');
    } catch (e: any) {
      toast.error(`Delete failed: ${e.message}`);
    }
  };

  const domains = ['All', ...new Set(incidents.map(i => i.domain).filter(Boolean))];
  
  const filteredIncidents = incidents.filter(inc => {
    if (domainFilter !== 'All' && inc.domain !== domainFilter) return false;
    if (statusFilter !== 'All' && inc.status !== statusFilter) return false;
    return true;
  });

  return (
    <div className="max-w-[1600px] mx-auto pb-10">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-xl font-bold text-[var(--text-primary)] mb-1">Incident Memory</h1>
          <p className="text-xs text-[var(--text-muted)]">Historical log of clustered incidents and LLM root cause diagnoses. <span className="text-[var(--text-dimmed)]">{incidents.length} total</span></p>
        </div>
        <div className="flex gap-3">
          <select 
            value={domainFilter}
            onChange={(e) => setDomainFilter(e.target.value)}
            className="bg-[var(--bg-modal)] border border-[var(--border-subtle)] text-[var(--text-input)] text-xs rounded-md px-3 py-2 outline-none cursor-pointer"
          >
            {domains.map(d => <option key={d}>{d}</option>)}
          </select>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="bg-[var(--bg-modal)] border border-[var(--border-subtle)] text-[var(--text-input)] text-xs rounded-md px-3 py-2 outline-none cursor-pointer"
          >
            <option>All</option>
            <option>OPEN</option>
            <option>RESOLVED</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="bg-[var(--bg-card)] border-none rounded-xl overflow-hidden shadow-sm">
        <table className="w-full text-left text-xs">
          <thead className="text-[10px] font-bold text-[var(--text-dimmed)] uppercase tracking-wider border-b border-transparent bg-transparent">
            <tr>
              <th className="p-5 font-medium">Status</th>
              <th className="p-5 font-medium">Incident Title</th>
              <th className="p-5 font-medium">Domain</th>
              <th className="p-5 font-medium">Impact</th>
              <th className="p-5 font-medium">Time</th>
              <th className="p-5 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-transparent">
            {isLoading ? (
              Array.from({ length: 5 }).map((_, idx) => (
                <tr key={idx}>
                  <td className="p-5"><div className="shimmer-bg h-6 w-16 rounded-full" /></td>
                  <td className="p-5">
                    <div className="shimmer-bg h-4 w-40 rounded mb-1.5" />
                    <div className="shimmer-bg h-3 w-20 rounded" />
                  </td>
                  <td className="p-5"><div className="shimmer-bg h-6 w-24 rounded" /></td>
                  <td className="p-5"><div className="shimmer-bg h-2.5 w-20 rounded" /></td>
                  <td className="p-5"><div className="shimmer-bg h-4 w-28 rounded" /></td>
                  <td className="p-5"><div className="shimmer-bg h-4 w-12 rounded" /></td>
                </tr>
              ))
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
                <td className="p-5">
                  {inc.status === 'OPEN' ? (
                    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-red-500/20 bg-red-500/10 text-[9px] font-bold text-red-400 uppercase tracking-wider">
                      <ShieldAlert size={10} /> Open
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-emerald-500/20 bg-emerald-500/10 text-[9px] font-bold text-emerald-400 uppercase tracking-wider">
                      <CheckCircle2 size={10} /> Resolved
                    </span>
                  )}
                </td>
                <td className="p-5">
                  <p className="font-bold text-[var(--text-primary)] text-sm mb-1">{inc.title}</p>
                  <p className="text-[var(--text-muted)] font-mono text-[10px]">inc_{inc.id}</p>
                </td>
                <td className="p-5">
                  <span className="inline-flex items-center gap-2 px-2.5 py-1 rounded bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[var(--text-secondary)]">
                    <Database size={12} className="text-[var(--text-muted)]" /> {inc.domain}
                  </span>
                </td>
                <td className="p-5">
                  <div className="flex items-center gap-3">
                    <div className="w-16 h-1.5 bg-[var(--bg-track)] rounded-full overflow-hidden">
                      <div className={`h-full ${inc.impact_score > 0.7 ? 'bg-red-500' : inc.impact_score > 0.4 ? 'bg-orange-500' : 'bg-yellow-500'} rounded-full`} style={{ width: `${inc.impact_score * 100}%` }}></div>
                    </div>
                    <span className="text-[var(--text-secondary)] font-medium">{inc.impact_score?.toFixed(2)}</span>
                  </div>
                </td>
                <td className="p-5">
                  <div className="flex items-center gap-1.5 text-[var(--text-secondary)]">
                    <Clock size={12} /> {inc.created_at ? new Date(inc.created_at).toLocaleString() : '—'}
                  </div>
                </td>
                <td className="p-5">
                  <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                    {inc.status === 'OPEN' ? (
                      <button 
                        onClick={() => resolveIncident(inc.id, true)}
                        className="text-[10px] font-bold text-emerald-400 hover:text-emerald-300 transition-colors cursor-pointer bg-transparent border-none"
                      >
                        Resolve
                      </button>
                    ) : (
                      <button 
                        onClick={() => resolveIncident(inc.id, false)}
                        className="text-[10px] font-bold text-orange-400 hover:text-orange-300 transition-colors cursor-pointer bg-transparent border-none"
                      >
                        Reopen
                      </button>
                    )}
                    <button 
                      onClick={() => deleteIncident(inc.id)}
                      className="text-[var(--text-muted)] hover:text-red-400 transition-colors cursor-pointer bg-transparent border-none"
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
          <div className="bg-[var(--bg-modal)] border border-[var(--border)] rounded-2xl w-[640px] max-h-[85vh] overflow-hidden shadow-2xl">
            
            {/* Modal Header */}
            <div className="flex items-center justify-between px-6 py-5 border-b border-[var(--border-subtle)]">
              <div className="flex items-center gap-3">
                <AlertTriangle size={20} className={selectedIncident.status === 'OPEN' ? 'text-red-500' : 'text-emerald-500'} />
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
                <div className="bg-[var(--bg-inset)] rounded-lg p-4">
                  <p className="text-[10px] font-bold text-[var(--text-muted)] uppercase tracking-widest mb-2">Status</p>
                  <p className={`text-sm font-bold ${selectedIncident.status === 'OPEN' ? 'text-red-400' : 'text-emerald-400'}`}>
                    {selectedIncident.status}
                  </p>
                </div>
                <div className="bg-[var(--bg-inset)] rounded-lg p-4">
                  <p className="text-[10px] font-bold text-[var(--text-muted)] uppercase tracking-widest mb-2">Impact Score</p>
                  <p className="text-sm font-bold text-[var(--text-primary)]">{selectedIncident.impact_score?.toFixed(2)}</p>
                </div>
              </div>

              {/* Context */}
              {(selectedIncident.source || selectedIncident.total_logs) && (
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-[var(--bg-inset)] rounded-lg p-4">
                    <p className="text-[10px] font-bold text-[var(--text-muted)] uppercase tracking-widest mb-2">Source File</p>
                    <p className="text-xs text-[var(--text-secondary)] font-mono truncate">{selectedIncident.source || '—'}</p>
                  </div>
                  <div className="bg-[var(--bg-inset)] rounded-lg p-4">
                    <p className="text-[10px] font-bold text-[var(--text-muted)] uppercase tracking-widest mb-2">Logs Analyzed</p>
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
                      <li key={i} className="flex items-start gap-2 text-sm text-emerald-400 bg-[var(--bg-inset)] rounded-lg p-3">
                        <Zap size={14} className="shrink-0 mt-0.5 text-emerald-500" />
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
                    className="bg-emerald-600 hover:bg-emerald-500 text-white font-bold rounded-lg px-4 py-2 text-xs flex items-center gap-2 transition-colors cursor-pointer border-none"
                  >
                    <CheckCircle2 size={14} /> Mark Resolved
                  </button>
                ) : (
                  <button 
                    onClick={() => { resolveIncident(selectedIncident.id, false); setSelectedIncident((p: any) => ({...p, status: 'OPEN'})); }}
                    className="bg-orange-600 hover:bg-orange-500 text-white font-bold rounded-lg px-4 py-2 text-xs flex items-center gap-2 transition-colors cursor-pointer border-none"
                  >
                    <AlertTriangle size={14} /> Reopen
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

