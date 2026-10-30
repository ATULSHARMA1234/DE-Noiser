'use client';

/**
 * Full analysis report for a single run.
 *
 * The Command Center used to render the whole investigation inline — failure
 * domain, the entire incident summary, every remediation hint and every cluster
 * — which buried the operational view under a wall of prose. It now shows that a
 * report exists and links here; this page is where the analysis is read.
 */

import React, { useState, useEffect } from 'react';
import { useParams, useRouter } from 'next/navigation';
import ReactEcharts from 'echarts-for-react';
import {
  ArrowLeft, Clock, Database, FileText, Layers, Loader2, ShieldAlert,
  TrendingUp, Zap, AlertTriangle, Maximize2,
} from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { TopologyModal } from '@/components/TopologyModal';

const cssVar = (name: string, fallback: string) => {
  if (typeof window === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
};

const PRIORITY_STYLES: Record<string, string> = {
  P0: 'bg-red-500/20 text-red-400 border border-red-500/30',
  P1: 'bg-orange-500/20 text-orange-400 border border-orange-500/30',
  P2: 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30',
  P3: 'bg-[var(--bg-inset)] text-[var(--text-muted)] border border-[var(--border-subtle)]',
};

/** The summary may be a string, a list of points, or a nested object. */
function summaryLines(summary: any): string[] {
  if (!summary) return [];
  if (Array.isArray(summary)) return summary.map(String);
  if (typeof summary === 'object') {
    const inner = summary.summary || summary.representative_log;
    return inner ? [String(inner)] : [JSON.stringify(summary)];
  }
  return [String(summary)];
}

function clusterLabel(c: any): string {
  const s = typeof c.summary === 'object'
    ? (c.summary?.summary || c.summary?.representative_log || '')
    : c.summary;
  return s && s !== 'Analyzing...' ? s : c.representative_template;
}

export default function AnalysisReport() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const runId = params?.id as string;

  const [run, setRun] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [topologyExpanded, setTopologyExpanded] = useState(false);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;

    apiFetch(`/runs/${runId}`)
      .then((data) => { if (!cancelled) setRun(data); })
      .catch((e) => { if (!cancelled) setError(e.message || 'Could not load this report.'); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [runId]);

  const clusters: any[] = run?.clusters_snapshot || [];
  const intelligence = run?.intelligence;

  const outliers = clusters
    .filter((c) => c.cluster_id === -1)
    .reduce((acc, c) => acc + (c.size || 0), 0);

  const projectionOption = () => {
    const points: any[] = [];
    clusters.forEach((c) => {
      (c.projection_2d || []).forEach((p: [number, number]) => {
        points.push([
          p[0], p[1],
          Math.max(4, Math.min(16, 3 + Math.sqrt(c.size || 1))),
          c.cluster_id === -1 ? 'Noise' : `C${c.cluster_id}`,
        ]);
      });
    });
    if (points.length === 0) return null;

    const groups = [...new Set(points.map((d) => d[3]))];
    const colors = [
      cssVar('--signal-alt', '#9d7bff'), cssVar('--signal-info', '#4a9eff'),
      cssVar('--signal-ok', '#35c08e'), cssVar('--signal-warn', '#f5a623'),
      cssVar('--signal-crit', '#f2555a'),
    ];
    return {
      backgroundColor: 'transparent',
      tooltip: { show: false },
      legend: { bottom: 0, left: 10, textStyle: { color: cssVar('--text-muted', '#6a717a'), fontSize: 9 }, icon: 'circle' },
      grid: { top: 10, bottom: 30, left: 10, right: 10 },
      // UMAP coordinates are not centred on zero, and a value axis includes the
      // origin unless scaled — without this every cluster lands in one corner.
      xAxis: { show: false, type: 'value', scale: true },
      yAxis: { show: false, type: 'value', scale: true },
      series: groups.map((g, gi) => ({
        name: g,
        type: 'scatter',
        symbolSize: (d: any) => d[2],
        data: points.filter((d) => d[3] === g),
        itemStyle: { color: colors[gi % colors.length], opacity: g === 'Noise' ? 0.35 : 0.75 },
      })),
    };
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-32 gap-3">
        <Loader2 size={28} className="animate-spin text-[var(--primary)]" />
        <p className="text-xs text-[var(--text-muted)] uppercase tracking-wider">Loading report…</p>
      </div>
    );
  }

  if (error || !run) {
    return (
      <div className="max-w-[900px] mx-auto py-16 text-center">
        <AlertTriangle size={32} className="mx-auto mb-3 text-[var(--signal-warn)]" />
        <p className="text-sm font-semibold text-[var(--text-primary)]">Report unavailable</p>
        <p className="text-xs text-[var(--text-muted)] mt-2">{error || 'This run no longer exists.'}</p>
        <button
          onClick={() => router.push('/app/runs')}
          className="mt-6 text-xs text-[var(--primary)] hover:underline cursor-pointer bg-transparent border-none"
        >
          Back to all runs
        </button>
      </div>
    );
  }

  const projection = projectionOption();

  return (
    <div className="space-y-6 max-w-[1600px] mx-auto pb-10">

      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <button
            onClick={() => router.back()}
            className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)] mb-3 cursor-pointer bg-transparent border-none p-0"
          >
            <ArrowLeft size={13} /> Back
          </button>
          <h1 className="text-xl font-bold text-[var(--text-primary)] flex items-center gap-2.5">
            <FileText size={20} className="text-[var(--primary)]" />
            Analysis Report
          </h1>
          <div className="flex items-center gap-3 mt-2 text-xs text-[var(--text-muted)] flex-wrap">
            <span className="flex items-center gap-1.5"><Database size={12} /> {run.source}</span>
            <span>·</span>
            <span className="flex items-center gap-1.5">
              <Clock size={12} /> {run.created_at ? new Date(run.created_at).toLocaleString() : 'unknown time'}
            </span>
            {run.duration_sec != null && (<><span>·</span><span>{Number(run.duration_sec).toFixed(1)}s</span></>)}
            <span>·</span>
            <span className="font-mono text-[10px] opacity-70">{run.id}</span>
          </div>
        </div>
        <span className={`text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 rounded-sm border ${
          run.status === 'Completed'
            ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20'
            : 'text-[var(--text-muted)] bg-[var(--bg-inset)] border-[var(--border-subtle)]'
        }`}>
          {run.status || 'Unknown'}
        </span>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-5">
          <p className="text-xs text-[var(--text-muted)] font-medium mb-3">Total Logs Processed</p>
          <span className="text-3xl font-bold text-[var(--text-primary)] leading-none">
            {run.raw_lines?.toLocaleString() ?? '—'}
          </span>
        </div>
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-5">
          <p className="text-xs text-[var(--text-muted)] font-medium mb-3">Outliers Detected</p>
          <div className="flex items-end gap-2">
            <span className="text-3xl font-bold text-[var(--text-primary)] leading-none">{outliers.toLocaleString()}</span>
            {outliers > 0 && <span className="text-xs text-red-500 font-medium pb-0.5 flex items-center"><TrendingUp size={12} className="mr-1" /> flagged</span>}
          </div>
        </div>
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-5">
          <p className="text-xs text-[var(--text-muted)] font-medium mb-3">Noise Reduced</p>
          <span className="text-3xl font-bold text-[var(--text-primary)] leading-none">
            {run.reduction_ratio != null ? `${(run.reduction_ratio * 100).toFixed(2)}%` : '—'}
          </span>
        </div>
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-5">
          <p className="text-xs text-[var(--text-muted)] font-medium mb-3">Semantic Clusters</p>
          <span className="text-3xl font-bold text-[var(--text-primary)] leading-none">
            {run.cluster_count?.toLocaleString() ?? clusters.length.toLocaleString()}
          </span>
        </div>
      </div>

      {/* Intelligence */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-6">
          <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-5 flex items-center gap-2">
            <ShieldAlert size={15} className="text-[var(--signal-crit)]" /> Incident Summary
          </h2>

          {intelligence ? (
            <>
              <div className="mb-5">
                <p className="text-[10px] font-bold text-[var(--text-muted)] uppercase tracking-widest mb-1.5">Failure Domain</p>
                <p className="text-sm font-semibold text-[var(--text-primary)] flex items-center gap-2">
                  <Database size={14} className="text-blue-400" /> {intelligence.failure_domain || '—'}
                </p>
              </div>

              <ul className="space-y-3 text-[13px] leading-relaxed text-[var(--text-input)]">
                {summaryLines(intelligence.incident_summary).map((line, i) => (
                  <li key={i} className="flex items-start gap-2.5">
                    <span className="w-1.5 h-1.5 rounded-full bg-[var(--primary)] mt-2 shrink-0" />
                    <span>{line}</span>
                  </li>
                ))}
                {summaryLines(intelligence.incident_summary).length === 0 && (
                  <li className="text-[var(--text-muted)]">This run produced no narrative summary.</li>
                )}
              </ul>
            </>
          ) : (
            <p className="text-xs text-[var(--text-muted)] leading-relaxed">
              No incident intelligence was generated for this run — it ran without the
              intelligence step, or the analysis found nothing worth escalating.
            </p>
          )}
        </div>

        <div className="lg:col-span-1 bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-6">
          <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-5 flex items-center gap-2">
            <Zap size={15} className="text-emerald-500" /> Remediation Hints
          </h2>
          <ul className="space-y-3 text-xs text-emerald-400 font-medium">
            {intelligence?.root_cause_hints?.length ? (
              intelligence.root_cause_hints.map((hint: string, i: number) => (
                <li key={i} className="flex items-start gap-2">
                  <Zap size={13} className="shrink-0 mt-0.5 text-emerald-500" />
                  <span className="leading-relaxed">{hint}</span>
                </li>
              ))
            ) : (
              <li className="text-[var(--text-muted)] font-normal">No remediation hints for this run.</li>
            )}
          </ul>
        </div>
      </div>

      {/* Clusters + projection */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-6">
          <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-1 flex items-center gap-2">
            <Layers size={15} className="text-[var(--primary)]" /> Anomaly Forensics
          </h2>
          <p className="text-xs text-[var(--text-muted)] mb-6">All {clusters.length} semantic clusters from this run</p>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-[10px] font-bold text-[var(--text-muted)] uppercase tracking-wider border-b border-[var(--border-subtle)]">
                <tr>
                  <th className="pb-4 font-medium">Cluster</th>
                  <th className="pb-4 font-medium">Pattern Label</th>
                  <th className="pb-4 font-medium">Count</th>
                  <th className="pb-4 font-medium">Score</th>
                  <th className="pb-4 font-medium">Priority</th>
                </tr>
              </thead>
              <tbody>
                {clusters.length ? clusters.map((c: any, i: number) => (
                  <tr key={i} className="hover:bg-[var(--bg-surface-hover)] transition-colors align-top">
                    <td className="py-3 px-2">
                      <span className={`inline-flex items-center justify-center w-7 h-7 rounded-lg text-[10px] font-bold ${
                        c.cluster_id === -1
                          ? 'bg-red-500/10 text-red-400 border border-red-500/20'
                          : 'bg-[var(--primary)]/10 text-[var(--primary)] border border-[var(--primary)]/20'
                      }`}>
                        {c.cluster_id === -1 ? '⚠' : `C${c.cluster_id}`}
                      </span>
                    </td>
                    <td className="py-3 px-2 font-medium text-[var(--text-primary)]">
                      <div className="max-w-[420px]">{clusterLabel(c)}</div>
                      {c.representative_log && (
                        <div className="mt-1 font-mono text-[10px] text-[var(--text-dimmed)] break-all max-w-[420px] opacity-80">
                          {c.representative_log}
                        </div>
                      )}
                    </td>
                    <td className="py-3 text-[var(--text-secondary)] font-mono whitespace-nowrap">{c.size?.toLocaleString()}</td>
                    <td className="py-3 font-bold">
                      <span className={c.anomaly_score > 0.7 ? 'text-red-400' : c.anomaly_score > 0.4 ? 'text-yellow-400' : 'text-[var(--text-muted)]'}>
                        {c.anomaly_score > 0 ? c.anomaly_score.toFixed(2) : '—'}
                      </span>
                    </td>
                    <td className="py-3">
                      <span className={`px-2 py-1 rounded-sm font-bold text-[9px] uppercase tracking-wider whitespace-nowrap ${
                        PRIORITY_STYLES[c.priority] || PRIORITY_STYLES.P3
                      }`}>
                        {c.priority || (c.cluster_id === -1 ? 'Noise' : 'P3')}
                      </span>
                    </td>
                  </tr>
                )) : (
                  <tr>
                    <td colSpan={5} className="py-8 text-center text-[var(--text-muted)]">
                      This run recorded no clusters.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="lg:col-span-1 bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-6">
          <div className="flex items-start justify-between mb-6">
            <div>
              <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-1">Neural Topology</h2>
              <p className="text-xs text-[var(--text-muted)]">HDBSCAN Projection</p>
            </div>
            {projection && <Maximize2 size={14} className="text-[var(--text-muted)] mt-0.5" />}
          </div>
          <div
            className={`h-[240px] bg-[var(--bg-inset)] rounded-lg border border-[var(--border-subtle)] relative transition-colors ${
              projection ? 'cursor-pointer hover:border-[var(--primary)]' : ''
            }`}
            // The scatter is unreadable in a side panel; clicking opens the
            // same option full screen.
            onClick={() => projection && setTopologyExpanded(true)}
            onKeyDown={(e) => {
              if (projection && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault();
                setTopologyExpanded(true);
              }
            }}
            role={projection ? 'button' : undefined}
            tabIndex={projection ? 0 : undefined}
            aria-label={projection ? 'Expand neural topology projection' : undefined}
          >
            {projection ? (
              <ReactEcharts option={projection} style={{ height: '100%', width: '100%' }} />
            ) : (
              <div className="absolute inset-0 flex items-center justify-center text-xs text-[var(--text-muted)] text-center px-6">
                This run carries no 2D projection.
              </div>
            )}
          </div>
        </div>
      </div>

      <TopologyModal
        isOpen={topologyExpanded}
        onClose={() => setTopologyExpanded(false)}
        option={projection}
      />

    </div>
  );
}
