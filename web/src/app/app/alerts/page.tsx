"use client";

import React, { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

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
  const [logs, setLogs] = useState<AlertLog[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchLogs = async () => {
    try {
      const res = await apiFetch("/alerts?limit=100");
      setLogs(res.data);
    } catch (err: any) {
      alert(err.message || "Failed to fetch alert logs");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLogs();
  }, []);

  return (
    <div className="p-8">
      <h1 className="text-3xl font-bold mb-6 text-white">Alert History</h1>
      <p className="text-gray-400 mb-8">Chronological log of every sent notification.</p>

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
                <th className="px-6 py-3 text-left font-medium text-gray-300">Priority</th>
                <th className="px-6 py-3 text-left font-medium text-gray-300">Status</th>
                <th className="px-6 py-3 text-left font-medium text-gray-300">HTTP Status</th>
                <th className="px-6 py-3 text-left font-medium text-gray-300">Latency (ms)</th>
                <th className="px-6 py-3 text-left font-medium text-gray-300">Error</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {logs.map((log) => (
                <tr key={log.id} className="hover:bg-white/5">
                  <td className="px-6 py-4 text-gray-400">{log.id}</td>
                  <td className="px-6 py-4 text-gray-300">{new Date(log.timestamp).toLocaleString()}</td>
                  <td className="px-6 py-4">
                    <span className={`px-2 py-1 rounded text-xs font-mono ${
                      log.priority === 'P0' ? 'bg-red-500/20 text-red-300' :
                      log.priority === 'P1' ? 'bg-orange-500/20 text-orange-300' :
                      log.priority === 'P2' ? 'bg-yellow-500/20 text-yellow-300' :
                      'bg-blue-500/20 text-blue-300'
                    }`}>
                      {log.priority}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <span className={`px-2 py-1 rounded text-xs font-mono ${
                      log.status === 'delivered' ? 'bg-green-500/20 text-green-300' :
                      log.status === 'failed' ? 'bg-red-500/20 text-red-300' :
                      'bg-gray-500/20 text-gray-300'
                    }`}>
                      {log.status}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-gray-400">{log.http_status || "N/A"}</td>
                  <td className="px-6 py-4 text-gray-400">{log.latency_ms?.toFixed(1) || "N/A"}</td>
                  <td className="px-6 py-4 text-gray-400 text-xs font-mono max-w-xs truncate" title={log.error || ""}>
                    {log.error || "-"}
                  </td>
                </tr>
              ))}
              {logs.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-6 py-8 text-center text-gray-500">
                    No alerts sent yet.
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
