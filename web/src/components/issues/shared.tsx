'use client';

import React from 'react';

/** Issue triage states, in the order a triage queue works through them. */
export const ISSUE_STATES = ['FOR_REVIEW', 'REVIEWED', 'IGNORED', 'RESOLVED'] as const;

export const STATE_LABELS: Record<string, string> = {
 FOR_REVIEW: 'For Review',
 REVIEWED: 'Reviewed',
 IGNORED: 'Ignored',
 RESOLVED: 'Resolved',
};

// A state is a workflow position, not a severity — colouring them all red (or
// all grey) makes the queue unreadable at a glance.
export const STATE_STYLES: Record<string, string> = {
 FOR_REVIEW: 'text-red-600 bg-red-500/10 border-red-500/30',
 REVIEWED: 'text-blue-600 bg-blue-500/10 border-blue-500/30',
 IGNORED: 'text-[var(--text-muted)] bg-[var(--bg-inset)] border-[var(--border-subtle)]',
 RESOLVED: 'text-emerald-600 bg-emerald-500/10 border-emerald-500/30',
};

export const SEVERITY_STYLES: Record<string, string> = {
 P0: 'text-red-500 bg-red-500/15 border-red-500/30',
 P1: 'text-orange-500 bg-orange-500/15 border-orange-500/30',
 P2: 'text-yellow-600 bg-yellow-500/15 border-yellow-500/30',
 P3: 'text-[var(--text-muted)] bg-[var(--bg-inset)] border-[var(--border-subtle)]',
};

export function timeAgo(iso?: string | null): string {
 if (!iso) return '—';
 const then = new Date(iso).getTime();
 if (Number.isNaN(then)) return '—';
 const secs = Math.max(0, (Date.now() - then) / 1000);
 if (secs < 60) return 'just now';

 const [value, unit] =
 secs < 3600 ? [Math.floor(secs / 60), 'minute'] :
 secs < 86400 ? [Math.floor(secs / 3600), 'hour'] :
 secs < 2592000 ? [Math.floor(secs / 86400), 'day'] :
 [Math.floor(secs / 2592000), 'month'];
 return `${value} ${unit}${value === 1 ? '' : 's'} ago`;
}

export function shortDate(iso?: string | null): string {
 if (!iso) return '—';
 const d = new Date(iso);
 if (Number.isNaN(d.getTime())) return '—';
 return d.toLocaleString(undefined, {
 year: 'numeric', month: '2-digit', day: '2-digit',
 hour: '2-digit', minute: '2-digit',
 });
}

export function Pill({ children, className = '' }: { children: React.ReactNode; className?: string }) {
 return (
 <span className={`inline-flex items-center px-2 py-0.5 rounded-sm border text-[10px] font-bold uppercase tracking-wider whitespace-nowrap ${className}`}>
 {children}
 </span>
 );
}

/**
 * Occurrence bars.
 *
 * Deliberately not a charting library: this is a fixed-width count-per-hour
 * histogram with no axes, and it renders in a table row hundreds of times. The
 * tallest bar sets the scale, so the shape of the trend is what reads — an
 * absolute scale would flatten every low-volume issue into an empty strip.
 */
export function EventHistogram({
 points,
 height = 32,
 color = 'var(--signal-crit)',
 showAxis = false,
}: {
 points: { ts: string; count: number }[];
 height?: number;
 color?: string;
 showAxis?: boolean;
}) {
 if (!points || points.length === 0) {
 return (
 <div className="flex items-center text-[10px] text-[var(--text-muted)]" style={{ height }}>
 No timestamped events
 </div>
 );
 }

 const max = Math.max(...points.map((p) => p.count), 1);

 return (
 <div>
 {/* Bars are capped: a series with only a handful of buckets stretched each
 one into a slab, which reads as a bar chart of something else entirely. */}
 <div className="flex items-end gap-[2px]" style={{ height }}>
 {points.map((p) => {
 const pct = (p.count / max) * 100;
 return (
 <div
 key={p.ts}
 className="flex-1 min-w-[2px] max-w-[10px] rounded-[1px] transition-opacity hover:opacity-70"
 // A zero-count hour still gets a hairline: an empty column and a
 // missing column look identical otherwise.
 style={{
 height: p.count > 0 ? `${Math.max(6, pct)}%` : '1px',
 backgroundColor: p.count > 0 ? color : 'var(--border-subtle)',
 }}
 title={`${new Date(p.ts).toLocaleString()} · ${p.count} event${p.count === 1 ? '' : 's'}`}
 />
 );
 })}
 </div>
 {showAxis && (
 <div className="flex justify-between mt-1.5 text-[10px] text-[var(--text-muted)] tnum">
 <span>{shortDate(points[0].ts)}</span>
 <span>peak {max.toLocaleString()}/h</span>
 <span>{shortDate(points[points.length - 1].ts)}</span>
 </div>
 )}
 </div>
 );
}
