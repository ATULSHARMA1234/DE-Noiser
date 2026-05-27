"use client";

import React, { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

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
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchLogs = async () => {
    try {
      const res = await apiFetch("/audit?limit=100");
      setLogs(res.data);
    } catch (err: any) {
      alert(err.message || "Failed to fetch audit logs");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLogs();
  }, []);

  return (
    <div className="p-8">
      <h1 className="text-3xl font-bold mb-6 text-white">Audit Logs</h1>
      <p className="text-gray-400 mb-8">Comprehensive audit trail of all mutating actions.</p>

      {loading ? (
        <div className="animate-pulse space-y-4">
          <div className="h-10 bg-white/5 rounded-md w-full"></div>
          <div className="h-10 bg-white/5 rounded-md w-full"></div>
        </div>
      ) : (
        <div className="bg-white/5 border border-white/10 rounded-lg overflow-x-auto">
          <table className="min-w-full divide-y divide-white/10 text-sm">
            <thead className="bg-white/5">
              <tr>
                <th className="px-6 py-3 text-left font-medium text-gray-300">ID</th>
                <th className="px-6 py-3 text-left font-medium text-gray-300">Timestamp</th>
                <th className="px-6 py-3 text-left font-medium text-gray-300">Action</th>
                <th className="px-6 py-3 text-left font-medium text-gray-300">Resource</th>
                <th className="px-6 py-3 text-left font-medium text-gray-300">User ID</th>
                <th className="px-6 py-3 text-left font-medium text-gray-300">IP Address</th>
                <th className="px-6 py-3 text-left font-medium text-gray-300">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {logs.map((log) => (
                <tr key={log.id} className="hover:bg-white/5">
                  <td className="px-6 py-4 text-gray-400">{log.id}</td>
                  <td className="px-6 py-4 text-gray-300">{new Date(log.timestamp).toLocaleString()}</td>
                  <td className="px-6 py-4">
                    <span className="px-2 py-1 bg-blue-500/20 text-blue-300 rounded text-xs font-mono">
                      {log.action}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-gray-300 font-mono text-xs">{log.resource_type}</td>
                  <td className="px-6 py-4 text-gray-400">{log.user_id || "System"}</td>
                  <td className="px-6 py-4 text-gray-400">{log.ip_address || "N/A"}</td>
                  <td className="px-6 py-4 text-gray-400 text-xs font-mono max-w-xs truncate" title={JSON.stringify(log.details)}>
                    {JSON.stringify(log.details)}
                  </td>
                </tr>
              ))}
              {logs.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-6 py-8 text-center text-gray-500">
                    No audit logs found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
