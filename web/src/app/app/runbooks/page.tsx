'use client';

import React, { useState, useEffect } from 'react';
import { apiFetch } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import { Play, Plus, Trash2, Power, Terminal, Settings, Globe, ShieldAlert, X } from 'lucide-react';
import { ConfirmModal } from '@/components/ConfirmModal';

export default function RunbooksPage() {
 const { toast } = useToast();
 const [runbooks, setRunbooks] = useState<any[]>([]);
 const [executions, setExecutions] = useState<any[]>([]);
 const [loading, setLoading] = useState(true);
 
 const [showCreateModal, setShowCreateModal] = useState(false);
 const [formData, setFormData] = useState({
 name: '',
 trigger_keyword: '',
 action_type: 'webhook',
 action_url: '',
 action_service: '',
 slack_webhook_url: '',
 pagerduty_integration_key: '',
 jira_url: '',
 jira_user: '',
 jira_api_token: ''
 });

 const [activeTab, setActiveTab] = useState('rules');

 const [confirmOpen, setConfirmOpen] = useState(false);
 const [confirmTitle, setConfirmTitle] = useState('');
 const [confirmMessage, setConfirmMessage] = useState('');
 const [confirmCallback, setConfirmCallback] = useState<(() => void) | null>(null);

 useEffect(() => {
 // eslint-disable-next-line react-hooks/immutability
 fetchRunbooks();
 // eslint-disable-next-line react-hooks/immutability
 fetchExecutions();
 }, []);

 const fetchRunbooks = async () => {
 setLoading(true);
 try {
 const data = await apiFetch('/runbooks');
 setRunbooks(data || []);
 } catch (e: any) {
 toast({ title: 'Error fetching runbooks', description: e.message, type: 'error' });
 } finally {
 setLoading(false);
 }
 };

 const fetchExecutions = async () => {
 try {
 const data = await apiFetch('/runbooks/executions');
 setExecutions(data || []);
 } catch (e: any) {
 console.error(e);
 }
 };

 const createRunbook = async (e: React.FormEvent) => {
 e.preventDefault();
 
 // Construct trigger and steps from form
 const payload = {
 name: formData.name,
 trigger_condition: formData.trigger_keyword ? { keyword: formData.trigger_keyword } : {},
 steps: [
 {
 name: `Execute ${formData.action_type}`,
 action: formData.action_type,
 ...(formData.action_type === 'webhook' ? { url: formData.action_url } : {}),
 ...(formData.action_type === 'restart_service' || formData.action_type === 'scale_pods' ? { service: formData.action_service } : {}),
 ...(formData.action_type === 'slack_notification' ? { slack_webhook_url: formData.slack_webhook_url } : {}),
 ...(formData.action_type === 'escalate' ? { pagerduty_integration_key: formData.pagerduty_integration_key } : {}),
 ...(formData.action_type === 'jira_issue' ? { 
   jira_url: formData.jira_url, 
   jira_user: formData.jira_user, 
   jira_api_token: formData.jira_api_token 
 } : {}),
 }
 ],
 enabled: true
 };

 try {
 await apiFetch('/runbooks', {
 method: 'POST',
 body: JSON.stringify(payload)
 });
 toast({ title: 'Runbook created' });
 setShowCreateModal(false);
 fetchRunbooks();
 } catch (e: any) {
 toast({ title: 'Creation failed', description: e.message, type: 'error' });
 }
 };

 // Execute a runbook on demand rather than waiting for an incident to match it.
 const [runningId, setRunningId] = useState<number | null>(null);
 const runNow = async (rb: any) => {
  setRunningId(rb.id);
  try {
   const res = await apiFetch(`/runbooks/${rb.id}/run`, { method: 'POST', body: JSON.stringify({}) });
   toast({
    title: `${rb.name}: ${res.status}`,
    description: res.logs?.[res.logs.length - 1] || 'Execution finished',
    type: res.status === 'FAILED' ? 'error' : 'info',
   });
   setActiveTab('history');
   fetchExecutions();
  } catch (e: any) {
   toast({ title: 'Execution failed', description: e.message, type: 'error' });
  } finally {
   setRunningId(null);
  }
 };

 const toggleEnabled = async (rb: any) => {
  try {
   await apiFetch(`/runbooks/${rb.id}`, { method: 'PUT', body: JSON.stringify({ enabled: !rb.enabled }) });
   toast({ title: rb.enabled ? 'Runbook disabled' : 'Runbook enabled' });
   fetchRunbooks();
  } catch (e: any) {
   toast({ title: 'Failed to update runbook', description: e.message, type: 'error' });
  }
 };

 // Full step-by-step log of one execution.
 const [openExecution, setOpenExecution] = useState<any | null>(null);

 const deleteRunbook = (id: number) => {
 setConfirmTitle('Delete Runbook');
 setConfirmMessage('Delete this runbook?');
 setConfirmCallback(() => async () => {
 try {
 await apiFetch(`/runbooks/${id}`, { method: 'DELETE' });
 toast({ title: 'Runbook deleted' });
 fetchRunbooks();
 } catch (e: any) {
 toast({ title: 'Deletion failed', type: 'error' });
 }
 });
 setConfirmOpen(true);
 };

 return (
 <div className="flex flex-col h-full space-y-6">
 <div className="flex items-center justify-between">
 <div>
 <h1 className="text-2xl font-bold text-[var(--text-primary)]">Runbook Automation</h1>
 <p className="text-[var(--text-secondary)] mt-1">Automatically remediate issues based on incident triggers.</p>
 </div>
 <button 
 onClick={() => setShowCreateModal(true)}
 className="bg-[var(--primary)] hover:bg-[var(--primary)] text-white px-4 py-2 rounded font-medium flex items-center gap-2 transition-colors"
 >
 <Plus size={16} /> Create Runbook
 </button>
 </div>

 <div className="flex gap-4 border-b border-[var(--border)]">
 <button 
 className={`pb-3 text-sm font-medium border-b-2 transition-colors ${activeTab === 'rules' ? 'border-[var(--primary)] text-[var(--primary)]' : 'border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}
 onClick={() => setActiveTab('rules')}
 >
 Active Runbooks
 </button>
 <button 
 className={`pb-3 text-sm font-medium border-b-2 transition-colors ${activeTab === 'history' ? 'border-[var(--primary)] text-[var(--primary)]' : 'border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}
 onClick={() => { setActiveTab('history'); fetchExecutions(); }}
 >
 Execution History
 </button>
 </div>

 {activeTab === 'rules' && (
 <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
 {loading ? (
 // Shimmer
 [...Array(2)].map((_, i) => (
 <div key={i} className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-6 animate-pulse h-48"></div>
 ))
 ) : runbooks.length === 0 ? (
 <div className="col-span-full py-12 text-center bg-[var(--bg-card)] border border-[var(--border)] rounded-lg">
 <Play className="mx-auto text-[var(--text-secondary)] opacity-50 mb-3" size={48} />
 <h3 className="text-lg font-medium text-[var(--text-primary)] mb-1">No Runbooks Configured</h3>
 <p className="text-sm text-[var(--text-secondary)] mb-6 max-w-md mx-auto">Create rules to automate actions like calling webhooks or restarting services when specific incidents occur.</p>
 <button onClick={() => setShowCreateModal(true)} className="bg-[var(--primary)] hover:bg-[var(--primary)] text-white px-4 py-2 rounded text-sm font-medium transition-colors">Create Runbook</button>
 </div>
 ) : (
 runbooks.map(rb => (
 <div key={rb.id} className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-6 relative group">
 <div className="absolute top-4 right-4 flex items-center gap-2">
 <button
 onClick={() => runNow(rb)}
 disabled={runningId === rb.id}
 title="Run this runbook now"
 className="text-[var(--text-secondary)] hover:text-green-500 transition-colors disabled:opacity-40"
 >
 <Play size={16} className={runningId === rb.id ? 'animate-pulse' : ''} />
 </button>
 <button
 onClick={() => toggleEnabled(rb)}
 title={rb.enabled ? 'Disable runbook' : 'Enable runbook'}
 className={`transition-colors ${rb.enabled ? 'text-green-500 hover:text-[var(--text-secondary)]' : 'text-[var(--text-muted)] hover:text-green-500'}`}
 >
 <Power size={16} />
 </button>
 <button
 onClick={() => deleteRunbook(rb.id)}
 title="Delete runbook"
 className="text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
 >
 <Trash2 size={16} />
 </button>
 </div>

 <div className="flex justify-between items-start mb-6">
 <div>
 <div className="flex items-center gap-2 mb-1">
 <h3 className="text-lg font-bold text-[var(--text-primary)]">{rb.name}</h3>
 {rb.enabled ? 
 <span className="bg-green-500/10 text-green-500 text-[10px] uppercase font-bold px-2 py-0.5 rounded">Active</span> : 
 <span className="bg-[var(--bg-app)] text-[var(--text-secondary)] text-[10px] uppercase font-bold px-2 py-0.5 rounded">Disabled</span>
 }
 </div>
 </div>
 </div>

 <div className="space-y-4">
 <div className="bg-[var(--bg-app)] p-3 rounded border border-[var(--border)]">
 <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-2 flex items-center gap-1">
 <ShieldAlert size={14} /> When Triggered By
 </div>
 <code className="text-sm text-[var(--primary)]">
 {JSON.stringify(rb.trigger_condition) === '{}' ? 'Any Incident' : JSON.stringify(rb.trigger_condition)}
 </code>
 </div>
 
 <div className="bg-[var(--bg-app)] p-3 rounded border border-[var(--border)]">
 <div className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-2 flex items-center gap-1">
 <Settings size={14} /> Then Execute
 </div>
 <ul className="text-sm text-[var(--text-primary)] space-y-2">
 {rb.steps.map((step: any, i: number) => (
 <li key={i} className="flex items-center gap-2">
 <span className="w-5 h-5 bg-[var(--bg-card)] border border-[var(--border)] rounded-full flex items-center justify-center text-xs">{i+1}</span>
 {step.name}
 </li>
 ))}
 </ul>
 </div>
 </div>
 </div>
 ))
 )}
 </div>
 )}

 {activeTab === 'history' && (
 <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg overflow-hidden">
 {executions.length === 0 ? (
 <div className="py-12 text-center text-[var(--text-secondary)] text-sm">No runbooks have been executed yet.</div>
 ) : (
 <div className="overflow-x-auto">
 <table className="w-full text-left text-sm">
 <thead className="bg-[var(--bg-app)] text-xs uppercase border-b border-[var(--border)] text-[var(--text-secondary)]">
 <tr>
 <th className="px-6 py-3 font-medium">Time</th>
 <th className="px-6 py-3 font-medium">Runbook ID</th>
 <th className="px-6 py-3 font-medium">Incident ID</th>
 <th className="px-6 py-3 font-medium">Status</th>
 <th className="px-6 py-3 font-medium">Logs</th>
 </tr>
 </thead>
 <tbody className="divide-y divide-[var(--border)] text-[var(--text-primary)]">
 {executions.map(exec => (
 <tr key={exec.id} onClick={() => setOpenExecution(exec)} className="hover:bg-[var(--bg-app)] cursor-pointer">
 <td className="px-6 py-4 whitespace-nowrap">{new Date(exec.created_at).toLocaleString()}</td>
 <td className="px-6 py-4">#{exec.runbook_id}</td>
 <td className="px-6 py-4 text-[var(--primary)]">#{exec.incident_id}</td>
 <td className="px-6 py-4">
 <span className={`px-2 py-0.5 rounded text-xs font-bold ${
 exec.status === 'SUCCESS' ? 'bg-green-500/10 text-green-500' :
 exec.status === 'FAILED' ? 'bg-red-500/10 text-red-500' :
 'bg-yellow-500/10 text-yellow-500'
 }`}>
 {exec.status}
 </span>
 </td>
 <td className="px-6 py-4 font-mono text-[10px] text-[var(--text-secondary)] max-w-xs truncate">
 {exec.logs[exec.logs.length - 1] || 'No logs'}
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 )}
 </div>
 )}

 {openExecution && (
 <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4" onClick={() => setOpenExecution(null)}>
 <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg shadow-xl w-full max-w-2xl overflow-hidden" onClick={e => e.stopPropagation()}>
 <div className="p-4 border-b border-[var(--border)] flex justify-between items-center bg-[var(--bg-app)]">
 <div>
 <h2 className="font-bold text-[var(--text-primary)]">Execution #{openExecution.id}</h2>
 <p className="text-xs text-[var(--text-secondary)] mt-0.5">
 Runbook #{openExecution.runbook_id}
 {openExecution.incident_id ? ` · Incident #${openExecution.incident_id}` : ' · No incident context'}
 {' · '}{new Date(openExecution.created_at).toLocaleString()}
 </p>
 </div>
 <button onClick={() => setOpenExecution(null)} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
 <X size={20} />
 </button>
 </div>
 <div className="p-4 max-h-[60vh] overflow-auto">
 <pre className="text-xs font-mono text-[var(--text-secondary)] whitespace-pre-wrap leading-relaxed">
{(openExecution.logs || []).join('\n') || 'No step logs were recorded.'}
 </pre>
 </div>
 </div>
 </div>
 )}

 {showCreateModal && (
 <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
 <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg shadow-xl w-full max-w-md overflow-hidden">
 <div className="p-4 border-b border-[var(--border)] flex justify-between items-center bg-[var(--bg-app)]">
 <h2 className="font-bold text-[var(--text-primary)]">Configure Automation</h2>
 <button onClick={() => setShowCreateModal(false)} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
 <X size={20} />
 </button>
 </div>
 <form onSubmit={createRunbook} className="p-6 space-y-4">
 <div>
 <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Runbook Name</label>
 <input 
 type="text" 
 required
 value={formData.name}
 onChange={e => setFormData({...formData, name: e.target.value})}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
 placeholder="e.g. Auto-Restart Payment Pods"
 />
 </div>

 <div>
 <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Trigger Condition: Keyword (Optional)</label>
 <input 
 type="text" 
 value={formData.trigger_keyword}
 onChange={e => setFormData({...formData, trigger_keyword: e.target.value})}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
 placeholder="e.g. OOMKilled"
 />
 </div>

 <div>
 <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Action Type</label>
 <select 
 value={formData.action_type}
 onChange={e => setFormData({...formData, action_type: e.target.value})}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
 >
 <option value="webhook">Call Webhook</option>
 <option value="restart_service">Restart Service</option>
 <option value="escalate">Escalate via PagerDuty</option>
 <option value="slack_notification">Send Slack Notification</option>
 <option value="jira_issue">Create Jira Issue</option>
 <option value="scale_pods">Scale Up Pods (K8s)</option>
 </select>
 </div>

 {formData.action_type === 'webhook' && (
 <div>
 <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Webhook URL</label>
 <input 
 type="url" 
 required
 value={formData.action_url}
 onChange={e => setFormData({...formData, action_url: e.target.value})}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
 placeholder="https://hooks.slack.com/services/..."
 />
 </div>
 )}

 {(formData.action_type === 'restart_service' || formData.action_type === 'scale_pods') && (
 <div>
 <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Service Name</label>
 <input 
 type="text" 
 required
 value={formData.action_service}
 onChange={e => setFormData({...formData, action_service: e.target.value})}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
 placeholder="e.g. payment-api"
 />
 </div>
 )}

 {formData.action_type === 'slack_notification' && (
  <div>
  <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Slack Webhook URL</label>
  <input 
  type="url" 
  required
  value={formData.slack_webhook_url}
  onChange={e => setFormData({...formData, slack_webhook_url: e.target.value})}
  className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
  placeholder="https://hooks.slack.com/services/..."
  />
  </div>
  )}

  {formData.action_type === 'escalate' && (
  <div>
  <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">PagerDuty Routing Key</label>
  <input 
  type="text" 
  required
  value={formData.pagerduty_integration_key}
  onChange={e => setFormData({...formData, pagerduty_integration_key: e.target.value})}
  className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
  placeholder="e.g. R02..."
  />
  </div>
  )}

  {formData.action_type === 'jira_issue' && (
  <div className="space-y-3">
  <div>
  <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Jira Base URL</label>
  <input 
  type="url" 
  required
  value={formData.jira_url}
  onChange={e => setFormData({...formData, jira_url: e.target.value})}
  className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
  placeholder="https://yourdomain.atlassian.net"
  />
  </div>
  <div>
  <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Jira User Email</label>
  <input 
  type="email" 
  required
  value={formData.jira_user}
  onChange={e => setFormData({...formData, jira_user: e.target.value})}
  className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
  placeholder="sre@domain.com"
  />
  </div>
  <div>
  <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Jira API Token</label>
  <input 
  type="password" 
  required
  value={formData.jira_api_token}
  onChange={e => setFormData({...formData, jira_api_token: e.target.value})}
  className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
  placeholder="Atlassian API Token"
  />
  </div>
  </div>
  )}

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
 className="bg-[var(--primary)] hover:bg-[var(--primary)] text-white px-4 py-2 rounded text-sm font-medium transition-colors"
 >
 Create Rule
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
