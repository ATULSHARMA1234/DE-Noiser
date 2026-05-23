'use client';

import React, { useState, useEffect, useRef } from 'react';
import ReactEcharts from 'echarts-for-react';
import { 
  Title, Text, Flex, Badge, Divider, Button, 
  TabGroup, TabList, Tab, TabPanels, TabPanel, Card, Grid,
  ProgressBar
} from '@tremor/react';
import { 
  Zap, ShieldCheck, RefreshCw, Terminal, Activity, BarChart3, 
  Database, History, TrendingUp, Cpu, LayoutDashboard, AlertTriangle, 
  Settings, Search, Bell, ShieldAlert, Network
} from 'lucide-react';

const StatusBadge = ({ label }: { label: string }) => {
    const colors: Record<string, string> = {
        'high_risk_anomaly': 'bg-red-500/20 text-red-400 border-red-500/50',
        'outlier': 'bg-red-500/20 text-red-400 border-red-500/50',
        'anomaly': 'bg-red-500/20 text-red-400 border-red-500/50',
        'new_pattern': 'bg-yellow-500/20 text-yellow-400 border-yellow-500/50',
        'known': 'bg-emerald-500/20 text-emerald-400 border-emerald-500/50',
    };
    return (
        <span className={`px-2 py-1 rounded-[4px] text-[9px] font-black border uppercase tracking-wider ${colors[label] || 'bg-slate-500/20 text-slate-400 border-slate-500/50'}`}>
            {label?.replace('_', ' ') || 'UNKNOWN'}
        </span>
    );
};

