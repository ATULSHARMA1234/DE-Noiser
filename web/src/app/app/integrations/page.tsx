'use client';

import React, { useState, useEffect } from 'react';
import { apiFetch } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import { Settings, Plus, Plug, CheckCircle2, Trash2, Cloud } from 'lucide-react';

export default function IntegrationsPage() {
  const { toast } = useToast();
  const [integrations, setIntegrations] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  
  const [showConfigModal, setShowConfigModal] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState('');
  
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

  const handleConnect = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const providerInfo = MARKETPLACE.find(m => m.id === selectedProvider);
      await apiFetch('/integrations', {
        method: 'POST',
        body: JSON.stringify({
          provider: selectedProvider,
          name: `${providerInfo?.name} Connection`,
          config: { connected_at: new Date().toISOString() }
        })
      });
      toast({ title: 'Integration connected' });
      setShowConfigModal(false);
      fetchIntegrations();
    } catch (e: any) {
      toast({ title: 'Connection failed', description: e.message, type: 'error' });
    }
  };

  const disconnect = async (id: number) => {
    if (!confirm('Disconnect this integration?')) return;
    try {
      await apiFetch(`/integrations/${id}`, { method: 'DELETE' });
      toast({ title: 'Integration disconnected' });
      fetchIntegrations();
    } catch (e: any) {
      toast({ title: 'Disconnection failed', type: 'error' });
    }
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
                <div className="flex gap-2">
                  <button className="flex-1 py-2 text-sm font-medium border border-[var(--border)] rounded text-[var(--text-primary)] hover:bg-[var(--bg-app)] transition-colors">
                    Configure
                  </button>
                  <button 
                    onClick={() => disconnect(activeIntegration.id)}
                    className="px-3 py-2 text-sm font-medium border border-red-500/20 text-red-500 rounded hover:bg-red-500/10 transition-colors"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              ) : (
                <button 
                  onClick={() => { setSelectedProvider(provider.id); setShowConfigModal(true); }}
                  className="w-full py-2 text-sm font-medium bg-fuchsia-600 hover:bg-fuchsia-700 text-white rounded transition-colors"
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
              <h2 className="font-bold text-[var(--text-primary)]">Connect {MARKETPLACE.find(m => m.id === selectedProvider)?.name}</h2>
              <button onClick={() => setShowConfigModal(false)} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
                &times;
              </button>
            </div>
            <form onSubmit={handleConnect} className="p-6 space-y-4">
              <p className="text-sm text-[var(--text-secondary)]">Provide the necessary credentials to connect this integration. (Mock step for sandbox)</p>
              
              <div>
                <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">API Key / Token</label>
                <input 
                  type="password" 
                  className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-fuchsia-500"
                  placeholder="••••••••••••••••"
                />
              </div>

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
                  className="bg-fuchsia-600 hover:bg-fuchsia-700 text-white px-4 py-2 rounded text-sm font-medium transition-colors"
                >
                  Authorize
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
