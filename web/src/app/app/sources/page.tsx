'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Plus, FileText, Upload, RefreshCw, HardDrive, Trash2, Play, Database } from 'lucide-react';
import { apiFetch, API_BASE, apiDelete } from '@/lib/api';
import { useRouter } from 'next/navigation';

export default function SourcesPage() {
  const [sources, setSources] = useState<any[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  const fetchSources = () => {
    apiFetch('/sources')
      .then(data => setSources(data))
      .catch(console.error);
  };

  useEffect(() => { fetchSources(); }, []);

  const handleDeleteSource = async (filename: string) => {
    if (!confirm(`Are you sure you want to delete ${filename}? This action cannot be undone.`)) return;
    try {
      await apiDelete(`/sources/${filename}`);
      fetchSources();
    } catch (e: any) {
      alert(`Failed to delete file: ${e.message}`);
    }
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      
      const res = await fetch(`${API_BASE}/sources/upload`, {
        method: 'POST',
        body: formData,
      });
      
      if (!res.ok) throw new Error('Upload failed');
      
      fetchSources();
    } catch (err: any) {
      alert(`Upload failed: ${err.message}`);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const totalSize = sources.reduce((acc, s) => acc + s.size_bytes, 0);
  const totalLines = sources.reduce((acc, s) => acc + (s.lines_estimate || 0), 0);

  const humanSize = (bytes: number) => {
    for (const unit of ['B', 'KB', 'MB', 'GB']) {
      if (bytes < 1024) return `${bytes.toFixed(1)} ${unit}`;
      bytes /= 1024;
    }
    return `${bytes.toFixed(1)} TB`;
  };

  return (
    <div className="max-w-[1600px] mx-auto pb-10">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-xl font-bold text-white mb-1">Sources & Connectors</h1>
          <p className="text-xs text-zinc-500">Manage local log files available for analysis. <span className="text-zinc-600">{sources.length} sources</span></p>
        </div>
        <div className="flex gap-3">
          <button 
            onClick={fetchSources}
            className="bg-white/5 hover:bg-white/10 text-white font-medium rounded-md px-4 py-2 text-xs border border-white/5 flex items-center gap-2 cursor-pointer transition-colors"
          >
            <RefreshCw size={14} /> Refresh
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".log,.txt,.json,.jsonl,.ndjson"
            onChange={handleUpload}
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="bg-fuchsia-600 hover:bg-fuchsia-500 disabled:opacity-50 text-white font-bold rounded-md px-4 py-2 text-xs border-none flex items-center gap-2 cursor-pointer transition-colors"
          >
            <Upload size={14} /> {uploading ? 'Uploading...' : 'Upload Log File'}
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="bg-[#121214] rounded-xl p-4">
          <p className="text-[10px] text-zinc-500 font-medium mb-1">Total Sources</p>
          <p className="text-2xl font-bold text-white">{sources.length}</p>
        </div>
        <div className="bg-[#121214] rounded-xl p-4">
          <p className="text-[10px] text-zinc-500 font-medium mb-1">Total Size</p>
          <p className="text-2xl font-bold text-fuchsia-400">{humanSize(totalSize)}</p>
        </div>
        <div className="bg-[#121214] rounded-xl p-4">
          <p className="text-[10px] text-zinc-500 font-medium mb-1">Estimated Lines</p>
          <p className="text-2xl font-bold text-emerald-400">{totalLines.toLocaleString()}</p>
        </div>
      </div>

      {/* Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        
        {sources.map((src) => (
          <div key={src.path} className="bg-[#121214] border-none rounded-xl p-6 flex flex-col h-[200px] shadow-sm group hover:bg-[#161618] transition-colors">
            <div className="flex items-start justify-between mb-4">
              <div className="w-10 h-10 rounded-lg bg-fuchsia-500/10 border border-fuchsia-500/20 flex items-center justify-center">
                <FileText size={18} className="text-fuchsia-400" />
              </div>
              <div className="flex items-center gap-2">
                <button 
                  onClick={(e) => { e.stopPropagation(); handleDeleteSource(src.name); }}
                  className="text-zinc-500 hover:text-red-400 transition-colors p-1"
                  title="Delete source"
                >
                  <Trash2 size={14} />
                </button>
                <span className="text-[10px] font-bold text-emerald-400 bg-emerald-400/10 px-2 py-0.5 rounded">Available</span>
              </div>
            </div>
            <div className="flex-1">
              <h2 className="text-sm font-bold text-white mb-1 truncate" title={src.name}>{src.name}</h2>
              <p className="text-xs text-zinc-500 font-mono">{src.size_human} · ~{src.lines_estimate?.toLocaleString()} lines</p>
            </div>
            <div className="mt-auto flex items-center justify-between pt-3 border-t border-white/5">
              <span className="text-[10px] text-zinc-600">Modified: {new Date(src.modified * 1000).toLocaleDateString()}</span>
              <button 
                onClick={() => router.push('/app')}
                className="text-[10px] font-bold text-fuchsia-500 hover:text-fuchsia-400 transition-colors uppercase tracking-widest flex items-center gap-1 cursor-pointer"
              >
                <Play size={10} fill="currentColor" /> Analyze
              </button>
            </div>
          </div>
        ))}

        {/* Upload Placeholder Card */}
        <button 
          onClick={() => fileInputRef.current?.click()}
          className="bg-[#121214] border border-dashed border-white/10 rounded-xl p-6 flex flex-col items-center justify-center h-[200px] hover:border-fuchsia-500/30 hover:bg-fuchsia-500/5 transition-all cursor-pointer group"
        >
          <div className="w-12 h-12 rounded-full bg-white/5 flex items-center justify-center mb-3 group-hover:bg-fuchsia-500/10 transition-colors">
            <Plus size={24} className="text-zinc-500 group-hover:text-fuchsia-400 transition-colors" />
          </div>
          <p className="text-sm font-medium text-zinc-400 group-hover:text-white transition-colors">Upload Log File</p>
          <p className="text-[10px] text-zinc-600 mt-1">.log, .txt, .json, .jsonl, .ndjson</p>
        </button>

        {/* Kubernetes Placeholder */}
        <div className="bg-[#121214] border-none rounded-xl p-6 flex flex-col h-[200px] shadow-sm opacity-60 hover:opacity-100 transition-opacity">
          <div className="flex items-start justify-between mb-4">
            <div className="w-10 h-10 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center justify-center">
              <Database size={18} className="text-blue-400" />
            </div>
            <span className="text-[10px] font-bold text-red-500 bg-red-500/10 px-2 py-0.5 rounded border border-red-500/20">Disconnected</span>
          </div>
          <div className="flex-1">
            <h2 className="text-sm font-bold text-white mb-1">Kubernetes API</h2>
            <p className="text-xs text-zinc-500 font-mono">cluster-1</p>
          </div>
          <div className="mt-auto flex items-center justify-between pt-3 border-t border-white/5">
            <span className="text-[10px] text-zinc-600">Enterprise Add-on</span>
            <button className="text-[10px] font-bold text-zinc-500 hover:text-white transition-colors uppercase tracking-widest flex items-center gap-1 cursor-pointer">
              Configure
            </button>
          </div>
        </div>

        {/* CloudWatch Placeholder */}
        <div className="bg-[#121214] border-none rounded-xl p-6 flex flex-col h-[200px] shadow-sm opacity-60 hover:opacity-100 transition-opacity">
          <div className="flex items-start justify-between mb-4">
            <div className="w-10 h-10 rounded-lg bg-yellow-500/10 border border-yellow-500/20 flex items-center justify-center">
              <HardDrive size={18} className="text-yellow-400" />
            </div>
            <span className="text-[10px] font-bold text-red-500 bg-red-500/10 px-2 py-0.5 rounded border border-red-500/20">Disconnected</span>
          </div>
          <div className="flex-1">
            <h2 className="text-sm font-bold text-white mb-1">AWS CloudWatch</h2>
            <p className="text-xs text-zinc-500 font-mono">us-east-1</p>
          </div>
          <div className="mt-auto flex items-center justify-between pt-3 border-t border-white/5">
            <span className="text-[10px] text-zinc-600">Enterprise Add-on</span>
            <button className="text-[10px] font-bold text-zinc-500 hover:text-white transition-colors uppercase tracking-widest flex items-center gap-1 cursor-pointer">
              Configure
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
