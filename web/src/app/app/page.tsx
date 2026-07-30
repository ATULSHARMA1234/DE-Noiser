'use client';

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import ReactEcharts from 'echarts-for-react';
import * as echarts from 'echarts';
import { Database, TrendingUp, Zap, Loader2, AlertTriangle, RefreshCw, FileText, Cpu, MemoryStick, HardDrive, Wifi, Search, ArrowRight, Maximize2 } from 'lucide-react';
import { LineChart, Line, ResponsiveContainer, YAxis } from 'recharts';
import { apiFetch, runAnalysis as runAnalysisJob } from '@/lib/api';
import { useTimeRange } from '@/context/TimeRangeContext';
import { useTasks } from '@/context/TaskContext';
import { TopologyModal } from '@/components/TopologyModal';

// ECharts draws to a canvas, where `var(--token)` is not a valid colour — it
// would silently render black. Resolve design tokens to their computed value.
const cssVar = (name: string, fallback: string) => {
 if (typeof window === 'undefined') return fallback;
 const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
 return v || fallback;
};

function Sparkline({ data, dataKey, color }: { data: any[], dataKey: string, color: string }) {
 // A sparkline alone shows shape but no reading. State the current value's
 // drift from the window average, so the shape means something.
 const series = data.map((d) => d?.[dataKey] ?? 0);
 const current = series[series.length - 1] ?? 0;
 const avg = series.length ? series.reduce((a, b) => a + b, 0) / series.length : 0;
 const delta = current - avg;
 const pct = avg ? (delta / avg) * 100 : 0;
 const flat = Math.abs(pct) < 1;

 return (
 <div className="ml-auto flex flex-col items-end gap-0.5">
 <LineChart width={120} height={36} data={data}>
 <Line type="monotone" dataKey={dataKey} stroke={color} strokeWidth={1.5} dot={false} isAnimationActive={false} />
 <YAxis domain={['dataMin - 10', 'dataMax + 10']} hide />
 </LineChart>
 <span
 className="mono text-[10px] tnum"
 style={{ color: flat ? 'var(--text-muted)' : delta > 0 ? 'var(--signal-warn)' : 'var(--signal-ok)' }}
 >
 {flat ? '≈ avg' : `${delta > 0 ? '+' : ''}${pct.toFixed(0)}% vs avg`}
 </span>
 </div>
 );
}

