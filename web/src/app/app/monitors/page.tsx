'use client';

import React, { useState, useEffect } from 'react';
import { apiFetch } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import { Bell, Plus, Edit2, Trash2, X, Activity, AlertTriangle, Terminal } from 'lucide-react';

export default function MonitorsPage() {
 const { toast } = useToast();
 const [monitors, setMonitors] = useState<any[]>([]);
 const [loading, setLoading] = useState(true);
 const [showModal, setShowModal] = useState(false);
 const [editingMonitor, setEditingMonitor] = useState<any>(null);

 // Form state
 const [formData, setFormData] = useState({
 name: '',
 type: 'log alert',
 query: '',
 message: '',
 severity: 'warning',
 threshold_critical: '',
 threshold_warning: '',
 enabled: true
 });

 useEffect(() => {
 fetchMonitors();
 }, []);

 async function fetchMonitors() {
 setLoading(true);
 try {
 const data = await apiFetch('/monitors');
 setMonitors(data || []);
 } catch (e: any) {
 toast({ title: 'Failed to fetch monitors', description: e.message, type: 'error' });
 } finally {
 setLoading(false);
 }
 };

 const handleOpenModal = (monitor: any = null) => {
 if (monitor) {
 setEditingMonitor(monitor);
 setFormData({
 name: monitor.name || '',
 type: monitor.type || 'log alert',
 query: monitor.query || '',
 message: monitor.message || '',
 severity: monitor.severity || 'warning',
 threshold_critical: monitor.threshold_critical?.toString() || '',
 threshold_warning: monitor.threshold_warning?.toString() || '',
 enabled: monitor.enabled !== false
 });
 } else {
 setEditingMonitor(null);
 setFormData({
 name: '',
 type: 'log alert',
 query: '',
 message: '',
 severity: 'warning',
 threshold_critical: '',
 threshold_warning: '',
 enabled: true
 });
 }
 setShowModal(true);
 };

 const handleSave = async (e: React.FormEvent) => {
 e.preventDefault();
 try {
 const payload = {
 ...formData,
 threshold_critical: formData.threshold_critical ? parseFloat(formData.threshold_critical) : null,
 threshold_warning: formData.threshold_warning ? parseFloat(formData.threshold_warning) : null,
 };

 if (editingMonitor) {
 await apiFetch(`/monitors/${editingMonitor.id}`, {
 method: 'PUT',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify(payload)
 });
 toast({ title: 'Monitor updated successfully' });
 } else {
 await apiFetch('/monitors', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify(payload)
 });
 toast({ title: 'Monitor created successfully' });
 }
 setShowModal(false);
 fetchMonitors();
 } catch (e: any) {
 const msg = typeof e?.message === 'string' ? e.message : JSON.stringify(e?.detail || e);
 toast({ title: 'Failed to save monitor', description: msg, type: 'error' });
 }
 };

 const handleDelete = async (id: number) => {
 if (!confirm('Are you sure you want to delete this monitor?')) return;
 try {
 await apiFetch(`/monitors/${id}`, { method: 'DELETE' });
 toast({ title: 'Monitor deleted' });
 fetchMonitors();
 } catch (e: any) {
 toast({ title: 'Failed to delete monitor', description: e.message, type: 'error' });
 }
 };

 const toggleEnabled = async (monitor: any) => {
 try {
 await apiFetch(`/monitors/${monitor.id}`, {
 method: 'PUT',
 body: JSON.stringify({ enabled: !monitor.enabled })
 });
 fetchMonitors();
 } catch (e: any) {
 toast({ title: 'Failed to update monitor state', description: e.message, type: 'error' });
 }
 };

 return (
 <div className="flex flex-col h-[calc(100vh-80px)] space-y-6">
 <div className="flex items-center justify-between">
 <div>
 <h1 className="text-2xl font-bold text-[var(--text-primary)]">Monitors</h1>
 <p className="text-[var(--text-secondary)] mt-1">Create and manage alerting rules based on your log queries and metrics.</p>
 </div>
 <button 
 onClick={() => handleOpenModal()}
 className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md font-medium flex items-center gap-2 transition-colors"
 >
 <Plus size={16} /> New Monitor
 </button>
 </div>

 <div className="flex-1 overflow-auto bg-[var(--bg-card)] border border-[var(--border)] rounded-lg">
 {loading ? (
 <div className="p-8 flex justify-center text-[var(--text-secondary)]">Loading monitors...</div>
 ) : monitors.length === 0 ? (
 <div className="flex flex-col items-center justify-center h-64 text-[var(--text-secondary)] text-center">
 <Bell size={48} className="mb-4 opacity-20" />
 <p className="text-lg font-medium text-[var(--text-primary)]">No monitors configured</p>
 <p className="text-sm mt-1 max-w-md">Get notified when logs match specific queries or metrics cross thresholds.</p>
 <button 
 onClick={() => handleOpenModal()}
 className="mt-6 text-blue-500 hover:underline text-sm font-medium"
 >
 Create your first monitor
 </button>
 </div>
 ) : (
 <table className="w-full text-left text-sm text-[var(--text-secondary)]">
 <thead className="bg-[var(--bg-app)] text-xs uppercase border-b border-[var(--border)]">
 <tr>
 <th className="px-6 py-3 font-medium">Status</th>
 <th className="px-6 py-3 font-medium">Name</th>
 <th className="px-6 py-3 font-medium">Query</th>
 <th className="px-6 py-3 font-medium">Type</th>
 <th className="px-6 py-3 font-medium text-right">Actions</th>
 </tr>
 </thead>
 <tbody className="divide-y divide-[var(--border)]">
 {monitors.map((m) => (
 <tr key={m.id} className="hover:bg-[var(--bg-app)] transition-colors">
 <td className="px-6 py-4">
 <button 
 onClick={() => toggleEnabled(m)}
 className={`px-2 py-1 rounded text-xs font-bold ${m.enabled ? 'bg-green-500/10 text-green-500 border border-green-500/20' : 'bg-zinc-500/10 text-zinc-500 border border-zinc-500/20'}`}
 >
 {m.enabled ? 'ACTIVE' : 'DISABLED'}
 </button>
 </td>
 <td className="px-6 py-4 font-semibold text-[var(--text-primary)]">{m.name}</td>
 <td className="px-6 py-4 font-mono text-xs truncate max-w-md">{m.query}</td>
 <td className="px-6 py-4 text-xs font-medium tracking-wider uppercase text-[var(--text-muted)] flex items-center gap-1">
 {m.type === 'log alert' ? <Terminal size={12} /> : <Activity size={12} />}
 {m.type}
 </td>
 <td className="px-6 py-4 text-right space-x-2">
 <button onClick={() => handleOpenModal(m)} className="p-1.5 hover:bg-[var(--bg-surface-hover)] rounded text-[var(--text-secondary)] hover:text-blue-400 transition-colors">
 <Edit2 size={16} />
 </button>
 <button onClick={() => handleDelete(m.id)} className="p-1.5 hover:bg-[var(--bg-surface-hover)] rounded text-[var(--text-secondary)] hover:text-red-400 transition-colors">
 <Trash2 size={16} />
 </button>
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 )}
 </div>

 {showModal && (
 <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
 <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-xl shadow-2xl w-full max-w-2xl flex flex-col max-h-[90vh]">
 <div className="p-5 border-b border-[var(--border)] flex justify-between items-center">
 <h2 className="text-lg font-bold text-[var(--text-primary)]">{editingMonitor ? 'Edit Monitor' : 'Create Monitor'}</h2>
 <button onClick={() => setShowModal(false)} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors p-1 rounded-md hover:bg-[var(--bg-app)]">
 <X size={20} />
 </button>
 </div>
 
 <form onSubmit={handleSave} className="flex flex-col flex-1 overflow-hidden">
 <div className="p-6 overflow-y-auto space-y-5">
 <div>
 <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">Monitor Name</label>
 <input 
 type="text" 
 required
 value={formData.name}
 onChange={e => setFormData({...formData, name: e.target.value})}
 placeholder="e.g. High Error Rate in Payment Service"
 className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-all"
 />
 </div>
 
 <div className="grid grid-cols-2 gap-4">
 <div>
 <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">Type</label>
 <select 
 value={formData.type}
 onChange={e => setFormData({...formData, type: e.target.value})}
 className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-all appearance-none"
 >
 <option value="log alert">Log Alert</option>
 <option value="metric alert">Metric Alert</option>
 </select>
 </div>
 <div>
 <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">Severity</label>
 <select 
 value={formData.severity}
 onChange={e => setFormData({...formData, severity: e.target.value})}
 className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-all appearance-none"
 >
 <option value="warning">Warning</option>
 <option value="critical">Critical</option>
 </select>
 </div>
 </div>

 <div>
 <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">Search Query</label>
 <input 
 type="text" 
 required
 value={formData.query}
 onChange={e => setFormData({...formData, query: e.target.value})}
 placeholder="e.g. level:ERROR AND service:payment"
 className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded px-3 py-2 text-sm font-mono text-[var(--text-primary)] focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-all"
 />
 </div>

 <div className="grid grid-cols-2 gap-4">
 <div>
 <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">Warning Threshold</label>
 <input 
 type="number" 
 value={formData.threshold_warning}
 onChange={e => setFormData({...formData, threshold_warning: e.target.value})}
 placeholder="e.g. 50"
 className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-all"
 />
 </div>
 <div>
 <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-1.5 flex items-center gap-1 text-red-400"><AlertTriangle size={12}/> Critical Threshold</label>
 <input 
 type="number" 
 value={formData.threshold_critical}
 onChange={e => setFormData({...formData, threshold_critical: e.target.value})}
 placeholder="e.g. 100"
 className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-all"
 />
 </div>
 </div>

 <div>
 <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">Notification Message</label>
 <textarea 
 value={formData.message}
 onChange={e => setFormData({...formData, message: e.target.value})}
 placeholder="Message to include in the alert notification..."
 rows={3}
 className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-all resize-none"
 />
 </div>
 </div>

 <div className="p-5 border-t border-[var(--border)] bg-[var(--bg-app)] flex justify-end gap-3 rounded-b-xl">
 <button 
 type="button" 
 onClick={() => setShowModal(false)}
 className="px-4 py-2 text-sm font-medium text-[var(--text-primary)] border border-[var(--border)] hover:bg-[var(--bg-card-hover)] rounded transition-colors"
 >
 Cancel
 </button>
 <button 
 type="submit" 
 className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded transition-colors"
 >
 {editingMonitor ? 'Save Changes' : 'Create Monitor'}
 </button>
 </div>
 </form>
 </div>
 </div>
 )}
 </div>
 );
}
