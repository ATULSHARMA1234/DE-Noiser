'use client';

import React, { useState, useEffect } from 'react';
import ReactEcharts from 'echarts-for-react';
import * as echarts from 'echarts';
import { Database, TrendingUp, Zap, Loader2, AlertTriangle, RefreshCw, FileText, Cpu, MemoryStick, HardDrive, Wifi, Search } from 'lucide-react';
import { LineChart, Line, ResponsiveContainer, YAxis } from 'recharts';
import { apiFetch, runAnalysis as runAnalysisJob } from '@/lib/api';
import { useTimeRange } from '@/context/TimeRangeContext';
import { useTasks } from '@/context/TaskContext';

const seededValue = (index: number, seed: number) => {
 const x = Math.sin(index * 12.9898 + seed * 78.233) * 43758.5453;
 return x - Math.floor(x);
};


function Sparkline({ data, dataKey, color }: { data: any[], dataKey: string, color: string }) {
 return (
 <div className="ml-auto">
 <LineChart width={120} height={40} data={data}>
 <Line type="monotone" dataKey={dataKey} stroke={color} strokeWidth={2} dot={false} isAnimationActive={false} />
 <YAxis domain={['dataMin - 10', 'dataMax + 10']} hide />
 </LineChart>
 </div>
 );
}

