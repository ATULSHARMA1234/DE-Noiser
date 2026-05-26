'use client';

import React, { useState, useEffect } from 'react';
import ReactEcharts from 'echarts-for-react';
import * as echarts from 'echarts';
import { Database, TrendingUp, Zap, Loader2, AlertTriangle, RefreshCw, FileText, Cpu, MemoryStick, HardDrive, Wifi } from 'lucide-react';
import { LineChart, Line, ResponsiveContainer, YAxis } from 'recharts';
import { apiFetch, runAnalysis as runAnalysisJob } from '@/lib/api';

const seededValue = (index: number, seed: number) => {
  const x = Math.sin(index * 12.9898 + seed * 78.233) * 43758.5453;
  return x - Math.floor(x);
};

const INITIAL_METRICS = Array.from({ length: 60 }, (_, i) => ({
  time: `05:${i.toString().padStart(2, '0')}`,
  cpu: i > 25 && i < 35 ? seededValue(i, 1) * 0.1 + 0.85 : seededValue(i, 2) * 0.2 + 0.3,
  mem: i > 26 ? seededValue(i, 3) * 0.1 + 0.8 : seededValue(i, 4) * 0.1 + 0.4,
  anomaly: (i === 26 || i === 28) ? 0.9 : null,
}));

function Sparkline({ data, dataKey, color }: { data: any[], dataKey: string, color: string }) {
  return (
    <div className="h-[40px] w-[120px] ml-auto">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data}>
          <Line type="monotone" dataKey={dataKey} stroke={color} strokeWidth={2} dot={false} isAnimationActive={false} />
          <YAxis domain={['dataMin - 10', 'dataMax + 10']} hide />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function CommandCenter() {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sources, setSources] = useState<any[]>([]);
  const [selectedSource, setSelectedSource] = useState('');
  const [elapsedTime, setElapsedTime] = useState(0);

  // Mock time series data for the metrics chart
  const [metrics] = useState<any[]>(INITIAL_METRICS);

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

  // Timer for loading state
  useEffect(() => {
    let interval: any;
    if (loading) {
      setElapsedTime(0);
      interval = setInterval(() => setElapsedTime(t => t + 1), 1000);
    }
    return () => clearInterval(interval);
  }, [loading]);

  const runAnalysis = async (source?: string) => {
    const target = source || selectedSource;
    if (!target) return;

    setLoading(true);
    setError(null);
    setData(null);
    try {
      const result = await runAnalysisJob({ source: target, intelligence: true });
      setData(result);
    } catch (err: any) {
      setError(err.message || 'Connection failed. Is the Python backend running on port 8000?');
    } finally {
      setLoading(false);
    }
  };

  const getMetricOption = () => ({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: { 
      data: ['CPU', 'Memory', 'Anomalies'], 
      bottom: 0, 
      textStyle: { color: '#71717a', fontSize: 10 },
      icon: 'circle'
    },
    grid: { top: 20, bottom: 40, left: 30, right: 10 },
    xAxis: { 
      type: 'category', 
      data: metrics.filter((_, i) => i % 3 === 0).map(m => m.time),
      axisLabel: { color: '#52525b', fontSize: 9 },
      axisLine: { lineStyle: { color: '#27272a' } },
      axisTick: { show: false }
    },
    yAxis: { 
      type: 'value', 
      min: 0, max: 1,
      splitLine: { lineStyle: { color: '#27272a', type: 'dashed' } },
      axisLabel: { color: '#52525b', fontSize: 9 }
    },
    series: [
      { name: 'CPU', type: 'line', smooth: true, data: metrics.map(m => m.cpu), itemStyle: { color: '#3b82f6' }, symbol: 'circle', symbolSize: 4, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(59, 130, 246, 0.3)' }, { offset: 1, color: 'rgba(59, 130, 246, 0)' }]) } },
      { name: 'Memory', type: 'line', smooth: true, data: metrics.map(m => m.mem), itemStyle: { color: '#10b981' }, symbol: 'circle', symbolSize: 4, areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(16, 185, 129, 0.3)' }, { offset: 1, color: 'rgba(16, 185, 129, 0)' }]) } },
      { name: 'Anomalies', type: 'scatter', data: metrics.map(m => m.anomaly), itemStyle: { color: '#d946ef' }, symbolSize: 8 }
    ]
  });

  const getTopologyOption = () => {
    // Use real cluster data if available
    if (data?.clusters) {
      const scatterData: any[] = [];
      data.clusters.forEach((c: any, idx: number) => {
        const count = Math.min(c.size, 50);
        for (let i = 0; i < count; i++) {
          const pointIndex = idx * 50 + i;
          scatterData.push([
            seededValue(pointIndex, 5) * 8 + idx * 2,
            seededValue(pointIndex, 6) * 8,
            seededValue(pointIndex, 7) * 3 + 2,
            c.cluster_id === -1 ? 'Noise' : `C${c.cluster_id}`
          ]);
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
          <FileText size={16} className="text-zinc-500 shrink-0" />
          <select 
            value={selectedSource}
            onChange={(e) => setSelectedSource(e.target.value)}
            className="bg-[#141416] border border-white/10 text-zinc-300 text-xs rounded-lg px-4 py-2.5 outline-none flex-1 max-w-md appearance-none cursor-pointer"
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
          className="bg-fuchsia-600 hover:bg-fuchsia-500 disabled:opacity-50 text-white font-bold rounded-lg px-5 py-2.5 text-xs flex items-center gap-2 transition-colors cursor-pointer"
        >
          {loading ? (
            <><Loader2 size={14} className="animate-spin" /> Analyzing... ({elapsedTime}s)</>
          ) : (
            <><RefreshCw size={14} /> Analyze</>
          )}
        </button>
      </div>

      {/* System Vitals Panel */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-[#121214] border-none rounded-xl p-4 shadow-sm flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <Cpu size={14} className="text-blue-400" />
              <p className="text-xs text-zinc-400 font-medium uppercase tracking-wider">CPU Usage</p>
            </div>
            <span className="text-2xl font-bold text-white">{vitals[vitals.length-1].cpu.toFixed(1)}%</span>
          </div>
          <Sparkline data={vitals} dataKey="cpu" color="#3b82f6" />
        </div>
        <div className="bg-[#121214] border-none rounded-xl p-4 shadow-sm flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <MemoryStick size={14} className="text-emerald-400" />
              <p className="text-xs text-zinc-400 font-medium uppercase tracking-wider">Memory</p>
            </div>
            <span className="text-2xl font-bold text-white">{vitals[vitals.length-1].mem.toFixed(1)}%</span>
          </div>
          <Sparkline data={vitals} dataKey="mem" color="#10b981" />
        </div>
        <div className="bg-[#121214] border-none rounded-xl p-4 shadow-sm flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <HardDrive size={14} className="text-fuchsia-400" />
              <p className="text-xs text-zinc-400 font-medium uppercase tracking-wider">Disk I/O</p>
            </div>
            <span className="text-2xl font-bold text-white">{vitals[vitals.length-1].disk.toFixed(0)} IOPS</span>
          </div>
          <Sparkline data={vitals} dataKey="disk" color="#d946ef" />
        </div>
        <div className="bg-[#121214] border-none rounded-xl p-4 shadow-sm flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <Wifi size={14} className="text-orange-400" />
              <p className="text-xs text-zinc-400 font-medium uppercase tracking-wider">Net Drops</p>
            </div>
            <span className="text-2xl font-bold text-white">{vitals[vitals.length-1].net.toFixed(0)} pkt/s</span>
          </div>
          <Sparkline data={vitals} dataKey="net" color="#f97316" />
        </div>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-[#121214] border-none rounded-xl p-5 shadow-sm">
          <p className="text-xs text-zinc-500 font-medium mb-3">Total Logs Processed</p>
          <div className="flex items-end gap-3">
            <span className="text-4xl font-bold text-white leading-none">{data?.total_logs?.toLocaleString() || '—'}</span>
          </div>
        </div>
        <div className="bg-[#121214] border-none rounded-xl p-5 shadow-sm">
          <p className="text-xs text-zinc-500 font-medium mb-3">Outliers Detected</p>
          <div className="flex items-end gap-3">
            <span className="text-4xl font-bold text-white leading-none">
              {data?.clusters ? data.clusters.filter((c:any) => c.cluster_id === -1).reduce((acc:number, c:any) => acc + c.size, 0).toLocaleString() : '—'}
            </span>
            {data?.clusters && <span className="text-xs text-red-500 font-medium pb-0.5 flex items-center"><TrendingUp size={12} className="mr-1"/> flagged</span>}
          </div>
        </div>
        <div className="bg-[#121214] border-none rounded-xl p-5 shadow-sm">
          <p className="text-xs text-zinc-500 font-medium mb-3">Noise Reduced</p>
          <div className="flex items-end gap-3">
            <span className="text-4xl font-bold text-white leading-none">
              {data?.total_logs && data?.clusters ? ((1 - (data.clusters.length / data.total_logs)) * 100).toFixed(2) + '%' : '—'}
            </span>
            {data && <span className="text-xs text-emerald-500 font-medium pb-0.5 flex items-center"><TrendingUp size={12} className="mr-1"/> efficiency</span>}
          </div>
        </div>
        <div className="bg-[#121214] border-none rounded-xl p-5 shadow-sm">
          <p className="text-xs text-zinc-500 font-medium mb-3">Semantic Clusters</p>
          <div className="flex items-end gap-3">
            <span className="text-4xl font-bold text-white leading-none">{data?.clusters?.length || '—'}</span>
            {data && <span className="text-xs text-fuchsia-400 font-medium pb-0.5">patterns</span>}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-6">
        
        {/* Metrics Chart */}
        <div className="col-span-2 bg-[#141416] border border-white/5 rounded-xl p-6">
          <h2 className="text-sm font-semibold text-white mb-1">Metrics & Anomalies Correlation</h2>
          <p className="text-xs text-zinc-500 mb-6">Past 1 Hour</p>
          <div className="h-[250px]">
            <ReactEcharts option={getMetricOption()} style={{ height: '100%', width: '100%' }} />
          </div>
        </div>

        {/* Current Investigation */}
        <div className="col-span-1 bg-[#121214] border-none rounded-xl p-6 flex flex-col shadow-sm">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-sm font-semibold text-white">Current Investigation</h2>
            {loading ? (
              <span className="text-[10px] font-bold uppercase tracking-wider text-blue-400 bg-blue-500/10 px-2.5 py-1 rounded-sm flex items-center gap-1.5">
                <Loader2 size={10} className="animate-spin" /> PROCESSING
              </span>
            ) : data?.intelligence ? (
              <span className="text-[10px] font-bold uppercase tracking-wider text-red-500 bg-red-500/10 px-2.5 py-1 rounded-sm flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse"></span> ACTIVE
              </span>
            ) : (
              <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-500 bg-zinc-800 px-2.5 py-1 rounded-sm">
                IDLE
              </span>
            )}
          </div>
          
          <div className="mb-5">
            <p className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest mb-1.5">Failure Domain</p>
            <p className="text-sm font-semibold text-white flex items-center gap-2">
              <Database size={14} className="text-blue-400" /> {data?.intelligence?.failure_domain || '—'}
            </p>
          </div>

          <div className="mb-5">
             <p className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest mb-2">Incident Summary</p>
             <ul className="space-y-3 text-xs text-zinc-300">
               {loading ? (
                 <li className="flex items-start gap-2.5 animate-pulse">
                   <span className="w-1.5 h-1.5 rounded-full bg-blue-500 mt-1 shrink-0"></span>
                   Neural Engine processing ({elapsedTime}s elapsed)...
                 </li>
               ) : error ? (
                 <li className="flex items-start gap-2.5 text-red-400">
                   <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                   {error}
                 </li>
               ) : data?.intelligence?.incident_summary ? (
                  Array.isArray(data.intelligence.incident_summary) ? data.intelligence.incident_summary.map((s:string, i:number) => (
                    <li key={i} className="flex items-start gap-2.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-fuchsia-500 mt-1 shrink-0"></span>
                      {s}
                    </li>
                  )) : (
                    <li className="flex items-start gap-2.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-fuchsia-500 mt-1 shrink-0"></span>
                      {data.intelligence.incident_summary}
                    </li>
                  )
               ) : (
                 <li className="flex items-start gap-2.5 text-zinc-500">
                   Select a source and run analysis to begin.
                 </li>
               )}
             </ul>
          </div>

          <div className="mt-auto bg-[#18181b] border-none rounded-lg p-5 shadow-inner">
             <p className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest mb-3">Remediation Hints</p>
             <ul className="space-y-3 text-xs text-emerald-400 font-medium">
               {data?.intelligence?.root_cause_hints ? data.intelligence.root_cause_hints.map((hint:string, i:number) => (
                 <li key={i} className="flex items-start gap-2">
                   <Zap size={14} className="shrink-0 mt-0.5 text-emerald-500" />
                   {hint}
                 </li>
               )) : (
                 <li className="flex items-start gap-2 text-zinc-500">
                   <Zap size={14} className="shrink-0 mt-0.5 text-zinc-600" />
                   {loading ? 'Generating remediation strategy...' : 'Pending analysis...'}
                 </li>
               )}
             </ul>
          </div>
          
          <div className="mt-4 flex justify-between text-[10px] text-zinc-600">
            <span>Confidence: 78%</span>
            <span>Model: Llama 3.3-70B Local</span>
          </div>
        </div>

      </div>

      <div className="grid grid-cols-3 gap-6">
        
        {/* Forensics Feed — show ALL clusters */}
        <div className="col-span-2 bg-[#121214] border-none rounded-xl p-6 shadow-sm">
          <h2 className="text-sm font-semibold text-white mb-1">Anomaly Forensics Feed</h2>
          <p className="text-xs text-zinc-500 mb-6">All Semantic Clusters · Sorted by Size</p>
          
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-[10px] font-bold text-zinc-600 uppercase tracking-wider border-b border-transparent">
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
                  <tr>
                    <td colSpan={5} className="py-8 text-center text-zinc-500">
                      <Loader2 size={20} className="animate-spin inline mr-2" />
                      Processing logs... ({elapsedTime}s)
                    </td>
                  </tr>
                ) : data?.clusters?.length ? data.clusters.map((c:any, i:number) => (
                  <tr key={i} className="hover:bg-white/5 transition-colors rounded-lg">
                    <td className="py-3 px-2">
                      <span className={`inline-flex items-center justify-center w-7 h-7 rounded-lg text-[10px] font-bold ${
                        c.cluster_id === -1 
                          ? 'bg-red-500/10 text-red-400 border border-red-500/20' 
                          : 'bg-fuchsia-500/10 text-fuchsia-400 border border-fuchsia-500/20'
                      }`}>
                        {c.cluster_id === -1 ? '⚠' : `C${c.cluster_id}`}
                      </span>
                    </td>
                    <td className="py-3 px-2 font-medium text-white truncate max-w-[280px]" title={c.representative_template}>
                      {c.keyword_flag && <span className="mr-1 text-[9px] font-bold text-red-400">🔴</span>}
                      {c.summary !== "Analyzing..." ? c.summary : c.representative_template}
                    </td>
                    <td className="py-3 text-zinc-400 font-mono">{c.size.toLocaleString()}</td>
                    <td className="py-3 font-bold">
                      <span className={c.anomaly_score > 0.7 ? 'text-red-400' : c.anomaly_score > 0.4 ? 'text-yellow-400' : 'text-zinc-500'}>
                        {c.anomaly_score > 0 ? c.anomaly_score.toFixed(2) : '—'}
                      </span>
                    </td>
                    <td className="py-3">
                      {(() => {
                        const p = c.priority;
                        const styles: Record<string, string> = {
                          P0: 'bg-red-500/20 text-red-300 border border-red-500/30',
                          P1: 'bg-orange-500/20 text-orange-300 border border-orange-500/30',
                          P2: 'bg-yellow-500/20 text-yellow-300 border border-yellow-500/30',
                          P3: 'bg-zinc-800 text-zinc-400 border border-zinc-700',
                        };
                        return (
                          <span className={`px-2 py-1 rounded-sm font-bold text-[9px] uppercase tracking-wider ${
                            styles[p] || styles['P3']
                          }`}>
                            {p || (c.cluster_id === -1 ? 'Noise' : 'P3')}
                          </span>
                        );
                      })()}
                    </td>
                  </tr>
                )) : (
                  <tr>
                    <td colSpan={5} className="py-8 text-center text-zinc-500">
                      {error ? error : 'Select a source and run analysis to populate data.'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Neural Topology */}
        <div className="col-span-1 bg-[#141416] border border-white/5 rounded-xl p-6">
          <h2 className="text-sm font-semibold text-white mb-1">Neural Topology</h2>
          <p className="text-xs text-zinc-500 mb-6">HDBSCAN Projection</p>
          <div className="h-[200px] bg-black/40 rounded-lg border border-white/5 relative">
            <ReactEcharts option={getTopologyOption()} style={{ height: '100%', width: '100%' }} />
          </div>
        </div>

      </div>

    </div>
  );
}
