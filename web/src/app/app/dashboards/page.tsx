'use client';

import React, { useState, useEffect } from 'react';
import { apiFetch } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import { LayoutGrid, Plus, Edit2, Trash2, ArrowLeft, BarChart2, Activity, List, X } from 'lucide-react';
import { Responsive, WidthProvider } from "react-grid-layout";

const ResponsiveGridLayout = WidthProvider(Responsive);

export default function DashboardsPage() {
  const { toast } = useToast();
  const [dashboards, setDashboards] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  
  const [selectedDashboard, setSelectedDashboard] = useState<any>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newDashName, setNewDashName] = useState('');
  const [widgetDataCache, setWidgetDataCache] = useState<Record<string, any>>({});

  useEffect(() => {
    fetchDashboards();
  }, []);

  const fetchDashboards = async () => {
    setLoading(true);
    try {
      const data = await apiFetch('/dashboards');
      setDashboards(data || []);
    } catch (e: any) {
      toast({ title: 'Error fetching dashboards', description: e.message, type: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const loadDashboard = async (dash: any) => {
    setSelectedDashboard(dash);
    setIsEditing(false);
    // Fetch data for all widgets
    for (const w of dash.widgets) {
      fetchWidgetData(dash.id, w.id);
    }
  };

  const fetchWidgetData = async (dashId: number, widgetId: string) => {
    try {
      const data = await apiFetch(`/dashboards/${dashId}/widgets/${widgetId}/data`);
      setWidgetDataCache(prev => ({ ...prev, [widgetId]: data }));
    } catch (e: any) {
      console.error('Failed to fetch widget data', e);
    }
  };

  const createDashboard = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const newDash = await apiFetch('/dashboards', {
        method: 'POST',
        body: JSON.stringify({ name: newDashName, layout: [], widgets: [], is_shared: false })
      });
      toast({ title: 'Dashboard created' });
      setShowCreateModal(false);
      setNewDashName('');
      fetchDashboards();
      loadDashboard(newDash);
    } catch (e: any) {
      toast({ title: 'Creation failed', description: e.message, type: 'error' });
    }
  };

  const deleteDashboard = async (id: number) => {
    if (!confirm('Delete this dashboard?')) return;
    try {
      await apiFetch(`/dashboards/${id}`, { method: 'DELETE' });
      toast({ title: 'Dashboard deleted' });
      if (selectedDashboard?.id === id) {
        setSelectedDashboard(null);
      }
      fetchDashboards();
    } catch (e: any) {
      toast({ title: 'Deletion failed', type: 'error' });
    }
  };

  const addWidget = async (type: string) => {
    if (!selectedDashboard) return;
    
    const newWidget = {
      id: `w_${Date.now()}`,
      type,
      title: `New ${type.replace('_', ' ')}`,
      config: {}
    };
    
    // Add layout coords to widget.layout if we wanted to persist, but for now we'll just add it to widgets array.
    const updatedWidgets = [...selectedDashboard.widgets, newWidget];
    const updatedDash = { ...selectedDashboard, widgets: updatedWidgets };
    
    try {
      await apiFetch(`/dashboards/${selectedDashboard.id}`, {
        method: 'PUT',
        body: JSON.stringify({ widgets: updatedWidgets })
      });
      setSelectedDashboard(updatedDash);
      fetchWidgetData(selectedDashboard.id, newWidget.id);
    } catch (e: any) {
      toast({ title: 'Failed to add widget', type: 'error' });
    }
  };

  const onLayoutChange = (layout: any) => {
    // Save layout state in the background if needed
    // The `layout` array contains {i, x, y, w, h}
  };

  const removeWidget = async (widgetId: string) => {
    if (!selectedDashboard) return;
    
    const updatedWidgets = selectedDashboard.widgets.filter((w: any) => w.id !== widgetId);
    const updatedDash = { ...selectedDashboard, widgets: updatedWidgets };
    
    try {
      await apiFetch(`/dashboards/${selectedDashboard.id}`, {
        method: 'PUT',
        body: JSON.stringify({ widgets: updatedWidgets })
      });
      setSelectedDashboard(updatedDash);
    } catch (e: any) {
      toast({ title: 'Failed to remove widget', type: 'error' });
    }
  };

  const renderWidget = (w: any) => {
    const data = widgetDataCache[w.id];
    
    if (!data) return <div className="h-full flex items-center justify-center text-[var(--text-secondary)] animate-pulse">Loading...</div>;

    if (w.type === 'metric_card') {
      return (
        <div className="flex flex-col items-center justify-center h-full">
          <div className="text-4xl font-bold text-[var(--text-primary)]">{data.value?.toLocaleString()}</div>
          <div className="text-sm text-[var(--text-secondary)] mt-2">{w.title}</div>
        </div>
      );
    }
    
    if (w.type === 'time_series') {
      // Mock simple sparkline
      const series = data.series?.[0]?.data || [];
      if (!series.length) return <div>No data</div>;
      
      const max = Math.max(...series.map((d: any) => d.value));
      const min = 0;
      const range = max - min || 1;
      
      const points = series.map((d: any, i: number) => {
        const x = (i / (series.length - 1)) * 100;
        const y = 100 - ((d.value - min) / range) * 100;
        return `${x},${y}`;
      }).join(' ');

      return (
        <div className="flex flex-col h-full w-full">
          <svg className="flex-1 w-full" preserveAspectRatio="none">
            <polyline points={points} fill="none" stroke="#a855f7" strokeWidth="2" vectorEffect="non-scaling-stroke" />
          </svg>
        </div>
      );
    }

    if (w.type === 'log_table') {
      return (
        <div className="overflow-auto h-full text-xs">
          <table className="w-full text-left">
            <tbody className="divide-y divide-[var(--border)]">
              {(data.logs || []).map((log: any, i: number) => (
                <tr key={i}>
                  <td className="py-1 pr-2 whitespace-nowrap text-[var(--text-secondary)]">{new Date(log.timestamp).toLocaleTimeString()}</td>
                  <td className={`py-1 pr-2 font-bold ${log.level === 'ERROR' ? 'text-red-400' : 'text-blue-400'}`}>{log.level}</td>
                  <td className="py-1 text-[var(--text-primary)] truncate max-w-[200px]">{log.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    return <div>Unknown Widget Type</div>;
  };

  if (selectedDashboard) {
    return (
      <div className="flex flex-col h-full space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button 
              onClick={() => setSelectedDashboard(null)}
              className="p-2 border border-[var(--border)] rounded hover:bg-[var(--bg-app)] transition-colors text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            >
              <ArrowLeft size={16} />
            </button>
            <div>
              <h1 className="text-2xl font-bold text-[var(--text-primary)]">{selectedDashboard.name}</h1>
              <p className="text-[var(--text-secondary)] mt-1">Custom Dashboard</p>
            </div>
          </div>
          <div className="flex gap-2">
            <button 
              onClick={() => setIsEditing(!isEditing)}
              className={`px-4 py-2 rounded font-medium flex items-center gap-2 transition-colors ${
                isEditing ? 'bg-fuchsia-600 text-white hover:bg-fuchsia-700' : 'border border-[var(--border)] text-[var(--text-primary)] hover:bg-[var(--bg-card-hover)]'
              }`}
            >
              <Edit2 size={16} /> {isEditing ? 'Done Editing' : 'Edit Dashboard'}
            </button>
          </div>
        </div>

        {isEditing && (
          <div className="bg-[var(--bg-card)] border border-fuchsia-500/30 rounded-lg p-4 flex items-center gap-4">
            <span className="text-sm font-medium text-[var(--text-primary)]">Add Widget:</span>
            <button onClick={() => addWidget('metric_card')} className="flex items-center gap-2 px-3 py-1.5 bg-[var(--bg-app)] border border-[var(--border)] rounded text-sm hover:border-fuchsia-500 transition-colors">
              <Activity size={14} className="text-fuchsia-400" /> Metric Card
            </button>
            <button onClick={() => addWidget('time_series')} className="flex items-center gap-2 px-3 py-1.5 bg-[var(--bg-app)] border border-[var(--border)] rounded text-sm hover:border-fuchsia-500 transition-colors">
              <BarChart2 size={14} className="text-blue-400" /> Time Series
            </button>
            <button onClick={() => addWidget('log_table')} className="flex items-center gap-2 px-3 py-1.5 bg-[var(--bg-app)] border border-[var(--border)] rounded text-sm hover:border-fuchsia-500 transition-colors">
              <List size={14} className="text-emerald-400" /> Log Table
            </button>
          </div>
        )}

        <div className="flex-1 w-full min-h-[400px]">
          {selectedDashboard.widgets.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-[300px] bg-[var(--bg-card)] border border-[var(--border)] rounded-lg text-[var(--text-secondary)]">
              <LayoutGrid size={32} className="mb-2 opacity-50" />
              <p>No widgets added yet.</p>
              {!isEditing && (
                <button onClick={() => setIsEditing(true)} className="mt-4 text-fuchsia-500 hover:underline text-sm font-medium">Edit dashboard to add widgets</button>
              )}
            </div>
          ) : (
            <ResponsiveGridLayout
              className="layout"
              layouts={{
                lg: selectedDashboard.widgets.map((w: any, i: number) => ({
                  i: w.id,
                  x: (i * 4) % 12,
                  y: Math.floor(i / 3) * 4,
                  w: 4,
                  h: 4,
                  minW: 2,
                  minH: 3
                }))
              }}
              breakpoints={{ lg: 1200, md: 996, sm: 768, xs: 480, xxs: 0 }}
              cols={{ lg: 12, md: 10, sm: 6, xs: 4, xxs: 2 }}
              rowHeight={50}
              onLayoutChange={onLayoutChange}
              isDraggable={isEditing}
              isResizable={isEditing}
              margin={[16, 16]}
            >
              {selectedDashboard.widgets.map((w: any) => (
                <div key={w.id} className={`bg-[var(--bg-card)] border ${isEditing ? 'border-fuchsia-500/50 border-dashed hover:border-fuchsia-500' : 'border-[var(--border)]'} rounded-lg overflow-hidden flex flex-col group h-full shadow-sm`}>
                  {isEditing && (
                    <button 
                      onClick={(e) => { e.stopPropagation(); removeWidget(w.id); }}
                      className="absolute top-2 right-2 p-1.5 bg-red-500/10 text-red-500 rounded hover:bg-red-500 hover:text-white transition-colors z-10 opacity-0 group-hover:opacity-100"
                    >
                      <X size={14} />
                    </button>
                  )}
                  <div className={`p-3 border-b border-[var(--border)] bg-[var(--bg-app)] text-sm font-semibold text-[var(--text-primary)] ${isEditing ? 'cursor-move' : ''}`}>
                    {w.title}
                  </div>
                  <div className="flex-1 p-4 overflow-hidden relative">
                    {renderWidget(w)}
                  </div>
                </div>
              ))}
            </ResponsiveGridLayout>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-[var(--text-primary)]">Dashboards</h1>
          <p className="text-[var(--text-secondary)] mt-1">Build custom views combining metrics, traces, and logs.</p>
        </div>
        <button 
          onClick={() => setShowCreateModal(true)}
          className="bg-fuchsia-600 hover:bg-fuchsia-700 text-white px-4 py-2 rounded font-medium flex items-center gap-2 transition-colors"
        >
          <Plus size={16} /> New Dashboard
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {loading ? (
          // Shimmer
          [...Array(3)].map((_, i) => (
            <div key={i} className="shimmer-bg border border-[var(--border)] rounded-lg p-6 h-32"></div>
          ))
        ) : dashboards.length === 0 ? (
          <div className="col-span-full py-12 text-center border border-[var(--border)] border-dashed rounded-lg">
            <LayoutGrid className="mx-auto text-[var(--text-secondary)] opacity-50 mb-3" size={32} />
            <h3 className="text-lg font-medium text-[var(--text-primary)] mb-1">No Dashboards Yet</h3>
            <p className="text-sm text-[var(--text-secondary)] mb-4">Create a dashboard to track important metrics and logs.</p>
            <button onClick={() => setShowCreateModal(true)} className="text-fuchsia-500 hover:underline text-sm font-medium">Create Dashboard</button>
          </div>
        ) : (
          dashboards.map(dash => (
            <div key={dash.id} className="bg-[var(--bg-card)] border border-[var(--border)] hover:border-fuchsia-500/50 rounded-lg p-6 transition-colors group cursor-pointer" onClick={() => loadDashboard(dash)}>
              <div className="flex justify-between items-start mb-4">
                <h3 className="text-lg font-bold text-[var(--text-primary)] group-hover:text-fuchsia-400 transition-colors">{dash.name}</h3>
                <button 
                  onClick={(e) => { e.stopPropagation(); deleteDashboard(dash.id); }}
                  className="text-[var(--text-secondary)] hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  <Trash2 size={16} />
                </button>
              </div>
              <div className="flex items-center gap-4 text-sm text-[var(--text-secondary)]">
                <span className="flex items-center gap-1"><LayoutGrid size={14} /> {dash.widgets?.length || 0} Widgets</span>
                <span className="flex items-center gap-1"><Activity size={14} /> {dash.is_shared ? 'Shared' : 'Private'}</span>
              </div>
            </div>
          ))
        )}
      </div>

      {showCreateModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-lg shadow-xl w-full max-w-md overflow-hidden">
            <div className="p-4 border-b border-[var(--border)] flex justify-between items-center bg-[var(--bg-app)]">
              <h2 className="font-bold text-[var(--text-primary)]">Create Dashboard</h2>
              <button onClick={() => setShowCreateModal(false)} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
                <X size={20} />
              </button>
            </div>
            <form onSubmit={createDashboard} className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-[var(--text-primary)] mb-1">Name</label>
                <input 
                  type="text" 
                  required
                  value={newDashName}
                  onChange={e => setNewDashName(e.target.value)}
                  className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-fuchsia-500"
                  placeholder="e.g. Production Overview"
                />
              </div>
              <div className="pt-4 flex justify-end gap-3">
                <button 
                  type="button" 
                  onClick={() => setShowCreateModal(false)}
                  className="px-4 py-2 rounded text-sm font-medium border border-[var(--border)] hover:bg-[var(--bg-app)] transition-colors text-[var(--text-primary)]"
                >
                  Cancel
                </button>
                <button 
                  type="submit" 
                  className="bg-fuchsia-600 hover:bg-fuchsia-700 text-white px-4 py-2 rounded text-sm font-medium transition-colors"
                >
                  Create
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