export default function CommandCenter() {
 const router = useRouter();
 const { timeRange } = useTimeRange();
 const { tasks, executeTask, attachRemoteTask } = useTasks();
 const [data, setData] = useState<any>(null);
 const [loading, setLoading] = useState(false);
 const [error, setError] = useState<string | null>(null);
 const [searchQuery, setSearchQuery] = useState('');
 const [searchLoading, setSearchLoading] = useState(false);
 const [searchResults, setSearchResults] = useState<any[] | null>(null);
 const [sources, setSources] = useState<any[]>([]);
 const [selectedSource, setSelectedSource] = useState('');
 const [elapsedTime, setElapsedTime] = useState(0);
 const [settings, setSettings] = useState<any>(null);
 const [topologyExpanded, setTopologyExpanded] = useState(false);

 // Fetch settings on mount
 useEffect(() => {
 apiFetch('/settings')
 .then(setSettings)
 .catch(() => null);
 }, []);

 // Real-time metrics data for the Metrics & Anomalies chart
 const [metrics, setMetrics] = useState<any[]>([]);

 useEffect(() => {
 const fetchMetrics = async () => {
 try {
 const res = await apiFetch('/metrics/stream?limit=60');
 if (res?.entries?.length) {
 const mapped = res.entries.map((e: any) => {
 const ts = e.timestamp ? new Date(e.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
 const cpu = e.cpu_percent ?? 0;
 const mem = e.memory_percent ?? 0;
 return {
 time: ts,
 cpu: cpu / 100,
 mem: mem / 100,
 anomaly: (cpu > 85 || mem > 90) ? Math.max(cpu, mem) / 100 : null,
 };
 });
 setMetrics(mapped);
 }
 } catch {
 // Keep previous metrics if backend is unavailable
 }
 };
 fetchMetrics();
 const id = setInterval(fetchMetrics, 5000);
 return () => clearInterval(id);
 }, []);

 // Auto-load latest analysis run on mount
 useEffect(() => {
 const loadLatestRun = async () => {
 try {
 const runs = await apiFetch('/analysis/runs');
 if (runs?.length > 0) {
 const latest = runs[0];
 // Reconstruct data from the saved run snapshot
 if (latest.clusters_snapshot?.length) {
 setData({
 run_id: latest.id,
 clusters: latest.clusters_snapshot,
 total_logs: latest.raw_lines,
 intelligence: latest.intelligence || null,
 status: 'loaded_from_history',
 });
 }
 }
 } catch {
 // No previous runs available, dashboard starts empty
 }
 };
 loadLatestRun();
 }, []);

 // Real-time sparkline data (Task 16)
 const [vitals, setVitals] = useState<any[]>(
 Array.from({ length: 20 }, () => ({ cpu: 0, mem: 0, disk: 0, net: 0 }))
 );
 // These vitals are the SemanticOS host's own, not the monitored fleet's. The
 // panel said only "System Vitals", which invited exactly that misreading.
 const [vitalsHost, setVitalsHost] = useState<string | null>(null);

 useEffect(() => {
 const padToWindow = (arr: any[]) =>
 Array.from({ length: 20 }, (_, i) => {
 const v = arr[arr.length - 20 + i];
 return {
 cpu: v?.cpu ?? 0,
 mem: v?.mem ?? 0,
 disk: v?.disk ?? 0,
 net: v?.net ?? 0,
 };
 });

 const fetchVitals = async () => {
 try {
 const res = await apiFetch('/vitals');
 if (res?.vitals) setVitals(padToWindow(res.vitals));
 if (res?.host) setVitalsHost(res.host);
 } catch {
 // Keep previous vitals if the backend is temporarily unavailable.
 }
 };

 fetchVitals();
 const intervalId = setInterval(fetchVitals, 5000);
 return () => clearInterval(intervalId);
 }, []);

 // Fetch available sources on mount
 useEffect(() => {
 apiFetch('/sources')
 .then((data) => {
 setSources(data);
 if (data.length > 0) setSelectedSource(data[0].path);
 })
 .catch(console.error);
 }, []);

 // Sync loading state and data with global tasks
 const activeAnalysisTask = tasks.find(t => t.status === 'running' && t.id.startsWith('analysis:'));
 const completedAnalysisTask = [...tasks].reverse().find(t => t.status === 'success' && t.id.startsWith('analysis:'));
 const failedAnalysisTask = [...tasks].reverse().find(t => t.status === 'error' && t.id.startsWith('analysis:'));

 useEffect(() => {
 setLoading(!!activeAnalysisTask);
 }, [activeAnalysisTask]);

 useEffect(() => {
 if (completedAnalysisTask && completedAnalysisTask.result) {
 setData(completedAnalysisTask.result);
 setError(null);
 }
 }, [completedAnalysisTask]);

 useEffect(() => {
 if (failedAnalysisTask && failedAnalysisTask.error) {
 setError(failedAnalysisTask.error);
 setLoading(false);
 }
 }, [failedAnalysisTask]);

 // Timer for loading state. Derived from the task's startedAt rather than
 // counted up from zero: this component unmounts whenever you navigate away,
 // so a local counter restarted at 0 on every return and claimed a long-running
 // analysis had just begun.
 const analysisStartedAt = activeAnalysisTask?.startedAt;
 useEffect(() => {
 if (!analysisStartedAt) {
 setElapsedTime(0);
 return;
 }
 const tick = () => setElapsedTime(Math.floor((Date.now() - analysisStartedAt) / 1000));
 tick();
 const interval = setInterval(tick, 1000);
 return () => clearInterval(interval);
 }, [analysisStartedAt]);

 // The run this panel links to. Both a fresh task result and the snapshot loaded
 // on mount carry the id, so the link works after a reload as well.
 const reportRunId: string | null = data?.run_id || null;
 const clusterCount: number | null = data?.clusters?.length ?? null;

 // One line of scale, so the card says something without reproducing the
 // report. The failure domain is its own field above; repeating it here would
 // just be the same sentence twice.
 const reportHeadline = (() => {
 if (!data) return '';
 const parts: string[] = [];
 if (data.total_logs) parts.push(`${data.total_logs.toLocaleString()} logs`);
 if (clusterCount != null) parts.push(`${clusterCount.toLocaleString()} clusters`);
 const outliers = (data.clusters || [])
 .filter((c: any) => c.cluster_id === -1)
 .reduce((acc: number, c: any) => acc + (c.size || 0), 0);
 if (outliers > 0) parts.push(`${outliers.toLocaleString()} outliers`);
 const hints = data.intelligence?.root_cause_hints?.length;
 if (hints) parts.push(`${hints} remediation hint${hints === 1 ? '' : 's'}`);
 return parts.length ? parts.join(' · ') : 'Analysis complete.';
 })();

 const taskIdForSource = (name: string) => `analysis:${name}`;

 const runAnalysis = (source?: string) => {
 const target = source || selectedSource;
 if (!target) return;

 const sourceName = sources.find(s => s.path === target)?.name || 'Source';
 // Use a stable ID so the same source can't be analyzed twice simultaneously
 const taskId = taskIdForSource(sourceName);
 executeTask(taskId, `Analyzing ${sourceName}`, runAnalysisJob(
 { source: target, intelligence: true },
 (remoteId) => attachRemoteTask(taskId, remoteId),
 ));
 };

 const handleSearch = async (e: React.FormEvent) => {
 e.preventDefault();
 if (!searchQuery.trim()) {
 setSearchResults(null);
 return;
 }
 setSearchLoading(true);
 try {
 const res = await apiFetch('/v1/logs/query', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify({ 
 query: searchQuery, 
 limit: 100,
 from_ts: timeRange.from,
 to_ts: timeRange.to
 })
 });
 setSearchResults(res.results || []);
 } catch (err: any) {
 console.error('LQL Search failed', err);
 } finally {
 setSearchLoading(false);
 }
 };

 const getMetricOption = () => {
 const chartData = metrics.length > 0 ? metrics : Array.from({ length: 20 }, () => ({ time: '', cpu: 0, mem: 0, anomaly: null }));
 const xLabels = chartData.filter((_, i) => i % 3 === 0).map(m => m.time);
 // The same thresholds the anomaly rule uses (cpu > 85 || mem > 90). Drawing
 // them makes the scatter points legible: you can see what a dot breached.
 const WARN = 0.85;
 const CRIT = 0.90;
 const C = {
 info: cssVar('--signal-info', '#4a9eff'),
 ok: cssVar('--signal-ok', '#35c08e'),
 warn: cssVar('--signal-warn', '#f5a623'),
 crit: cssVar('--signal-crit', '#f2555a'),
 text: cssVar('--text-primary', '#e7e9ec'),
 muted: cssVar('--text-muted', '#6a717a'),
 border: cssVar('--border', 'rgba(255,255,255,0.09)'),
 borderSubtle: cssVar('--border-subtle', 'rgba(255,255,255,0.045)'),
 elevated: cssVar('--bg-elevated', '#121519'),
 };
 return {
 backgroundColor: 'transparent',
 // Shared crosshair: read every series at one instant, like an APM chart.
 tooltip: {
 trigger: 'axis',
 axisPointer: { type: 'cross', label: { backgroundColor: C.elevated }, crossStyle: { color: 'rgba(255,255,255,0.2)' } },
 backgroundColor: C.elevated,
 borderColor: C.border,
 borderWidth: 1,
 textStyle: { color: C.text, fontSize: 11 },
 formatter: (params: any) => {
 if (!params?.length) return '';
 let html = `<div style="opacity:.65;margin-bottom:3px">${params[0].axisValue}</div>`;
 let breached = false;
 params.forEach((p: any) => {
 if (p.value != null) {
 if (p.value >= WARN) breached = true;
 html += `<div>${p.marker} ${p.seriesName}: <b>${(p.value * 100).toFixed(1)}%</b></div>`;
 }
 });
 if (breached) html += `<div style="margin-top:3px;color:var(--signal-warn)">above threshold</div>`;
 return html;
 }
 },
 legend: {
 data: ['CPU', 'Memory', 'Anomalies'],
 bottom: 0,
 textStyle: { color: C.muted, fontSize: 10 },
 icon: 'rect',
 itemWidth: 8,
 itemHeight: 8,
 },
 grid: { top: 16, bottom: 40, left: 40, right: 12 },
 xAxis: {
 type: 'category',
 data: xLabels,
 axisLabel: { color: C.muted, fontSize: 9 },
 axisLine: { lineStyle: { color: C.border } },
 axisTick: { show: false }
 },
 yAxis: {
 type: 'value',
 min: 0, max: 1,
 splitLine: { lineStyle: { color: C.borderSubtle } },
 axisLabel: { color: C.muted, fontSize: 9, formatter: (v: number) => `${(v * 100).toFixed(0)}%` }
 },
 series: [
 {
 name: 'CPU', type: 'line', smooth: false, data: chartData.map(m => m.cpu),
 lineStyle: { width: 1.5, color: C.info },
 itemStyle: { color: C.info }, symbol: 'none',
 // Thresholds ride on the first series so they render once.
 markLine: {
 silent: true, symbol: 'none',
 label: { formatter: (p: any) => p.name, color: C.muted, fontSize: 9, position: 'insideEndTop' },
 data: [
 { yAxis: WARN, name: 'warn 85', lineStyle: { color: C.warn, type: 'dashed', width: 1, opacity: 0.7 } },
 { yAxis: CRIT, name: 'crit 90', lineStyle: { color: C.crit, type: 'dashed', width: 1, opacity: 0.7 } },
 ],
 },
 },
 {
 name: 'Memory', type: 'line', smooth: false, data: chartData.map(m => m.mem),
 lineStyle: { width: 1.5, color: C.ok },
 itemStyle: { color: C.ok }, symbol: 'none',
 },
 {
 name: 'Anomalies', type: 'scatter', data: chartData.map(m => m.anomaly),
 itemStyle: { color: C.crit }, symbolSize: 7,
 },
 ]
 };
 };

 // Real UMAP coordinates for the current run, or null when the run carries
 // none. Never invent points: a synthetic scatter under a "HDBSCAN Projection"
 // heading reads as a real clustering result.
 const projectionPoints = () => {
 if (!data?.clusters) return null;
 const scatterData: any[] = [];
 data.clusters.forEach((c: any) => {
 (c.projection_2d || []).forEach((point: [number, number]) => {
 scatterData.push([
 point[0],
 point[1],
 // Marker radius grows with cluster size, but on a sqrt scale and
 // clamped: the old linear formula turned a 2,913-log cluster into a
 // ~126px blob that swallowed the whole panel.
 Math.max(4, Math.min(16, 3 + Math.sqrt(c.size))),
 c.cluster_id === -1 ? 'Noise' : `C${c.cluster_id}`
 ]);
 });
 });
 return scatterData.length > 0 ? scatterData : null;
 };

 const getTopologyOption = () => {
 const scatterData = projectionPoints();
 if (scatterData) {
 const groups = [...new Set(scatterData.map(d => d[3]))];
 const colors = [cssVar('--signal-alt', '#9d7bff'), cssVar('--signal-info', '#4a9eff'), cssVar('--signal-ok', '#35c08e'), cssVar('--signal-warn', '#f5a623'), cssVar('--signal-crit', '#f2555a')];
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
 data: scatterData.filter(d => d[3] === g),
 itemStyle: { color: colors[gi % colors.length], opacity: g === 'Noise' ? 0.35 : 0.75 },
 })),
 };
 }
 // No projection in this run — render an empty grid; the caller shows why.
 return {
 backgroundColor: 'transparent',
 tooltip: { show: false },
 grid: { top: 10, bottom: 30, left: 10, right: 10 },
 xAxis: { show: false }, yAxis: { show: false },
 series: [],
 };
 };

 const hasProjection = projectionPoints() !== null;

 return (
 <div className="space-y-6 max-w-[1600px] mx-auto pb-10">

 {/* Source Selector + Run Button */}
 <div className="flex items-center gap-4">
 <div className="flex-1 flex items-center gap-3">
 <FileText size={16} className="text-[var(--text-muted)] shrink-0" />
 <select 
 value={selectedSource}
 onChange={(e) => setSelectedSource(e.target.value)}
 className="bg-[var(--bg-modal)] border border-[var(--border)] text-[var(--text-input)] text-xs rounded-lg px-4 py-2.5 outline-none flex-1 max-w-md appearance-none cursor-pointer"
 >
 {sources.map((src) => (
 <option key={src.path} value={src.path}>
 {src.name} ({src.size_human} · ~{src.lines_estimate?.toLocaleString()} lines)
 </option>
 ))}
 </select>
 </div>
 <button
 onClick={() => runAnalysis()}
 disabled={loading || !selectedSource}
 className="bg-[var(--primary)] hover:bg-[var(--primary)] disabled:opacity-50 text-white font-bold rounded-lg px-5 py-2.5 text-xs flex items-center gap-2 transition-colors cursor-pointer border-none"
 >
 {loading ? (
 <><Loader2 size={14} className="animate-spin" /> Analyzing... ({elapsedTime}s)</>
 ) : (
 <><RefreshCw size={14} /> Analyze</>
 )}
 </button>
 </div>

 {/* Log Query Language (LQL) Search */}
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 shadow-sm">
 <form onSubmit={handleSearch} className="relative">
 <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-[var(--text-muted)]" />
 <input
 type="text"
 value={searchQuery}
 onChange={(e) => setSearchQuery(e.target.value)}
 placeholder="Search logs with LQL (e.g., service:payment AND level:ERROR)..."
 className="w-full bg-[var(--bg-inset)] border border-[var(--border)] rounded-lg py-2.5 pl-11 pr-10 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)] transition-colors"
 />
 {searchLoading && (
 <Loader2 size={14} className="absolute right-4 top-1/2 -translate-y-1/2 text-[var(--primary)] animate-spin" />
 )}
 </form>
 {searchResults && (
 <div className="mt-4 pt-4 border-t border-[var(--border-subtle)] overflow-x-auto">
 <div className="flex justify-between items-center mb-2">
 <span className="text-xs font-semibold text-[var(--text-primary)]">LQL Results ({searchResults.length})</span>
 <button onClick={() => setSearchResults(null)} className="text-xs text-[var(--text-muted)] hover:text-[var(--text-primary)]">Clear</button>
 </div>
 <table className="w-full text-left text-xs">
 <thead>
 <tr className="border-b border-[var(--border)]">
 <th className="py-2 text-[var(--text-muted)] font-medium">Timestamp</th>
 <th className="py-2 text-[var(--text-muted)] font-medium">Level</th>
 <th className="py-2 text-[var(--text-muted)] font-medium">Message</th>
 </tr>
 </thead>
 <tbody>
 {searchResults.length === 0 ? (
 <tr>
 <td colSpan={3} className="py-4 text-center text-[var(--text-muted)] italic">No logs found matching this query.</td>
 </tr>
 ) : (
 searchResults.slice(0, 10).map((log, i) => (
 <tr key={i} className="border-b border-[var(--border-subtle)] hover:bg-[var(--bg-surface)]">
 <td className="py-2 pr-4 text-[var(--text-dimmed)] whitespace-nowrap">{new Date(log.timestamp).toLocaleTimeString()}</td>
 <td className="py-2 pr-4">
 <span className={`px-1.5 py-0.5 rounded-sm text-[10px] font-bold ${log.level === 'ERROR' ? 'bg-red-500/10 text-red-500' : 'bg-blue-500/10 text-blue-500'}`}>{log.level || 'INFO'}</span>
 </td>
 <td className="py-2 text-[var(--text-primary)] break-all font-mono opacity-80">{log.message}</td>
 </tr>
 ))
 )}
 </tbody>
 </table>
 {searchResults.length > 10 && (
 <p className="text-[10px] text-[var(--text-muted)] mt-2 text-center">Showing first 10 of {searchResults.length} results. Use specific filters to narrow down.</p>
 )}
 </div>
 )}
 </div>

 {/* System Vitals Panel — the SemanticOS host, not the monitored fleet */}
 <div className="flex items-baseline gap-2 -mb-1">
 <p className="eyebrow">SemanticOS Host Vitals</p>
 <span className="text-[10px] text-[var(--text-muted)]">
 {vitalsHost ? `${vitalsHost} — this platform node, not your monitored services` : 'this platform node, not your monitored services'}
 </span>
 </div>
 <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-[3px] p-3.5 flex items-center justify-between">
 <div>
 <div className="flex items-center gap-2 mb-1">
 <Cpu size={14} className="text-blue-400" />
 <p className="eyebrow">CPU Usage</p>
 </div>
 <span className="text-[22px] font-semibold tnum mono text-[var(--text-primary)]">{vitals[vitals.length-1].cpu.toFixed(1)}%</span>
 </div>
 <Sparkline data={vitals} dataKey="cpu" color="var(--signal-info)" />
 </div>
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-[3px] p-3.5 flex items-center justify-between">
 <div>
 <div className="flex items-center gap-2 mb-1">
 <MemoryStick size={14} style={{ color: "var(--signal-ok)" }} />
 <p className="eyebrow">Memory</p>
 </div>
 <span className="text-[22px] font-semibold tnum mono text-[var(--text-primary)]">{vitals[vitals.length-1].mem.toFixed(1)}%</span>
 </div>
 <Sparkline data={vitals} dataKey="mem" color="var(--signal-ok)" />
 </div>
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-[3px] p-3.5 flex items-center justify-between">
 <div>
 <div className="flex items-center gap-2 mb-1">
 <HardDrive size={14} className="text-[var(--primary)]" />
 <p className="eyebrow">Disk I/O</p>
 </div>
 <span className="text-[22px] font-semibold tnum mono text-[var(--text-primary)]">{vitals[vitals.length-1].disk.toFixed(0)} IOPS</span>
 </div>
 <Sparkline data={vitals} dataKey="disk" color="var(--signal-alt)" />
 </div>
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-[3px] p-3.5 flex items-center justify-between">
 <div>
 <div className="flex items-center gap-2 mb-1">
 <Wifi size={14} style={{ color: "var(--signal-warn)" }} />
 <p className="eyebrow">Net Drops</p>
 </div>
 <span className="text-[22px] font-semibold tnum mono text-[var(--text-primary)]">{vitals[vitals.length-1].net.toFixed(0)} pkt/s</span>
 </div>
 <Sparkline data={vitals} dataKey="net" color="var(--signal-warn)" />
 </div>
 </div>

 {/* KPIs */}
 <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-5 shadow-sm">
 <p className="text-xs text-[var(--text-muted)] font-medium mb-3">Total Logs Processed</p>
 <div className="flex items-end gap-3">
 {loading ? (
 <div className="shimmer-bg h-9 w-28 rounded my-1" />
 ) : (
 <span className="text-4xl font-bold text-[var(--text-primary)] leading-none">{data?.total_logs?.toLocaleString() || '—'}</span>
 )}
 </div>
 </div>
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-5 shadow-sm">
 <p className="text-xs text-[var(--text-muted)] font-medium mb-3">Outliers Detected</p>
 <div className="flex items-end gap-3">
 {loading ? (
 <div className="shimmer-bg h-9 w-20 rounded my-1" />
 ) : (
 <>
 <span className="text-4xl font-bold text-[var(--text-primary)] leading-none">
 {data?.clusters ? data.clusters.filter((c:any) => c.cluster_id === -1).reduce((acc:number, c:any) => acc + c.size, 0).toLocaleString() : '—'}
 </span>
 {data?.clusters && <span className="text-xs text-red-500 font-medium pb-0.5 flex items-center"><TrendingUp size={12} className="mr-1"/> flagged</span>}
 </>
 )}
 </div>
 </div>
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-5 shadow-sm">
 <p className="text-xs text-[var(--text-muted)] font-medium mb-3">Noise Reduced</p>
 <div className="flex items-end gap-3">
 {loading ? (
 <div className="shimmer-bg h-9 w-24 rounded my-1" />
 ) : (
 <>
 <span className="text-4xl font-bold text-[var(--text-primary)] leading-none">
 {data?.total_logs && data?.clusters ? ((1 - (data.clusters.length / data.total_logs)) * 100).toFixed(2) + '%' : '—'}
 </span>
 {data && <span className="text-xs text-emerald-500 font-medium pb-0.5 flex items-center"><TrendingUp size={12} className="mr-1"/> efficiency</span>}
 </>
 )}
 </div>
 </div>
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-5 shadow-sm">
 <p className="text-xs text-[var(--text-muted)] font-medium mb-3">Semantic Clusters</p>
 <div className="flex items-end gap-3">
 {loading ? (
 <div className="shimmer-bg h-9 w-16 rounded my-1" />
 ) : (
 <>
 <span className="text-4xl font-bold text-[var(--text-primary)] leading-none">{data?.clusters?.length || '—'}</span>
 {data && <span className="text-xs text-[var(--primary)] font-medium pb-0.5">patterns</span>}
 </>
 )}
 </div>
 </div>
 </div>

 <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
 
 {/* Metrics Chart */}
 <div className="lg:col-span-2 bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-6">
 <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-1">Metrics & Anomalies Correlation</h2>
 <p className="text-xs text-[var(--text-muted)] mb-6">Past 1 Hour</p>
 <div className="h-[250px]">
 <ReactEcharts option={getMetricOption()} style={{ height: '100%', width: '100%' }} />
 </div>
 </div>

 {/* Current Investigation */}
 <div className="lg:col-span-1 bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-6 flex flex-col shadow-sm">
 <div className="flex items-center justify-between mb-6">
 <h2 className="text-sm font-semibold text-[var(--text-primary)]">Current Investigation</h2>
 {loading ? (
 <span className="text-[10px] font-bold uppercase tracking-wider text-blue-400 bg-blue-500/10 px-2.5 py-1 rounded-sm flex items-center gap-1.5 border border-blue-500/20">
 <Loader2 size={10} className="animate-spin" /> PROCESSING
 </span>
 ) : data?.intelligence ? (
 <span className="text-[10px] font-bold uppercase tracking-wider text-red-500 bg-red-500/10 px-2.5 py-1 rounded-sm flex items-center gap-1.5 border border-red-500/20">
 <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse"></span> ACTIVE
 </span>
 ) : (
 <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)] bg-[var(--bg-inset)] border border-[var(--border-subtle)] px-2.5 py-1 rounded-sm">
 IDLE
 </span>
 )}
 </div>
 
 <div className="mb-5">
 <p className="text-[10px] font-bold text-[var(--text-muted)] uppercase tracking-widest mb-1.5">Failure Domain</p>
 {loading ? (
 <div className="shimmer-bg h-5 w-32 rounded" />
 ) : (
 <p className="text-sm font-semibold text-[var(--text-primary)] flex items-center gap-2">
 <Database size={14} className="text-blue-400" /> {data?.intelligence?.failure_domain || '—'}
 </p>
 )}
 </div>

 {/* The report itself is not rendered here. The full narrative summary,
 every remediation hint and every cluster used to be dumped into this
 panel, which buried the operational view. The Command Center states
 that a report exists and what it found; reading it is one click. */}
 <div className="flex-1 flex flex-col justify-center">
 {loading ? (
 <div className="space-y-2.5 py-4">
 <div className="shimmer-bg h-4 w-full rounded" />
 <div className="shimmer-bg h-4 w-5/6 rounded" />
 <div className="shimmer-bg h-4 w-2/3 rounded" />
 </div>
 ) : error ? (
 <div className="flex items-start gap-2.5 text-red-400 bg-red-500/5 p-3 rounded-lg border border-red-500/10 text-xs">
 <AlertTriangle size={14} className="shrink-0 mt-0.5" />
 {error}
 </div>
 ) : reportRunId ? (
 <button
 type="button"
 onClick={() => router.push(`/app/runs/${reportRunId}`)}
 className="group w-full text-left bg-[var(--bg-inset)] border border-[var(--border-subtle)] hover:border-[var(--primary)]/40 rounded-lg p-5 transition-colors cursor-pointer"
 >
 <div className="flex items-start gap-3">
 <div className="w-9 h-9 rounded-lg bg-[var(--primary)]/10 border border-[var(--primary)]/20 flex items-center justify-center shrink-0">
 <FileText size={16} className="text-[var(--primary)]" />
 </div>
 <div className="min-w-0">
 <p className="text-sm font-semibold text-[var(--text-primary)]">Report generated</p>
 <p className="text-[11px] text-[var(--text-muted)] mt-1 leading-relaxed">
 {reportHeadline}
 </p>
 </div>
 </div>
 <span className="mt-4 inline-flex items-center gap-1.5 text-xs font-semibold text-[var(--primary)] group-hover:gap-2.5 transition-all">
 View full analysis report <ArrowRight size={13} />
 </span>
 </button>
 ) : data ? (
 // A run finished but was never persisted (e.g. an older in-memory
 // result); say so rather than offering a link that 404s.
 <p className="text-xs text-[var(--text-muted)] py-4">
 Analysis complete. This result was not persisted, so there is no report to open.
 </p>
 ) : (
 <p className="text-xs text-[var(--text-muted)] py-4">
 Select a source and run analysis to generate a report.
 </p>
 )}
 </div>

 <div className="mt-5 pt-4 border-t border-[var(--border-subtle)] flex justify-between text-[10px] text-[var(--text-muted)]">
 <span>{clusterCount != null ? `${clusterCount.toLocaleString()} clusters` : 'Confidence: —'}</span>
 <span>Model: {settings?.llm_model || 'Llama 3.3-70B Local'}</span>
 </div>
 </div>

 </div>

 <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
 
 {/* Forensics Feed — show ALL clusters */}
 <div className="lg:col-span-2 bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-6 shadow-sm">
 <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-1">Anomaly Forensics Feed</h2>
 <p className="text-xs text-[var(--text-muted)] mb-6">All Semantic Clusters · Sorted by Size</p>
 
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
 <tbody className="divide-y divide-transparent">
 {loading ? (
 Array.from({ length: 5 }).map((_, idx) => (
 <tr key={idx}>
 <td className="py-3 px-2"><div className="shimmer-bg h-7 w-7 rounded-lg" /></td>
 <td className="py-3 px-2"><div className="shimmer-bg h-4 w-64 rounded" /></td>
 <td className="py-3"><div className="shimmer-bg h-4 w-12 rounded" /></td>
 <td className="py-3"><div className="shimmer-bg h-4 w-8 rounded" /></td>
 <td className="py-3"><div className="shimmer-bg h-6 w-12 rounded-full" /></td>
 </tr>
 ))
 ) : data?.clusters?.length ? data.clusters.map((c:any, i:number) => (
 <tr key={i} className="hover:bg-[var(--bg-surface-hover)] transition-colors rounded-lg">
 <td className="py-3 px-2">
 <span className={`inline-flex items-center justify-center w-7 h-7 rounded-lg text-[10px] font-bold ${
 c.cluster_id === -1 
 ? 'bg-red-500/10 text-red-400 border border-red-500/20' 
 : 'bg-[var(--primary)] text-[var(--primary)] border border-[var(--primary)]'
 }`}>
 {c.cluster_id === -1 ? '⚠' : `C${c.cluster_id}`}
 </span>
 </td>
 <td className="py-3 px-2 font-medium text-[var(--text-primary)] truncate max-w-[280px]" title={c.representative_template}>
 {c.keyword_flag && <span className="mr-1 text-[9px] font-bold text-red-400">🔴</span>}
 {(() => { const s = typeof c.summary === 'object' ? (c.summary?.summary || c.summary?.representative_log || '') : c.summary; return s && s !== 'Analyzing...' ? s : c.representative_template; })()}
 </td>
 <td className="py-3 text-[var(--text-secondary)] font-mono">{c.size.toLocaleString()}</td>
 <td className="py-3 font-bold">
 <span className={c.anomaly_score > 0.7 ? 'text-red-400' : c.anomaly_score > 0.4 ? 'text-yellow-400' : 'text-[var(--text-muted)]'}>
 {c.anomaly_score > 0 ? c.anomaly_score.toFixed(2) : '—'}
 </span>
 </td>
 <td className="py-3">
 {(() => {
 const p = c.priority;
 return (
 <span className={`px-2 py-1 rounded-sm font-bold text-[9px] uppercase tracking-wider ${
 PRIORITY_STYLES[p] || PRIORITY_STYLES.P3
 }`}>
 {p || (c.cluster_id === -1 ? 'Noise' : 'P3')}
 </span>
 );
 })()}
 </td>
 </tr>
 )) : (
 <tr>
 <td colSpan={5} className="py-8 text-center text-[var(--text-muted)]">
 {error ? error : 'Select a source and run analysis to populate data.'}
 </td>
 </tr>
 )}
 </tbody>
 </table>
 </div>
 </div>

 {/* Neural Topology */}
 <div className="lg:col-span-1 bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-6">
 <div className="flex items-start justify-between mb-6">
 <div>
 <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-1">Neural Topology</h2>
 <p className="text-xs text-[var(--text-muted)]">HDBSCAN Projection</p>
 </div>
 {hasProjection && <Maximize2 size={14} className="text-[var(--text-muted)] mt-0.5" />}
 </div>
 <div
 className={`h-[200px] bg-[var(--bg-inset)] rounded-lg border border-[var(--border-subtle)] relative transition-colors ${
 hasProjection ? 'cursor-pointer hover:border-[var(--primary)]' : ''
 }`}
 // The scatter is unreadable at 200px; clicking opens the same option
 // full screen. Only offer it when there is something to enlarge.
 onClick={() => hasProjection && setTopologyExpanded(true)}
 onKeyDown={(e) => {
 if (hasProjection && (e.key === 'Enter' || e.key === ' ')) {
 e.preventDefault();
 setTopologyExpanded(true);
 }
 }}
 role={hasProjection ? 'button' : undefined}
 tabIndex={hasProjection ? 0 : undefined}
 aria-label={hasProjection ? 'Expand neural topology projection' : undefined}
 >
 <ReactEcharts option={getTopologyOption()} style={{ height: '100%', width: '100%' }} />
 {!hasProjection && (
 <div className="absolute inset-0 flex items-center justify-center text-xs text-[var(--text-muted)] text-center px-6 pointer-events-none">
 {loading
 ? 'Computing UMAP projection…'
 : data?.clusters
 ? 'This run carries no 2D projection.'
 : 'Run an analysis to project clusters.'}
 </div>
 )}
 </div>
 </div>

 </div>

 <TopologyModal
 isOpen={topologyExpanded}
 onClose={() => setTopologyExpanded(false)}
 option={getTopologyOption()}
 />

 </div>
 );
}

const PRIORITY_STYLES: Record<string, string> = {
 P0: 'bg-red-500/20 text-red-400 border border-red-500/30',
 P1: 'bg-orange-500/20 text-orange-400 border border-orange-500/30',
 P2: 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30',
 P3: 'bg-[var(--bg-inset)] text-[var(--text-muted)] border border-[var(--border-subtle)]',
};

