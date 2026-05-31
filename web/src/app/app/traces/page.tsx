'use client';

import React, { useState, useEffect } from 'react';
import { apiFetch } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import { Activity, Clock, Search, AlertCircle, Server, ChevronRight, ArrowLeft } from 'lucide-react';

export default function TracesPage() {
  const { toast } = useToast();
  const [traces, setTraces] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const [traceDetail, setTraceDetail] = useState<any>(null);
  const [selectedSpan, setSelectedSpan] = useState<any>(null);

  const fetchTraces = async () => {
    setLoading(true);
    try {
      const data = await apiFetch('/traces');
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
  }, []);


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
    
    // Sort spans by start time
    const spans = [...traceDetail.spans].sort((a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime());
    const traceStart = new Date(traceDetail.start_time).getTime();
    const traceDuration = traceDetail.duration_ms;

    // Calculate depth
    const spanMap = new Map();
    spans.forEach(s => spanMap.set(s.span_id, s));
    
    const getDepth = (spanId: string): number => {
      let depth = 0;
      let current = spanMap.get(spanId);
      while (current && current.parent_span_id) {
        depth++;
        current = spanMap.get(current.parent_span_id);
      }
      return depth;
    };

    return (
      <div className="flex flex-col gap-1 mt-6 border border-[var(--border)] rounded-lg p-4 bg-[var(--bg-card)] overflow-x-auto">
        <h3 className="font-medium text-[var(--text-primary)] mb-4 flex items-center gap-2">
          <Activity size={18} className="text-blue-500" />
          Trace Waterfall
        </h3>
        
        {/* Timeline header */}
        <div className="relative h-6 border-b border-[var(--border)] mb-2 text-xs text-[var(--text-secondary)]">
          <span className="absolute left-0">0ms</span>
          <span className="absolute right-0">{traceDuration.toFixed(2)}ms</span>
        </div>

        {spans.map(span => {
          const startOffset = new Date(span.start_time).getTime() - traceStart;
          const leftPercent = traceDuration > 0 ? (startOffset / traceDuration) * 100 : 0;
          const widthPercent = traceDuration > 0 ? Math.max((span.duration_ms / traceDuration) * 100, 0.5) : 100;
          const depth = getDepth(span.span_id);
          const hasError = span.status_code === 'ERROR';

          return (
            <div 
              key={span.span_id} 
              className={`relative flex items-center py-1 px-2 hover:bg-[var(--bg-app)] cursor-pointer rounded ${selectedSpan?.span_id === span.span_id ? 'bg-[var(--bg-app)] ring-1 ring-blue-500' : ''}`}
              onClick={() => setSelectedSpan(span)}
            >
              <div className="w-1/3 truncate pr-4" style={{ paddingLeft: `${depth * 16}px` }}>
                <span className={`text-xs font-medium ${hasError ? 'text-red-400' : 'text-[var(--text-primary)]'}`}>
                  {span.operation_name}
                </span>
                <span className="text-[10px] text-[var(--text-secondary)] ml-2">{span.service_name}</span>
              </div>
              <div className="w-2/3 relative h-6 border-l border-[var(--border)]">
                <div 
                  className={`absolute h-4 top-1 rounded-sm ${hasError ? 'bg-red-500/80' : 'bg-blue-500/80'} shadow-sm transition-all hover:brightness-110`}
                  style={{ left: `${leftPercent}%`, width: `${widthPercent}%` }}
                />
                <span className="absolute text-[10px] text-[var(--text-secondary)] top-1" style={{ left: `calc(${leftPercent + widthPercent}% + 8px)` }}>
                  {span.duration_ms.toFixed(2)}ms
                </span>
              </div>
            </div>
          );
        })}
      </div>
    );
  };

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
                className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 pl-10 pr-4 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
          </div>
          
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
                ) : traces.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-6 py-12 text-center">
                      <Activity className="mx-auto mb-3 text-[var(--text-secondary)] opacity-50" size={32} />
                      <p className="text-[var(--text-primary)] font-medium">No traces found</p>
                      <p className="text-sm mt-1">Send OTLP traces to /v1/traces to see them here.</p>
                    </td>
                  </tr>
                ) : (
                  traces.map((trace) => (
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
