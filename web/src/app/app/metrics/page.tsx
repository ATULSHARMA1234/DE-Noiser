'use client';

import React, { useState, useEffect } from 'react';
import { apiFetch } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import { Activity, Plus, Trash2, LineChart, Server, Terminal, X } from 'lucide-react';

export default function MetricsPage() {
  const { toast } = useToast();
  const [rules, setRules] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [formData, setFormData] = useState({
    name: '',
    query: '',
    aggregation: 'count',
    window_seconds: 60
  });

  const [metricData, setMetricData] = useState<Record<number, any>>({});
  const [deployments, setDeployments] = useState<any[]>([]);

  useEffect(() => {
    fetchRules();
    fetchDeployments();
  }, []);

  const fetchDeployments = async () => {
    try {
      const data = await apiFetch('/deployments');
      setDeployments(data || []);
    } catch (e) {
      console.error(e);
    }
  };

  const fetchRules = async () => {
    setLoading(true);
    try {
      const data = await apiFetch('/metrics/rules');
      setRules(data || []);
      
      // Fetch preview data for each rule
      const previews: any = {};
      for (const rule of data || []) {
        try {
          const res = await apiFetch(`/metrics/rules/${rule.id}/data`);
          previews[rule.id] = res.data;
        } catch (e) {
          // ignore
        }
      }
      setMetricData(previews);
    } catch (e: any) {
      toast({ title: 'Error fetching metric rules', description: e.message, type: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const createRule = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await apiFetch('/metrics/rules', {
        method: 'POST',
        body: JSON.stringify(formData)
      });
      toast({ title: 'Metric Rule created' });
      setShowCreateModal(false);
      setFormData({ name: '', query: '', aggregation: 'count', window_seconds: 60 });
      fetchRules();
    } catch (e: any) {
      toast({ title: 'Creation failed', description: e.message, type: 'error' });
    }
  };

  const deleteRule = async (id: number) => {
    if (!confirm('Delete this metric rule?')) return;
    try {
      await apiFetch(`/metrics/rules/${id}`, { method: 'DELETE' });
      toast({ title: 'Rule deleted' });
      fetchRules();
    } catch (e: any) {
      toast({ title: 'Deletion failed', type: 'error' });
    }
  };

  const renderSparkline = (dataPoints: any[]) => {
    if (!dataPoints || dataPoints.length === 0) return (
      <div className="h-24 w-full flex items-center justify-center border border-[var(--border)] border-dashed rounded text-[var(--text-secondary)] text-sm">
        No Data
      </div>
    );
    
    const min = 0;
    const max = Math.max(...dataPoints.map((d: any) => d.value), 1);
    const range = max - min;
    
    const points = dataPoints.map((d: any, i: number) => {
      const x = (i / (dataPoints.length - 1)) * 100;
      const y = 100 - ((d.value - min) / range) * 100;
      return `${x},${y}`;
    }).join(' ');

    return (
      <div className="h-24 w-full relative group">
        <svg className="w-full h-full" preserveAspectRatio="none">
          <polyline points={points} fill="none" stroke="#a855f7" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        </svg>
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--text-primary)]">Log-to-Metrics & Deployments</h1>
          <p className="text-[var(--text-secondary)] mt-1">Convert high-volume logs into cost-efficient time-series metrics. Correlate with recent deployments.</p>
        </div>
        <button 
          onClick={() => setShowCreateModal(true)}
          className="bg-fuchsia-600 hover:bg-fuchsia-700 text-white px-4 py-2 rounded font-medium flex items-center gap-2 transition-colors"
        >
          <Plus size={16} /> New Metric Rule
        </button>
      </div>

      {deployments.length > 0 && (
        <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-6">
          <h3 className="text-lg font-bold text-[var(--text-primary)] mb-4">Recent Deployments</h3>
          <div className="flex gap-4 overflow-x-auto pb-2">
            {deployments.map(dep => (
              <div key={dep.id} className="min-w-[250px] border border-[var(--border)] rounded p-3 flex flex-col gap-2">
                <div className="flex justify-between items-center">
                  <span className="font-bold text-sm text-[var(--text-primary)]">{dep.service}</span>
                  <span className="text-xs font-mono bg-blue-500/10 text-blue-500 px-1.5 py-0.5 rounded">{dep.version}</span>
                </div>
                <div className="flex justify-between items-center text-xs text-[var(--text-secondary)]">
                  <span>{dep.environment}</span>
                  <span>{new Date(dep.timestamp).toLocaleTimeString()}</span>
                </div>
                {dep.description && <p className="text-xs text-[var(--text-secondary)] truncate">{dep.description}</p>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {loading ? (
          // Shimmer
          [...Array(2)].map((_, i) => (
            <div key={i} className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-6 animate-pulse h-48"></div>
          ))
        ) : rules.length === 0 ? (
          <div className="col-span-full py-12 text-center bg-[var(--bg-card)] border border-[var(--border)] rounded-lg">
            <LineChart className="mx-auto text-[var(--text-secondary)] opacity-50 mb-3" size={48} />
            <h3 className="text-lg font-medium text-[var(--text-primary)] mb-1">No Extraction Rules</h3>
            <p className="text-sm text-[var(--text-secondary)] mb-6 max-w-md mx-auto">Define rules to automatically extract numerical metrics from your logs in real-time, reducing the need for long-term log retention.</p>
            <button onClick={() => setShowCreateModal(true)} className="bg-fuchsia-600 hover:bg-fuchsia-700 text-white px-4 py-2 rounded text-sm font-medium transition-colors">Create First Rule</button>
          </div>
        ) : (
          rules.map(rule => (
            <div key={rule.id} className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-6 relative group">
              <button 
                onClick={() => deleteRule(rule.id)}
                className="absolute top-4 right-4 text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
              >
                <Trash2 size={16} />
              </button>
              
              <div className="flex justify-between items-start mb-4">
                <div>
                  <h3 className="text-lg font-bold text-[var(--text-primary)] mb-1">{rule.name}</h3>
                  <div className="flex items-center gap-3 text-xs text-[var(--text-secondary)]">
                    <span className="flex items-center gap-1 bg-[var(--bg-app)] px-2 py-1 rounded"><Terminal size={12} /> {rule.query}</span>
                    <span className="flex items-center gap-1"><Activity size={12} /> {rule.aggregation} (every {rule.window_seconds}s)</span>
                  </div>
                </div>
              </div>

              <div className="mt-6">
                <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-2">Live Preview (24h)</div>
                {renderSparkline(metricData[rule.id] || [])}
              </div>
            </div>
          ))
        )}
      </div>

      {showCreateModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg shadow-xl w-full max-w-md overflow-hidden">
            <div className="p-4 border-b border-[var(--border)] flex justify-between items-center bg-[var(--bg-app)]">
              <h2 className="font-bold text-[var(--text-primary)]">Create Metric Rule</h2>
              <button onClick={() => setShowCreateModal(false)} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
                <X size={20} />
              </button>
            </div>
            <form onSubmit={createRule} className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Metric Name</label>
                <input 
                  type="text" 
                  required
                  value={formData.name}
                  onChange={e => setFormData({...formData, name: e.target.value})}
                  className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-fuchsia-500"
                  placeholder="e.g. auth_failures_total"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Matching Query (DSL)</label>
                <input 
                  type="text" 
                  required
                  value={formData.query}
                  onChange={e => setFormData({...formData, query: e.target.value})}
                  className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm font-mono text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-fuchsia-500"
                  placeholder='e.g. level:ERROR AND "authentication failed"'
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Aggregation</label>
                  <select 
                    value={formData.aggregation}
                    onChange={e => setFormData({...formData, aggregation: e.target.value})}
                    className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-fuchsia-500"
                  >
                    <option value="count">Count Matches</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Window (sec)</label>
                  <input 
                    type="number" 
                    min="10"
                    required
                    value={formData.window_seconds}
                    onChange={e => setFormData({...formData, window_seconds: parseInt(e.target.value)})}
                    className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-fuchsia-500"
                  />
                </div>
              </div>

              <div className="pt-4 flex justify-end gap-3 border-t border-[var(--border)] mt-6">
                <button 
                  type="button" 
                  onClick={() => setShowCreateModal(false)}
                  className="px-4 py-2 rounded text-sm font-medium border border-[var(--border)] hover:bg-[var(--bg-app)] transition-colors text-[var(--text-primary)]"
                >
                  Cancel
                </button>
                <button 
                  type="submit" 
                  className="bg-fuchsia-600 hover:bg-fuchsia-700 text-white px-4 py-2 rounded text-sm font-medium transition-colors"
                >
                  Save Rule
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
