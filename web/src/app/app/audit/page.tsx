"use client";

import React, { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { useToast } from "@/context/ToastContext";
import { Clock, ChevronLeft, ChevronRight } from "lucide-react";

interface AuditLog {
 id: number;
 user_id: number | null;
 action: string;
 resource_type: string;
 resource_id: string | null;
 details: any;
 ip_address: string | null;
 timestamp: string;
}

const PAGE_SIZE = 100;

// <input type="date"> gives a local calendar day; the API wants an instant.
const dayStart = (day: string) => new Date(`${day}T00:00:00`).toISOString();
const nextDayStart = (day: string) => {
 const d = new Date(`${day}T00:00:00`);
 d.setDate(d.getDate() + 1);
 return d.toISOString();
};

export default function AuditLogsPage() {
 const { toast } = useToast();
 const [logs, setLogs] = useState<AuditLog[]>([]);
 const [total, setTotal] = useState(0);
 const [loading, setLoading] = useState(true);
 const [page, setPage] = useState(0);
 const [fromDay, setFromDay] = useState("");
 const [toDay, setToDay] = useState("");

 const fetchLogs = async () => {
 setLoading(true);
 try {
 const params = new URLSearchParams({ limit: String(PAGE_SIZE), skip: String(page * PAGE_SIZE) });
 if (fromDay) params.set("since", dayStart(fromDay));
 if (toDay) params.set("until", nextDayStart(toDay));
 const res = await apiFetch(`/audit/?${params}`);
 setLogs(res?.data ?? []);
 setTotal(res?.total ?? 0);
 } catch (err: any) {
 toast.error(err.message || "Failed to fetch audit logs");
 } finally {
 setLoading(false);
 }
 };

 useEffect(() => {
 fetchLogs();
 }, [page, fromDay, toDay]);

 const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
 const setRange = (setter: (v: string) => void) => (e: React.ChangeEvent<HTMLInputElement>) => {
 setter(e.target.value);
 setPage(0);
 };

 return (
 <div className="max-w-[1600px] mx-auto pb-10">
 {/* Header */}
 <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
 <div>
 <h1 className="text-xl font-bold text-[var(--text-primary)] mb-1">Security & Audit Logs</h1>
 <p className="text-xs text-[var(--text-muted)]">Comprehensive audit trail of all database and system mutations. <span className="text-[var(--text-dimmed)]">{total.toLocaleString()} total</span></p>
 </div>
 <div className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
 <label className="flex items-center gap-1.5">From
 <input type="date" value={fromDay} max={toDay || undefined} onChange={setRange(setFromDay)}
 className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded px-2 py-1 text-[var(--text-primary)]" />
 </label>
 <label className="flex items-center gap-1.5">To
 <input type="date" value={toDay} min={fromDay || undefined} onChange={setRange(setToDay)}
 className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded px-2 py-1 text-[var(--text-primary)]" />
 </label>
 {(fromDay || toDay) && (
 <button onClick={() => { setFromDay(""); setToDay(""); setPage(0); }}
 className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] cursor-pointer bg-transparent border-none">Clear</button>
 )}
 </div>
 </div>

 {/* Table */}
 <div className="bg-[var(--bg-card)] border-none rounded-xl overflow-hidden shadow-sm">
 <table className="w-full text-left text-xs">
 <thead className="text-[10px] font-bold text-[var(--text-dimmed)] uppercase tracking-wider border-b border-transparent bg-transparent">
 <tr>
 <th className="p-5 font-medium">ID</th>
 <th className="p-5 font-medium">Timestamp</th>
 <th className="p-5 font-medium">Action</th>
 <th className="p-5 font-medium">Resource</th>
 <th className="p-5 font-medium">User ID</th>
 <th className="p-5 font-medium">IP Address</th>
 <th className="p-5 font-medium">Details</th>
 </tr>
 </thead>
 <tbody className="divide-y divide-transparent">
 {loading ? (
 Array.from({ length: 5 }).map((_, idx) => (
 <tr key={idx}>
 <td className="p-5"><div className="shimmer-bg h-4 w-8 rounded" /></td>
 <td className="p-5"><div className="shimmer-bg h-4 w-32 rounded" /></td>
 <td className="p-5"><div className="shimmer-bg h-6 w-20 rounded-full" /></td>
 <td className="p-5"><div className="shimmer-bg h-4 w-24 rounded" /></td>
 <td className="p-5"><div className="shimmer-bg h-4 w-12 rounded" /></td>
 <td className="p-5"><div className="shimmer-bg h-4 w-20 rounded" /></td>
 <td className="p-5"><div className="shimmer-bg h-4 w-48 rounded" /></td>
 </tr>
 ))
 ) : logs.length === 0 ? (
 <tr>
 <td colSpan={7} className="p-8 text-center text-[var(--text-muted)]">
 No audit logs found.
 </td>
 </tr>
 ) : logs.map((log) => (
 <tr key={log.id} className="hover:bg-[var(--bg-surface-hover)] transition-colors">
 <td className="p-5 text-[var(--text-muted)] font-mono text-[10px]">{log.id}</td>
 <td className="p-5">
 <div className="flex items-center gap-1.5 text-[var(--text-secondary)]">
 <Clock size={12} /> {new Date(log.timestamp).toLocaleString()}
 </div>
 </td>
 <td className="p-5">
 <span className="inline-flex items-center px-2 py-0.5 bg-[var(--primary)] text-[var(--primary)] border border-[var(--primary)] rounded text-[10px] font-mono font-bold uppercase tracking-wider">
 {log.action}
 </span>
 </td>
 <td className="p-5 text-[var(--text-secondary)] font-mono text-[10px]">{log.resource_type}</td>
 <td className="p-5 text-[var(--text-secondary)]">{log.user_id || "System"}</td>
 <td className="p-5 text-[var(--text-secondary)] font-mono">{log.ip_address || "—"}</td>
 <td className="p-5 text-[var(--text-muted)] text-[10px] font-mono max-w-xs truncate" title={JSON.stringify(log.details)}>
 {JSON.stringify(log.details)}
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>

 {total > PAGE_SIZE && (
 <div className="flex items-center justify-end gap-3 mt-4 text-xs text-[var(--text-muted)]">
 <span>Page {page + 1} of {pageCount}</span>
 <button onClick={() => setPage(p => p - 1)} disabled={page === 0 || loading} aria-label="Previous page"
 className="p-1.5 rounded border border-[var(--border-subtle)] bg-transparent text-[var(--text-secondary)] disabled:opacity-30 cursor-pointer disabled:cursor-not-allowed">
 <ChevronLeft size={14} />
 </button>
 <button onClick={() => setPage(p => p + 1)} disabled={page + 1 >= pageCount || loading} aria-label="Next page"
 className="p-1.5 rounded border border-[var(--border-subtle)] bg-transparent text-[var(--text-secondary)] disabled:opacity-30 cursor-pointer disabled:cursor-not-allowed">
 <ChevronRight size={14} />
 </button>
 </div>
 )}
 </div>
 );
}

