'use client';

/**
 * Issue triage queue.
 *
 * Runs answer "what did this analysis find?"; this page answers the questions a
 * team works from — is this new, is it getting worse, who has it. The unit here
 * is the durable issue (one log pattern, tracked across runs), not the cluster,
 * which only exists inside the run that produced it.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Bug, Loader2, RefreshCw, Search, SlidersHorizontal } from 'lucide-react';
import { apiFetch } from '@/lib/api';
import { IssueDetailPanel } from '@/components/issues/IssueDetailPanel';
import {
 EventHistogram, ISSUE_STATES, Pill, SEVERITY_STYLES, STATE_LABELS, STATE_STYLES, timeAgo,
} from '@/components/issues/shared';

const SORTS = [
 { value: 'last_seen', label: 'Last seen' },
 { value: 'first_seen', label: 'First seen' },
 { value: 'events', label: 'Event count' },
 { value: 'severity', label: 'Severity' },
 { value: 'anomaly', label: 'Anomaly score' },
];

const PAGE_SIZE = 50;

export default function IssuesPage() {
 const [issues, setIssues] = useState<any[]>([]);
 const [counts, setCounts] = useState<Record<string, number>>({});
 const [total, setTotal] = useState(0);
 const [facets, setFacets] = useState<any>({ service: [], severity: [], state: [], assignee: [] });
 const [loading, setLoading] = useState(true);
 const [error, setError] = useState<string | null>(null);

 const [state, setState] = useState<string>('FOR_REVIEW');
 const [services, setServices] = useState<string[]>([]);
 const [severities, setSeverities] = useState<string[]>([]);
 const [sort, setSort] = useState('last_seen');
 const [query, setQuery] = useState('');
 const [debouncedQuery, setDebouncedQuery] = useState('');
 const [offset, setOffset] = useState(0);
 const [selectedId, setSelectedId] = useState<number | null>(null);

 useEffect(() => {
 const t = setTimeout(() => { setDebouncedQuery(query); setOffset(0); }, 250);
 return () => clearTimeout(t);
 }, [query]);

 const load = useCallback(() => {
 setLoading(true);
 const params = new URLSearchParams({ state, sort, limit: String(PAGE_SIZE), offset: String(offset) });
 if (services.length) params.set('service', services.join(','));
 if (severities.length) params.set('severity', severities.join(','));
 if (debouncedQuery.trim()) params.set('q', debouncedQuery.trim());

 apiFetch(`/issues?${params.toString()}`)
 .then((data) => {
 setIssues(data?.issues || []);
 setCounts(data?.counts || {});
 setTotal(data?.total || 0);
 setError(null);
 })
 .catch((e) => setError(e.message || 'Could not load issues.'))
 .finally(() => setLoading(false));
 }, [state, sort, offset, services, severities, debouncedQuery]);

 useEffect(() => { load(); }, [load]);

 const loadFacets = useCallback(() => {
 apiFetch('/issues/facets')
 .then((data) => setFacets(data?.facets || {}))
 .catch(() => undefined);
 }, []);
 useEffect(() => { loadFacets(); }, [loadFacets]);

 const toggle = (list: string[], setList: (v: string[]) => void, value: string) => {
 setOffset(0);
 setList(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
 };

 // Prev/Next in the detail panel walks the list that is on screen, so triage
 // stays inside the filter the user chose.
 const selectedIndex = useMemo(
 () => issues.findIndex((i) => i.id === selectedId),
 [issues, selectedId],
 );

 const afterChange = () => { load(); loadFacets(); };

 return (
 <div className="max-w-[1600px] mx-auto pb-10">

 <div className="flex items-center justify-between gap-4 mb-5 flex-wrap">
 <div>
 <h1 className="text-xl font-bold text-[var(--text-primary)] flex items-center gap-2.5">
 <Bug size={20} className="text-[var(--primary)]" />
 Issues
 </h1>
 <p className="text-xs text-[var(--text-muted)] mt-1">
 Log patterns tracked across every analysis run, with the state and owner you assign them.
 </p>
 </div>
 <button
 onClick={() => { load(); loadFacets(); }}
 className="flex items-center gap-2 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border)] rounded-lg px-3 py-2 bg-transparent cursor-pointer transition-colors"
 >
 <RefreshCw size={13} /> Refresh
 </button>
 </div>

 <div className="grid grid-cols-1 lg:grid-cols-[240px_1fr] gap-5 items-start">

 {/* ── Facet rail ─────────────────────────────────────────────── */}
 <aside className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 lg:sticky lg:top-4">
 <div className="flex items-center gap-2 mb-4 text-xs font-semibold text-[var(--text-primary)]">
 <SlidersHorizontal size={13} className="text-[var(--text-muted)]" /> Filters
 </div>

 <FacetGroup
 title="Severity"
 values={facets.severity || []}
 selected={severities}
 onToggle={(v) => toggle(severities, setSeverities, v)}
 />
 <FacetGroup
 title="Service"
 values={facets.service || []}
 selected={services}
 onToggle={(v) => toggle(services, setServices, v)}
 />

 {(services.length > 0 || severities.length > 0) && (
 <button
 onClick={() => { setServices([]); setSeverities([]); setOffset(0); }}
 className="mt-2 text-[11px] text-[var(--primary)] hover:underline bg-transparent border-none p-0 cursor-pointer"
 >
 Clear filters
 </button>
 )}
 </aside>

 {/* ── List ───────────────────────────────────────────────────── */}
 <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">

 <div className="flex items-end gap-1 px-4 pt-3 border-b border-[var(--border-subtle)] overflow-x-auto">
 {[...ISSUE_STATES, 'ALL'].map((s) => {
 const active = state === s;
 const count = s === 'ALL'
 ? Object.values(counts).reduce((a, b) => a + b, 0)
 : counts[s] ?? 0;
 return (
 <button
 key={s}
 onClick={() => { setState(s); setOffset(0); }}
 className={`px-3 py-2 text-xs font-medium border-none bg-transparent cursor-pointer whitespace-nowrap border-b-2 transition-colors ${
 active
 ? 'text-[var(--text-primary)] border-b-[var(--primary)]'
 : 'text-[var(--text-muted)] border-b-transparent hover:text-[var(--text-primary)]'
 }`}
 style={{ borderBottomStyle: 'solid' }}
 >
 {s === 'ALL' ? 'All' : STATE_LABELS[s]}
 <span className="ml-1.5 text-[10px] tnum opacity-70">{count > 99 ? '99+' : count}</span>
 </button>
 );
 })}
 </div>

 <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--border-subtle)] flex-wrap">
 <div className="relative flex-1 min-w-[220px]">
 <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-muted)]" />
 <input
 value={query}
 onChange={(e) => setQuery(e.target.value)}
 placeholder="Filter issues by message or template…"
 className="w-full bg-[var(--bg-inset)] border border-[var(--border)] rounded-lg py-2 pl-9 pr-3 text-xs text-[var(--text-primary)] outline-none focus:ring-1 focus:ring-[var(--primary)]"
 />
 </div>
 <select
 value={sort}
 onChange={(e) => setSort(e.target.value)}
 className="bg-[var(--bg-modal)] border border-[var(--border)] text-[var(--text-input)] text-xs rounded-lg px-3 py-2 outline-none cursor-pointer"
 >
 {SORTS.map((s) => <option key={s.value} value={s.value}>Sort: {s.label}</option>)}
 </select>
 <span className="text-[11px] text-[var(--text-muted)] tnum">
 {total.toLocaleString()} issue{total === 1 ? '' : 's'}
 </span>
 </div>

 {error && <div className="px-4 py-6 text-xs text-[var(--signal-crit)]">{error}</div>}

 {loading && issues.length === 0 ? (
 <div className="flex items-center justify-center py-16">
 <Loader2 size={22} className="animate-spin text-[var(--primary)]" />
 </div>
 ) : issues.length === 0 ? (
 <div className="px-4 py-16 text-center">
 <p className="text-sm text-[var(--text-primary)] font-medium">No issues here</p>
 <p className="text-xs text-[var(--text-muted)] mt-1.5">
 {total === 0 && state === 'FOR_REVIEW'
 ? 'Run an analysis — every cluster it finds becomes an issue tracked from that point on.'
 : 'Nothing matches the current filters.'}
 </p>
 </div>
 ) : (
 <ul>
 {issues.map((issue) => (
 <li key={issue.id}>
 <button
 onClick={() => setSelectedId(issue.id)}
 className={`w-full text-left px-4 py-3 flex items-center gap-4 border-b border-[var(--border-subtle)] bg-transparent cursor-pointer transition-colors hover:bg-[var(--bg-app)] ${
 selectedId === issue.id ? 'bg-[var(--bg-app)]' : ''
 }`}
 style={{ borderLeft: `3px solid ${severityRail(issue.severity)}` }}
 >
 <div className="min-w-0 flex-1">
 <div className="flex items-center gap-2 flex-wrap">
 <Pill className={SEVERITY_STYLES[issue.severity] || SEVERITY_STYLES.P3}>{issue.severity}</Pill>
 <span className="text-sm font-medium text-[var(--text-primary)] truncate max-w-[520px]">
 {issue.title}
 </span>
 {issue.state !== 'FOR_REVIEW' && (
 <Pill className={STATE_STYLES[issue.state]}>{STATE_LABELS[issue.state]}</Pill>
 )}
 </div>
 <div className="mt-1 flex items-center gap-2 text-[11px] text-[var(--text-muted)] flex-wrap">
 <span className="font-mono px-1.5 py-0.5 rounded bg-[var(--bg-inset)] border border-[var(--border-subtle)]">
 {issue.service}
 </span>
 <span>·</span>
 <span>Last seen {timeAgo(issue.last_seen)}</span>
 <span>·</span>
 <span>First seen {timeAgo(issue.first_seen)}</span>
 {issue.assignee && (<><span>·</span><span>{issue.assignee.email}</span></>)}
 </div>
 </div>

 <div className="hidden md:block w-[160px] shrink-0">
 <EventHistogram points={issue.sparkline || []} height={26} />
 </div>

 <div className="w-[90px] shrink-0 text-right">
 <div className="text-sm font-bold text-[var(--text-primary)] tnum">
 {(issue.total_events || 0).toLocaleString()}
 </div>
 <div className="text-[10px] text-[var(--text-muted)]">events</div>
 </div>
 </button>
 </li>
 ))}
 </ul>
 )}

 {total > PAGE_SIZE && (
 <div className="flex items-center justify-between px-4 py-3">
 <button
 onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
 disabled={offset === 0}
 className="px-3 py-1.5 rounded-lg border border-[var(--border)] bg-transparent text-xs text-[var(--text-secondary)] disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
 >
 Previous
 </button>
 <span className="text-[11px] text-[var(--text-muted)] tnum">
 {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total.toLocaleString()}
 </span>
 <button
 onClick={() => setOffset((o) => o + PAGE_SIZE)}
 disabled={offset + PAGE_SIZE >= total}
 className="px-3 py-1.5 rounded-lg border border-[var(--border)] bg-transparent text-xs text-[var(--text-secondary)] disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
 >
 Next
 </button>
 </div>
 )}
 </div>
 </div>

 {selectedId !== null && (
 <IssueDetailPanel
 issueId={selectedId}
 onClose={() => setSelectedId(null)}
 onChanged={afterChange}
 onPrevious={selectedIndex > 0 ? () => setSelectedId(issues[selectedIndex - 1].id) : undefined}
 onNext={
 selectedIndex >= 0 && selectedIndex < issues.length - 1
 ? () => setSelectedId(issues[selectedIndex + 1].id)
 : undefined
 }
 />
 )}
 </div>
 );
}