export default function CommandCenter() {
 const { timeRange } = useTimeRange();
 const { tasks, executeTask } = useTasks();
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

 // Timer for loading state
 useEffect(() => {
 let interval: any;
 if (loading) {
 setElapsedTime(0);
 interval = setInterval(() => setElapsedTime(t => t + 1), 1000);
 }
 return () => clearInterval(interval);
 }, [loading]);

 const taskIdForSource = (name: string) => `analysis:${name}`;

 const runAnalysis = (source?: string) => {
 const target = source || selectedSource;
 if (!target) return;

 const sourceName = sources.find(s => s.path === target)?.name || 'Source';
 // Use a stable ID so the same source can't be analyzed twice simultaneously
 executeTask(taskIdForSource(sourceName), `Analyzing ${sourceName}`, runAnalysisJob({ source: target, intelligence: true }));
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
 return {
 backgroundColor: 'transparent',
 tooltip: {
 trigger: 'axis',
 formatter: (params: any) => {
 if (!params?.length) return '';
 let html = `<div style="font-size:11px">${params[0].axisValue}</div>`;
 params.forEach((p: any) => {
 if (p.value != null) {
 const val = p.seriesName === 'Anomalies' ? `${(p.value * 100).toFixed(1)}%` : `${(p.value * 100).toFixed(1)}%`;
 html += `<div>${p.marker} ${p.seriesName}: <b>${val}</b></div>`;
 }
 });
 return html;
 }
 },
 legend: {
 data: ['CPU', 'Memory', 'Anomalies'],
 bottom: 0,
 textStyle: { color: '#71717a', fontSize: 10 },
 icon: 'circle'
 },
 grid: { top: 20, bottom: 40, left: 40, right: 10 },
 xAxis: {
 type: 'category',
 data: xLabels,
 axisLabel: { color: '#52525b', fontSize: 9 },
 axisLine: { lineStyle: { color: '#27272a' } },
 axisTick: { show: false }
 },
 yAxis: {
 type: 'value',
 min: 0, max: 1,
 splitLine: { lineStyle: { color: '#27272a', type: 'dashed' } },
 axisLabel: { color: '#52525b', fontSize: 9, formatter: (v: number) => `${(v * 100).toFixed(0)}%` }
 },
 series: [
 { name: 'CPU', type: 'line', smooth: true, data: chartData.map(m => m.cpu), itemStyle: { color: '#3b82f6' }, symbol: 'circle', symbolSize: 4, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(59, 130, 246, 0.3)' }, { offset: 1, color: 'rgba(59, 130, 246, 0)' }]) } },
 { name: 'Memory', type: 'line', smooth: true, data: chartData.map(m => m.mem), itemStyle: { color: '#10b981' }, symbol: 'circle', symbolSize: 4, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(16, 185, 129, 0.3)' }, { offset: 1, color: 'rgba(16, 185, 129, 0)' }]) } },
 { name: 'Anomalies', type: 'scatter', data: chartData.map(m => m.anomaly), itemStyle: { color: '#d946ef' }, symbolSize: 8 }
 ]
 };
 };

 const getTopologyOption = () => {
 // Use real cluster data if available
 if (data?.clusters) {
 const scatterData: any[] = [];
 data.clusters.forEach((c: any, idx: number) => {
 if (c.projection_2d && c.projection_2d.length > 0) {
 c.projection_2d.forEach((point: [number, number], i: number) => {
 scatterData.push([
 point[0],
 point[1],
 c.size > 50 ? 5 + (c.size / 50) : 3 + (c.size / 10), // Base symbol size on cluster size
 c.cluster_id === -1 ? 'Noise' : `C${c.cluster_id}`
 ]);
 });
 }
 });
 const groups = [...new Set(scatterData.map(d => d[3]))];
 const colors = ['#d946ef', '#3b82f6', '#10b981', '#f59e0b', '#ef4444'];
 return {
 backgroundColor: 'transparent',
 tooltip: { show: false },
 legend: { bottom: 0, left: 10, textStyle: { color: '#71717a', fontSize: 9 }, icon: 'circle' },
 grid: { top: 10, bottom: 30, left: 10, right: 10 },
 xAxis: { show: false }, yAxis: { show: false },
 series: groups.map((g, gi) => ({
 name: g,
 type: 'scatter',
 symbolSize: (d: any) => d[2] * 2,
 data: scatterData.filter(d => d[3] === g),
 itemStyle: { color: colors[gi % colors.length], opacity: g === 'Noise' ? 0.3 : 0.8 },
 })),
 };
 }
 // Fallback placeholder
 const scatterData = Array.from({ length: 150 }, (_, i) => [
 seededValue(i, 8) * 10,
 seededValue(i, 9) * 10,
 seededValue(i, 10) * 5 + 2,
 seededValue(i, 11) > 0.9 ? 'C1' : seededValue(i, 12) > 0.8 ? 'C2' : 'Noise'
 ]);
 return {
 backgroundColor: 'transparent',
 tooltip: { show: false },
 legend: { bottom: 0, left: 10, textStyle: { color: '#71717a', fontSize: 9 }, icon: 'circle' },
 grid: { top: 10, bottom: 30, left: 10, right: 10 },
 xAxis: { show: false }, yAxis: { show: false },
 series: [
 { name: 'C1', type: 'scatter', symbolSize: (d: any) => d[2] * 2, data: scatterData.filter(d => d[3] === 'C1'), itemStyle: { color: '#d946ef', opacity: 0.8 } },
 { name: 'C2', type: 'scatter', symbolSize: (d: any) => d[2] * 2, data: scatterData.filter(d => d[3] === 'C2'), itemStyle: { color: '#3b82f6', opacity: 0.8 } },
 { name: 'Noise', type: 'scatter', symbolSize: (d: any) => d[2] * 2, data: scatterData.filter(d => d[3] === 'Noise'), itemStyle: { color: '#10b981', opacity: 0.3 } }
 ]
 };
 };

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

 {/* System Vitals Panel */}
 <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 shadow-sm flex items-center justify-between">
 <div>
 <div className="flex items-center gap-2 mb-1">
 <Cpu size={14} className="text-blue-400" />
 <p className="text-xs text-[var(--text-muted)] font-medium uppercase tracking-wider">CPU Usage</p>
 </div>
 <span className="text-2xl font-bold text-[var(--text-primary)]">{vitals[vitals.length-1].cpu.toFixed(1)}%</span>
 </div>
 <Sparkline data={vitals} dataKey="cpu" color="#3b82f6" />
 </div>
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 shadow-sm flex items-center justify-between">
 <div>
 <div className="flex items-center gap-2 mb-1">
 <MemoryStick size={14} className="text-emerald-400" />
 <p className="text-xs text-[var(--text-muted)] font-medium uppercase tracking-wider">Memory</p>
 </div>
 <span className="text-2xl font-bold text-[var(--text-primary)]">{vitals[vitals.length-1].mem.toFixed(1)}%</span>
 </div>
 <Sparkline data={vitals} dataKey="mem" color="#10b981" />
 </div>
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 shadow-sm flex items-center justify-between">
 <div>
 <div className="flex items-center gap-2 mb-1">
 <HardDrive size={14} className="text-[var(--primary)]" />
 <p className="text-xs text-[var(--text-muted)] font-medium uppercase tracking-wider">Disk I/O</p>
 </div>
 <span className="text-2xl font-bold text-[var(--text-primary)]">{vitals[vitals.length-1].disk.toFixed(0)} IOPS</span>
 </div>
 <Sparkline data={vitals} dataKey="disk" color="#d946ef" />
 </div>
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 shadow-sm flex items-center justify-between">
 <div>
 <div className="flex items-center gap-2 mb-1">
 <Wifi size={14} className="text-orange-400" />
 <p className="text-xs text-[var(--text-muted)] font-medium uppercase tracking-wider">Net Drops</p>
 </div>
 <span className="text-2xl font-bold text-[var(--text-primary)]">{vitals[vitals.length-1].net.toFixed(0)} pkt/s</span>
 </div>
 <Sparkline data={vitals} dataKey="net" color="#f97316" />
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

 <div className="mb-5">
 <p className="text-[10px] font-bold text-[var(--text-muted)] uppercase tracking-widest mb-2">Incident Summary</p>
 <ul className="space-y-3 text-xs text-[var(--text-input)]">
 {loading ? (
 <div className="space-y-2">
 <div className="shimmer-bg h-4 w-full rounded" />
 <div className="shimmer-bg h-4 w-5/6 rounded" />
 <div className="shimmer-bg h-4 w-2/3 rounded" />
 </div>
 ) : error ? (
 <li className="flex items-start gap-2.5 text-red-400 bg-red-500/5 p-3 rounded-lg border border-red-500/10">
 <AlertTriangle size={14} className="shrink-0 mt-0.5" />
 {error}
 </li>
 ) : data?.intelligence?.incident_summary ? (
 Array.isArray(data.intelligence.incident_summary) ? data.intelligence.incident_summary.map((s:string, i:number) => (
 <li key={i} className="flex items-start gap-2.5">
 <span className="w-1.5 h-1.5 rounded-full bg-[var(--primary)] mt-1.5 shrink-0"></span>
 {s}
 </li>
 )) : (
 <li className="flex items-start gap-2.5">
 <span className="w-1.5 h-1.5 rounded-full bg-[var(--primary)] mt-1.5 shrink-0"></span>
 {data.intelligence.incident_summary}
 </li>
 )
 ) : (
 <li className="flex items-start gap-2.5 text-[var(--text-muted)]">
 Select a source and run analysis to begin.
 </li>
 )}
 </ul>
 </div>

 <div className="mt-auto bg-[var(--bg-inset)] border border-[var(--border-subtle)] rounded-lg p-5 shadow-inner">
 <p className="text-[10px] font-bold text-[var(--text-muted)] uppercase tracking-widest mb-3">Remediation Hints</p>
 <ul className="space-y-3 text-xs text-emerald-400 font-medium">
 {loading ? (
 <div className="space-y-2">
 <div className="shimmer-bg h-4 w-full rounded" />
 <div className="shimmer-bg h-4 w-4/5 rounded" />
 </div>
 ) : data?.intelligence?.root_cause_hints ? data.intelligence.root_cause_hints.map((hint:string, i:number) => (
 <li key={i} className="flex items-start gap-2">
 <Zap size={14} className="shrink-0 mt-0.5 text-emerald-500" />
 {hint}
 </li>
 )) : (
 <li className="flex items-start gap-2 text-[var(--text-muted)]">
 <Zap size={14} className="shrink-0 mt-0.5 text-[var(--text-dimmed)]" />
 Pending analysis...
 </li>
 )}
 </ul>
 </div>
 
 <div className="mt-4 flex justify-between text-[10px] text-[var(--text-muted)]">
 <span>Confidence: {data?.intelligence ? '85%' : '—'}</span>
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
 {c.summary !== "Analyzing..." ? c.summary : c.representative_template}
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
 <h2 className="text-sm font-semibold text-[var(--text-primary)] mb-1">Neural Topology</h2>
 <p className="text-xs text-[var(--text-muted)] mb-6">HDBSCAN Projection</p>
 <div className="h-[200px] bg-[var(--bg-inset)] rounded-lg border border-[var(--border-subtle)] relative">
 <ReactEcharts option={getTopologyOption()} style={{ height: '100%', width: '100%' }} />
 </div>
 </div>

 </div>

 </div>
 );
}

const PRIORITY_STYLES: Record<string, string> = {
 P0: 'bg-red-500/20 text-red-400 border border-red-500/30',
 P1: 'bg-orange-500/20 text-orange-400 border border-orange-500/30',
 P2: 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30',
 P3: 'bg-[var(--bg-inset)] text-[var(--text-muted)] border border-[var(--border-subtle)]',
};

