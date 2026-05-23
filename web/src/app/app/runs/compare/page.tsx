'use client';

import React, { useState, useEffect, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { apiFetch } from '@/lib/api';
import { ArrowLeft, GitCompare, RefreshCw, AlertTriangle, TrendingUp, TrendingDown, CheckCircle2, Info, ArrowRight } from 'lucide-react';

const PRIORITY_STYLES: Record<string, string> = {
  P0: 'bg-red-500/20 text-red-300 border border-red-500/30',
  P1: 'bg-orange-500/20 text-orange-300 border border-orange-500/30',
  P2: 'bg-yellow-500/20 text-yellow-300 border border-yellow-500/30',
  P3: 'bg-zinc-800 text-zinc-400 border border-zinc-700',
  unknown: 'bg-zinc-800 text-zinc-500 border border-zinc-700',
  none: 'bg-transparent text-zinc-600',
  resolved: 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
};

function DriftIcon({ kind }: { kind: string }) {
  switch (kind) {
    case 'emerged': return <AlertTriangle size={14} className="text-red-400" />;
    case 'resolved': return <CheckCircle2 size={14} className="text-emerald-400" />;
    case 'escalated': return <TrendingUp size={14} className="text-orange-400" />;
    case 'de_escalated': return <TrendingDown size={14} className="text-yellow-400" />;
    case 'volume_surge': return <TrendingUp size={14} className="text-fuchsia-400" />;
    case 'volume_drop': return <TrendingDown size={14} className="text-blue-400" />;
    case 'anomaly_spike': return <AlertTriangle size={14} className="text-orange-400" />;
    case 'stable': return <Info size={14} className="text-zinc-500" />;
    default: return <Info size={14} className="text-zinc-500" />;
  }
}

function CompareView() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const runA = searchParams.get('run_a');
  const runB = searchParams.get('run_b');

  const [report, setReport] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runA || !runB) {
      setError("Missing run_a or run_b parameters.");
      setLoading(false);
      return;
    }

    apiFetch(`/runs/compare?run_a=${runA}&run_b=${runB}`)
      .then(data => {
        setReport(data);
        setLoading(false);
      })
      .catch(e => {
        setError(e.message);
        setLoading(false);
      });
  }, [runA, runB]);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-zinc-500">
        <RefreshCw size={24} className="animate-spin mb-4" />
        <p>Analyzing drift between snapshots...</p>
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="max-w-4xl mx-auto py-10">
        <button onClick={() => router.back()} className="text-zinc-500 hover:text-white flex items-center gap-2 mb-6 transition-colors border-none bg-transparent cursor-pointer text-sm">
          <ArrowLeft size={16} /> Back to Runs
        </button>
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-6 rounded-xl flex items-start gap-3">
          <AlertTriangle size={20} className="shrink-0 mt-0.5" />
          <div>
            <h3 className="font-bold mb-1">Failed to compare runs</h3>
            <p className="text-sm opacity-80">{error || "Unknown error"}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-[1600px] mx-auto pb-10">
      <button onClick={() => router.back()} className="text-zinc-500 hover:text-white flex items-center gap-2 mb-6 transition-colors border-none bg-transparent cursor-pointer text-sm">
        <ArrowLeft size={16} /> Back to Runs
      </button>

      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-xl font-bold text-white mb-1 flex items-center gap-3">
            <GitCompare size={20} className="text-fuchsia-500" />
            Drift Analysis
          </h1>
          <p className="text-xs text-zinc-500">
            Comparing Baseline <span className="font-mono text-zinc-300">{runA}</span> vs Current <span className="font-mono text-zinc-300">{runB}</span>
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
        <div className="lg:col-span-2 bg-[#121214] border border-white/5 rounded-xl p-6 shadow-sm">
          <h2 className="text-xs font-bold text-zinc-500 uppercase tracking-wider mb-4">Executive Summary</h2>
          <p className="text-sm text-zinc-300 leading-relaxed">{report.summary}</p>
          
          <div className="flex items-center gap-6 mt-6 pt-6 border-t border-white/5">
            <div>
              <p className="text-[10px] text-zinc-500 uppercase font-bold mb-1">Health Delta</p>
              <p className={`text-xl font-bold ${report.health_delta > 0.05 ? 'text-emerald-400' : report.health_delta < -0.05 ? 'text-red-400' : 'text-zinc-400'}`}>
                {report.health_delta > 0 ? '+' : ''}{report.health_delta.toFixed(2)}
              </p>
            </div>
            <div className="w-px h-8 bg-white/5"></div>
            <div>
              <p className="text-[10px] text-zinc-500 uppercase font-bold mb-1">Baseline Clusters</p>
              <p className="text-xl font-bold text-white">{report.total_clusters_a}</p>
            </div>
            <div className="w-px h-8 bg-white/5"></div>
            <div>
              <p className="text-[10px] text-zinc-500 uppercase font-bold mb-1">Current Clusters</p>
              <p className="text-xl font-bold text-white">{report.total_clusters_b}</p>
            </div>
          </div>
        </div>

        <div className="bg-[#121214] border border-white/5 rounded-xl p-6 shadow-sm">
          <h2 className="text-xs font-bold text-zinc-500 uppercase tracking-wider mb-4">Drift Events Breakdown</h2>
          <div className="space-y-3">
            {[
              { label: 'Emerged', key: 'emerged', color: 'text-red-400' },
              { label: 'Escalated', key: 'escalated', color: 'text-orange-400' },
              { label: 'Volume Surge', key: 'volume_surge', color: 'text-fuchsia-400' },
              { label: 'Anomaly Spike', key: 'anomaly_spike', color: 'text-yellow-400' },
              { label: 'Resolved', key: 'resolved', color: 'text-emerald-400' },
              { label: 'De-escalated', key: 'de_escalated', color: 'text-emerald-400' },
              { label: 'Stable', key: 'stable', color: 'text-zinc-500' },
            ].map(({ label, key, color }) => (
              <div key={key} className="flex items-center justify-between">
                <span className={`text-xs ${color}`}>{label}</span>
                <span className="text-xs font-mono font-bold text-white">{report.counts[key]}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="bg-[#121214] border border-white/5 rounded-xl shadow-sm overflow-hidden">
        <div className="p-5 border-b border-white/5">
          <h2 className="text-sm font-bold text-white">Event Log</h2>
        </div>
        <table className="w-full text-left text-xs">
          <thead className="text-[10px] font-bold text-zinc-600 uppercase tracking-wider bg-black/20">
            <tr>
              <th className="p-4 w-32">Signal</th>
              <th className="p-4 w-32">Severity</th>
              <th className="p-4">Description &amp; Pattern</th>
              <th className="p-4 w-40">Priority Shift</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {report.events.map((e: any, i: number) => (
              <tr key={i} className={`hover:bg-white/5 transition-colors ${e.kind === 'emerged' || e.kind === 'escalated' ? 'bg-red-500/5' : ''}`}>
                <td className="p-4">
                  <div className="flex items-center gap-2">
                    <DriftIcon kind={e.kind} />
                    <span className="capitalize font-medium text-zinc-300">{e.kind.replace('_', ' ')}</span>
                  </div>
                </td>
                <td className="p-4">
                  <span className={`text-[9px] font-bold uppercase tracking-wider ${
                    e.severity === 'CRITICAL' ? 'text-red-400' :
                    e.severity === 'HIGH' ? 'text-orange-400' :
                    e.severity === 'MEDIUM' ? 'text-fuchsia-400' : 'text-zinc-500'
                  }`}>
                    {e.severity}
                  </span>
                </td>
                <td className="p-4">
                  <p className="text-white font-medium mb-1">{e.description.split(': ')[0]}</p>
                  <p className="text-[10px] text-zinc-500 font-mono truncate max-w-[600px] bg-black/30 p-1.5 rounded">
                    {e.cluster_b?.template || e.cluster_a?.template || "N/A"}
                  </p>
                </td>
                <td className="p-4">
                  <div className="flex items-center gap-2">
                    {e.priority_before !== 'none' && (
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase ${PRIORITY_STYLES[e.priority_before] || PRIORITY_STYLES.unknown}`}>
                        {e.priority_before}
                      </span>
                    )}
                    {e.priority_before !== 'none' && e.priority_after !== 'resolved' && (
                      <ArrowRight size={10} className="text-zinc-600" />
                    )}
                    {e.priority_after !== 'resolved' && (
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase ${PRIORITY_STYLES[e.priority_after] || PRIORITY_STYLES.unknown}`}>
                        {e.priority_after}
                      </span>
                    )}
                    {e.priority_after === 'resolved' && (
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase ${PRIORITY_STYLES.resolved}`}>
                        RESOLVED
                      </span>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function ComparePage() {
  return (
    <Suspense fallback={<div className="p-10 text-zinc-500 text-center"><RefreshCw className="animate-spin inline" /> Loading...</div>}>
      <CompareView />
    </Suspense>
  );
}