export default function Dashboard({ userRole }: { userRole: 'admin' | 'sre' | 'viewer' | null }) {
    const [activeTab, setActiveTab] = useState(0);
    const [loading, setLoading] = useState(false);
    const [data, setData] = useState<any>(null);
    const [liveLogs, setLiveLogs] = useState<string[]>([]);
    const [metrics, setMetrics] = useState<any[]>(Array.from({ length: 30 }, (_, i) => ({ time: i, cpu: 15 + Math.random() * 5, mem: 40 + Math.random() * 5 })));
    const scrollRef = useRef<HTMLDivElement>(null);

    const runAnalysis = async () => {
        setLoading(true);
        try {
            const response = await fetch('http://localhost:8000/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source: 'data/stress_test_1M.log', intelligence: true })
            });
            const result = await response.json();
            setData(result);
            setLiveLogs(prev => [...prev, `[NEURAL ENGINE] Signal Synchronization Successful.`]);
        } catch (err) {
            setLiveLogs(prev => [...prev, `[ERROR] Analysis connection failed.`]);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        runAnalysis();
        const interval = setInterval(() => {
            setLiveLogs(prev => [...prev.slice(-15), `[HEARTBEAT] Signal Pulse @ ${new Date().toLocaleTimeString()}`]);
        }, 5000);
        return () => clearInterval(interval);
    }, []);

    useEffect(() => {
        if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }, [liveLogs]);

    const getTopologyOption = () => {
        if (!data?.clusters) return {};
        const scatterData = data.clusters.map((c: any) => [
            Math.random() * 10, Math.random() * 10, c.size, c.summary,
            c.cluster_id === -1 ? '#f43f5e' : '#10b981'
        ]);
        return {
            backgroundColor: 'transparent',
            tooltip: { trigger: 'item' },
            grid: { top: 10, bottom: 10, left: 10, right: 10 },
            xAxis: { show: false }, yAxis: { show: false },
            series: [{
                type: 'scatter',
                symbolSize: (val: any) => Math.log(val[2] + 1) * 15,
                data: scatterData,
                itemStyle: { color: (p: any) => p.data[4], opacity: 0.6 }
            }]
        };
    };

    const getMetricOption = () => ({
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis' },
        grid: { top: 40, bottom: 20, left: 40, right: 20 },
        xAxis: { type: 'category', data: metrics.map(m => m.time), axisLine: { lineStyle: { color: '#334155' } } },
        yAxis: { type: 'value', splitLine: { lineStyle: { color: '#1e293b' } } },
        series: [
            { name: 'CPU %', type: 'line', smooth: true, data: metrics.map(m => m.cpu), itemStyle: { color: '#3b82f6' } },
            { name: 'Memory %', type: 'line', smooth: true, data: metrics.map(m => m.mem), itemStyle: { color: '#10b981' } }
        ]
    });

    return (
        <div className="flex h-screen bg-[#020617] text-[#f8fafc] font-sans overflow-hidden">
            
            {/* SIDEBAR */}
            <aside className="w-20 lg:w-64 border-r border-white/5 bg-slate-950/50 backdrop-blur-3xl flex flex-col p-6 gap-8 z-50">
                <div className="flex items-center gap-3">
                    <div className="w-10 h-10 bg-cyan-500 rounded-xl flex items-center justify-center">
                        <Zap size={20} className="text-white" />
                    </div>
                    <h1 className="hidden lg:block text-lg font-black tracking-tighter">SemanticOS</h1>
                </div>

                <nav className="flex flex-col gap-2 w-full">
                    {[
                        { icon: LayoutDashboard, label: 'Pulse', tab: 0 },
                        { icon: History, label: 'Archives', tab: 1 },
                        { icon: Bell, label: 'Alerts', tab: 0 },
                    ].map((item, i) => (
                        <button 
                            key={i}
                            onClick={() => setActiveTab(item.tab)}
                            className={`flex items-center gap-4 px-4 py-3 rounded-xl transition-all ${activeTab === item.tab ? 'bg-cyan-500 text-white' : 'text-slate-500 hover:bg-white/5'}`}
                        >
                            <item.icon size={20} />
                            <span className="hidden lg:block text-[10px] font-black uppercase tracking-widest">{item.label}</span>
                        </button>
                    ))}
                </nav>
            </aside>

            {/* MAIN */}
            <main className="flex-1 flex flex-col overflow-hidden relative">
                <header className="h-20 border-b border-white/5 bg-slate-950/20 backdrop-blur-md flex items-center justify-between px-10 shrink-0 z-40">
                    <div className="flex items-center gap-4">
                        <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                        <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Neural Agent ACTIVE</span>
                    </div>
                    <Button 
                        onClick={runAnalysis}
                        loading={loading}
                        className="bg-cyan-600 hover:bg-cyan-500 border-none px-6 rounded-xl font-black uppercase tracking-widest text-[10px]"
                        icon={RefreshCw}
                    >
                        Scan Signal
                    </Button>
                </header>

                <div className="flex-1 overflow-y-auto p-10">
                    <TabGroup index={activeTab} onIndexChange={setActiveTab}>
                        <TabPanels>
                            <TabPanel>
                                <Grid numItemsLg={3} className="gap-8">
                                    {/* AI Insights Card */}
                                    <Card className="lg:col-span-2 bg-slate-900/40 border border-white/5 p-10 rounded-[2.5rem] shadow-2xl overflow-hidden relative">
                                        <div className="absolute top-0 right-0 p-10 opacity-[0.03] pointer-events-none"><Zap size={200} /></div>
                                        <h3 className="text-xs font-black uppercase tracking-widest text-cyan-500 mb-8">Incident Intelligence</h3>
                                        <p className="text-3xl font-bold text-white tracking-tight leading-tight mb-10">
                                            {data?.intelligence?.incident_summary || "Scanning signal stream..."}
                                        </p>
                                        <Flex className="gap-8 justify-start">
                                            <div>
                                                <p className="text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1">Failure Domain</p>
                                                <p className="text-2xl font-black text-white tracking-tighter">{data?.intelligence?.failure_domain || "N/A"}</p>
                                            </div>
                                            <div className="w-[1px] h-10 bg-white/10" />
                                            <div>
                                                <p className="text-[9px] font-black text-slate-500 uppercase tracking-widest mb-1">Confidence</p>
                                                <p className="text-2xl font-black text-emerald-400 tracking-tighter">98.4%</p>
                                            </div>
                                        </Flex>
                                    </Card>

                                    {/* System Health */}
                                    <Card className="bg-slate-900/40 border border-white/5 p-10 rounded-[2.5rem] flex flex-col items-center justify-center">
                                        <h3 className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-8">Anomaly Index</h3>
                                        <div className="relative w-40 h-40 flex items-center justify-center">
                                            <svg className="w-full h-full transform -rotate-90">
                                                <circle cx="80" cy="80" r="70" stroke="currentColor" strokeWidth="10" fill="transparent" className="text-slate-800" />
                                                <circle cx="80" cy="80" r="70" stroke="currentColor" strokeWidth="10" fill="transparent" className="text-rose-500" strokeDasharray={440} strokeDashoffset={440 - (440 * (data ? 0.85 : 0.1))} />
                                            </svg>
                                            <span className="absolute text-5xl font-black text-white">{data ? 85 : 0}</span>
                                        </div>
                                    </Card>

                                    {/* Metrics Chart */}
                                    <Card className="lg:col-span-3 bg-slate-900/40 border border-white/5 rounded-[2.5rem] p-10 h-[400px]">
                                        <h3 className="text-xs font-black uppercase tracking-widest text-slate-500 mb-8">Metrics Correlation</h3>
                                        <ReactEcharts option={getMetricOption()} style={{ height: '300px', width: '100%' }} />
                                    </Card>

                                    {/* Table */}
                                    <Card className="lg:col-span-3 bg-slate-900/40 border border-white/5 rounded-[2.5rem] overflow-hidden">
                                        <table className="w-full text-left">
                                            <thead className="bg-slate-950/30">
                                                <tr>
                                                    <th className="p-6 text-[10px] font-black text-slate-500 uppercase tracking-widest">Signal</th>
                                                    <th className="p-6 text-[10px] font-black text-slate-500 uppercase tracking-widest">Weight</th>
                                                    <th className="p-6 text-[10px] font-black text-slate-500 uppercase tracking-widest">Label</th>
                                                    <th className="p-6 text-[10px] font-black text-slate-500 uppercase tracking-widest">Diagnosis</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {data?.clusters ? data.clusters.map((c: any) => (
                                                    <tr key={c.id} className="border-b border-white/5 hover:bg-white/5">
                                                        <td className="p-6 font-mono text-cyan-500 font-bold text-xs px-8">SIG-{c.id}</td>
                                                        <td className="p-6 text-sm font-bold text-white">{c.size?.toLocaleString()}</td>
                                                        <td className="p-6"><StatusBadge label={c.cluster_id === -1 ? 'outlier' : 'known'} /></td>
                                                        <td className="p-6 text-sm font-bold text-slate-100">{c.summary || "Analysing..."}</td>
                                                    </tr>
                                                )) : (
                                                    <tr><td colSpan={4} className="p-20 text-center text-slate-500 uppercase tracking-widest text-[10px] animate-pulse">Initializing Neural Engine...</td></tr>
                                                )}
                                            </tbody>
                                        </table>
                                    </Card>
                                </Grid>
                            </TabPanel>

                            <TabPanel>
                                <div className="space-y-6">
                                    <h2 className="text-2xl font-black text-white tracking-tighter">Memory Bank</h2>
                                    {[1, 2, 3].map(i => (
                                        <div key={i} className="p-6 bg-white/5 rounded-2xl flex justify-between items-center border border-transparent hover:border-cyan-500/50 transition-all cursor-pointer">
                                            <div className="flex items-center gap-4">
                                                <div className="p-3 bg-cyan-500/10 rounded-xl text-cyan-400"><History size={20} /></div>
                                                <div>
                                                    <p className="text-white font-bold">Analysis Snapshot #{i}00X</p>
                                                    <p className="text-[9px] text-slate-500 uppercase tracking-widest font-black">2026-05-09</p>
                                                </div>
                                            </div>
                                            <Badge color="emerald">ARCHIVED</Badge>
                                        </div>
                                    ))}
                                </div>
                            </TabPanel>
                        </TabPanels>
                    </TabGroup>
                </div>

                <div className="fixed bottom-0 right-0 left-20 lg:left-64 h-56 bg-[#020617]/95 backdrop-blur-3xl border-t border-white/5 p-8 overflow-hidden z-50">
                    <div className="flex items-center gap-3 mb-4 border-b border-white/5 pb-3">
                        <Terminal size={14} className="text-cyan-500" />
                        <span className="font-mono text-[10px] font-black uppercase tracking-widest text-slate-500">Neural Signal Stream</span>
                    </div>
                    <div ref={scrollRef} className="h-full overflow-y-auto space-y-1 font-mono text-[10px] text-slate-500 scrollbar-hide">
                        {liveLogs.map((log, i) => (
                            <div key={i} className="flex gap-6 opacity-60">
                                <span className="text-slate-700">[{new Date().toLocaleTimeString()}]</span>
                                <span className={log.includes('ERROR') ? 'text-rose-500' : (log.includes('SIGNAL') ? 'text-cyan-400 font-bold' : '')}>{log}</span>
                            </div>
                        ))}
                    </div>
                </div>
            </main>
        </div>
    );
}