function severityRail(severity: string): string {
 return {
 P0: 'var(--signal-crit)',
 P1: 'var(--signal-warn)',
 P2: 'var(--signal-warn)',
 }[severity] || 'transparent';
}

function FacetGroup({
 title, values, selected, onToggle,
}: {
 title: string;
 values: { value: string; count: number }[];
 selected: string[];
 onToggle: (value: string) => void;
}) {
 const [expanded, setExpanded] = useState(false);
 const visible = expanded ? values : values.slice(0, 8);

 return (
 <div className="mb-4">
 <h3 className="text-[11px] font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-2">{title}</h3>
 {values.length === 0 && <p className="text-[11px] text-[var(--text-muted)]">No values yet.</p>}
 <ul className="space-y-0.5">
 {visible.map((v) => (
 <li key={v.value}>
 <label className="flex items-center gap-2 text-xs text-[var(--text-primary)] py-1 px-1.5 rounded hover:bg-[var(--bg-app)] cursor-pointer">
 <input
 type="checkbox"
 checked={selected.includes(v.value)}
 onChange={() => onToggle(v.value)}
 className="cursor-pointer accent-[var(--primary)]"
 />
 <span className="truncate flex-1">{v.value}</span>
 <span className="text-[10px] text-[var(--text-muted)] tnum">{v.count}</span>
 </label>
 </li>
 ))}
 </ul>
 {values.length > 8 && (
 <button
 onClick={() => setExpanded((e) => !e)}
 className="mt-1 text-[11px] text-[var(--primary)] hover:underline bg-transparent border-none p-0 cursor-pointer"
 >
 {expanded ? 'Show less' : `View ${values.length - 8} more`}
 </button>
 )}
 </div>
 );
}
