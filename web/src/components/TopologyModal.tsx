'use client';

import React, { useEffect } from 'react';
import { X } from 'lucide-react';
import ReactEcharts from 'echarts-for-react';

interface TopologyModalProps {
 isOpen: boolean;
 onClose: () => void;
 /** The same ECharts option the card renders, re-tuned here for the larger canvas. */
 option: any;
 title?: string;
 subtitle?: string;
}

const cssVar = (name: string, fallback: string) => {
 if (typeof window === 'undefined') return fallback;
 const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
 return v || fallback;
};

// The card option is sized for a ~200px panel: 9px legend, hidden axes, no
// tooltip. At full screen those choices read as a shrunken thumbnail, so
// re-tune here rather than asking every caller to keep a second option object.
function expandedOption(option: any) {
 if (!option) return option;
 const symbolSize = option.series?.[0]?.symbolSize;
 const muted = cssVar('--text-muted', '#6a717a');
 const line = cssVar('--border-subtle', '#e3e6ea');

 // UMAP coordinates are not anchored at zero, and ECharts value axes include
 // the origin unless told otherwise — that is what packs every cluster into
 // one corner. `scale: true` fits the axes to the data instead.
 const axis = (name: string) => ({
 show: true,
 type: 'value',
 scale: true,
 name,
 nameTextStyle: { color: muted, fontSize: 10 },
 axisLine: { show: false },
 axisTick: { show: false },
 axisLabel: { color: muted, fontSize: 10, formatter: (v: number) => v.toFixed(1) },
 splitLine: { show: true, lineStyle: { color: line, type: 'dashed', opacity: 0.7 } },
 });

 return {
 ...option,
 tooltip: {
 show: true,
 trigger: 'item',
 formatter: (p: any) => {
 const [x, y] = p.data ?? [];
 return `<strong>${p.seriesName}</strong><br/>x ${Number(x).toFixed(3)} · y ${Number(y).toFixed(3)}`;
 },
 },
 legend: {
 ...(option.legend || {}),
 bottom: 8,
 left: 'center',
 textStyle: { ...(option.legend?.textStyle || {}), fontSize: 12 },
 itemGap: 18,
 },
 grid: { top: 28, bottom: 64, left: 56, right: 32 },
 xAxis: { ...axis('UMAP-1'), nameLocation: 'middle', nameGap: 26 },
 yAxis: { ...axis('UMAP-2'), nameLocation: 'middle', nameGap: 38 },
 // Scroll/drag to inspect dense regions once the axes fit the data.
 dataZoom: [
 { type: 'inside', xAxisIndex: 0, filterMode: 'none' },
 { type: 'inside', yAxisIndex: 0, filterMode: 'none' },
 ],
 series: (option.series || []).map((s: any) => ({
 ...s,
 symbolSize: typeof symbolSize === 'function' ? (d: any) => symbolSize(d) * 1.6 : s.symbolSize,
 })),
 };
}

export function TopologyModal({
 isOpen,
 onClose,
 option,
 title = 'Neural Topology',
 subtitle = 'HDBSCAN Projection',
}: TopologyModalProps) {
 // Escape closes, and the page behind must not scroll while the overlay is up.
 useEffect(() => {
 if (!isOpen) return;
 const onKey = (e: KeyboardEvent) => {
 if (e.key === 'Escape') onClose();
 };
 window.addEventListener('keydown', onKey);
 const previousOverflow = document.body.style.overflow;
 document.body.style.overflow = 'hidden';
 return () => {
 window.removeEventListener('keydown', onKey);
 document.body.style.overflow = previousOverflow;
 };
 }, [isOpen, onClose]);

 if (!isOpen) return null;

 return (
 <div
 className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-[250] p-4 sm:p-8"
 onClick={onClose}
 role="dialog"
 aria-modal="true"
 aria-label={title}
 >
 <div
 className="bg-[var(--bg-card)] border border-[var(--border)] rounded-2xl shadow-2xl w-full max-w-6xl h-[85vh] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-150"
 onClick={(e) => e.stopPropagation()}
 >
 <div className="p-4 border-b border-[var(--border)] flex justify-between items-center bg-[var(--bg-app)] shrink-0">
 <div>
 <h2 className="text-sm font-semibold text-[var(--text-primary)]">{title}</h2>
 <p className="text-xs text-[var(--text-muted)]">{subtitle}</p>
 </div>
 <button
 onClick={onClose}
 aria-label="Close"
 className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors border-none bg-transparent cursor-pointer"
 >
 <X size={20} />
 </button>
 </div>
 <div className="flex-1 p-4 min-h-0">
 <div className="h-full w-full bg-[var(--bg-inset)] rounded-lg border border-[var(--border-subtle)]">
 <ReactEcharts
 option={expandedOption(option)}
 style={{ height: '100%', width: '100%' }}
 opts={{ renderer: 'canvas' }}
 />
 </div>
 </div>
 </div>
 </div>
 );
}
