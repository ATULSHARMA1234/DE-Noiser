"use client";

import React, { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { useToast } from "@/context/ToastContext";
import { ShieldAlert, Clock } from "lucide-react";

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

export default function AuditLogsPage() {
  const { toast } = useToast();
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchLogs = async () => {
    setLoading(true);
    try {
      const res = await apiFetch("/audit?limit=100");
      setLogs(res.data);
    } catch (err: any) {
      toast.error(err.message || "Failed to fetch audit logs");
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
        <h1 className="text-xl font-bold text-[var(--text-primary)] mb-1">Security & Audit Logs</h1>
        <p className="text-xs text-[var(--text-muted)]">Comprehensive audit trail of all database and system mutations. <span className="text-[var(--text-dimmed)]">{logs.length} total</span></p>
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
                  <span className="inline-flex items-center px-2 py-0.5 bg-fuchsia-500/10 text-fuchsia-400 border border-fuchsia-500/10 rounded text-[10px] font-mono font-bold uppercase tracking-wider">
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
    </div>
  );
}

