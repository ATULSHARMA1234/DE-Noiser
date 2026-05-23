'use client';

import React, { useState, useEffect } from 'react';
import { Shield, Cpu, HardDrive, Save, Check, RefreshCw, Webhook, Plus, Trash2, TestTube2, Bell, CheckCircle2, XCircle, AlertTriangle, ChevronDown } from 'lucide-react';
import { apiFetch, apiPut, apiPost, apiDelete } from '@/lib/api';

// ── Types ─────────────────────────────────────────────────────────────────────

interface WebhookDest {
  id: string;
  name: string;
  channel_type: 'slack' | 'pagerduty' | 'teams' | 'generic';
  url: string;
  min_priority: string;
  enabled: boolean;
  extra: Record<string, any>;
}

interface DeliveryRecord {
  webhook_id: string;
  alert_fingerprint: string;
  priority: string;
  status: 'delivered' | 'failed' | 'skipped';
  http_status: number | null;
  latency_ms: number;
  error: string | null;
  timestamp: string;
}

// ── Toggle ────────────────────────────────────────────────────────────────────

function Toggle({ checked, onChange }: { checked: boolean; onChange: () => void }) {
  return (
    <label className="relative inline-flex items-center cursor-pointer">
      <input type="checkbox" checked={checked} onChange={onChange} className="sr-only peer" />
      <div className="w-11 h-6 bg-zinc-700 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-fuchsia-500"></div>
    </label>
  );
}

const PRIORITY_STYLES: Record<string, string> = {
  P0: 'bg-red-500/20 text-red-300 border border-red-500/30',
  P1: 'bg-orange-500/20 text-orange-300 border border-orange-500/30',
  P2: 'bg-yellow-500/20 text-yellow-300 border border-yellow-500/30',
  P3: 'bg-zinc-800 text-zinc-400 border border-zinc-700',
};

