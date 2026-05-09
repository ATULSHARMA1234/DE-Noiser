'use client';

import React, { useState, useEffect } from 'react';
import { Shield, Cpu, HardDrive, Save, Check, RefreshCw } from 'lucide-react';
import { apiFetch, apiPut } from '@/lib/api';

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

  useEffect(() => {
    apiFetch('/settings')
      .then(data => {
        setSettings(prev => ({ ...prev, ...data }));
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const handleSave = async () => {
    try {
      await apiPut('/settings', settings);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e: any) {
      alert(`Failed to save: ${e.message}`);
    }
  };

  const updateSetting = (key: string, value: any) => {
    setSettings(prev => ({ ...prev, [key]: value }));
    setSaved(false);
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
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-xl font-bold text-white mb-1">Settings</h1>
          <p className="text-xs text-zinc-500">Configure local engine preferences, privacy, and models.</p>
        </div>
        <button
          onClick={handleSave}
          className={`font-bold rounded-lg px-5 py-2.5 text-xs flex items-center gap-2 transition-all cursor-pointer ${
            saved 
              ? 'bg-emerald-600 text-white'
              : 'bg-fuchsia-600 hover:bg-fuchsia-500 text-white'
          }`}
        >
          {saved ? <><Check size={14} /> Saved!</> : <><Save size={14} /> Save Changes</>}
        </button>
      </div>

      <div className="space-y-6 max-w-4xl">
        
        {/* Card 1: Privacy & Data */}
        <div className="bg-[#121214] border-none rounded-xl p-6 shadow-sm">
          <div className="flex items-center justify-between mb-6 pb-6 border-b border-white/5">
            <div>
              <h2 className="text-sm font-bold text-white mb-1">Privacy & Data</h2>
              <p className="text-xs text-zinc-500">All processing happens locally. No data leaves your machine unless explicitly configured.</p>
            </div>
            <Shield size={24} className="text-emerald-500" />
          </div>
          
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-bold text-white mb-1">Store Raw Logs</p>
                <p className="text-[10px] text-zinc-500">Keep a local copy of ingested raw logs for deeper forensics.</p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input type="checkbox" checked={settings.store_raw_logs} onChange={() => updateSetting('store_raw_logs', !settings.store_raw_logs)} className="sr-only peer" />
                <div className="w-11 h-6 bg-zinc-700 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-fuchsia-500"></div>
              </label>
            </div>
            
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-bold text-white mb-1">Auto-Redact PII (IPs, Emails, Tokens)</p>
                <p className="text-[10px] text-zinc-500">Mask sensitive data before passing to local LLM or clustering.</p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input type="checkbox" checked={settings.redact_pii} onChange={() => updateSetting('redact_pii', !settings.redact_pii)} className="sr-only peer" />
                <div className="w-11 h-6 bg-zinc-700 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-fuchsia-500"></div>
              </label>
            </div>

            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-bold text-white mb-1">Auto-Analyze on Upload</p>
                <p className="text-[10px] text-zinc-500">Automatically trigger analysis when a new log file is uploaded.</p>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input type="checkbox" checked={settings.auto_analyze} onChange={() => updateSetting('auto_analyze', !settings.auto_analyze)} className="sr-only peer" />
                <div className="w-11 h-6 bg-zinc-700 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-fuchsia-500"></div>
              </label>
            </div>
          </div>
        </div>

        {/* Card 2: Local Intelligence (LLM) */}
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
              <select 
                value={settings.llm_model}
                onChange={(e) => updateSetting('llm_model', e.target.value)}
                className="w-full bg-[#1a1a1c] border border-white/10 text-white text-sm rounded-md px-4 py-2.5 outline-none appearance-none cursor-pointer"
              >
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
                <input 
                  type="range" 
                  min="0" max="100" 
                  value={settings.confidence_threshold} 
                  onChange={(e) => updateSetting('confidence_threshold', parseInt(e.target.value))}
                  className="w-full h-1.5 bg-zinc-700 rounded-lg appearance-none cursor-pointer accent-fuchsia-500"
                />
                <span className="text-xs text-zinc-400 font-mono w-10 text-right">{settings.confidence_threshold}%</span>
              </div>
              <p className="text-[10px] text-zinc-600 mt-2">Only surface diagnoses with confidence above this threshold.</p>
            </div>
          </div>
        </div>

        {/* Card 3: Retention & Engine Performance */}
        <div className="bg-[#121214] border-none rounded-xl p-6 shadow-sm">
          <div className="flex items-center justify-between mb-6 pb-6 border-b border-white/5">
            <div>
              <h2 className="text-sm font-bold text-white mb-1">Retention & Engine Performance</h2>
              <p className="text-xs text-zinc-500">Manage disk usage and memory consumption.</p>
            </div>
            <HardDrive size={24} className="text-blue-500" />
          </div>
          
          <div className="grid grid-cols-2 gap-6">
            <div>
              <p className="text-xs font-bold text-zinc-300 mb-2">Snapshot Retention (Days)</p>
              <input 
                type="number" 
                value={settings.retention_days}
                onChange={(e) => updateSetting('retention_days', parseInt(e.target.value) || 30)}
                className="w-full bg-[#1a1a1c] border border-white/10 text-white text-sm rounded-md px-4 py-2.5 outline-none"
              />
              <p className="text-[10px] text-zinc-500 mt-1">Automatically purge analysis runs older than this.</p>
            </div>
            
            <div>
              <p className="text-xs font-bold text-zinc-300 mb-2">Neural Sampling Threshold</p>
              <input 
                type="number" 
                value={settings.sampling_threshold}
                onChange={(e) => updateSetting('sampling_threshold', parseInt(e.target.value) || 50000)}
                className="w-full bg-[#1a1a1c] border border-white/10 text-white text-sm rounded-md px-4 py-2.5 outline-none"
              />
              <p className="text-[10px] text-zinc-500 mt-1">If unique patterns exceed this, use random sampling for speed.</p>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
