'use client';
 
import React, { useState, useEffect, useRef } from 'react';
import { Plus, FileText, Upload, RefreshCw, HardDrive, Trash2, Play, Database, X, Terminal, Cpu } from 'lucide-react';
import { apiFetch, API_BASE, apiDelete } from '@/lib/api';
import { useRouter } from 'next/navigation';
 
export default function SourcesPage() {
  const [sources, setSources] = useState<any[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  const [activeModal, setActiveModal] = useState<'k8s' | 'aws' | 'docker' | null>(null);
  const [k8sPods, setK8sPods] = useState<any[]>([]);
  const [awsGroups, setAwsGroups] = useState<any[]>([]);
  const [dockerContainers, setDockerContainers] = useState<any[]>([]);
  const [connectorStatus, setConnectorStatus] = useState<string>('disconnected');
  const [connectorMsg, setConnectorMsg] = useState<string>('');
  
  const [loadingConnectorData, setLoadingConnectorData] = useState(false);
  const [fetchingLogs, setFetchingLogs] = useState(false);
  
  const [selectedK8sPod, setSelectedK8sPod] = useState<any | null>(null);
  const [selectedAwsGroup, setSelectedAwsGroup] = useState<any | null>(null);
  const [selectedDockerContainer, setSelectedDockerContainer] = useState<any | null>(null);

  const openK8sConnector = async () => {
    setActiveModal('k8s');
    setLoadingConnectorData(true);
    setSelectedK8sPod(null);
    try {
      const data = await apiFetch('/connectors/k8s/pods');
      setK8sPods(data.pods || []);
      setConnectorStatus(data.status);
      setConnectorMsg(data.message || '');
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingConnectorData(false);
    }
  };

  const openAwsConnector = async () => {
    setActiveModal('aws');
    setLoadingConnectorData(true);
    setSelectedAwsGroup(null);
    try {
      const data = await apiFetch('/connectors/aws/groups');
      setAwsGroups(data.groups || []);
      setConnectorStatus(data.status);
      setConnectorMsg(data.message || '');
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingConnectorData(false);
    }
  };

  const openDockerConnector = async () => {
    setActiveModal('docker');
    setLoadingConnectorData(true);
    setSelectedDockerContainer(null);
    try {
      const data = await apiFetch('/connectors/docker/containers');
      setDockerContainers(data.containers || []);
      setConnectorStatus(data.status);
      setConnectorMsg(data.message || '');
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingConnectorData(false);
    }
  };

  const handleK8sFetch = async () => {
    if (!selectedK8sPod) return;
    setFetchingLogs(true);
    try {
      const formData = new URLSearchParams();
      formData.append('namespace', selectedK8sPod.namespace);
      formData.append('pod_name', selectedK8sPod.name);

      const res = await fetch(`${API_BASE}/connectors/k8s/fetch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: formData.toString()
      });

      if (!res.ok) throw new Error('Failed to fetch pod logs');
      const result = await res.json();
      alert(`Successfully ingested ${result.lines} log lines from ${selectedK8sPod.name}!`);
      setActiveModal(null);
      fetchSources();
    } catch (err: any) {
      alert(`Failed: ${err.message}`);
    } finally {
      setFetchingLogs(false);
    }
  };

  const handleAwsFetch = async () => {
    if (!selectedAwsGroup) return;
    setFetchingLogs(true);
    try {
      const formData = new URLSearchParams();
      formData.append('log_group', selectedAwsGroup.name);

      const res = await fetch(`${API_BASE}/connectors/aws/fetch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: formData.toString()
      });

      if (!res.ok) throw new Error('Failed to fetch CloudWatch logs');
      const result = await res.json();
      alert(`Successfully ingested ${result.lines} log lines from CloudWatch log group!`);
      setActiveModal(null);
      fetchSources();
    } catch (err: any) {
      alert(`Failed: ${err.message}`);
    } finally {
      setFetchingLogs(false);
    }
  };

  const handleDockerFetch = async () => {
    if (!selectedDockerContainer) return;
    setFetchingLogs(true);
    try {
      const formData = new URLSearchParams();
      formData.append('container_name', selectedDockerContainer.name);

      const res = await fetch(`${API_BASE}/connectors/docker/fetch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: formData.toString()
      });

      if (!res.ok) throw new Error('Failed to fetch Docker logs');
      const result = await res.json();
      alert(`Successfully ingested ${result.lines} log lines from Docker container ${selectedDockerContainer.name}!`);
      setActiveModal(null);
      fetchSources();
    } catch (err: any) {
      alert(`Failed: ${err.message}`);
    } finally {
      setFetchingLogs(false);
    }
  };

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

        {/* Kubernetes Connector Card */}
        <div className="bg-[#121214] border-none rounded-xl p-6 flex flex-col h-[200px] shadow-sm hover:bg-[#161618] transition-colors">
          <div className="flex items-start justify-between mb-4">
            <div className="w-10 h-10 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center justify-center">
              <Database size={18} className="text-blue-400" />
            </div>
            <span className="text-[10px] font-bold text-emerald-400 bg-emerald-400/10 px-2 py-0.5 rounded border border-emerald-500/20">Ready</span>
          </div>
          <div className="flex-1">
            <h2 className="text-sm font-bold text-white mb-1">Kubernetes API</h2>
            <p className="text-xs text-zinc-500 font-mono">cluster-1</p>
          </div>
          <div className="mt-auto flex items-center justify-between pt-3 border-t border-white/5">
            <span className="text-[10px] text-zinc-600">Enterprise Connector</span>
            <button 
              onClick={openK8sConnector}
              className="text-[10px] font-bold text-fuchsia-500 hover:text-fuchsia-400 transition-colors uppercase tracking-widest flex items-center gap-1 cursor-pointer bg-transparent border-none"
            >
              Configure
            </button>
          </div>
        </div>

        {/* CloudWatch Connector Card */}
        <div className="bg-[#121214] border-none rounded-xl p-6 flex flex-col h-[200px] shadow-sm hover:bg-[#161618] transition-colors">
          <div className="flex items-start justify-between mb-4">
            <div className="w-10 h-10 rounded-lg bg-yellow-500/10 border border-yellow-500/20 flex items-center justify-center">
              <HardDrive size={18} className="text-yellow-400" />
            </div>
            <span className="text-[10px] font-bold text-emerald-400 bg-emerald-400/10 px-2 py-0.5 rounded border border-emerald-500/20">Ready</span>
          </div>
          <div className="flex-1">
            <h2 className="text-sm font-bold text-white mb-1">AWS CloudWatch</h2>
            <p className="text-xs text-zinc-500 font-mono">us-east-1</p>
          </div>
          <div className="mt-auto flex items-center justify-between pt-3 border-t border-white/5">
            <span className="text-[10px] text-zinc-600">Enterprise Connector</span>
            <button 
              onClick={openAwsConnector}
              className="text-[10px] font-bold text-fuchsia-500 hover:text-fuchsia-400 transition-colors uppercase tracking-widest flex items-center gap-1 cursor-pointer bg-transparent border-none"
            >
              Configure
            </button>
          </div>
        </div>

        {/* Docker Connector Card */}
        <div className="bg-[#121214] border-none rounded-xl p-6 flex flex-col h-[200px] shadow-sm hover:bg-[#161618] transition-colors">
          <div className="flex items-start justify-between mb-4">
            <div className="w-10 h-10 rounded-lg bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center">
              <Cpu size={18} className="text-emerald-400" />
            </div>
            <span className="text-[10px] font-bold text-emerald-400 bg-emerald-400/10 px-2 py-0.5 rounded border border-emerald-500/20">Ready</span>
          </div>
          <div className="flex-1">
            <h2 className="text-sm font-bold text-white mb-1">Docker Socket</h2>
            <p className="text-xs text-zinc-500 font-mono">local-daemon</p>
          </div>
          <div className="mt-auto flex items-center justify-between pt-3 border-t border-white/5">
            <span className="text-[10px] text-zinc-600">Enterprise Connector</span>
            <button 
              onClick={openDockerConnector}
              className="text-[10px] font-bold text-fuchsia-500 hover:text-fuchsia-400 transition-colors uppercase tracking-widest flex items-center gap-1 cursor-pointer bg-transparent border-none"
            >
              Configure
            </button>
          </div>
        </div>

      </div>

      {/* --- KUBERNETES DISCOVERY MODAL --- */}
      {activeModal === 'k8s' && (
        <div className="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-[#121214] border border-white/10 rounded-2xl w-full max-w-[650px] max-h-[85vh] overflow-hidden flex flex-col shadow-2xl">
            {/* Modal Header */}
            <div className="p-6 border-b border-white/5 flex items-center justify-between">
              <div>
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  <Database size={18} className="text-blue-400" /> Kubernetes Pod Log Collector
                </h3>
                <p className="text-[11px] text-zinc-500 mt-1">Discover active pods and stream logs dynamically into SemanticOS.</p>
              </div>
              <button 
                onClick={() => setActiveModal(null)}
                className="text-zinc-500 hover:text-white transition-colors bg-transparent border-none cursor-pointer p-1"
              >
                <X size={18} />
              </button>
            </div>

            {/* Warning / Sandbox message if applicable */}
            {connectorStatus === 'simulated' && (
              <div className="mx-6 mt-4 p-3 bg-fuchsia-500/10 border border-fuchsia-500/20 rounded-lg flex items-center gap-3">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-fuchsia-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-fuchsia-500"></span>
                </span>
                <p className="text-[10px] text-fuchsia-400 font-medium">
                  {connectorMsg || "Local kubeconfig not detected. Operating in sandbox demo mode."}
                </p>
              </div>
            )}

            {/* Modal Content */}
            <div className="p-6 overflow-y-auto flex-1">
              {loadingConnectorData ? (
                <div className="flex flex-col items-center justify-center py-12 gap-3">
                  <RefreshCw size={24} className="text-zinc-500 animate-spin" />
                  <p className="text-xs text-zinc-500">Scanning cluster topology...</p>
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="text-xs font-bold text-zinc-400 uppercase tracking-wider mb-2">Available Pods ({k8sPods.length})</div>
                  <div className="border border-white/5 rounded-lg overflow-hidden bg-[#18181b]">
                    <div className="max-h-[300px] overflow-y-auto divide-y divide-white/5">
                      {k8sPods.map(pod => (
                        <div 
                          key={pod.name}
                          onClick={() => setSelectedK8sPod(pod)}
                          className={`p-3 flex items-center justify-between cursor-pointer transition-colors ${selectedK8sPod?.name === pod.name ? 'bg-fuchsia-500/10' : 'hover:bg-white/5'}`}
                        >
                          <div className="flex flex-col">
                            <span className="text-xs font-bold text-white truncate max-w-[300px]">{pod.name}</span>
                            <span className="text-[10px] text-zinc-500">Namespace: <span className="font-mono text-zinc-400">{pod.namespace}</span></span>
                          </div>
                          <div className="flex items-center gap-3">
                            <span className="text-[10px] font-mono text-zinc-500">{pod.ip}</span>
                            <span className={`text-[9px] font-bold px-2 py-0.5 rounded ${pod.status === 'Running' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}`}>{pod.status}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Modal Footer */}
            <div className="p-6 border-t border-white/5 flex items-center justify-between bg-[#161618]">
              <span className="text-[10px] text-zinc-500 font-mono">
                {selectedK8sPod ? `Target: ${selectedK8sPod.namespace}/${selectedK8sPod.name}` : "Select a pod source to fetch"}
              </span>
              <div className="flex gap-3">
                <button 
                  onClick={() => setActiveModal(null)}
                  className="bg-transparent border border-white/10 hover:bg-white/5 text-zinc-400 hover:text-white font-medium rounded-md px-4 py-2 text-xs cursor-pointer"
                >
                  Cancel
                </button>
                <button 
                  onClick={handleK8sFetch}
                  disabled={!selectedK8sPod || fetchingLogs}
                  className="bg-fuchsia-600 hover:bg-fuchsia-500 disabled:opacity-50 text-white font-bold rounded-md px-4 py-2 text-xs border-none cursor-pointer flex items-center gap-2"
                >
                  {fetchingLogs ? 'Streaming logs...' : 'Ingest Pod Logs'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* --- AWS CLOUDWATCH DISCOVERY MODAL --- */}
      {activeModal === 'aws' && (
        <div className="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-[#121214] border border-white/10 rounded-2xl w-full max-w-[650px] max-h-[85vh] overflow-hidden flex flex-col shadow-2xl">
            {/* Modal Header */}
            <div className="p-6 border-b border-white/5 flex items-center justify-between">
              <div>
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  <HardDrive size={18} className="text-yellow-400" /> AWS CloudWatch Log Collector
                </h3>
                <p className="text-[11px] text-zinc-500 mt-1">Select an active CloudWatch log group to fetch and analyze stream patterns.</p>
              </div>
              <button 
                onClick={() => setActiveModal(null)}
                className="text-zinc-500 hover:text-white transition-colors bg-transparent border-none cursor-pointer p-1"
              >
                <X size={18} />
              </button>
            </div>

            {/* Sandbox Notice */}
            {connectorStatus === 'simulated' && (
              <div className="mx-6 mt-4 p-3 bg-fuchsia-500/10 border border-fuchsia-500/20 rounded-lg flex items-center gap-3">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-fuchsia-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-fuchsia-500"></span>
                </span>
                <p className="text-[10px] text-fuchsia-400 font-medium">
                  {connectorMsg || "AWS credentials not detected. Operating in sandbox demo mode."}
                </p>
              </div>
            )}

            {/* Modal Content */}
            <div className="p-6 overflow-y-auto flex-1">
              {loadingConnectorData ? (
                <div className="flex flex-col items-center justify-center py-12 gap-3">
                  <RefreshCw size={24} className="text-zinc-500 animate-spin" />
                  <p className="text-xs text-zinc-500">Describing AWS Log Groups...</p>
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="text-xs font-bold text-zinc-400 uppercase tracking-wider mb-2">Log Groups ({awsGroups.length})</div>
                  <div className="border border-white/5 rounded-lg overflow-hidden bg-[#18181b]">
                    <div className="max-h-[300px] overflow-y-auto divide-y divide-white/5">
                      {awsGroups.map(group => (
                        <div 
                          key={group.name}
                          onClick={() => setSelectedAwsGroup(group)}
                          className={`p-3 flex items-center justify-between cursor-pointer transition-colors ${selectedAwsGroup?.name === group.name ? 'bg-fuchsia-500/10' : 'hover:bg-white/5'}`}
                        >
                          <div className="flex flex-col truncate max-w-[450px]">
                            <span className="text-xs font-bold text-white truncate">{group.name}</span>
                            <span className="text-[9px] text-zinc-500 font-mono truncate">{group.arn}</span>
                          </div>
                          <span className="text-[10px] text-yellow-400 font-bold bg-yellow-400/5 px-2 py-0.5 rounded">{humanSize(group.stored_bytes)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Modal Footer */}
            <div className="p-6 border-t border-white/5 flex items-center justify-between bg-[#161618]">
              <span className="text-[10px] text-zinc-500 font-mono truncate max-w-[300px]">
                {selectedAwsGroup ? `Group: ${selectedAwsGroup.name}` : "Select a log group to pull"}
              </span>
              <div className="flex gap-3">
                <button 
                  onClick={() => setActiveModal(null)}
                  className="bg-transparent border border-white/10 hover:bg-white/5 text-zinc-400 hover:text-white font-medium rounded-md px-4 py-2 text-xs cursor-pointer"
                >
                  Cancel
                </button>
                <button 
                  onClick={handleAwsFetch}
                  disabled={!selectedAwsGroup || fetchingLogs}
                  className="bg-fuchsia-600 hover:bg-fuchsia-500 disabled:opacity-50 text-white font-bold rounded-md px-4 py-2 text-xs border-none cursor-pointer flex items-center gap-2"
                >
                  {fetchingLogs ? 'Downloading events...' : 'Fetch AWS Logs'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* --- DOCKER ENGINE DISCOVERY MODAL --- */}
      {activeModal === 'docker' && (
        <div className="fixed inset-0 bg-black/75 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-[#121214] border border-white/10 rounded-2xl w-full max-w-[650px] max-h-[85vh] overflow-hidden flex flex-col shadow-2xl">
            {/* Modal Header */}
            <div className="p-6 border-b border-white/5 flex items-center justify-between">
              <div>
                <h3 className="text-base font-bold text-white flex items-center gap-2">
                  <Cpu size={18} className="text-emerald-400" /> Docker Container Log Collector
                </h3>
                <p className="text-[11px] text-zinc-500 mt-1">Select an active container from the host Docker engine to inspect telemetry logs.</p>
              </div>
              <button 
                onClick={() => setActiveModal(null)}
                className="text-zinc-500 hover:text-white transition-colors bg-transparent border-none cursor-pointer p-1"
              >
                <X size={18} />
              </button>
            </div>

            {/* Sandbox Notice */}
            {connectorStatus === 'simulated' && (
              <div className="mx-6 mt-4 p-3 bg-fuchsia-500/10 border border-fuchsia-500/20 rounded-lg flex items-center gap-3">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-fuchsia-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-fuchsia-500"></span>
                </span>
                <p className="text-[10px] text-fuchsia-400 font-medium">
                  {connectorMsg || "Docker Engine socket not detected. Operating in sandbox demo mode."}
                </p>
              </div>
            )}

            {/* Modal Content */}
            <div className="p-6 overflow-y-auto flex-1">
              {loadingConnectorData ? (
                <div className="flex flex-col items-center justify-center py-12 gap-3">
                  <RefreshCw size={24} className="text-zinc-500 animate-spin" />
                  <p className="text-xs text-zinc-500">Querying local Docker daemon...</p>
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="text-xs font-bold text-zinc-400 uppercase tracking-wider mb-2">Running Containers ({dockerContainers.length})</div>
                  <div className="border border-white/5 rounded-lg overflow-hidden bg-[#18181b]">
                    <div className="max-h-[300px] overflow-y-auto divide-y divide-white/5">
                      {dockerContainers.map(container => (
                        <div 
                          key={container.id}
                          onClick={() => setSelectedDockerContainer(container)}
                          className={`p-3 flex items-center justify-between cursor-pointer transition-colors ${selectedDockerContainer?.id === container.id ? 'bg-fuchsia-500/10' : 'hover:bg-white/5'}`}
                        >
                          <div className="flex flex-col truncate max-w-[400px]">
                            <span className="text-xs font-bold text-white">{container.name}</span>
                            <span className="text-[10px] text-zinc-500">Image: <span className="font-mono text-zinc-400">{container.image}</span></span>
                          </div>
                          <div className="flex items-center gap-3">
                            <span className="text-[10px] text-zinc-500 font-mono">ID: {container.id}</span>
                            <span className={`text-[9px] font-bold px-2 py-0.5 rounded ${container.status === 'running' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-zinc-800 text-zinc-400'}`}>{container.status}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Modal Footer */}
            <div className="p-6 border-t border-white/5 flex items-center justify-between bg-[#161618]">
              <span className="text-[10px] text-zinc-500 font-mono">
                {selectedDockerContainer ? `Container: ${selectedDockerContainer.name}` : "Select a running container"}
              </span>
              <div className="flex gap-3">
                <button 
                  onClick={() => setActiveModal(null)}
                  className="bg-transparent border border-white/10 hover:bg-white/5 text-zinc-400 hover:text-white font-medium rounded-md px-4 py-2 text-xs cursor-pointer"
                >
                  Cancel
                </button>
                <button 
                  onClick={handleDockerFetch}
                  disabled={!selectedDockerContainer || fetchingLogs}
                  className="bg-fuchsia-600 hover:bg-fuchsia-500 disabled:opacity-50 text-white font-bold rounded-md px-4 py-2 text-xs border-none cursor-pointer flex items-center gap-2"
                >
                  {fetchingLogs ? 'Extracting logs...' : 'Ingest Docker Logs'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