const CHANNEL_ICONS: Record<string, string> = {
  slack: '\uD83D\uDD14', pagerduty: '\uD83D\uDCDF', teams: '\uD83D\uDCBC', generic: '\uD83C\uDF10'
};

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const [settings, setSettings] = useState({
    store_raw_logs: true,
    redact_pii: true,
    llm_model: 'llama-3.3-70b',
    confidence_threshold: 70,
    retention_days: 30,
    sampling_threshold: 50000,
    auto_analyze: false,
  });
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);

  const [webhooks, setWebhooks] = useState<WebhookDest[]>([]);
  const [deliveryLog, setDeliveryLog] = useState<DeliveryRecord[]>([]);
  const [showAddForm, setShowAddForm] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [newWebhook, setNewWebhook] = useState({
    name: '', channel_type: 'slack', url: '', min_priority: 'P1', enabled: true, extra: {} as Record<string, any>,
  });
  const [addError, setAddError] = useState<string | null>(null);
  const [addLoading, setAddLoading] = useState(false);

  useEffect(() => {
    apiFetch('/settings')
      .then(data => { setSettings(prev => ({ ...prev, ...data })); setLoading(false); })
      .catch(() => setLoading(false));
    fetchWebhooks();
    fetchDeliveryLog();
  }, []);

  const fetchWebhooks = async () => {
    try { setWebhooks(await apiFetch('/webhooks')); } catch {}
  };

  const fetchDeliveryLog = async () => {
    try { setDeliveryLog(await apiFetch('/webhooks/log?limit=20')); } catch {}
  };

  const handleSave = async () => {
    try {
      await apiPut('/settings', settings);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) { alert(`Failed to save: ${e.message}`); }
  };

  const updateSetting = (key: string, value: any) => {
    setSettings(prev => ({ ...prev, [key]: value }));
    setSaved(false);
  };

  const handleAddWebhook = async () => {
    if (!newWebhook.name || !newWebhook.url) { setAddError('Name and URL are required.'); return; }
    setAddLoading(true); setAddError(null);
    try {
      await apiPost('/webhooks', newWebhook);
      await fetchWebhooks();
      setShowAddForm(false);
      setNewWebhook({ name: '', channel_type: 'slack', url: '', min_priority: 'P1', enabled: true, extra: {} });
    } catch (e: any) {
      setAddError(e.message || 'Failed to register webhook');
    } finally { setAddLoading(false); }
  };

  const handleDeleteWebhook = async (id: string) => {
    if (!confirm('Delete this alert destination?')) return;
    try {
      await apiDelete(`/webhooks/${id}`);
      setWebhooks(prev => prev.filter(w => w.id !== id));
    } catch (e: any) { alert(`Delete failed: ${e.message}`); }
  };

  const handleToggleWebhook = async (wh: WebhookDest) => {
    try {
      await apiPut(`/webhooks/${wh.id}`, { enabled: !wh.enabled });
      setWebhooks(prev => prev.map(w => w.id === wh.id ? { ...w, enabled: !w.enabled } : w));
    } catch {}
  };

  const handleTestFire = async (id: string) => {
    setTestingId(id);
    try {
      const result = await apiPost(`/webhooks/${id}/test`, {});
      await fetchDeliveryLog();
      if (result.status === 'delivered') {
        alert(`Test alert delivered! HTTP ${result.http_status} in ${result.latency_ms?.toFixed(0)}ms`);
      } else {
        alert(`Test delivery failed: ${result.error || 'Unknown error'}`);
      }
    } catch (e: any) { alert(`Test failed: ${e.message}`); }
    finally { setTestingId(null); }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-zinc-500">
        <RefreshCw size={20} className="animate-spin mr-2" /> Loading settings...
      </div>
    );
  }

  return (
    <div className="max-w-[1600px] mx-auto pb-10">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-xl font-bold text-white mb-1">Settings</h1>
          <p className="text-xs text-zinc-500">Configure local engine preferences, privacy, models, and alert routing.</p>
        </div>
        <button
          onClick={handleSave}
          className={`font-bold rounded-lg px-5 py-2.5 text-xs flex items-center gap-2 transition-all cursor-pointer ${saved ? 'bg-emerald-600 text-white' : 'bg-fuchsia-600 hover:bg-fuchsia-500 text-white'}`}
        >
          {saved ? <><Check size={14} /> Saved!</> : <><Save size={14} /> Save Changes</>}
        </button>
      </div>

      <div className="space-y-6 max-w-4xl">

        {/* Privacy & Data */}
        <div className="bg-[#121214] border-none rounded-xl p-6 shadow-sm">
          <div className="flex items-center justify-between mb-6 pb-6 border-b border-white/5">
            <div>
              <h2 className="text-sm font-bold text-white mb-1">Privacy &amp; Data</h2>
              <p className="text-xs text-zinc-500">All processing happens locally. No data leaves your machine unless explicitly configured.</p>
            </div>
            <Shield size={24} className="text-emerald-500" />
          </div>
          <div className="space-y-6">
            {[
              { key: 'store_raw_logs', label: 'Store Raw Logs', desc: 'Keep a local copy of ingested raw logs for deeper forensics.' },
              { key: 'redact_pii', label: 'Auto-Redact PII (IPs, Emails, Tokens)', desc: 'Mask sensitive data before passing to local LLM or clustering.' },
              { key: 'auto_analyze', label: 'Auto-Analyze on Upload', desc: 'Automatically trigger analysis when a new log file is uploaded.' },
            ].map(({ key, label, desc }) => (
              <div key={key} className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-bold text-white mb-1">{label}</p>
                  <p className="text-[10px] text-zinc-500">{desc}</p>
                </div>
                <Toggle checked={(settings as any)[key]} onChange={() => updateSetting(key, !(settings as any)[key])} />
              </div>
            ))}
          </div>
        </div>

        {/* LLM */}
        <div className="bg-[#121214] border-none rounded-xl p-6 shadow-sm">
          <div className="flex items-center justify-between mb-6 pb-6 border-b border-white/5">
            <div>
              <h2 className="text-sm font-bold text-white mb-1">Local Intelligence (LLM)</h2>
              <p className="text-xs text-zinc-500">Configure the model used for root-cause diagnosis.</p>
            </div>
            <Cpu size={24} className="text-fuchsia-500" />
          </div>
          <div className="space-y-6">
            <div>
              <p className="text-xs font-bold text-zinc-300 mb-2">Active Model</p>
              <select value={settings.llm_model} onChange={e => updateSetting('llm_model', e.target.value)}
                className="w-full bg-[#1a1a1c] border border-white/10 text-white text-sm rounded-md px-4 py-2.5 outline-none appearance-none cursor-pointer">
                <option value="llama-3.3-70b">Llama 3.3-70B (Local / Default)</option>
                <option value="llama-3.1-8b">Llama 3.1-8B (Fast / Lightweight)</option>
                <option value="gpt-4o">GPT-4o (Cloud / OpenAI)</option>
                <option value="claude-sonnet">Claude Sonnet (Cloud / Anthropic)</option>
                <option value="local-fallback">Local Heuristic Fallback (No LLM)</option>
              </select>
            </div>
            <div>
              <p className="text-xs font-bold text-zinc-300 mb-4">Diagnosis Confidence Threshold</p>
              <div className="flex items-center gap-4">
                <input type="range" min="0" max="100" value={settings.confidence_threshold}
                  onChange={e => updateSetting('confidence_threshold', parseInt(e.target.value))}
                  className="w-full h-1.5 bg-zinc-700 rounded-lg appearance-none cursor-pointer accent-fuchsia-500" />
                <span className="text-xs text-zinc-400 font-mono w-10 text-right">{settings.confidence_threshold}%</span>
              </div>
            </div>
          </div>
        </div>

        {/* Retention */}
        <div className="bg-[#121214] border-none rounded-xl p-6 shadow-sm">
          <div className="flex items-center justify-between mb-6 pb-6 border-b border-white/5">
            <div>
              <h2 className="text-sm font-bold text-white mb-1">Retention &amp; Engine Performance</h2>
              <p className="text-xs text-zinc-500">Manage disk usage and memory consumption.</p>
            </div>
            <HardDrive size={24} className="text-blue-500" />
          </div>
          <div className="grid grid-cols-2 gap-6">
            <div>
              <p className="text-xs font-bold text-zinc-300 mb-2">Snapshot Retention (Days)</p>
              <input type="number" value={settings.retention_days}
                onChange={e => updateSetting('retention_days', parseInt(e.target.value) || 30)}
                className="w-full bg-[#1a1a1c] border border-white/10 text-white text-sm rounded-md px-4 py-2.5 outline-none" />
            </div>
            <div>
              <p className="text-xs font-bold text-zinc-300 mb-2">Neural Sampling Threshold</p>
              <input type="number" value={settings.sampling_threshold}
                onChange={e => updateSetting('sampling_threshold', parseInt(e.target.value) || 50000)}
                className="w-full bg-[#1a1a1c] border border-white/10 text-white text-sm rounded-md px-4 py-2.5 outline-none" />
            </div>
          </div>
        </div>

        {/* Alert Routing */}
        <div className="bg-[#121214] border border-white/5 rounded-xl shadow-sm overflow-hidden">
          <div className="flex items-center justify-between p-6 border-b border-white/5">
            <div>
              <h2 className="text-sm font-bold text-white mb-1 flex items-center gap-2">
                <Bell size={16} className="text-fuchsia-400" /> Alert Routing
              </h2>
              <p className="text-xs text-zinc-500">
                Configure destinations for automatic P0/P1 alerts. Supports Slack, PagerDuty, Teams, and generic webhooks.
              </p>
            </div>
            <button onClick={() => setShowAddForm(s => !s)}
              className="bg-fuchsia-600 hover:bg-fuchsia-500 text-white text-xs font-bold px-4 py-2 rounded-lg flex items-center gap-1.5 transition-colors cursor-pointer border-none">
              <Plus size={13} /> Add Destination
            </button>
          </div>

          {showAddForm && (
            <div className="p-6 bg-[#18181b] border-b border-white/5 space-y-4">
              <p className="text-xs font-bold text-zinc-300 uppercase tracking-wider">New Alert Destination</p>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-[10px] font-bold text-zinc-500 uppercase block mb-1">Display Name</label>
                  <input value={newWebhook.name} onChange={e => setNewWebhook(p => ({ ...p, name: e.target.value }))}
                    placeholder="#sre-alerts"
                    className="w-full bg-[#121214] border border-white/10 text-white text-xs rounded-lg px-3 py-2 outline-none focus:border-fuchsia-500/50" />
                </div>
                <div>
                  <label className="text-[10px] font-bold text-zinc-500 uppercase block mb-1">Channel Type</label>
                  <select value={newWebhook.channel_type} onChange={e => setNewWebhook(p => ({ ...p, channel_type: e.target.value as any }))}
                    className="w-full bg-[#121214] border border-white/10 text-white text-xs rounded-lg px-3 py-2 outline-none appearance-none cursor-pointer focus:border-fuchsia-500/50">
                    <option value="slack">Slack</option>
                    <option value="pagerduty">PagerDuty</option>
                    <option value="teams">Microsoft Teams</option>
                    <option value="generic">Generic Webhook</option>
                  </select>
                </div>
                <div className="col-span-2">
                  <label className="text-[10px] font-bold text-zinc-500 uppercase block mb-1">Endpoint URL</label>
                  <input value={newWebhook.url} onChange={e => setNewWebhook(p => ({ ...p, url: e.target.value }))}
                    placeholder="https://hooks.slack.com/services/..."
                    className="w-full bg-[#121214] border border-white/10 text-white text-xs rounded-lg px-3 py-2 outline-none focus:border-fuchsia-500/50 font-mono" />
                </div>
                <div>
                  <label className="text-[10px] font-bold text-zinc-500 uppercase block mb-1">Minimum Priority</label>
                  <select value={newWebhook.min_priority} onChange={e => setNewWebhook(p => ({ ...p, min_priority: e.target.value }))}
                    className="w-full bg-[#121214] border border-white/10 text-white text-xs rounded-lg px-3 py-2 outline-none appearance-none cursor-pointer focus:border-fuchsia-500/50">
                    <option value="P0">P0 — Critical only</option>
                    <option value="P1">P1 — High and above</option>
                    <option value="P2">P2 — Medium and above</option>
                    <option value="P3">P3 — All alerts</option>
                  </select>
                </div>
                {newWebhook.channel_type === 'pagerduty' && (
                  <div>
                    <label className="text-[10px] font-bold text-zinc-500 uppercase block mb-1">PagerDuty Routing Key</label>
                    <input onChange={e => setNewWebhook(p => ({ ...p, extra: { ...p.extra, routing_key: e.target.value } }))}
                      placeholder="R01AB2C3D4E5F6..."
                      className="w-full bg-[#121214] border border-white/10 text-white text-xs rounded-lg px-3 py-2 outline-none focus:border-fuchsia-500/50 font-mono" />
                  </div>
                )}
              </div>
              {addError && (
                <div className="flex items-center gap-2 text-red-400 text-xs bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
                  <AlertTriangle size={12} /> {addError}
                </div>
              )}
              <div className="flex justify-end gap-3">
                <button onClick={() => { setShowAddForm(false); setAddError(null); }}
                  className="text-zinc-400 text-xs px-4 py-2 rounded-lg hover:bg-white/5 transition-colors cursor-pointer border-none">Cancel</button>
                <button onClick={handleAddWebhook} disabled={addLoading}
                  className="bg-fuchsia-600 hover:bg-fuchsia-500 disabled:opacity-50 text-white text-xs font-bold px-4 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer border-none">
                  {addLoading ? <><RefreshCw size={12} className="animate-spin" /> Registering...</> : <><Webhook size={12} /> Register</>}
                </button>
              </div>
            </div>
          )}

          {webhooks.length === 0 ? (
            <div className="p-12 text-center">
              <Bell size={32} className="text-zinc-700 mx-auto mb-3" />
              <p className="text-sm font-bold text-zinc-500">No alert destinations configured</p>
              <p className="text-xs text-zinc-600 mt-1">Add a Slack, PagerDuty, or Teams webhook to receive P0/P1 alerts automatically.</p>
            </div>
          ) : (
            <div className="divide-y divide-white/5">
              {webhooks.map(wh => (
                <div key={wh.id} className="flex items-center justify-between px-6 py-4 hover:bg-white/5 transition-colors">
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-lg">{CHANNEL_ICONS[wh.channel_type] || '\uD83C\uDF10'}</span>
                    <div className="min-w-0">
                      <p className="text-sm font-bold text-white truncate">{wh.name}</p>
                      <p className="text-[10px] text-zinc-500 font-mono truncate max-w-[240px]">{wh.url}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 shrink-0">
                    <span className={`px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider ${PRIORITY_STYLES[wh.min_priority] || PRIORITY_STYLES.P3}`}>
                      {'>'}= {wh.min_priority}
                    </span>
                    <Toggle checked={wh.enabled} onChange={() => handleToggleWebhook(wh)} />
                    <button onClick={() => handleTestFire(wh.id)} disabled={testingId === wh.id} title="Send test alert"
                      className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-white/10 text-zinc-400 hover:text-fuchsia-400 transition-colors cursor-pointer border-none disabled:opacity-50">
                      {testingId === wh.id ? <RefreshCw size={13} className="animate-spin" /> : <TestTube2 size={13} />}
                    </button>
                    <button onClick={() => handleDeleteWebhook(wh.id)} title="Delete"
                      className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-red-500/10 text-zinc-600 hover:text-red-400 transition-colors cursor-pointer border-none">
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="border-t border-white/5">
            <button onClick={() => { setShowLog(s => !s); if (!showLog) fetchDeliveryLog(); }}
              className="w-full flex items-center justify-between px-6 py-3 text-xs font-bold text-zinc-500 hover:text-white hover:bg-white/5 transition-colors cursor-pointer border-none">
              <span className="flex items-center gap-2"><Bell size={12} /> Delivery Audit Log</span>
              <ChevronDown size={14} className={`transition-transform ${showLog ? 'rotate-180' : ''}`} />
            </button>
            {showLog && (
              <div className="px-6 pb-6 space-y-2 max-h-60 overflow-y-auto">
                {deliveryLog.length === 0
                  ? <p className="text-xs text-zinc-600 text-center py-4">No delivery records yet.</p>
                  : deliveryLog.map((r, i) => (
                    <div key={i} className="flex items-center justify-between text-[10px] bg-black/30 rounded-lg px-3 py-2 font-mono">
                      <div className="flex items-center gap-2 min-w-0">
                        {r.status === 'delivered' ? <CheckCircle2 size={12} className="text-emerald-400 shrink-0" />
                          : r.status === 'failed' ? <XCircle size={12} className="text-red-400 shrink-0" />
                          : <AlertTriangle size={12} className="text-yellow-400 shrink-0" />}
                        <span className={`${PRIORITY_STYLES[r.priority] || ''} px-1 rounded text-[9px] font-bold`}>{r.priority}</span>
                        <span className="text-zinc-400 truncate">{r.alert_fingerprint}</span>
                      </div>
                      <div className="flex items-center gap-3 text-zinc-500 shrink-0">
                        {r.latency_ms > 0 && <span>{r.latency_ms.toFixed(0)}ms</span>}
                        {r.http_status && <span>HTTP {r.http_status}</span>}
                        <span>{r.timestamp?.slice(11, 19)}</span>
                      </div>
                    </div>
                  ))}
              </div>
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
