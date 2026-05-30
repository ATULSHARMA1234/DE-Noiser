'use client';

import React, { useState, useEffect } from 'react';
import { apiFetch } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import { Search, Save, History, Play, Terminal, Database, Clock, ChevronRight } from 'lucide-react';

export default function ExplorePage() {
  const { toast } = useToast();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [savedQueries, setSavedQueries] = useState<any[]>([]);
  const [engine, setEngine] = useState<string>('');

  useEffect(() => {
    fetchSavedQueries();
  }, []);

  const fetchSavedQueries = async () => {
    try {
      const data = await apiFetch('/query/saved');
      setSavedQueries(data || []);
    } catch (e: any) {
      // ignore
    }
  };

  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!query.trim()) return;

    setLoading(true);
    try {
      const data = await apiFetch('/query', {
        method: 'POST',
        body: JSON.stringify({ query, limit: 100 })
      });
      setResults(data.logs || []);
      setEngine(data.engine || 'in-memory');
    } catch (e: any) {
      toast({ title: 'Query Failed', description: e.message, type: 'error' });
    } finally {
      setLoading(false);
    }
  };

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
                disabled={loading}
                className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded font-medium flex items-center gap-2 transition-colors disabled:opacity-50"
              >
                {loading ? <Clock className="animate-spin" size={16} /> : <Play size={16} />}
                Run
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
              <button onClick={() => handleSampleClick('"database connection failed"')} className="hover:text-blue-400">"exact phrase"</button>
            </div>
          </div>

          {/* Results Area */}
          <div className="flex-1 overflow-auto">
            {engine && (
              <div className="bg-[var(--bg-app)] text-[10px] uppercase tracking-wider text-[var(--text-secondary)] px-4 py-1 border-b border-[var(--border)] flex justify-between">
                <span>Engine: {engine}</span>
                <span>{results.length} results</span>
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
                      <tr key={i} className="hover:bg-[var(--bg-app)]">
                        <td className="px-4 py-2 whitespace-nowrap text-[var(--text-secondary)]">{typeof timestamp === 'number' ? new Date(timestamp * 1000).toISOString() : timestamp}</td>
                        <td className={`px-4 py-2 font-semibold ${levelColor}`}>{level}</td>
                        <td className="px-4 py-2 text-[var(--text-secondary)] truncate max-w-[120px]">{source}</td>
                        <td className="px-4 py-2 text-[var(--text-primary)] break-all">{message}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
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
                        onClick={() => { setQuery(sq.query_text); handleSearch(); }}
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
        </div>
      </div>
    </div>
  );
}
