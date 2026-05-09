'use client';

import React, { useState, useEffect } from 'react';
import { Play, CheckCircle2, Clock, XCircle, Trash2, RefreshCw } from 'lucide-react';
import { apiFetch, apiDelete } from '@/lib/api';

export default function AnalysisRunsPage() {
  const [runs, setRuns] = useState<any[]>([]);

  const fetchRuns = () => {
    apiFetch('/runs')
      .then(data => setRuns(data))
      .catch(console.error);
  };

  useEffect(() => { fetchRuns(); }, []);

  const handleDelete = async (runId: string) => {
    if (!confirm('Delete this analysis run?')) return;
    try {
      await apiDelete(`/runs/${runId}`);
      fetchRuns();
    } catch (e: any) {
      alert(`Failed: ${e.message}`);
    }
  };

  return (
    <div className="max-w-[1600px] mx-auto pb-10">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-xl font-bold text-white mb-1">Analysis Runs</h1>
          <p className="text-xs text-zinc-500">History of batch processing runs, HDBSCAN clustering, and snapshots. <span className="text-zinc-600">{runs.length} total</span></p>
        </div>
        <div className="flex gap-3">
          <button 
            onClick={fetchRuns}
            className="bg-white/5 hover:bg-white/10 text-white font-medium rounded-md px-4 py-2 text-xs border border-white/5 flex items-center gap-2 cursor-pointer transition-colors"
          >
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      {/* Stats Summary */}
      {runs.length > 0 && (
        <div className="grid grid-cols-4 gap-4 mb-6">
          <div className="bg-[#121214] rounded-xl p-4">
            <p className="text-[10px] text-zinc-500 font-medium mb-1">Total Runs</p>
            <p className="text-2xl font-bold text-white">{runs.length}</p>
          </div>
          <div className="bg-[#121214] rounded-xl p-4">
            <p className="text-[10px] text-zinc-500 font-medium mb-1">Total Logs Processed</p>
            <p className="text-2xl font-bold text-white">{runs.reduce((acc, r) => acc + (r.raw_lines || 0), 0).toLocaleString()}</p>
          </div>
          <div className="bg-[#121214] rounded-xl p-4">
            <p className="text-[10px] text-zinc-500 font-medium mb-1">Avg Reduction</p>
            <p className="text-2xl font-bold text-emerald-400">
              {(runs.reduce((acc, r) => acc + (r.reduction_ratio || 0), 0) / runs.length * 100).toFixed(1)}%
            </p>
          </div>
          <div className="bg-[#121214] rounded-xl p-4">
            <p className="text-[10px] text-zinc-500 font-medium mb-1">Avg Duration</p>
            <p className="text-2xl font-bold text-fuchsia-400">
              {(runs.reduce((acc, r) => acc + (r.duration_sec || 0), 0) / runs.length).toFixed(1)}s
            </p>
          </div>
        </div>
      )}

      {/* Table */}
      <div className="bg-[#121214] border-none rounded-xl overflow-hidden shadow-sm">
        <table className="w-full text-left text-xs">
          <thead className="text-[10px] font-bold text-zinc-600 uppercase tracking-wider border-b border-transparent bg-transparent">
            <tr>
              <th className="p-5 font-medium">Status</th>
              <th className="p-5 font-medium">Run ID</th>
              <th className="p-5 font-medium">Source</th>
              <th className="p-5 font-medium">Stats</th>
              <th className="p-5 font-medium">Time</th>
              <th className="p-5 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-transparent">
            {runs.length === 0 ? (
              <tr>
                <td colSpan={6} className="p-8 text-center text-zinc-500">
                  No analysis runs yet. Use the "Run Analysis" button to start one.
                </td>
              </tr>
            ) : runs.map((run: any) => (
              <tr key={run.id} className="hover:bg-white/5 transition-colors rounded-lg">
                <td className="p-5">
                  {run.status === "Completed" ? (
                    <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full border border-emerald-500/20 bg-emerald-500/10 text-[9px] font-bold text-emerald-500 uppercase tracking-wider">
                      <CheckCircle2 size={12} /> Completed
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full border border-red-500/20 bg-red-500/10 text-[9px] font-bold text-red-500 uppercase tracking-wider">
                      <XCircle size={12} /> {run.status}
                    </span>
                  )}
                </td>
                <td className="p-5">
                  <span className="text-zinc-300 font-medium font-mono">{run.id}</span>
                </td>
                <td className="p-5">
                  <span className="inline-flex items-center px-2 py-1 rounded bg-white/5 border border-white/5 text-[10px] text-zinc-400 font-mono truncate max-w-[200px]">
                    {run.source}
                  </span>
                </td>
                <td className="p-5">
                  <div className="flex flex-col gap-0.5">
                    <span className="text-zinc-300"><strong className="text-white">{run.raw_lines?.toLocaleString()}</strong> raw lines</span>
                    <span className="text-zinc-300"><strong className="text-fuchsia-400">{run.cluster_count}</strong> clusters</span>
                    <span className="text-emerald-400 font-medium text-[10px]">{(run.reduction_ratio * 100).toFixed(2)}% reduced</span>
                  </div>
                </td>
                <td className="p-5">
                  <div className="text-zinc-400 flex items-center gap-1.5 mb-1">
                    <Clock size={12} /> {run.created_at ? new Date(run.created_at).toLocaleString() : '—'}
                  </div>
                  <div className="text-zinc-500 text-[10px]">Duration: {run.duration_sec?.toFixed(2)}s</div>
                </td>
                <td className="p-5">
                  <button 
                    onClick={() => handleDelete(run.id)}
                    className="text-zinc-600 hover:text-red-400 transition-colors cursor-pointer"
                  >
                    <Trash2 size={14} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
