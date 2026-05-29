"use client";

import React, { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { useToast } from "@/context/ToastContext";
import { Bell, Clock } from "lucide-react";

interface AlertLog {
  id: number;
  webhook_id: string;
  alert_fingerprint: string;
  priority: string;
  status: string;
  http_status: number | null;
  latency_ms: number | null;
  error: string | null;
  timestamp: string;
}

export default function AlertsHistoryPage() {
  const { toast } = useToast();
  const [logs, setLogs] = useState<AlertLog[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchLogs = async () => {
    setLoading(true);
    try {
      const res = await apiFetch("/alerts?limit=100");
      setLogs(res.data);
    } catch (err: any) {
      toast.error(err.message || "Failed to fetch alert logs");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLogs();
  }, []);

  return (
    <div className="max-w-[1600px] mx-auto pb-10">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-xl font-bold text-[var(--text-primary)] mb-1">Alert History</h1>
        <p className="text-xs text-[var(--text-muted)]">Chronological log of every sent notification. <span className="text-[var(--text-dimmed)]">{logs.length} total</span></p>
      </div>

      {/* Table */}
      <div className="bg-[var(--bg-card)] border-none rounded-xl overflow-hidden shadow-sm">
        <table className="w-full text-left text-xs">
          <thead className="text-[10px] font-bold text-[var(--text-dimmed)] uppercase tracking-wider border-b border-transparent bg-transparent">
            <tr>
              <th className="p-5 font-medium">ID</th>
              <th className="p-5 font-medium">Timestamp</th>
              <th className="p-5 font-medium">Priority</th>
              <th className="p-5 font-medium">Status</th>
              <th className="p-5 font-medium">HTTP</th>
              <th className="p-5 font-medium">Latency</th>
              <th className="p-5 font-medium">Error</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-transparent">
            {loading ? (
              Array.from({ length: 5 }).map((_, idx) => (
                <tr key={idx}>
                  <td className="p-5"><div className="shimmer-bg h-4 w-10 rounded" /></td>
                  <td className="p-5"><div className="shimmer-bg h-4 w-32 rounded" /></td>
                  <td className="p-5"><div className="shimmer-bg h-6 w-12 rounded-full" /></td>
                  <td className="p-5"><div className="shimmer-bg h-6 w-20 rounded-full" /></td>
                  <td className="p-5"><div className="shimmer-bg h-4 w-12 rounded" /></td>
                  <td className="p-5"><div className="shimmer-bg h-4 w-16 rounded" /></td>
                  <td className="p-5"><div className="shimmer-bg h-4 w-28 rounded" /></td>
                </tr>
              ))
            ) : logs.length === 0 ? (
              <tr>
                <td colSpan={7} className="p-8 text-center text-[var(--text-muted)]">
                  No alerts sent yet. Configure alert channels in Settings to start receiving notifications.
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
                  <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[9px] font-bold uppercase tracking-wider ${
                    log.priority === 'P0' ? 'bg-red-500/15 text-red-400 border border-red-500/20' :
                    log.priority === 'P1' ? 'bg-orange-500/15 text-orange-400 border border-orange-500/20' :
                    log.priority === 'P2' ? 'bg-yellow-500/15 text-yellow-400 border border-yellow-500/20' :
                    'bg-blue-500/15 text-blue-400 border border-blue-500/20'
                  }`}>
                    {log.priority}
                  </span>
                </td>
                <td className="p-5">
                  <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[9px] font-bold uppercase tracking-wider ${
                    log.status === 'delivered' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' :
                    log.status === 'failed' ? 'bg-red-500/10 text-red-400 border border-red-500/20' :
                    'bg-[var(--bg-surface-hover)] text-[var(--text-muted)] border border-[var(--border-subtle)]'
                  }`}>
                    {log.status}
                  </span>
                </td>
                <td className="p-5 text-[var(--text-secondary)]">{log.http_status || "—"}</td>
                <td className="p-5 text-[var(--text-secondary)]">{log.latency_ms?.toFixed(1) || "—"}<span className="text-[var(--text-dimmed)] ml-0.5">ms</span></td>
                <td className="p-5 text-[var(--text-muted)] text-[10px] font-mono max-w-xs truncate" title={log.error || ""}>
                  {log.error || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
