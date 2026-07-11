import React, { useState } from 'react';

interface Span {
 span_id: string;
 parent_span_id: string | null;
 operation_name: string;
 service_name: string;
 start_time: string;
 duration_ms: number;
 status_code: string;
 attributes: Record<string, any>;
 events: any[];
}

interface FlameGraphProps {
 spans: Span[];
 traceStartTime: string;
 traceDurationMs: number;
 onSpanSelect: (span: Span) => void;
 selectedSpanId: string | null;
}

export function FlameGraph({ spans, traceStartTime, traceDurationMs, onSpanSelect, selectedSpanId }: FlameGraphProps) {
 const [zoomLevel, setZoomLevel] = useState(1);
 const [panOffset, setPanOffset] = useState(0);

 if (!spans || spans.length === 0) return null;

 // Sort by start time
 const sortedSpans = [...spans].sort((a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime());
 const traceStartTs = new Date(traceStartTime).getTime();

 // Depth calculation
 const spanMap = new Map<string, Span>();
 sortedSpans.forEach(s => spanMap.set(s.span_id, s));

 const getDepth = (spanId: string): number => {
 let depth = 0;
 let current = spanMap.get(spanId);
 while (current && current.parent_span_id) {
 depth++;
 current = spanMap.get(current.parent_span_id);
 }
 return depth;
 };

 const calculateColor = (serviceName: string) => {
 let hash = 0;
 for (let i = 0; i < serviceName.length; i++) {
 hash = serviceName.charCodeAt(i) + ((hash << 5) - hash);
 }
 const hue = Math.abs(hash % 360);
 return `hsl(${hue}, 70%, 60%)`;
 };

 return (
 <div className="flex flex-col w-full font-sans">
 <div className="flex justify-between items-center mb-2">
 <div className="text-xs font-semibold text-[var(--text-secondary)]">
 Total Duration: {traceDurationMs.toFixed(2)}ms
 </div>
 <div className="flex gap-2">
 <button 
 onClick={() => setZoomLevel(z => Math.min(z * 1.5, 10))}
 className="p-1 rounded bg-[var(--bg-surface)] border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
 >
 Zoom In
 </button>
 <button 
 onClick={() => {
 setZoomLevel(1);
 setPanOffset(0);
 }}
 className="p-1 rounded bg-[var(--bg-surface)] border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
 >
 Reset
 </button>
 <button 
 onClick={() => setZoomLevel(z => Math.max(z / 1.5, 1))}
 className="p-1 rounded bg-[var(--bg-surface)] border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
 >
 Zoom Out
 </button>
 </div>
 </div>

 <div className="relative border border-[var(--border)] rounded bg-[var(--bg-app)] overflow-x-auto overflow-y-auto max-h-[400px]">
 <div className="relative min-w-full pb-8" style={{ width: `${100 * zoomLevel}%`, transform: `translateX(${panOffset}px)` }}>
 {/* Timeline Ruler */}
 <div className="sticky top-0 h-6 border-b border-[var(--border)] bg-[var(--bg-app)] z-10 text-[10px] text-[var(--text-muted)] flex items-end pb-1 overflow-hidden">
 <span className="absolute left-1">0ms</span>
 <span className="absolute right-1">{traceDurationMs.toFixed(2)}ms</span>
 {/* Ticks could be added here */}
 </div>

 <div className="pt-2">
 {sortedSpans.map(span => {
 const startOffset = new Date(span.start_time).getTime() - traceStartTs;
 const leftPercent = traceDurationMs > 0 ? (startOffset / traceDurationMs) * 100 : 0;
 const widthPercent = traceDurationMs > 0 ? Math.max((span.duration_ms / traceDurationMs) * 100, 0.5) : 100;
 const depth = getDepth(span.span_id);
 const hasError = span.status_code === 'ERROR';
 const isSelected = selectedSpanId === span.span_id;

 return (
 <div 
 key={span.span_id} 
 className={`relative flex items-center h-7 group cursor-pointer mb-1 ${isSelected ? 'ring-1 ring-blue-500 bg-blue-500/10' : 'hover:bg-[var(--bg-surface-hover)]'}`}
 onClick={() => onSpanSelect(span)}
 >
 <div className="w-1/4 shrink-0 truncate pr-2 border-r border-[var(--border)] relative flex items-center h-full z-10" style={{ paddingLeft: `${Math.max(4, depth * 12)}px` }}>
 <div className="w-2 h-2 rounded-full mr-2 shrink-0" style={{ backgroundColor: hasError ? 'red' : calculateColor(span.service_name) }} />
 <span className={`text-[11px] font-medium truncate ${hasError ? 'text-red-400' : 'text-[var(--text-primary)]'}`}>
 {span.operation_name}
 </span>
 </div>
 
 <div className="w-3/4 relative h-full flex items-center shrink-0">
 <div 
 className="absolute h-5 rounded-sm shadow-sm transition-all overflow-hidden"
 style={{ 
 left: `${leftPercent}%`, 
 width: `${widthPercent}%`,
 backgroundColor: hasError ? 'rgba(239, 68, 68, 0.8)' : calculateColor(span.service_name),
 opacity: isSelected ? 1 : 0.8,
 boxShadow: isSelected ? '0 0 0 2px var(--bg-app), 0 0 0 4px var(--primary)' : 'none'
 }}
 >
 {/* Only show text if width is enough */}
 {widthPercent > 10 && (
 <span className="px-2 text-[10px] font-semibold text-white truncate leading-5 mix-blend-screen">
 {span.duration_ms.toFixed(1)}ms
 </span>
 )}
 </div>
 </div>
 </div>
 );
 })}
 </div>
 </div>
 </div>
 </div>
 );
}
