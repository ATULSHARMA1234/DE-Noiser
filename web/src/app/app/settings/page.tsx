'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Shield, Cpu, HardDrive, Save, Check, RefreshCw, Webhook, Plus, Trash2, TestTube2, Bell, CheckCircle2, XCircle, AlertTriangle, ChevronDown, Cloud } from 'lucide-react';
import { apiFetch, apiPut, apiPost, apiDelete } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import { ConfirmModal } from '@/components/ConfirmModal';

// ── Types ─────────────────────────────────────────────────────────────────────

type ChannelType = 'slack' | 'pagerduty' | 'teams' | 'generic';
type JsonObject = Record<string, unknown>;

interface WebhookDest {
  id: string;
  name: string;
  channel_type: ChannelType;
  url: string;
  min_priority: string;
  enabled: boolean;
  extra: JsonObject;
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

interface SettingsState {
  store_raw_logs: boolean;
  redact_pii: boolean;
  llm_model: string;
  confidence_threshold: number;
  retention_days: number;
  sampling_threshold: number;
  auto_analyze: boolean;
  s3_enabled: boolean;
  s3_endpoint: string;
  s3_bucket: string;
  s3_access_key: string;
  s3_secret_key: string;
}

type SettingKey = keyof SettingsState;
type BooleanSettingKey = 'store_raw_logs' | 'redact_pii' | 'auto_analyze';

interface NewWebhook {
  name: string;
  channel_type: ChannelType;
  url: string;
  min_priority: string;
  enabled: boolean;
  extra: JsonObject;
}

interface TestWebhookResult {
  status: 'delivered' | 'failed' | 'skipped';
  http_status?: number | null;
  latency_ms?: number;
  error?: string | null;
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Unknown error';
}

// ── Toggle ────────────────────────────────────────────────────────────────────

function Toggle({ checked, onChange }: { checked: boolean; onChange: () => void }) {
  return (
    <label className="relative inline-flex items-center cursor-pointer">
      <input type="checkbox" checked={checked} onChange={onChange} className="sr-only peer" />
      <div className="w-11 h-6 bg-[var(--bg-track)] rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[var(--primary)]"></div>
    </label>
  );
}

const PRIORITY_STYLES: Record<string, string> = {
  P0: 'bg-red-500/20 text-red-400 border border-red-500/30',
  P1: 'bg-orange-500/20 text-orange-400 border border-orange-500/30',
  P2: 'bg-yellow-500/20 text-yellow-400 border border-yellow-500/30',
  P3: 'bg-[var(--bg-inset)] text-[var(--text-muted)] border border-[var(--border-subtle)]',
};

const CHANNEL_ICONS: Record<string, string> = {
  slack: '🔔', pagerduty: '📟', teams: '💼', generic: '🌐'
};

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const { toast } = useToast();
  const [settings, setSettings] = useState<SettingsState>({
    store_raw_logs: true,
    redact_pii: true,
    llm_model: 'llama-3.3-70b',
    confidence_threshold: 70,
    retention_days: 30,
    sampling_threshold: 50000,
    auto_analyze: false,
    s3_enabled: false,
    s3_endpoint: 'http://localhost:9000',
    s3_bucket: 'semanticos-logs',
    s3_access_key: '',
    s3_secret_key: '',
  });
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);

  const [webhooks, setWebhooks] = useState<WebhookDest[]>([]);
  const [deliveryLog, setDeliveryLog] = useState<DeliveryRecord[]>([]);
  const [showAddForm, setShowAddForm] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [newWebhook, setNewWebhook] = useState<NewWebhook>({
    name: '', channel_type: 'slack', url: '', min_priority: 'P1', enabled: true, extra: {},
  });
  const [addError, setAddError] = useState<string | null>(null);
  const [addLoading, setAddLoading] = useState(false);

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmTitle, setConfirmTitle] = useState('');
  const [confirmMessage, setConfirmMessage] = useState('');
  const [confirmCallback, setConfirmCallback] = useState<(() => void) | null>(null);

  const fetchWebhooks = useCallback(async () => {
    try { setWebhooks(await apiFetch('/webhooks') as WebhookDest[]); } catch {}
  }, []);

  const fetchDeliveryLog = useCallback(async () => {
    try { setDeliveryLog(await apiFetch('/webhooks/log?limit=20') as DeliveryRecord[]); } catch {}
  }, []);

  useEffect(() => {
    let active = true;

    void Promise.all([
      apiFetch('/settings').catch(() => null),
      apiFetch('/webhooks').catch(() => []),
      apiFetch('/webhooks/log?limit=20').catch(() => []),
    ]).then(([settingsData, webhookData, deliveryData]) => {
      if (!active) return;
      if (settingsData) {
        setSettings(prev => ({ ...prev, ...(settingsData as Partial<SettingsState>) }));
      }
      setWebhooks(webhookData as WebhookDest[]);
      setDeliveryLog(deliveryData as DeliveryRecord[]);
      setLoading(false);
    });

    return () => { active = false; };
  }, []);

  const handleSave = async () => {
    try {
      await apiPut('/settings', settings);
      setSaved(true);
      toast.success('Settings saved successfully');
      setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) { 
      toast.error(`Failed to save settings: ${errorMessage(e)}`); 
    }
  };

  const updateSetting = <K extends SettingKey>(key: K, value: SettingsState[K]) => {
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
      toast.success('Alert destination registered successfully');
    } catch (e: unknown) {
      setAddError(errorMessage(e) || 'Failed to register webhook');
    } finally { setAddLoading(false); }
  };

  const handleDeleteWebhook = (id: string) => {
    setConfirmTitle('Delete Alert Destination');
    setConfirmMessage('Delete this alert destination?');
    setConfirmCallback(() => async () => {
      try {
        await apiDelete(`/webhooks/${id}`);
        setWebhooks(prev => prev.filter(w => w.id !== id));
        toast.success('Alert destination deleted');
      } catch (e: unknown) { 
        toast.error(`Delete failed: ${errorMessage(e)}`); 
      }
    });
    setConfirmOpen(true);
  };

  const handleToggleWebhook = async (wh: WebhookDest) => {
    try {
      await apiPut(`/webhooks/${wh.id}`, { enabled: !wh.enabled });
      setWebhooks(prev => prev.map(w => w.id === wh.id ? { ...w, enabled: !w.enabled } : w));
      toast.success(`Webhook ${wh.enabled ? 'disabled' : 'enabled'}`);
    } catch {
      toast.error('Failed to toggle webhook');
    }
  };

  const handleTestFire = async (id: string) => {
    setTestingId(id);
    try {
      const result = await apiPost(`/webhooks/${id}/test`, {}) as TestWebhookResult;
      await fetchDeliveryLog();
      if (result.status === 'delivered') {
        toast.success(`Test alert delivered! HTTP ${result.http_status} in ${result.latency_ms?.toFixed(0)}ms`);
      } else {
        toast.error(`Test delivery failed: ${result.error || 'Unknown error'}`);
      }
    } catch (e: unknown) { 
      toast.error(`Test failed: ${errorMessage(e)}`); 
    } finally { 
      setTestingId(null); 
    }
  };

  if (loading) {
    return (
      <div className="max-w-[1600px] mx-auto pb-10">
        <div className="flex items-center justify-between mb-8">
          <div className="space-y-2">
            <div className="shimmer-bg h-6 w-32 rounded" />
            <div className="shimmer-bg h-4 w-96 rounded" />
          </div>
          <div className="shimmer-bg h-10 w-32 rounded" />
        </div>
        <div className="space-y-6 max-w-4xl">
          <div className="shimmer-bg h-64 w-full rounded-xl" />
          <div className="shimmer-bg h-64 w-full rounded-xl" />
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-[1600px] mx-auto pb-10">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-xl font-bold text-[var(--text-primary)] mb-1">Settings</h1>
          <p className="text-xs text-[var(--text-muted)]">Configure local engine preferences, privacy, models, and alert routing.</p>
        </div>
        <button
          onClick={handleSave}
          className={`font-bold rounded-lg px-5 py-2.5 text-xs flex items-center gap-2 transition-all cursor-pointer border-none ${saved ? 'bg-emerald-600 text-white' : 'bg-fuchsia-600 hover:bg-fuchsia-500 text-white'}`}
        >
          {saved ? <><Check size={14} /> Saved!</> : <><Save size={14} /> Save Changes</>}
        </button>
      </div>

      <div className="space-y-6 max-w-4xl">

        {/* Privacy & Data */}
        <div className="bg-[var(--bg-card)] border-none rounded-xl p-6 shadow-sm">
          <div className="flex items-center justify-between mb-6 pb-6 border-b border-[var(--border-subtle)]">
            <div>
              <h2 className="text-sm font-bold text-[var(--text-primary)] mb-1">Privacy &amp; Data</h2>
              <p className="text-xs text-[var(--text-muted)]">All processing happens locally. No data leaves your machine unless explicitly configured.</p>
            </div>
            <Shield size={24} className="text-emerald-500" />
          </div>
          <div className="space-y-6">
            {([
              { key: 'store_raw_logs', label: 'Store Raw Logs', desc: 'Keep a local copy of ingested raw logs for deeper forensics.' },
              { key: 'redact_pii', label: 'Auto-Redact PII (IPs, Emails, Tokens)', desc: 'Mask sensitive data before passing to local LLM or clustering.' },
              { key: 'auto_analyze', label: 'Auto-Analyze on Upload', desc: 'Automatically trigger analysis when a new log file is uploaded.' },
            ] satisfies { key: BooleanSettingKey; label: string; desc: string }[]).map(({ key, label, desc }) => (
              <div key={key} className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-bold text-[var(--text-primary)] mb-1">{label}</p>
                  <p className="text-[10px] text-[var(--text-muted)]">{desc}</p>
                </div>
                <Toggle checked={settings[key]} onChange={() => updateSetting(key, !settings[key])} />
              </div>
            ))}
          </div>
        </div>

        {/* LLM */}
        <div className="bg-[var(--bg-card)] border-none rounded-xl p-6 shadow-sm">
          <div className="flex items-center justify-between mb-6 pb-6 border-b border-[var(--border-subtle)]">
            <div>
              <h2 className="text-sm font-bold text-[var(--text-primary)] mb-1">Local Intelligence (LLM)</h2>
              <p className="text-xs text-[var(--text-muted)]">Configure the model used for root-cause diagnosis.</p>
            </div>
            <Cpu size={24} className="text-fuchsia-500" />
          </div>
          <div className="space-y-6">
            <div>
              <p className="text-xs font-bold text-[var(--text-secondary)] mb-2">Active Model</p>
              <select value={settings.llm_model} onChange={e => updateSetting('llm_model', e.target.value)}
                className="w-full bg-[var(--bg-input)] border border-[var(--border)] text-[var(--text-primary)] text-sm rounded-md px-4 py-2.5 outline-none appearance-none cursor-pointer">
                <option value="llama-3.3-70b">Llama 3.3-70B (Local / Default)</option>
                <option value="llama-3.1-8b">Llama 3.1-8B (Fast / Lightweight)</option>
                <option value="gpt-4o">GPT-4o (Cloud / OpenAI)</option>
                <option value="claude-sonnet">Claude Sonnet (Cloud / Anthropic)</option>
                <option value="local-fallback">Local Heuristic Fallback (No LLM)</option>
              </select>
            </div>
            <div>
              <p className="text-xs font-bold text-[var(--text-secondary)] mb-4">Diagnosis Confidence Threshold</p>
              <div className="flex items-center gap-4">
                <input type="range" min="0" max="100" value={settings.confidence_threshold}
                  onChange={e => updateSetting('confidence_threshold', parseInt(e.target.value))}
                  className="w-full h-1.5 bg-[var(--bg-track)] rounded-lg appearance-none cursor-pointer accent-fuchsia-500" />
                <span className="text-xs text-[var(--text-secondary)] font-mono w-10 text-right">{settings.confidence_threshold}%</span>
              </div>
            </div>
          </div>
        </div>

        {/* Retention */}
        <div className="bg-[var(--bg-card)] border-none rounded-xl p-6 shadow-sm">
          <div className="flex items-center justify-between mb-6 pb-6 border-b border-[var(--border-subtle)]">
            <div>
              <h2 className="text-sm font-bold text-[var(--text-primary)] mb-1">Retention &amp; Engine Performance</h2>
              <p className="text-xs text-[var(--text-muted)]">Manage disk usage and memory consumption.</p>
            </div>
            <HardDrive size={24} className="text-blue-500" />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <p className="text-xs font-bold text-[var(--text-secondary)] mb-2">Snapshot Retention (Days)</p>
              <input type="number" value={settings.retention_days}
                onChange={e => updateSetting('retention_days', parseInt(e.target.value) || 30)}
                className="w-full bg-[var(--bg-input)] border border-[var(--border)] text-[var(--text-primary)] text-sm rounded-md px-4 py-2.5 outline-none" />
            </div>
            <div>
              <p className="text-xs font-bold text-[var(--text-secondary)] mb-2">Neural Sampling Threshold</p>
              <input type="number" value={settings.sampling_threshold}
                onChange={e => updateSetting('sampling_threshold', parseInt(e.target.value) || 50000)}
                className="w-full bg-[var(--bg-input)] border border-[var(--border)] text-[var(--text-primary)] text-sm rounded-md px-4 py-2.5 outline-none" />
            </div>
          </div>
        </div>

        {/* Object Storage */}
        <div className="bg-[var(--bg-card)] border-none rounded-xl p-6 shadow-sm">
          <div className="flex items-center justify-between mb-6 pb-6 border-b border-[var(--border-subtle)]">
            <div>
              <h2 className="text-sm font-bold text-[var(--text-primary)] mb-1">Object Storage Archive</h2>
              <p className="text-xs text-[var(--text-muted)]">Compress and archive logs past the retention window to S3-compatible storage.</p>
            </div>
            <Cloud size={24} className="text-cyan-400" />
          </div>
          <div className="space-y-5">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-bold text-[var(--text-primary)] mb-1">Enable S3/MinIO Archival</p>
                <p className="text-[10px] text-[var(--text-muted)]">The retention scheduler uploads compressed old logs before deleting local copies.</p>
              </div>
              <Toggle checked={settings.s3_enabled} onChange={() => updateSetting('s3_enabled', !settings.s3_enabled)} />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <p className="text-xs font-bold text-[var(--text-secondary)] mb-2">Endpoint</p>
                <input value={settings.s3_endpoint}
                  onChange={e => updateSetting('s3_endpoint', e.target.value)}
                  className="w-full bg-[var(--bg-input)] border border-[var(--border)] text-[var(--text-primary)] text-sm rounded-md px-4 py-2.5 outline-none font-mono" />
              </div>
              <div>
                <p className="text-xs font-bold text-[var(--text-secondary)] mb-2">Bucket</p>
                <input value={settings.s3_bucket}
                  onChange={e => updateSetting('s3_bucket', e.target.value)}
                  className="w-full bg-[var(--bg-input)] border border-[var(--border)] text-[var(--text-primary)] text-sm rounded-md px-4 py-2.5 outline-none font-mono" />
              </div>
              <div>
                <p className="text-xs font-bold text-[var(--text-secondary)] mb-2">Access Key</p>
                <input value={settings.s3_access_key}
                  onChange={e => updateSetting('s3_access_key', e.target.value)}
                  className="w-full bg-[var(--bg-input)] border border-[var(--border)] text-[var(--text-primary)] text-sm rounded-md px-4 py-2.5 outline-none font-mono" />
              </div>
              <div>
                <p className="text-xs font-bold text-[var(--text-secondary)] mb-2">Secret Key</p>
                <input type="password" value={settings.s3_secret_key}
                  onChange={e => updateSetting('s3_secret_key', e.target.value)}
                  className="w-full bg-[var(--bg-input)] border border-[var(--border)] text-[var(--text-primary)] text-sm rounded-md px-4 py-2.5 outline-none font-mono" />
              </div>
            </div>
          </div>
        </div>

        {/* Alert Routing */}
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl shadow-sm overflow-hidden">
          <div className="flex items-center justify-between p-6 border-b border-[var(--border-subtle)]">
            <div>
              <h2 className="text-sm font-bold text-[var(--text-primary)] mb-1 flex items-center gap-2">
                <Bell size={16} className="text-fuchsia-400" /> Alert Routing
              </h2>
              <p className="text-xs text-[var(--text-muted)]">
                Configure destinations for automatic P0/P1 alerts. Supports Slack, PagerDuty, Teams, and generic webhooks.
              </p>
            </div>
            <button onClick={() => setShowAddForm(s => !s)}
              className="bg-fuchsia-600 hover:bg-fuchsia-500 text-white text-xs font-bold px-4 py-2 rounded-lg flex items-center gap-1.5 transition-colors cursor-pointer border-none">
              <Plus size={13} /> Add Destination
            </button>
          </div>

          {showAddForm && (
            <div className="p-6 bg-[var(--bg-inset)] border-b border-[var(--border-subtle)] space-y-4">
              <p className="text-xs font-bold text-[var(--text-primary)] uppercase tracking-wider">New Alert Destination</p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="text-[10px] font-bold text-[var(--text-muted)] uppercase block mb-1">Display Name</label>
                  <input value={newWebhook.name} onChange={e => setNewWebhook(p => ({ ...p, name: e.target.value }))}
                    placeholder="#sre-alerts"
                    className="w-full bg-[var(--bg-card)] border border-[var(--border)] text-[var(--text-primary)] text-xs rounded-lg px-3 py-2 outline-none focus:border-fuchsia-500/50" />
                </div>
                <div>
                  <label className="text-[10px] font-bold text-[var(--text-muted)] uppercase block mb-1">Channel Type</label>
                  <select value={newWebhook.channel_type} onChange={e => setNewWebhook(p => ({ ...p, channel_type: e.target.value as ChannelType }))}
                    className="w-full bg-[var(--bg-card)] border border-[var(--border)] text-[var(--text-primary)] text-xs rounded-lg px-3 py-2 outline-none appearance-none cursor-pointer focus:border-fuchsia-500/50">
                    <option value="slack">Slack</option>
                    <option value="pagerduty">PagerDuty</option>
                    <option value="teams">Microsoft Teams</option>
                    <option value="generic">Generic Webhook</option>
                  </select>
                </div>
                <div className="col-span-1 md:col-span-2">
                  <label className="text-[10px] font-bold text-[var(--text-muted)] uppercase block mb-1">Endpoint URL</label>
                  <input value={newWebhook.url} onChange={e => setNewWebhook(p => ({ ...p, url: e.target.value }))}
                    placeholder="https://hooks.slack.com/services/..."
                    className="w-full bg-[var(--bg-card)] border border-[var(--border)] text-[var(--text-primary)] text-xs rounded-lg px-3 py-2 outline-none focus:border-fuchsia-500/50 font-mono" />
                </div>
                <div>
                  <label className="text-[10px] font-bold text-[var(--text-muted)] uppercase block mb-1">Minimum Priority</label>
                  <select value={newWebhook.min_priority} onChange={e => setNewWebhook(p => ({ ...p, min_priority: e.target.value }))}
                    className="w-full bg-[var(--bg-card)] border border-[var(--border)] text-[var(--text-primary)] text-xs rounded-lg px-3 py-2 outline-none appearance-none cursor-pointer focus:border-fuchsia-500/50">
                    <option value="P0">P0 — Critical only</option>
                    <option value="P1">P1 — High and above</option>
                    <option value="P2">P2 — Medium and above</option>
                    <option value="P3">P3 — All alerts</option>
                  </select>
                </div>
                {newWebhook.channel_type === 'pagerduty' && (
                  <div>
                    <label className="text-[10px] font-bold text-[var(--text-muted)] uppercase block mb-1">PagerDuty Routing Key</label>
                    <input onChange={e => setNewWebhook(p => ({ ...p, extra: { ...p.extra, routing_key: e.target.value } }))}
                      placeholder="R01AB2C3D4E5F6..."
                      className="w-full bg-[var(--bg-card)] border border-[var(--border)] text-[var(--text-primary)] text-xs rounded-lg px-3 py-2 outline-none focus:border-fuchsia-500/50 font-mono" />
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
                  className="text-[var(--text-muted)] hover:text-[var(--text-primary)] text-xs px-4 py-2 rounded-lg hover:bg-[var(--bg-surface-hover)] transition-colors cursor-pointer border-none bg-transparent">Cancel</button>
                <button onClick={handleAddWebhook} disabled={addLoading}
                  className="bg-fuchsia-600 hover:bg-fuchsia-500 disabled:opacity-50 text-white text-xs font-bold px-4 py-2 rounded-lg flex items-center gap-2 transition-colors cursor-pointer border-none">
                  {addLoading ? <><RefreshCw size={12} className="animate-spin" /> Registering...</> : <><Webhook size={12} /> Register</>}
                </button>
              </div>
            </div>
          )}

          {webhooks.length === 0 ? (
            <div className="p-12 text-center">
              <Bell size={32} className="text-[var(--text-dimmed)] mx-auto mb-3" />
              <p className="text-sm font-bold text-[var(--text-secondary)]">No alert destinations configured</p>
              <p className="text-xs text-[var(--text-muted)] mt-1">Add a Slack, PagerDuty, or Teams webhook to receive P0/P1 alerts automatically.</p>
            </div>
          ) : (
            <div className="divide-y divide-[var(--border-subtle)]">
              {webhooks.map(wh => (
                <div key={wh.id} className="flex items-center justify-between px-6 py-4 hover:bg-[var(--bg-surface-hover)] transition-colors">
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-lg">{CHANNEL_ICONS[wh.channel_type] || '🌐'}</span>
                    <div className="min-w-0">
                      <p className="text-sm font-bold text-[var(--text-primary)] truncate">{wh.name}</p>
                      <p className="text-[10px] text-[var(--text-muted)] font-mono truncate max-w-[240px]">{wh.url}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-4 shrink-0">
                    <span className={`px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider ${PRIORITY_STYLES[wh.min_priority] || PRIORITY_STYLES.P3}`}>
                      {'>'}= {wh.min_priority}
                    </span>
                    <Toggle checked={wh.enabled} onChange={() => handleToggleWebhook(wh)} />
                    <button onClick={() => handleTestFire(wh.id)} disabled={testingId === wh.id} title="Send test alert"
                      className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-[var(--bg-surface-hover)] text-[var(--text-muted)] hover:text-fuchsia-400 transition-colors cursor-pointer border-none bg-transparent disabled:opacity-50">
                      {testingId === wh.id ? <RefreshCw size={13} className="animate-spin" /> : <TestTube2 size={13} />}
                    </button>
                    <button onClick={() => handleDeleteWebhook(wh.id)} title="Delete"
                      className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-red-500/10 text-[var(--text-muted)] hover:text-red-400 transition-colors cursor-pointer border-none bg-transparent">
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="border-t border-[var(--border-subtle)]">
            <button onClick={() => { setShowLog(s => !s); if (!showLog) fetchDeliveryLog(); }}
              className="w-full flex items-center justify-between px-6 py-3 text-xs font-bold text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)] transition-colors cursor-pointer border-none bg-transparent">
              <span className="flex items-center gap-2"><Bell size={12} /> Delivery Audit Log</span>
              <ChevronDown size={14} className={`transition-transform ${showLog ? 'rotate-180' : ''}`} />
            </button>
            {showLog && (
              <div className="px-6 pb-6 space-y-2 max-h-60 overflow-y-auto">
                {deliveryLog.length === 0
                  ? <p className="text-xs text-[var(--text-muted)] text-center py-4">No delivery records yet.</p>
                  : deliveryLog.map((r, i) => (
                    <div key={i} className="flex items-center justify-between text-[10px] bg-[var(--bg-inset)] rounded-lg px-3 py-2 font-mono">
                      <div className="flex items-center gap-2 min-w-0">
                        {r.status === 'delivered' ? <CheckCircle2 size={12} className="text-emerald-400 shrink-0" />
                          : r.status === 'failed' ? <XCircle size={12} className="text-red-400 shrink-0" />
                          : <AlertTriangle size={12} className="text-yellow-400 shrink-0" />}
                        <span className={`${PRIORITY_STYLES[r.priority] || ''} px-1 rounded text-[9px] font-bold`}>{r.priority}</span>
                        <span className="text-[var(--text-secondary)] truncate">{r.alert_fingerprint}</span>
                      </div>
                      <div className="flex items-center gap-3 text-[var(--text-muted)] shrink-0">
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

