'use client';

import React, { useState, useEffect } from 'react';
import { apiFetch } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import { Zap, Plus, Settings, AlertCircle, Activity, ShieldAlert, CheckCircle2, HelpCircle, X } from 'lucide-react';
import { ConfirmModal } from '@/components/ConfirmModal';

export default function SLOsPage() {
 const { toast } = useToast();
 const [slos, setSlos] = useState<any[]>([]);
 const [sloStatusList, setSloStatusList] = useState<any>({});
 const [loading, setLoading] = useState(true);
 const [showModal, setShowModal] = useState(false);
 const [formData, setFormData] = useState({
 name: '',
 service: '',
 sli_type: 'availability',
 target_percentage: 99.9,
 window_days: 30,
 latency_threshold_ms: 500
 });

 const [confirmOpen, setConfirmOpen] = useState(false);
 const [confirmTitle, setConfirmTitle] = useState('');
 const [confirmMessage, setConfirmMessage] = useState('');
 const [confirmCallback, setConfirmCallback] = useState<(() => void) | null>(null);

 useEffect(() => {
 // eslint-disable-next-line react-hooks/immutability
 fetchSLOs();
 }, []);

 const fetchSLOs = async () => {
 setLoading(true);
 try {
 const data = await apiFetch('/slos');
 setSlos(data || []);
 
 // Fetch status for each SLO
 const statusData: any = {};
 for (const slo of data || []) {
 try {
 const status = await apiFetch(`/slos/${slo.id}/status`);
 statusData[slo.id] = status;
 } catch (e) {
 console.error(`Failed to fetch status for SLO ${slo.id}`);
 }
 }
 setSloStatusList(statusData);
 } catch (e: any) {
 toast({ title: 'Error fetching SLOs', description: e.message, type: 'error' });
 } finally {
 setLoading(false);
 }
 };

 const handleCreate = async (e: React.FormEvent) => {
 e.preventDefault();
 try {
 await apiFetch('/slos', {
 method: 'POST',
 body: JSON.stringify(formData)
 });
 toast({ title: 'SLO created' });
 setShowModal(false);
 setFormData({ name: '', service: '', sli_type: 'availability', target_percentage: 99.9, window_days: 30, latency_threshold_ms: 500 });
 fetchSLOs();
 } catch (e: any) {
 toast({ title: 'Failed to create SLO', description: e.message, type: 'error' });
 }
 };

 const deleteSLO = (id: number) => {
 setConfirmTitle('Delete SLO');
 setConfirmMessage('Are you sure you want to delete this SLO?');
 setConfirmCallback(() => async () => {
 try {
 await apiFetch(`/slos/${id}`, { method: 'DELETE' });
 fetchSLOs();
 } catch (e: any) {
 toast({ title: 'Failed to delete SLO', type: 'error' });
 }
 });
 setConfirmOpen(true);
 };

 // Very simple sparkline renderer
 const renderSparkline = (dataPoints: any[], status: string) => {
 if (!dataPoints || dataPoints.length === 0) return null;
 const min = Math.min(...dataPoints.map((d: any) => d.value));
 const max = Math.max(...dataPoints.map((d: any) => d.value));
 const range = max - min || 1;
 
 let color = '#3b82f6'; // blue
 if (status === 'WARNING') color = '#eab308'; // yellow
 if (status === 'BREACHED') color = '#ef4444'; // red

 const points = dataPoints.map((d: any, i: number) => {
 const x = (i / (dataPoints.length - 1)) * 100;
 const y = 100 - ((d.value - min) / range) * 100;
 return `${x},${y}`;
 }).join(' ');

 return (
 <svg className="w-full h-12" preserveAspectRatio="none">
 <polyline points={points} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" />
 </svg>
 );
 };

 return (
 <div className="flex flex-col h-full space-y-6">
 <div className="flex items-center justify-between">
 <div>
 <h1 className="text-2xl font-bold text-[var(--text-primary)]">Service Level Objectives</h1>
 <p className="text-[var(--text-secondary)] mt-1">Monitor reliability targets, error budgets, and burn rates.</p>
 </div>
 <button 
 onClick={() => setShowModal(true)}
 className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded font-medium flex items-center gap-2 transition-colors"
 >
 <Plus size={16} /> Create SLO
 </button>
 </div>

 <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
 {loading ? (
 // Shimmer loading
 [...Array(3)].map((_, i) => (
 <div key={i} className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-6">
 <div className="flex justify-between mb-4">
 <div className="shimmer-bg h-6 rounded w-1/2"></div>
 <div className="shimmer-bg h-6 rounded w-16"></div>
 </div>
 <div className="space-y-3 mt-8">
 <div className="shimmer-bg h-4 rounded w-full"></div>
 <div className="shimmer-bg h-4 rounded w-3/4"></div>
 </div>
 </div>
 ))
 ) : slos.length === 0 ? (
 <div className="col-span-full flex flex-col items-center justify-center p-12 bg-[var(--bg-card)] border border-[var(--border)] rounded-lg text-center">
 <Zap size={48} className="mb-4 text-[var(--text-secondary)] opacity-50" />
 <h3 className="text-lg font-medium text-[var(--text-primary)] mb-2">No SLOs Defined</h3>
 <p className="text-[var(--text-secondary)] mb-6 max-w-md">Set up your first Service Level Objective to start tracking error budgets and burn rates based on tracing data.</p>
 <button 
 onClick={() => setShowModal(true)}
 className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded font-medium transition-colors"
 >
 Create your first SLO
 </button>
 </div>
 ) : (
 slos.map(slo => {
 const status = sloStatusList[slo.id];
 
 let statusIcon = <CheckCircle2 size={24} className="text-green-500" />;
 let statusBadge = <span className="bg-green-500/10 text-green-500 px-2 py-0.5 rounded text-xs font-medium uppercase tracking-wider">Healthy</span>;
 let gaugeColor = 'bg-green-500';
 
 // NO_DATA means nothing in the window could be measured. It must not render
 // as a green "Healthy" card — an unmeasured objective is the one an operator
 // most needs to notice.
 const noData = status?.status === 'NO_DATA';

 if (noData) {
 statusIcon = <HelpCircle size={24} className="text-[var(--text-muted)]" />;
 statusBadge = <span className="bg-[var(--bg-app)] text-[var(--text-muted)] px-2 py-0.5 rounded text-xs font-medium uppercase tracking-wider">No data</span>;
 gaugeColor = 'bg-[var(--text-dimmed)]';
 } else if (status?.status === 'WARNING') {
 statusIcon = <AlertCircle size={24} className="text-yellow-500" />;
 statusBadge = <span className="bg-yellow-500/10 text-yellow-500 px-2 py-0.5 rounded text-xs font-medium uppercase tracking-wider">Warning</span>;
 gaugeColor = 'bg-yellow-500';
 } else if (status?.status === 'BREACHED') {
 statusIcon = <ShieldAlert size={24} className="text-red-500" />;
 statusBadge = <span className="bg-red-500/10 text-red-500 px-2 py-0.5 rounded text-xs font-medium uppercase tracking-wider">Breached</span>;
 gaugeColor = 'bg-red-500';
 }

 // Guard the divide: a zero budget produced NaN and an unrenderable bar.
 const budgetPercent = status && status.error_budget_total > 0
 ? Math.max(0, Math.min(100, (status.error_budget_remaining / status.error_budget_total) * 100))
 : 0;

 return (
 <div key={slo.id} className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-6 relative group overflow-hidden">
 <button 
 onClick={() => deleteSLO(slo.id)}
 className="absolute top-4 right-4 text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
 >
 <X size={16} />
 </button>
 
 <div className="flex justify-between items-start mb-6">
 <div>
 <h3 className="text-lg font-bold text-[var(--text-primary)] leading-tight mb-1">{slo.name}</h3>
 <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
 <span>{slo.service}</span>
 <span>•</span>
 <span>{slo.sli_type}</span>
 </div>
 </div>
 {statusIcon}
 </div>

 {status && noData ? (
 <div className="py-6 text-center">
 <HelpCircle className="mx-auto mb-2 text-[var(--text-dimmed)]" size={24} />
 <div className="text-sm font-medium text-[var(--text-secondary)]">Not enough data to measure</div>
 <p className="text-xs text-[var(--text-muted)] mt-1.5 leading-relaxed max-w-[320px] mx-auto">
 {slo.sli_type === 'latency'
 ? `No logs from ${slo.service} in the last ${slo.window_days}d carry a duration field, so this objective cannot be evaluated.`
 : `No logs from ${slo.service} in the last ${slo.window_days}d.`}
 </p>
 {status.threshold_ms ? (
 <p className="text-[10px] text-[var(--text-dimmed)] mt-2 font-mono">objective ≤ {status.threshold_ms}ms</p>
 ) : null}
 </div>
 ) : status ? (
 <>
 <div className="grid grid-cols-2 gap-4 mb-6">
 <div>
 <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-1">Current SLI</div>
 <div className="text-2xl font-bold text-[var(--text-primary)]">
 {status.current_value.toFixed(2)}%
 </div>
 <div className="text-xs text-[var(--text-secondary)] mt-1">Target: {slo.target_percentage}%</div>
 </div>
 <div>
 <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-1">Burn Rate</div>
 <div className="text-2xl font-bold text-[var(--text-primary)]">
 {status.burn_rate.toFixed(1)}x
 </div>
 <div className="text-xs text-[var(--text-secondary)] mt-1">{statusBadge}</div>
 </div>
 </div>

 <div className="mb-4">
 <div className="flex justify-between text-xs mb-1">
 <span className="text-[var(--text-secondary)]">Error Budget Remaining</span>
 <span className="font-medium text-[var(--text-primary)]">
 {status.error_budget_remaining} / {status.error_budget_total} events
 </span>
 </div>
 <div className="h-2 bg-[var(--bg-app)] rounded-full overflow-hidden">
 <div 
 className={`h-full ${gaugeColor} transition-all`} 
 style={{ width: `${budgetPercent}%` }}
 />
 </div>
 </div>
 
 <div className="mt-4 pt-4 border-t border-[var(--border)]">
 <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-2">Trend ({slo.window_days}d)</div>
 {renderSparkline(status.data_points, status.status)}
 </div>
 </>
 ) : (
 <div className="py-8 text-center text-sm text-[var(--text-secondary)]">
 <Activity className="animate-pulse mx-auto mb-2 opacity-50" size={24} />
 Calculating status...
 </div>
 )}
 </div>
 );
 })
 )}
 </div>

 {showModal && (
 <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
 <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg shadow-xl w-full max-w-md overflow-hidden">
 <div className="p-4 border-b border-[var(--border)] flex justify-between items-center bg-[var(--bg-app)]">
 <h2 className="font-bold text-[var(--text-primary)]">Create New SLO</h2>
 <button onClick={() => setShowModal(false)} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
 <X size={20} />
 </button>
 </div>
 <form onSubmit={handleCreate} className="p-6 space-y-4">
 <div>
 <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">SLO Name</label>
 <input 
 type="text" 
 required
 value={formData.name}
 onChange={e => setFormData({...formData, name: e.target.value})}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-blue-500"
 placeholder="e.g. Payment Gateway Availability"
 />
 </div>
 
 <div>
 <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Service Name</label>
 <input 
 type="text" 
 required
 value={formData.service}
 onChange={e => setFormData({...formData, service: e.target.value})}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-blue-500"
 placeholder="e.g. payment-api"
 />
 </div>

 <div>
 <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">SLI Type</label>
 <select 
 value={formData.sli_type}
 onChange={e => setFormData({...formData, sli_type: e.target.value})}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-blue-500"
 >
 <option value="availability">Availability (Success Rate)</option>
 <option value="latency">Latency (request duration)</option>
 </select>
 </div>

 {/* The latency objective used to be a 500ms constant inside the engine, so
 every latency SLO shared a threshold nobody chose. */}
 {formData.sli_type === 'latency' && (
 <div>
 <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Latency Objective (ms)</label>
 <input
 type="number"
 min="1" max="600000"
 required
 value={formData.latency_threshold_ms}
 onChange={e => setFormData({...formData, latency_threshold_ms: parseFloat(e.target.value)})}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-blue-500"
 />
 <p className="text-xs text-[var(--text-muted)] mt-1.5 leading-relaxed">
 Measured only over log lines carrying a duration field (duration_ms, latency_ms, elapsed_ms, response_time_ms). Lines without one are excluded, not counted as passing.
 </p>
 </div>
 )}

 <div className="grid grid-cols-2 gap-4">
 <div>
 <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Target %</label>
 <input 
 type="number" 
 step="0.01"
 min="50" max="99.99"
 required
 value={formData.target_percentage}
 onChange={e => setFormData({...formData, target_percentage: parseFloat(e.target.value)})}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-blue-500"
 />
 </div>
 <div>
 <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Window (Days)</label>
 <input 
 type="number" 
 min="1" max="90"
 required
 value={formData.window_days}
 onChange={e => setFormData({...formData, window_days: parseInt(e.target.value)})}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-blue-500"
 />
 </div>
 </div>

 <div className="pt-4 flex justify-end gap-3 border-t border-[var(--border)] mt-6">
 <button 
 type="button" 
 onClick={() => setShowModal(false)}
 className="px-4 py-2 rounded text-sm font-medium border border-[var(--border)] hover:bg-[var(--bg-app)] transition-colors text-[var(--text-primary)]"
 >
 Cancel
 </button>
 <button 
 type="submit" 
 className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded text-sm font-medium transition-colors"
 >
 Create SLO
 </button>
 </div>
 </form>
 </div>
 </div>
 )}

 <ConfirmModal
 isOpen={confirmOpen}
 onClose={() => setConfirmOpen(false)}
 onConfirm={confirmCallback || (() => {})}
 title={confirmTitle}
 message={confirmMessage}
 />
 </div>
 );
}
