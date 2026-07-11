'use client';

import React, { useState, useEffect } from 'react';
import { apiFetch } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import { useTimeRange } from '@/context/TimeRangeContext';
import { Activity, Clock, Search, AlertCircle, Server, ChevronRight, ArrowLeft } from 'lucide-react';
import { FlameGraph } from '@/components/FlameGraph';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip as RechartsTooltip, Cell } from 'recharts';

export default function TracesPage() {
 const { toast } = useToast();
 const { timeRange } = useTimeRange();
 const [traces, setTraces] = useState<any[]>([]);
 const [loading, setLoading] = useState(true);
 const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
 const [traceDetail, setTraceDetail] = useState<any>(null);
 const [selectedSpan, setSelectedSpan] = useState<any>(null);
 const [searchTerm, setSearchTerm] = useState('');

 const fetchTraces = async () => {
 setLoading(true);
 try {
 const data = await apiFetch(`/traces?from_ts=${timeRange.from}&to_ts=${timeRange.to}`);
 setTraces(data || []);
 } catch (e: any) {
 toast({ title: 'Error fetching traces', description: e.message, type: 'error' });
 } finally {
 setLoading(false);
 }
 };

 useEffect(() => {
 fetchTraces();
 // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [timeRange]);


 const loadTraceDetail = async (traceId: string) => {
 setSelectedTraceId(traceId);
 try {
 const data = await apiFetch(`/traces/${traceId}`);
 setTraceDetail(data);
 setSelectedSpan(null);
 } catch (e: any) {
 toast({ title: 'Error fetching trace detail', description: e.message, type: 'error' });
 }
 };

 const renderWaterfall = () => {
 if (!traceDetail || !traceDetail.spans) return null;
 return (
 <div className="mt-6 border border-[var(--border)] rounded-lg p-4 bg-[var(--bg-card)] overflow-x-auto">
 <h3 className="font-medium text-[var(--text-primary)] mb-4 flex items-center gap-2">
 <Activity size={18} className="text-blue-500" />
 Trace Flame Graph
 </h3>
 <FlameGraph 
 spans={traceDetail.spans} 
 traceStartTime={traceDetail.start_time} 
 traceDurationMs={traceDetail.duration_ms} 
 onSpanSelect={setSelectedSpan} 
 selectedSpanId={selectedSpan?.span_id || null} 
 />
 </div>
 );
 };

 // Generate latency distribution data for the list view
 const latencyData = traces.length > 0 ? (() => {
 const buckets = [
 { name: '0-50ms', min: 0, max: 50, count: 0 },
 { name: '50-200ms', min: 50, max: 200, count: 0 },
 { name: '200-500ms', min: 200, max: 500, count: 0 },
 { name: '500-1s', min: 500, max: 1000, count: 0 },
 { name: '>1s', min: 1000, max: Infinity, count: 0 }
 ];
 traces.forEach(t => {
 const dur = t.duration_ms;
 const bucket = buckets.find(b => dur >= b.min && dur < b.max);
 if (bucket) bucket.count++;
 });
 return buckets;
 })() : [];

 const filteredTraces = traces.filter((t) => {
 if (!searchTerm.trim()) return true;
 const terms = searchTerm.toLowerCase().split(/\s+/);
 
 return terms.every(term => {
 if (term.startsWith('service:')) {
 return t.root_service.toLowerCase().includes(term.split(':')[1]);
 }
 if (term.startsWith('status:')) {
 const status = term.split(':')[1];
 if (status === 'error') return t.error_count > 0;
 if (status === 'ok') return t.error_count === 0;
 }
 return (
 t.trace_id.toLowerCase().includes(term) ||
 t.root_service.toLowerCase().includes(term) ||
 t.root_operation.toLowerCase().includes(term)
 );
 });
 });

 return (
 <div className="flex flex-col h-full space-y-6">
 <div className="flex items-center justify-between">
 <div>
 <h1 className="text-2xl font-bold text-[var(--text-primary)]">Distributed Tracing</h1>
 <p className="text-[var(--text-secondary)] mt-1">OpenTelemetry trace visualization and waterfall analysis.</p>
 </div>
 </div>

 {selectedTraceId && traceDetail ? (
 <div className="flex flex-col h-full gap-6">
 <button 
 onClick={() => setSelectedTraceId(null)}
 className="flex items-center gap-2 text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] w-fit"
 >
 <ArrowLeft size={16} /> Back to Traces
 </button>
 
 <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
 <div className="md:col-span-3">
 <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-6">
 <div className="flex justify-between items-start mb-6">
 <div>
 <h2 className="text-xl font-mono font-bold text-[var(--text-primary)] mb-2">{traceDetail.trace_id}</h2>
 <div className="flex gap-4 text-sm text-[var(--text-secondary)]">
 <span className="flex items-center gap-1"><Server size={14} /> {traceDetail.root_service}</span>
 <span className="flex items-center gap-1"><Activity size={14} /> {traceDetail.root_operation}</span>
 <span className="flex items-center gap-1"><Clock size={14} /> {new Date(traceDetail.start_time).toLocaleString()}</span>
 </div>
 </div>
 <div className="text-right">
 <div className="text-2xl font-bold text-[var(--text-primary)]">{traceDetail.duration_ms.toFixed(2)}ms</div>
 <div className="text-sm text-[var(--text-secondary)]">{traceDetail.span_count} spans</div>
 </div>
 </div>
 
 {renderWaterfall()}
 </div>
 </div>
 
 <div className="md:col-span-1">
 <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4 h-full overflow-y-auto max-h-[600px]">
 <h3 className="font-medium text-[var(--text-primary)] mb-4">Span Details</h3>
 {selectedSpan ? (
 <div className="space-y-4">
 <div>
 <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-1">Operation</div>
 <div className="font-mono text-sm break-words text-[var(--text-primary)]">{selectedSpan.operation_name}</div>
 </div>
 <div>
 <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-1">Service</div>
 <div className="text-sm text-[var(--text-primary)]">{selectedSpan.service_name}</div>
 </div>
 <div>
 <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-1">Duration</div>
 <div className="text-sm text-[var(--text-primary)]">{selectedSpan.duration_ms.toFixed(2)}ms</div>
 </div>
 {selectedSpan.status_code === 'ERROR' && (
 <div className="text-red-400 text-sm font-medium flex items-center gap-1">
 <AlertCircle size={14} /> ERROR
 </div>
 )}
 
 {Object.keys(selectedSpan.attributes || {}).length > 0 && (
 <div className="mt-4">
 <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-2">Attributes</div>
 <div className="space-y-2">
 {Object.entries(selectedSpan.attributes).map(([k, v]) => (
 <div key={k} className="text-xs grid grid-cols-3 gap-2">
 <span className="text-[var(--text-secondary)] truncate" title={k}>{k}</span>
 <span className="col-span-2 font-mono text-[var(--text-primary)] break-all">{String(v)}</span>
 </div>
 ))}
 </div>
 </div>
 )}

 {(selectedSpan.events || []).length > 0 && (
 <div className="mt-4">
 <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-2">Events</div>
 <div className="space-y-2 border-l-2 border-blue-500 pl-3">
 {selectedSpan.events.map((ev: any, i: number) => (
 <div key={i} className="text-xs mb-2">
 <div className="text-[var(--text-primary)] font-medium">{ev.name}</div>
 <div className="text-[var(--text-secondary)]">{new Date(ev.timestamp).toLocaleTimeString()}</div>
 </div>
 ))}
 </div>
 </div>
 )}
 </div>
 ) : (
 <div className="text-center text-[var(--text-secondary)] py-8 text-sm">
 Select a span from the waterfall to view details.
 </div>
 )}
 </div>
 </div>
 </div>
 </div>
 ) : (
 <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg overflow-hidden">
 <div className="p-4 border-b border-[var(--border)] flex gap-4">
 <div className="relative flex-1">
 <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-secondary)]" size={16} />
 <input 
 type="text" 
 placeholder="Search traces (e.g., service:payment status:error)..." 
 value={searchTerm}
 onChange={(e) => setSearchTerm(e.target.value)}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 pl-10 pr-4 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-blue-500"
 />
 </div>
 </div>
 
 {latencyData.length > 0 && !loading && (
 <div className="p-4 border-b border-[var(--border)] bg-[var(--bg-surface)]">
 <h3 className="text-xs font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-2">Latency Distribution</h3>
 <div className="h-24 w-full">
 <ResponsiveContainer width="100%" height="100%">
 <BarChart data={latencyData} margin={{ top: 5, right: 0, left: -25, bottom: 0 }}>
 <XAxis dataKey="name" axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: 'var(--text-muted)' }} />
 <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: 'var(--text-muted)' }} />
 <RechartsTooltip 
 contentStyle={{ backgroundColor: 'var(--bg-card)', borderColor: 'var(--border)', fontSize: '12px' }}
 itemStyle={{ color: 'var(--text-primary)' }}
 cursor={{ fill: 'var(--bg-surface-hover)' }}
 />
 <Bar dataKey="count" radius={[2, 2, 0, 0]}>
 {latencyData.map((entry, index) => (
 <Cell key={`cell-${index}`} fill={index > 2 ? '#ef4444' : '#3b82f6'} />
 ))}
 </Bar>
 </BarChart>
 </ResponsiveContainer>
 </div>
 </div>
 )}

 <div className="overflow-x-auto">
 <table className="w-full text-left text-sm text-[var(--text-secondary)]">
 <thead className="bg-[var(--bg-app)] text-xs uppercase border-b border-[var(--border)]">
 <tr>
 <th className="px-6 py-3 font-medium">Trace ID</th>
 <th className="px-6 py-3 font-medium">Service & Operation</th>
 <th className="px-6 py-3 font-medium">Start Time</th>
 <th className="px-6 py-3 font-medium">Duration</th>
 <th className="px-6 py-3 font-medium">Spans</th>
 <th className="px-6 py-3 font-medium">Status</th>
 <th className="px-6 py-3 font-medium text-right">Action</th>
 </tr>
 </thead>
 <tbody className="divide-y divide-[var(--border)]">
 {loading ? (
 // Shimmer loading
 [...Array(5)].map((_, i) => (
 <tr key={i} className="animate-pulse">
 <td className="px-6 py-4"><div className="h-4 bg-[var(--border)] rounded w-24"></div></td>
 <td className="px-6 py-4"><div className="h-4 bg-[var(--border)] rounded w-48"></div></td>
 <td className="px-6 py-4"><div className="h-4 bg-[var(--border)] rounded w-32"></div></td>
 <td className="px-6 py-4"><div className="h-4 bg-[var(--border)] rounded w-16"></div></td>
 <td className="px-6 py-4"><div className="h-4 bg-[var(--border)] rounded w-8"></div></td>
 <td className="px-6 py-4"><div className="h-4 bg-[var(--border)] rounded w-12"></div></td>
 <td className="px-6 py-4 text-right"><div className="h-4 bg-[var(--border)] rounded w-16 ml-auto"></div></td>
 </tr>
 ))
 ) : filteredTraces.length === 0 ? (
 <tr>
 <td colSpan={7} className="px-6 py-12 text-center">
 <Activity className="mx-auto mb-3 text-[var(--text-secondary)] opacity-50" size={32} />
 <p className="text-[var(--text-primary)] font-medium">No traces match your search</p>
 </td>
 </tr>
 ) : (
 filteredTraces.map((trace) => (
 <tr key={trace.trace_id} className="hover:bg-[var(--bg-app)] transition-colors">
 <td className="px-6 py-4 font-mono text-xs text-[var(--text-primary)]">{trace.trace_id.substring(0, 16)}...</td>
 <td className="px-6 py-4">
 <div className="font-medium text-[var(--text-primary)]">{trace.root_operation}</div>
 <div className="text-xs">{trace.root_service}</div>
 </td>
 <td className="px-6 py-4">{new Date(trace.start_time).toLocaleString()}</td>
 <td className="px-6 py-4">{trace.duration_ms.toFixed(2)}ms</td>
 <td className="px-6 py-4">{trace.span_count}</td>
 <td className="px-6 py-4">
 {trace.error_count > 0 ? (
 <span className="inline-flex items-center gap-1 text-red-400 bg-red-400/10 px-2 py-0.5 rounded text-xs">
 <AlertCircle size={12} /> {trace.error_count} errors
 </span>
 ) : (
 <span className="text-green-500 bg-green-500/10 px-2 py-0.5 rounded text-xs">OK</span>
 )}
 </td>
 <td className="px-6 py-4 text-right">
 <button 
 onClick={() => loadTraceDetail(trace.trace_id)}
 className="text-blue-500 hover:text-blue-400 font-medium inline-flex items-center gap-1 text-sm"
 >
 View <ChevronRight size={16} />
 </button>
 </td>
 </tr>
 ))
 )}
 </tbody>
 </table>
 </div>
 </div>
 )}
 </div>
 );
}
