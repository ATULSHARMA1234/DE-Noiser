'use client';

import React, { useState, useEffect } from 'react';
import { apiFetch } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import { Settings, Plus, Plug, CheckCircle2, Trash2, Cloud } from 'lucide-react';
import { ConfirmModal } from '@/components/ConfirmModal';

export default function IntegrationsPage() {
 const { toast } = useToast();
 const [integrations, setIntegrations] = useState<any[]>([]);
 const [loading, setLoading] = useState(true);
 
 const [showConfigModal, setShowConfigModal] = useState(false);
 const [selectedProvider, setSelectedProvider] = useState('');
 // Set when the modal is editing an already-connected integration rather than
 // creating one; null means "connect".
 const [editing, setEditing] = useState<any | null>(null);
 const [credential, setCredential] = useState('');
 // GitHub needs owner/name as well as a token — without it every call fails
 // "not configured", and there was nowhere in this dialog to supply it.
 const [repo, setRepo] = useState('');
 // Deployments sync into markers under a service name. Defaulting to the repo
 // name is right for one-service repos and collapses a monorepo's services into
 // a single series, so it is overridable.
 const [serviceName, setServiceName] = useState('');
 const [busyId, setBusyId] = useState<number | null>(null);

 const [confirmOpen, setConfirmOpen] = useState(false);
 const [confirmTitle, setConfirmTitle] = useState('');
 const [confirmMessage, setConfirmMessage] = useState('');
 const [confirmCallback, setConfirmCallback] = useState<(() => void) | null>(null);
 
 const MARKETPLACE = [
 { id: 'aws', name: 'AWS CloudWatch', icon: Cloud, color: 'text-orange-500', desc: 'Ingest logs and metrics from AWS services.' },
 { id: 'slack', name: 'Slack', icon: Cloud, color: 'text-purple-500', desc: 'Send alerts and runbook notifications to Slack channels.' },
 { id: 'github', name: 'GitHub Actions', icon: Cloud, color: 'text-gray-400', desc: 'Correlate deployments and pull requests with anomalies.' },
 { id: 'pagerduty', name: 'PagerDuty', icon: Plug, color: 'text-green-500', desc: 'Trigger and escalate incidents automatically.' }
 ];

 useEffect(() => {
 // eslint-disable-next-line react-hooks/immutability
 fetchIntegrations();
 }, []);

 const fetchIntegrations = async () => {
 setLoading(true);
 try {
 const data = await apiFetch('/integrations');
 setIntegrations(data || []);
 } catch (e: any) {
 toast({ title: 'Error fetching integrations', description: e.message, type: 'error' });
 } finally {
 setLoading(false);
 }
 };

 const openConnect = (providerId: string) => {
 setSelectedProvider(providerId);
 setEditing(null);
 setCredential('');
 setRepo('');
 setServiceName('');
 setShowConfigModal(true);
 };

 const openConfigure = (providerId: string, integration: any) => {
 setSelectedProvider(providerId);
 setEditing(integration);
 setCredential('');
 setRepo(integration?.config?.repo || '');
 setServiceName(integration?.config?.service || '');
 setShowConfigModal(true);
 };

 // Verify the stored credential actually works, rather than finding out later
 // when an alert silently fails to deliver.
 const testIntegration = async (integration: any) => {
 setBusyId(integration.id);
 try {
 const res = await apiFetch(`/integrations/${integration.id}/test`, { method: 'POST' });
 toast({
 title: res.status === 'ok' ? 'Connection verified' : `Test ${res.status}`,
 description: res.detail,
 type: res.status === 'failed' ? 'error' : 'info',
 });
 } catch (e: any) {
 toast({ title: 'Test failed', description: e.message, type: 'error' });
 } finally {
 setBusyId(null);
 }
 };

 // Pull deployments in as markers the Metrics page can correlate against.
 const syncIntegration = async (integration: any) => {
 setBusyId(integration.id);
 try {
 const res = await apiFetch(`/integrations/${integration.id}/sync`, { method: 'POST' });
 toast({
 title: `Synced ${res.repo}`,
 description: `${res.deployments_imported} new deployment marker(s) from ${res.deployments_seen} deployment(s)`,
 });
 fetchIntegrations();
 } catch (e: any) {
 toast({ title: 'Sync failed', description: e.message, type: 'error' });
 } finally {
 setBusyId(null);
 }
 };

 const handleSubmit = async (e: React.FormEvent) => {
 e.preventDefault();
 try {
 const providerInfo = MARKETPLACE.find(m => m.id === selectedProvider);
 if (editing) {
 // Only send the credential when one was typed; an empty field means
 // "keep the stored token" rather than "erase it".
 const config: Record<string, any> = { updated_at: new Date().toISOString() };
 if (credential) config.api_key = credential;
 if (selectedProvider === 'github') {
 config.repo = repo.trim();
 config.service = serviceName.trim();
 }
 await apiFetch(`/integrations/${editing.id}`, { method: 'PUT', body: JSON.stringify({ config }) });
 toast({ title: 'Integration updated' });
 } else {
 await apiFetch('/integrations', {
 method: 'POST',
 body: JSON.stringify({
 provider: selectedProvider,
 name: `${providerInfo?.name} Connection`,
 config: {
 connected_at: new Date().toISOString(),
 ...(credential ? { api_key: credential } : {}),
 ...(selectedProvider === 'github' && repo.trim() ? { repo: repo.trim() } : {}),
 ...(selectedProvider === 'github' && serviceName.trim() ? { service: serviceName.trim() } : {}),
 }
 })
 });
 toast({ title: 'Integration connected' });
 }
 setShowConfigModal(false);
 setCredential('');
 fetchIntegrations();
 } catch (e: any) {
 toast({ title: editing ? 'Update failed' : 'Connection failed', description: e.message, type: 'error' });
 }
 };

 const disconnect = (id: number) => {
 setConfirmTitle('Disconnect Integration');
 setConfirmMessage('Disconnect this integration?');
 setConfirmCallback(() => async () => {
 try {
 await apiFetch(`/integrations/${id}`, { method: 'DELETE' });
 toast({ title: 'Integration disconnected' });
 fetchIntegrations();
 } catch (e: any) {
 toast({ title: 'Disconnection failed', type: 'error' });
 }
 });
 setConfirmOpen(true);
 };

 const isConnected = (providerId: string) => {
 return integrations.some(i => i.provider === providerId);
 };

 return (
 <div className="flex flex-col h-full space-y-6">
 <div className="flex items-center justify-between">
 <div>
 <h1 className="text-2xl font-bold text-[var(--text-primary)]">Integrations</h1>
 <p className="text-[var(--text-secondary)] mt-1">Connect third-party services to enrich logs, send alerts, and track deployments.</p>
 </div>
 </div>

 <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
 {MARKETPLACE.map(provider => {
 const connected = isConnected(provider.id);
 const activeIntegration = integrations.find(i => i.provider === provider.id);
 
 return (
 <div key={provider.id} className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-6 flex flex-col relative group">
 <div className="flex justify-between items-start mb-4">
 <div className={`p-3 rounded-lg bg-[var(--bg-app)] ${provider.color}`}>
 <provider.icon size={24} />
 </div>
 {connected && (
 <div className="flex items-center gap-1 text-xs font-bold text-green-500 bg-green-500/10 px-2 py-1 rounded uppercase">
 <CheckCircle2 size={14} /> Connected
 </div>
 )}
 </div>
 
 <h3 className="text-lg font-bold text-[var(--text-primary)] mb-2">{provider.name}</h3>
 <p className="text-sm text-[var(--text-secondary)] mb-6 flex-1">{provider.desc}</p>
 
 {connected ? (
 <div className="space-y-2">
 {activeIntegration?.config?.repo && (
 <p className="text-xs font-mono text-[var(--text-muted)] truncate">
 {activeIntegration.config.repo}
 {activeIntegration.config.last_synced_at && (
 <span className="ml-1">· synced {new Date(activeIntegration.config.last_synced_at).toLocaleDateString()}</span>
 )}
 </p>
 )}
 <div className="flex gap-2">
 <button
 onClick={() => openConfigure(provider.id, activeIntegration)}
 className="flex-1 py-2 text-sm font-medium border border-[var(--border)] rounded text-[var(--text-primary)] hover:bg-[var(--bg-app)] transition-colors"
 >
 Configure
 </button>
 <button
 onClick={() => testIntegration(activeIntegration)}
 disabled={busyId === activeIntegration.id}
 title="Verify the stored credential"
 className="px-3 py-2 text-sm font-medium border border-[var(--border)] rounded text-[var(--text-primary)] hover:bg-[var(--bg-app)] transition-colors disabled:opacity-40"
 >
 Test
 </button>
 {provider.id === 'github' && (
 <button
 onClick={() => syncIntegration(activeIntegration)}
 disabled={busyId === activeIntegration.id}
 title="Import deployments as markers"
 className="px-3 py-2 text-sm font-medium border border-[var(--border)] rounded text-[var(--text-primary)] hover:bg-[var(--bg-app)] transition-colors disabled:opacity-40"
 >
 Sync
 </button>
 )}
 <button
 onClick={() => disconnect(activeIntegration.id)}
 className="px-3 py-2 text-sm font-medium border border-red-500/20 text-red-500 rounded hover:bg-red-500/10 transition-colors"
 >
 <Trash2 size={16} />
 </button>
 </div>
 </div>
 ) : (
 <button
 onClick={() => openConnect(provider.id)}
 className="w-full py-2 text-sm font-medium bg-[var(--primary)] hover:bg-[var(--primary)] text-white rounded transition-colors"
 >
 Connect
 </button>
 )}
 </div>
 )
 })}
 </div>

 {showConfigModal && (
 <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
 <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg shadow-xl w-full max-w-md overflow-hidden">
 <div className="p-4 border-b border-[var(--border)] flex justify-between items-center bg-[var(--bg-app)]">
 <h2 className="font-bold text-[var(--text-primary)]">{editing ? 'Configure' : 'Connect'} {MARKETPLACE.find(m => m.id === selectedProvider)?.name}</h2>
 <button onClick={() => setShowConfigModal(false)} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
 &times;
 </button>
 </div>
 <form onSubmit={handleSubmit} className="p-6 space-y-4">
 <p className="text-sm text-[var(--text-secondary)]">
 {editing
 ? 'Update the stored credential. Leave the field empty to keep the current one.'
 : 'Provide the credential this integration should authenticate with.'}
 </p>

 <div>
 <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">API Key / Token</label>
 <input
 type="password"
 value={credential}
 onChange={(e) => setCredential(e.target.value)}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
 placeholder={editing ? 'Unchanged' : '••••••••••••••••'}
 />
 <p className="text-xs text-[var(--text-secondary)] mt-1">Stored server-side and never returned by the API.</p>
 </div>

 {selectedProvider === 'github' && (
 <div>
 <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Repository</label>
 <input
 type="text"
 value={repo}
 onChange={(e) => setRepo(e.target.value)}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
 placeholder="owner/name"
 />
 <p className="text-xs text-[var(--text-secondary)] mt-1">
 Required — GitHub calls (issues, Actions logs, deployment sync) all target one repository.
 </p>
 </div>
 )}

 {selectedProvider === 'github' && (
 <div>
 <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Service name <span className="text-[var(--text-muted)] font-normal">(optional)</span></label>
 <input
 type="text"
 value={serviceName}
 onChange={(e) => setServiceName(e.target.value)}
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
 placeholder={repo.trim() ? repo.trim().split('/').pop() : 'defaults to the repo name'}
 />
 <p className="text-xs text-[var(--text-secondary)] mt-1">
 Deployment markers are recorded under this service. Set it explicitly for a monorepo, where the repo name alone would merge every service into one series.
 </p>
 </div>
 )}

 <div className="pt-4 flex justify-end gap-3 border-t border-[var(--border)] mt-6">
 <button 
 type="button" 
 onClick={() => setShowConfigModal(false)}
 className="px-4 py-2 rounded text-sm font-medium border border-[var(--border)] hover:bg-[var(--bg-app)] transition-colors text-[var(--text-primary)]"
 >
 Cancel
 </button>
 <button 
 type="submit"
 className="bg-[var(--primary)] hover:bg-[var(--primary)] text-white px-4 py-2 rounded text-sm font-medium transition-colors"
 >
 {editing ? 'Save' : 'Authorize'}
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
