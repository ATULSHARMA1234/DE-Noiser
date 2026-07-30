'use client';

import React, { useEffect, useState } from 'react';
import {
 X, ChevronLeft, ChevronRight, GitCommit, ArrowRight, Loader2, Send, ExternalLink,
} from 'lucide-react';
import Link from 'next/link';
import { apiFetch, apiPatch, apiPost } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import {
 EventHistogram, ISSUE_STATES, Pill, SEVERITY_STYLES, STATE_LABELS, STATE_STYLES,
 shortDate, timeAgo,
} from './shared';

type Tab = 'log' | 'attributes' | 'json';

/**
 * The full record of one issue, opened over the list.
 *
 * Deliberately a panel rather than a route: triage is a queue, and navigating
 * away to a report page loses the queue's scroll position and filters every
 * time an issue is opened.
 */
export function IssueDetailPanel({
 issueId,
 onClose,
 onChanged,
 onPrevious,
 onNext,
}: {
 issueId: number;
 onClose: () => void;
 onChanged: () => void;
 onPrevious?: () => void;
 onNext?: () => void;
}) {
 const { toast } = useToast();
 const [issue, setIssue] = useState<any>(null);
 const [loading, setLoading] = useState(true);
 const [error, setError] = useState<string | null>(null);
 const [sampleIndex, setSampleIndex] = useState(0);
 const [tab, setTab] = useState<Tab>('log');
 const [assignees, setAssignees] = useState<any[]>([]);
 const [comment, setComment] = useState('');
 const [saving, setSaving] = useState(false);

 const load = React.useCallback(() => {
 setLoading(true);
 apiFetch(`/issues/${issueId}`)
 .then((data) => { setIssue(data); setError(null); })
 .catch((e) => setError(e.message || 'Could not load this issue.'))
 .finally(() => setLoading(false));
 }, [issueId]);

 useEffect(() => { load(); setSampleIndex(0); setTab('log'); }, [load]);

 useEffect(() => {
 apiFetch(`/issues/${issueId}/assignees`)
 .then((data) => setAssignees(data?.users || []))
 .catch(() => setAssignees([]));
 }, [issueId]);

 useEffect(() => {
 const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
 window.addEventListener('keydown', onKey);
 return () => window.removeEventListener('keydown', onKey);
 }, [onClose]);

 const patch = async (body: any) => {
 setSaving(true);
 try {
 await apiPatch(`/issues/${issueId}`, body);
 load();
 onChanged();
 } catch (e: any) {
 toast.error(e.message || 'Could not update this issue.');
 } finally {
 setSaving(false);
 }
 };

 const submitComment = async (e: React.FormEvent) => {
 e.preventDefault();
 const body = comment.trim();
 if (!body) return;
 setSaving(true);
 try {
 await apiPost(`/issues/${issueId}/comments`, { body });
 setComment('');
 load();
 } catch (e: any) {
 toast.error(e.message || 'Could not post the comment.');
 } finally {
 setSaving(false);
 }
 };

 const samples: any[] = issue?.samples || [];
 const sample = samples[sampleIndex];
 const tags: Record<string, any[]> = issue?.tags || {};
 const suspect = issue?.suspect_deployment;

 return (
 <div className="fixed inset-0 z-[240] flex justify-end bg-black/40" onClick={onClose}>
 <aside
 className="bg-[var(--bg-card)] border-l border-[var(--border)] w-full max-w-[980px] h-full flex flex-col shadow-2xl animate-in slide-in-from-right duration-150"
 onClick={(e) => e.stopPropagation()}
 role="dialog"
 aria-modal="true"
 aria-label="Issue details"
 >
 {/* A 2px accent rail at the top edge, matching the severity of the issue. */}
 <div className={`h-[3px] w-full ${issue?.severity === 'P0' ? 'bg-red-500' : issue?.severity === 'P1' ? 'bg-orange-500' : issue?.severity === 'P2' ? 'bg-yellow-500' : 'bg-[var(--border)]'}`} />

 <header className="px-6 py-4 border-b border-[var(--border-subtle)] shrink-0">
 <div className="flex items-start justify-between gap-4">
 <div className="flex items-center gap-2 flex-wrap min-w-0">
 <Pill className={STATE_STYLES[issue?.state] || STATE_STYLES.IGNORED}>
 {STATE_LABELS[issue?.state] || issue?.state || '—'}
 </Pill>
 <Pill className={SEVERITY_STYLES[issue?.severity] || SEVERITY_STYLES.P3}>{issue?.severity || 'P3'}</Pill>
 {issue?.is_noise && <Pill className={SEVERITY_STYLES.P3}>Outlier</Pill>}
 </div>
 <div className="flex items-center gap-1 shrink-0">
 <button
 onClick={onPrevious}
 disabled={!onPrevious}
 className="p-1.5 rounded-md border border-[var(--border)] bg-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
 aria-label="Previous issue"
 >
 <ChevronLeft size={16} />
 </button>
 <button
 onClick={onNext}
 disabled={!onNext}
 className="p-1.5 rounded-md border border-[var(--border)] bg-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
 aria-label="Next issue"
 >
 <ChevronRight size={16} />
 </button>
 <button
 onClick={onClose}
 className="p-1.5 rounded-md border-none bg-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer"
 aria-label="Close"
 >
 <X size={18} />
 </button>
 </div>
 </div>
 <h2 className="mt-3 text-base font-semibold text-[var(--text-primary)] leading-snug">
 {loading && !issue ? 'Loading…' : issue?.title}
 </h2>
 {issue?.template && (
 <p className="mt-1 font-mono text-[11px] text-[var(--text-muted)] break-all">{issue.template}</p>
 )}
 <div className="mt-2 flex items-center gap-2 text-xs text-[var(--text-muted)]">
 <span className="px-1.5 py-0.5 rounded bg-[var(--bg-inset)] border border-[var(--border-subtle)] font-mono text-[10px]">
 {issue?.service || 'unknown'}
 </span>
 {issue?.fingerprint && <span className="font-mono text-[10px] opacity-70">#{issue.fingerprint}</span>}
 </div>
 </header>

 {error && (
 <div className="p-6 text-sm text-[var(--signal-crit)]">{error}</div>
 )}

 {loading && !issue ? (
 <div className="flex-1 flex items-center justify-center">
 <Loader2 className="animate-spin text-[var(--primary)]" size={22} />
 </div>
 ) : issue && (
 <div className="flex-1 overflow-y-auto">
 <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px]">
 {/* ── Main column ─────────────────────────────────────────── */}
 <div className="p-6 space-y-5 min-w-0">

 {/* Seen window */}
 <div className="flex items-center gap-3 flex-wrap text-xs border border-[var(--border-subtle)] rounded-lg px-4 py-3 bg-[var(--bg-app)]">
 <span className="text-[var(--text-muted)]">First seen</span>
 <span className="font-medium text-[var(--text-primary)]">{timeAgo(issue.first_seen)}</span>
 <span className="text-[var(--text-muted)] tnum">{shortDate(issue.first_seen)}</span>
 <ArrowRight size={13} className="text-[var(--text-muted)]" />
 <span className="text-[var(--text-muted)]">Last seen</span>
 <span className="font-medium text-[var(--text-primary)]">{timeAgo(issue.last_seen)}</span>
 <span className="text-[var(--text-muted)] tnum">{shortDate(issue.last_seen)}</span>
 </div>

 {/* Volume + trend */}
 <div className="border border-[var(--border-subtle)] rounded-lg overflow-hidden">
 <div className="flex">
 <div className="px-6 py-5 border-r border-[var(--border-subtle)] bg-[var(--bg-app)] shrink-0 min-w-[150px]">
 <div className="text-3xl font-bold text-[var(--text-primary)] tnum leading-none">
 {(issue.total_events || 0).toLocaleString()}
 </div>
 <div className="mt-1 text-[11px] text-[var(--text-muted)]">
 events · {issue.run_count} run{issue.run_count === 1 ? '' : 's'}
 </div>
 {issue.last_run_id && (
 <Link
 href={`/app/runs/${issue.last_run_id}`}
 className="mt-3 inline-flex items-center gap-1 text-[11px] text-[var(--primary)] hover:underline"
 >
 Latest run <ExternalLink size={11} />
 </Link>
 )}
 </div>
 <div className="flex-1 px-4 py-4 min-w-0">
 <EventHistogram points={issue.histogram || []} height={72} showAxis />
 </div>
 </div>
 </div>

 {/* Suspect deploy */}
 <div className="border border-[var(--border-subtle)] rounded-lg px-4 py-3">
 <div className="flex items-center gap-3 flex-wrap text-xs">
 <span className="text-[var(--text-muted)] w-[110px] shrink-0">Suspect deploy</span>
 {suspect ? (
 <>
 <GitCommit size={14} className="text-[var(--text-muted)]" />
 <span className="font-mono text-[11px] text-[var(--primary)]">{suspect.version}</span>
 <span className="text-[var(--text-primary)]">{suspect.description || suspect.service}</span>
 <span className="text-[var(--text-muted)]">
 {suspect.environment} · {suspect.minutes_before_first_seen} min before first seen
 </span>
 </>
 ) : (
 <span className="text-[var(--text-muted)]">
 No deployment recorded in the 24h before this issue appeared.
 </span>
 )}
 </div>
 </div>

 {/* Tag prevalence — what is distinctive about this issue's lines */}
 <div className="border border-[var(--border-subtle)] rounded-lg px-4 py-3">
 <div className="flex items-start gap-3">
 <span className="text-xs text-[var(--text-muted)] w-[110px] shrink-0 pt-1">Tags</span>
 <div className="flex flex-wrap gap-1.5">
 {Object.keys(tags).length === 0 && (
 <span className="text-xs text-[var(--text-muted)]">
 No attributes were attached to these log lines.
 </span>
 )}
 {Object.entries(tags).flatMap(([key, values]) =>
 (values || []).map((v: any) => (
 <span
 key={`${key}:${v.value}`}
 className="inline-flex items-center gap-1 px-2 py-1 rounded bg-[var(--bg-inset)] border border-[var(--border-subtle)] text-[11px] font-mono text-[var(--text-secondary)]"
 title={`${v.count.toLocaleString()} of this issue's sampled events`}
 >
 {key}:{v.value}
 <span className="text-[var(--primary)] font-semibold">({v.pct}%)</span>
 </span>
 ))
 )}
 </div>
 </div>
 </div>

 {/* Sample browser */}
 <div>
 <div className="flex items-center justify-between mb-2">
 <h3 className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wider">
 Log sample
 </h3>
 <div className="flex items-center gap-2">
 <span className="text-[11px] text-[var(--text-muted)] tnum">
 {samples.length ? `${sampleIndex + 1} of ${samples.length}` : '0'}
 </span>
 <button
 onClick={() => setSampleIndex((i) => Math.max(0, i - 1))}
 disabled={sampleIndex === 0}
 className="px-2 py-1 rounded-md border border-[var(--border)] bg-transparent text-[11px] text-[var(--text-secondary)] disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
 >
 Previous
 </button>
 <button
 onClick={() => setSampleIndex((i) => Math.min(samples.length - 1, i + 1))}
 disabled={sampleIndex >= samples.length - 1}
 className="px-2 py-1 rounded-md border border-[var(--border)] bg-transparent text-[11px] text-[var(--text-secondary)] disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
 >
 Next
 </button>
 </div>
 </div>

 <div className="border border-[var(--border-subtle)] rounded-lg overflow-hidden">
 <div className="flex items-center gap-1 px-2 py-1.5 border-b border-[var(--border-subtle)] bg-[var(--bg-app)]">
 {(['log', 'attributes', 'json'] as Tab[]).map((t) => (
 <button
 key={t}
 onClick={() => setTab(t)}
 className={`px-3 py-1 rounded-md text-[11px] font-medium capitalize border-none cursor-pointer transition-colors ${
 tab === t
 ? 'bg-[var(--primary)] text-white'
 : 'bg-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
 }`}
 >
 {t}
 </button>
 ))}
 {sample?.timestamp && (
 <span className="ml-auto pr-2 text-[11px] text-[var(--text-muted)] tnum">
 {shortDate(sample.timestamp)}
 </span>
 )}
 </div>

 <div className="p-3 bg-[var(--bg-inset)] max-h-[280px] overflow-auto">
 {!sample && (
 <p className="text-xs text-[var(--text-muted)]">This issue carries no stored samples.</p>
 )}
 {sample && tab === 'log' && (
 <pre className="font-mono text-[11px] text-[var(--text-primary)] whitespace-pre-wrap break-all">
 {sample.raw}
 </pre>
 )}
 {sample && tab === 'attributes' && (
 <table className="w-full text-[11px]">
 <tbody>
 {[
 ['source', sample.source],
 ['line', sample.line_number],
 ['timestamp', sample.timestamp],
 ...Object.entries(sample.metadata || {}),
 ].map(([k, v]) => (
 <tr key={String(k)} className="border-b border-[var(--border-subtle)] last:border-0">
 <td className="py-1.5 pr-4 font-mono text-[var(--text-muted)] w-[140px] align-top">{String(k)}</td>
 <td className="py-1.5 font-mono text-[var(--text-primary)] break-all">{v == null ? '—' : String(v)}</td>
 </tr>
 ))}
 </tbody>
 </table>
 )}
 {sample && tab === 'json' && (
 <pre className="font-mono text-[11px] text-[var(--text-primary)] whitespace-pre-wrap break-all">
 {JSON.stringify(sample, null, 2)}
 </pre>
 )}
 </div>
 </div>
 </div>

 {/* Activity */}
 <div>
 <h3 className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wider mb-2">
 Activity
 </h3>
 <ul className="space-y-1.5">
 {(issue.activity || []).length === 0 && (
 <li className="text-xs text-[var(--text-muted)]">Nothing has happened to this issue yet.</li>
 )}
 {(issue.activity || []).map((event: any) => (
 <li key={event.id} className="flex items-baseline gap-2 text-xs">
 <span className="text-[var(--text-muted)] tnum w-[110px] shrink-0">{timeAgo(event.created_at)}</span>
 <span className="text-[var(--text-primary)]">{describeEvent(event)}</span>
 </li>
 ))}
 </ul>
 </div>
 </div>

 {/* ── Ownership rail ──────────────────────────────────────── */}
 <div className="border-t lg:border-t-0 lg:border-l border-[var(--border-subtle)] p-5 space-y-5 bg-[var(--bg-app)]">
 <div>
 <h3 className="text-xs font-semibold text-[var(--text-primary)] mb-3">Ownership</h3>

 <label className="block text-[11px] text-[var(--text-muted)] mb-1">State</label>
 <select
 value={issue.state}
 disabled={saving}
 onChange={(e) => patch({ state: e.target.value })}
 className="w-full mb-3 bg-[var(--bg-modal)] border border-[var(--border)] text-[var(--text-input)] text-xs rounded-lg px-2.5 py-2 outline-none cursor-pointer"
 >
 {ISSUE_STATES.map((s) => (
 <option key={s} value={s}>{STATE_LABELS[s]}</option>
 ))}
 </select>

 <label className="block text-[11px] text-[var(--text-muted)] mb-1">Severity</label>
 <select
 value={issue.severity}
 disabled={saving}
 onChange={(e) => patch({ severity: e.target.value })}
 className="w-full mb-3 bg-[var(--bg-modal)] border border-[var(--border)] text-[var(--text-input)] text-xs rounded-lg px-2.5 py-2 outline-none cursor-pointer"
 >
 {['P0', 'P1', 'P2', 'P3'].map((s) => <option key={s} value={s}>{s}</option>)}
 </select>

 <label className="block text-[11px] text-[var(--text-muted)] mb-1">Assigned to</label>
 <select
 value={issue.assignee?.id ?? 0}
 disabled={saving}
 onChange={(e) => patch({ assignee_id: Number(e.target.value) })}
 className="w-full bg-[var(--bg-modal)] border border-[var(--border)] text-[var(--text-input)] text-xs rounded-lg px-2.5 py-2 outline-none cursor-pointer"
 >
 <option value={0}>Unassigned</option>
 {assignees.map((u) => <option key={u.id} value={u.id}>{u.email}</option>)}
 </select>
 </div>

 <div>
 <h3 className="text-xs font-semibold text-[var(--text-primary)] mb-2">Comments</h3>
 <ul className="space-y-2 mb-3 max-h-[240px] overflow-y-auto">
 {(issue.comments || []).length === 0 && (
 <li className="text-[11px] text-[var(--text-muted)]">No comments yet.</li>
 )}
 {(issue.comments || []).map((c: any) => (
 <li key={c.id} className="border border-[var(--border-subtle)] rounded-lg p-2.5 bg-[var(--bg-card)]">
 <div className="flex items-baseline justify-between gap-2">
 <span className="text-[11px] font-medium text-[var(--text-primary)] truncate">{c.author_email}</span>
 <span className="text-[10px] text-[var(--text-muted)] shrink-0">{timeAgo(c.created_at)}</span>
 </div>
 <p className="mt-1 text-xs text-[var(--text-secondary)] break-words">{c.body}</p>
 </li>
 ))}
 </ul>

 <form onSubmit={submitComment} className="relative">
 <textarea
 value={comment}
 onChange={(e) => setComment(e.target.value)}
 placeholder="Write a comment…"
 rows={3}
 className="w-full bg-[var(--bg-modal)] border border-[var(--border)] rounded-lg p-2.5 pr-9 text-xs text-[var(--text-input)] outline-none focus:ring-1 focus:ring-[var(--primary)] resize-none"
 />
 <button
 type="submit"
 disabled={saving || !comment.trim()}
 aria-label="Post comment"
 className="absolute right-2 bottom-3 text-[var(--primary)] disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer bg-transparent border-none"
 >
 {saving ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
 </button>
 </form>
 </div>
 </div>
 </div>
 </div>
 )}
 </aside>
 </div>
 );
}

/** One activity line, in the words a reader would use. */
function describeEvent(event: any): string {
 const actor = event.actor_email ? `${event.actor_email} ` : '';
 const detail = event.detail || {};
 switch (event.kind) {
 case 'state':
 return `${actor}moved this from ${STATE_LABELS[detail.from] || detail.from} to ${STATE_LABELS[detail.to] || detail.to}`;
 case 'severity':
 return `${actor}changed severity from ${detail.from} to ${detail.to}`;
 case 'assignee':
 return detail.to ? `${actor}assigned this to ${detail.to}` : `${actor}unassigned this`;
 case 'comment':
 return `${actor}commented: ${detail.preview || ''}`;
 case 'regression':
 return `Recurred after being resolved — ${detail.events || 0} events in run ${detail.run_id}`;
 case 'seen':
 return detail.first
 ? `First detected in run ${detail.run_id} (${detail.events || 0} events)`
 : `Seen again in run ${detail.run_id} (${detail.events || 0} events)`;
 default:
 return event.kind;
 }
}
