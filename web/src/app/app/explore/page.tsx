'use client';

import React, { useState, useEffect } from 'react';
import { apiFetch, apiPost } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import { useTimeRange } from '@/context/TimeRangeContext';
import { Search, Save, History, Play, Terminal, Database, Clock, ChevronRight, Activity, Pause, Filter, X } from 'lucide-react';
import { BarChart, Bar, ResponsiveContainer, XAxis, YAxis, Tooltip } from 'recharts';

export default function ExplorePage() {
 const { toast } = useToast();
 const { timeRange } = useTimeRange();
 const [query, setQuery] = useState('');
 const [results, setResults] = useState<any[]>([]);
 const [loading, setLoading] = useState(false);
 const [viewMode, setViewMode] = useState<'list' | 'patterns'>('list');
 const [savedQueries, setSavedQueries] = useState<any[]>([]);
 const [engine, setEngine] = useState<string>('');
 const [histogram, setHistogram] = useState<any[]>([]);
 const [facets, setFacets] = useState<any>({ source: [], level: [] });
 const [selectedLog, setSelectedLog] = useState<any>(null);
 const [liveTailing, setLiveTailing] = useState(false);
 const liveTailingRef = React.useRef(liveTailing);
 const [logFiles, setLogFiles] = useState<{name: string; label: string; size_bytes: number}[]>([]);
 const [selectedFile, setSelectedFile] = useState<string>('');

 useEffect(() => {
 liveTailingRef.current = liveTailing;
 }, [liveTailing]);

 useEffect(() => {
 // eslint-disable-next-line react-hooks/immutability
 fetchSavedQueries();
 fetchLogFiles();
 fetchFacets(selectedFile);
 handleSearch(undefined, query, selectedFile); // Initial search
 }, [timeRange]);

 async function fetchFacets(fileOverride?: string) {
 const activeFile = fileOverride ?? selectedFile;
 try {
 const params = new URLSearchParams();
 // When a specific file is selected, don't limit by time range
 // so the user can see ALL logs in that file
 if (!activeFile) {
  if (timeRange.from) params.set('from_ts', String(timeRange.from));
  if (timeRange.to) params.set('to_ts', String(timeRange.to));
 }
 if (activeFile) params.set('file_name', activeFile);
 const data = await apiFetch(`/query/facets?${params.toString()}`);
 if (data?.facets) setFacets(data.facets);
 } catch (e: any) {
 // ignore
 }
 };

 async function fetchLogFiles() {
 try {
 const data = await apiFetch('/query/log-files');
 setLogFiles(data?.files || []);
 } catch (e: any) {
 // ignore
 }
 };

 async function fetchSavedQueries() {
 try {
 const data = await apiFetch('/query/saved');
 setSavedQueries(data || []);
 } catch (e: any) {
 // ignore
 }
 };

 async function handleSearch(e?: React.FormEvent, overrideQuery?: string, fileOverride?: string) {
 if (e) e.preventDefault();
 const activeQuery = (overrideQuery ?? query).trim();
 const activeFile = fileOverride ?? selectedFile;

 // When a specific file is selected, search ALL time so static
 // log files with old/future timestamps still show results.
 const fromTs = activeFile ? undefined : timeRange.from;
 const toTs = activeFile ? undefined : timeRange.to;

 setLoading(true);
 try {
 const data = await apiPost('/query', {
 query: activeQuery, 
 limit: 100,
 from_ts: fromTs,
 to_ts: toTs,
 group_by: viewMode === 'patterns' ? 'pattern' : undefined,
 file_name: activeFile || undefined
 });
 setResults(data.logs || []);
 setEngine(data.engine || 'in-memory');
 fetchFacets(activeFile);
 
 const histData = await apiPost('/query/histogram', {
 query: activeQuery, 
 from_ts: fromTs,
 to_ts: toTs,
 file_name: activeFile || undefined
 });
 setHistogram(histData.buckets || []);
 } catch (e: any) {
 toast({ title: 'Query Failed', description: e.message, type: 'error' });
 } finally {
 setLoading(false);
 }
 };

 useEffect(() => {
 let interval: NodeJS.Timeout;
 if (liveTailing) {
 interval = setInterval(() => {
 if (liveTailingRef.current) {
 handleSearch(undefined, query);
 }
 }, 3000);
 }
 return () => clearInterval(interval);
 }, [liveTailing, query, timeRange, viewMode]);

 const saveQuery = async () => {
 const name = prompt('Enter a name for this query:');
 if (!name) return;
 
 try {
 await apiFetch('/query/saved', {
 method: 'POST',
 body: JSON.stringify({ name, query_text: query })
 });
 toast({ title: 'Query saved' });
 fetchSavedQueries();
 } catch (e: any) {
 toast({ title: 'Failed to save query', description: e.message, type: 'error' });
 }
 };

 const deleteSavedQuery = async (id: number) => {
 try {
 await apiFetch(`/query/saved/${id}`, { method: 'DELETE' });
 fetchSavedQueries();
 } catch (e: any) {
 toast({ title: 'Failed to delete query', type: 'error' });
 }
 };

 const handleSampleClick = (sample: string) => {
 setQuery(sample);
 };

 const toggleFacet = (field: string, value: string) => {
 const term = `${field}:${value}`;
 if (query.includes(term)) {
 setQuery(query.replace(new RegExp(`\\b(?:AND\\s+)?${term}(?:\\s+AND)?\\b`), '').trim());
 } else {
 setQuery(query ? `${query} AND ${term}` : term);
 }
 };

 const handleFileChange = (val: string) => {
 setSelectedFile(val);
 // Pass the new file value directly to avoid stale state
 handleSearch(undefined, query, val);
 };

 return (
 <div className="flex flex-col h-[calc(100vh-80px)] space-y-6">
 <div className="flex items-center justify-between">
 <div>
 <h1 className="text-2xl font-bold text-[var(--text-primary)]">Log Explorer</h1>
 <p className="text-[var(--text-secondary)] mt-1">Search, filter, and analyze logs using the SemanticOS DSL.</p>
 </div>
 </div>

 <div className="flex gap-6 h-full min-h-0">
 {/* Main query area */}
 <div className="flex-1 flex flex-col h-full min-w-0 bg-[var(--bg-card)] border border-[var(--border)] rounded-lg overflow-hidden">
 
 {/* Query Bar */}
 <div className="p-4 border-b border-[var(--border)] bg-[var(--bg-app)]">
 <form onSubmit={handleSearch} className="flex gap-2">
 <select
 value={selectedFile}
 onChange={(e) => handleFileChange(e.target.value)}
 className="bg-[var(--bg-card)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-blue-500 min-w-[200px] max-w-[280px]"
 >
 <option value="">All Log Files</option>
 {logFiles.map((f) => (
 <option key={f.name} value={f.name}>
 {f.label} ({(f.size_bytes / 1024).toFixed(1)} KB)
 </option>
 ))}
 </select>

 <div className="relative flex-1 flex items-center bg-[var(--bg-card)] border border-[var(--border)] rounded overflow-hidden focus-within:ring-1 focus-within:ring-blue-500">
 <Terminal className="text-[var(--text-secondary)] ml-3" size={16} />
 <input 
 type="text" 
 value={query}
 onChange={(e) => setQuery(e.target.value)}
 placeholder='e.g. level:ERROR AND "connection timeout" OR service:payment' 
 className="w-full bg-transparent border-none py-3 px-3 text-sm font-mono text-[var(--text-primary)] focus:outline-none focus:ring-0"
 />
 </div>
 <button 
 type="submit" 
 disabled={loading && !liveTailing}
 className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded font-medium flex items-center gap-2 transition-colors disabled:opacity-50"
 >
 {loading && !liveTailing ? <Clock className="animate-spin" size={16} /> : <Play size={16} />}
 Run
 </button>
 <button 
 type="button" 
 onClick={() => setLiveTailing(!liveTailing)}
 className={`px-4 py-2 rounded font-medium flex items-center gap-2 transition-colors border ${liveTailing ? 'bg-green-500/10 text-green-500 border-green-500/20' : 'border-[var(--border)] hover:bg-[var(--bg-card-hover)] text-[var(--text-primary)]'}`}
 >
 {liveTailing ? <Pause size={16} /> : <Activity size={16} />}
 {liveTailing ? 'Tailing' : 'Live Tail'}
 </button>
 <button 
 type="button" 
 onClick={saveQuery}
 disabled={!query}
 className="border border-[var(--border)] hover:bg-[var(--bg-card-hover)] text-[var(--text-primary)] px-4 py-2 rounded font-medium flex items-center gap-2 transition-colors disabled:opacity-50"
 >
 <Save size={16} />
 Save
 </button>
 </form>
 
 <div className="flex gap-2 mt-3 text-xs text-[var(--text-secondary)]">
 <span className="font-medium text-[var(--text-primary)]">Examples:</span>
 <button onClick={() => handleSampleClick('level:ERROR')} className="hover:text-blue-400">level:ERROR</button>
 <span>•</span>
 <button onClick={() => handleSampleClick('service:payment AND level:ERROR')} className="hover:text-blue-400">service:payment AND level:ERROR</button>
 <span>•</span>
 <button onClick={() => handleSampleClick('"database connection failed"')} className="hover:text-blue-400">&quot;exact phrase&quot;</button>
 </div>
 </div>

 {/* Results Area */}
 <div className="flex-1 overflow-auto flex flex-col min-h-0">
 {engine && (
 <div className="bg-[var(--bg-app)] text-[10px] uppercase tracking-wider text-[var(--text-secondary)] px-4 py-2 border-b border-[var(--border)] flex justify-between items-center">
 <div className="flex items-center gap-4">
 <span>Engine: {engine}</span>
 <span>{results.length} results</span>
 </div>
 <div className="flex bg-[var(--bg-card)] rounded border border-[var(--border)] p-0.5">
 <button 
 onClick={() => { setViewMode('list'); setResults([]); }}
 className={`px-3 py-1 rounded-sm text-xs font-medium transition-colors ${viewMode === 'list' ? 'bg-[var(--bg-app)] text-blue-500 shadow-sm' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}
 >
 List
 </button>
 <button 
 onClick={() => { setViewMode('patterns'); setResults([]); }}
 className={`px-3 py-1 rounded-sm text-xs font-medium transition-colors ${viewMode === 'patterns' ? 'bg-[var(--bg-app)] text-blue-500 shadow-sm' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'}`}
 >
 Patterns
 </button>
 </div>
 </div>
 )}
 
 {!loading && histogram.length > 0 && viewMode === 'list' && (
 <div className="h-32 border-b border-[var(--border)] bg-[var(--bg-card)] p-4 pt-2">
 <ResponsiveContainer width="100%" height="100%">
 <BarChart data={histogram} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
 <XAxis 
 dataKey="timestamp" 
 tickFormatter={(ts) => new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
 stroke="var(--border)"
 tick={{ fill: 'var(--text-secondary)', fontSize: 10 }}
 />
 <YAxis 
 stroke="var(--border)"
 tick={{ fill: 'var(--text-secondary)', fontSize: 10 }}
 />
 <Tooltip 
 contentStyle={{ backgroundColor: 'var(--bg-modal)', border: '1px solid var(--border)', fontSize: '12px' }}
 labelFormatter={(ts) => new Date(ts).toLocaleString()}
 />
 <Bar dataKey="count" fill="#3b82f6" radius={[2, 2, 0, 0]} />
 </BarChart>
 </ResponsiveContainer>
 </div>
 )}
 
 {loading ? (
 <div className="p-8 space-y-4">
 {[...Array(5)].map((_, i) => (
 <div key={i} className="animate-pulse flex gap-4">
 <div className="h-4 bg-[var(--border)] rounded w-24"></div>
 <div className="h-4 bg-[var(--border)] rounded w-16"></div>
 <div className="h-4 bg-[var(--border)] rounded flex-1"></div>
 </div>
 ))}
 </div>
 ) : results.length > 0 ? (
 viewMode === 'patterns' ? (
 <div className="flex-1 overflow-auto">
 <table className="w-full text-left text-sm text-[var(--text-secondary)]">
 <thead className="bg-[var(--bg-app)] text-xs uppercase border-b border-[var(--border)] sticky top-0">
 <tr>
 <th className="px-4 py-2 font-medium w-24 text-right">Volume</th>
 <th className="px-4 py-2 font-medium">Log Pattern</th>
 </tr>
 </thead>
 <tbody className="divide-y divide-[var(--border)] font-mono text-[11px] sm:text-xs">
 {results.map((log, i) => (
 <tr key={i} className="hover:bg-[var(--bg-app)]">
 <td className="px-4 py-2 whitespace-nowrap font-semibold text-[var(--text-primary)] text-right">{log.count}</td>
 <td className="px-4 py-2 text-[var(--text-primary)] break-all">{log.pattern}</td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 ) : (
 <div className="flex-1 overflow-auto">
 <table className="w-full text-left text-sm text-[var(--text-secondary)]">
 <thead className="bg-[var(--bg-app)] text-xs uppercase border-b border-[var(--border)] sticky top-0">
 <tr>
 <th className="px-4 py-2 font-medium w-48">Timestamp</th>
 <th className="px-4 py-2 font-medium w-24">Level</th>
 <th className="px-4 py-2 font-medium w-32">Source</th>
 <th className="px-4 py-2 font-medium">Message</th>
 </tr>
 </thead>
 <tbody className="divide-y divide-[var(--border)] font-mono text-[11px] sm:text-xs">
 {results.map((log, i) => {
 const level = log.level || 'INFO';
 let levelColor = 'text-[var(--text-secondary)]';
 if (level === 'ERROR' || level === 'FATAL') levelColor = 'text-red-400';
 else if (level === 'WARN') levelColor = 'text-yellow-400';
 else if (level === 'INFO') levelColor = 'text-blue-400';
 
 // Fallback formatting if raw text is provided
 const timestamp = log.timestamp || new Date().toISOString();
 const source = log.source || log.service || 'unknown';
 const message = typeof log === 'string' ? log : (log.message || log.log || JSON.stringify(log));

 return (
 <tr 
 key={i} 
 onClick={() => setSelectedLog(log)}
 className={`hover:bg-[var(--bg-app)] cursor-pointer ${selectedLog === log ? 'bg-[var(--bg-app)] ring-1 ring-inset ring-blue-500/50' : ''}`}
 >
 <td className="px-4 py-2 whitespace-nowrap text-[var(--text-secondary)]">{typeof timestamp === 'number' ? new Date(timestamp * 1000).toISOString() : timestamp}</td>
 <td className={`px-4 py-2 font-semibold ${levelColor}`}>{level}</td>
 <td className="px-4 py-2 text-[var(--text-secondary)] truncate max-w-[120px]">{source}</td>
 <td className="px-4 py-2 text-[var(--text-primary)] break-all">{message}</td>
 </tr>
 );
 })}
 </tbody>
 </table>
 </div>
 )
 ) : (
 <div className="flex flex-col items-center justify-center h-full text-[var(--text-secondary)] p-8 text-center">
 <Search size={48} className="mb-4 opacity-20" />
 <p className="text-lg font-medium text-[var(--text-primary)]">No results found</p>
 <p className="text-sm mt-1 max-w-md">Try modifying your query or ensure that logs have been ingested via the /ingest endpoint.</p>
 </div>
 )}
 </div>
 </div>

 {/* Sidebar */}
 <div className="w-72 flex-shrink-0 flex flex-col gap-4">
 <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4 flex-1">
 <h3 className="font-medium text-[var(--text-primary)] mb-4 flex items-center gap-2">
 <History size={16} /> Saved Queries
 </h3>
 
 {savedQueries.length === 0 ? (
 <p className="text-sm text-[var(--text-secondary)]">No saved queries yet.</p>
 ) : (
 <div className="space-y-2">
 {savedQueries.map(sq => (
 <div key={sq.id} className="group flex flex-col p-2 rounded hover:bg-[var(--bg-app)] border border-transparent hover:border-[var(--border)] transition-colors">
 <div className="flex justify-between items-start">
 <button 
 onClick={() => { setQuery(sq.query_text); handleSearch(undefined, sq.query_text); }}
 className="text-sm font-medium text-[var(--text-primary)] hover:text-blue-400 text-left"
 >
 {sq.name}
 </button>
 <button 
 onClick={() => deleteSavedQuery(sq.id)}
 className="text-xs text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
 >
 Delete
 </button>
 </div>
 <div className="text-[10px] font-mono text-[var(--text-secondary)] truncate mt-1">
 {sq.query_text}
 </div>
 </div>
 ))}
 </div>
 )}
 </div>
 
 <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg p-4 flex-1 overflow-y-auto">
 <h3 className="font-medium text-[var(--text-primary)] mb-4 flex items-center gap-2">
 <Filter size={16} /> Filters
 </h3>
 
 <div className="space-y-4">
 <div>
 <h4 className="text-xs font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-2">Sources</h4>
 {facets.source.length === 0 ? <p className="text-xs text-[var(--text-secondary)]">No sources available.</p> : (
 <ul className="space-y-1">
 {facets.source.map((f: any) => (
 <li key={f.value}>
 <button onClick={() => toggleFacet('source', f.value)} className="w-full text-left flex justify-between items-center text-sm py-1 px-2 rounded hover:bg-[var(--bg-app)] text-[var(--text-primary)]">
 <span className="truncate max-w-[150px]">{f.value}</span>
 <span className="text-[10px] text-[var(--text-secondary)] bg-[var(--bg-inset)] px-1.5 py-0.5 rounded">{f.count}</span>
 </button>
 </li>
 ))}
 </ul>
 )}
 </div>
 
 <div>
 <h4 className="text-xs font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-2">Levels</h4>
 {facets.level.length === 0 ? <p className="text-xs text-[var(--text-secondary)]">No levels available.</p> : (
 <ul className="space-y-1">
 {facets.level.map((f: any) => (
 <li key={f.value}>
 <button onClick={() => toggleFacet('level', f.value)} className="w-full text-left flex justify-between items-center text-sm py-1 px-2 rounded hover:bg-[var(--bg-app)] text-[var(--text-primary)]">
 <span className="truncate max-w-[150px]">{f.value}</span>
 <span className="text-[10px] text-[var(--text-secondary)] bg-[var(--bg-inset)] px-1.5 py-0.5 rounded">{f.count}</span>
 </button>
 </li>
 ))}
 </ul>
 )}
 </div>
 </div>
 </div>
 </div>
 </div>
 
 {/* Log Detail Drawer overlaying sidebar when active */}
 {selectedLog && (
 <div className="absolute right-0 top-[80px] bottom-0 w-1/3 min-w-[400px] bg-[var(--bg-card)] border-l border-[var(--border)] shadow-2xl z-20 flex flex-col">
 <div className="p-4 border-b border-[var(--border)] flex justify-between items-center bg-[var(--bg-app)]">
 <h3 className="font-medium text-[var(--text-primary)]">Log Details</h3>
 <button onClick={() => setSelectedLog(null)} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] p-1 rounded hover:bg-[var(--bg-card)] transition-colors">
 <X size={18} />
 </button>
 </div>
 <div className="p-4 overflow-y-auto flex-1 text-sm space-y-4 font-mono">
 <div>
 <span className="text-xs text-[var(--text-secondary)] uppercase tracking-wider block mb-1">Timestamp</span>
 <span className="text-[var(--text-primary)]">{typeof selectedLog.timestamp === 'number' ? new Date(selectedLog.timestamp * 1000).toISOString() : selectedLog.timestamp || 'Unknown'}</span>
 </div>
 <div>
 <span className="text-xs text-[var(--text-secondary)] uppercase tracking-wider block mb-1">Level</span>
 <span className={`font-semibold ${selectedLog.level === 'ERROR' || selectedLog.level === 'FATAL' ? 'text-red-400' : selectedLog.level === 'WARN' ? 'text-yellow-400' : 'text-blue-400'}`}>{selectedLog.level || 'INFO'}</span>
 </div>
 <div>
 <span className="text-xs text-[var(--text-secondary)] uppercase tracking-wider block mb-1">Source</span>
 <span className="text-[var(--text-primary)]">{selectedLog.source || selectedLog.service || 'unknown'}</span>
 </div>
 <div>
 <span className="text-xs text-[var(--text-secondary)] uppercase tracking-wider block mb-1">Message</span>
 <div className="bg-[var(--bg-app)] p-3 rounded text-[var(--text-primary)] break-all text-xs whitespace-pre-wrap border border-[var(--border)]">
 {selectedLog.message || (typeof selectedLog === 'string' ? selectedLog : '')}
 </div>
 </div>
 <div>
 <span className="text-xs text-[var(--text-secondary)] uppercase tracking-wider block mb-1">Raw JSON attributes</span>
 <pre className="bg-[var(--bg-app)] p-3 rounded text-[var(--text-primary)] break-all text-[10px] whitespace-pre-wrap border border-[var(--border)] overflow-x-auto">
 {JSON.stringify(selectedLog, null, 2)}
 </pre>
 </div>
 </div>
 </div>
 )}
 </div>
 );
}
