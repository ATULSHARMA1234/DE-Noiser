'use client';

import React, { useState, useEffect } from 'react';
import { apiFetch, apiPut, apiPost } from '@/lib/api';
import { useToast } from '@/context/ToastContext';
import { ShimmerCardList } from '@/components/ShimmerSkeleton';
import { LayoutGrid, Plus, Edit2, Trash2, ArrowLeft, BarChart2, Activity, List, X, PieChart as PieChartIcon, Hash, Gauge, Grid3x3, FileText } from 'lucide-react';
import { Responsive, WidthProvider } from "react-grid-layout/legacy";
import { ConfirmModal } from '@/components/ConfirmModal';
import { ResponsiveContainer, PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip as RechartsTooltip } from 'recharts';
import { DashboardHeader } from '@/components/widgets/DashboardHeader';
import { WidgetConfigModal } from '@/components/widgets/WidgetConfigModal';
import { MarkdownWidget } from '@/components/widgets/MarkdownWidget';

const ResponsiveGridLayout = WidthProvider(Responsive);

// Must mirror the server's alias map (api/dashboards.py) — dashboards created
// earlier persist these legacy type names.
const WIDGET_ALIAS: Record<string, string> = {
 stat: 'metric_card',
 timeseries: 'time_series',
 logs: 'incident_feed',
 log_table: 'incident_feed',
};

// Signal palette — severity reads the same here as everywhere else in the app.
const SLICE_COLORS = ['var(--signal-crit)', 'var(--signal-warn)', 'var(--signal-info)', 'var(--signal-ok)', 'var(--signal-alt)', 'var(--text-muted)'];
const CHART_TOOLTIP = {
 backgroundColor: 'var(--bg-elevated)',
 border: '1px solid var(--border)',
 borderRadius: '3px',
 fontSize: '11px',
 color: 'var(--text-primary)',
};

// Every entry maps to a widget type + config the API already serves
// (api/dashboards.py::get_widget_data). Named by what it tells you, not by the
// chart primitive it happens to use.
type CatalogItem = { key: string; title: string; kind: string; icon: any; type: string; config?: Record<string, any>; w: number; h: number };
const WIDGET_CATALOG: CatalogItem[] = [
 { key: 'open_incidents', title: 'Open Incidents', kind: 'Metric', icon: Hash, type: 'metric_card', config: { metric: 'open_incidents' }, w: 3, h: 3 },
 { key: 'total_incidents', title: 'Total Incidents', kind: 'Metric', icon: Hash, type: 'metric_card', config: { metric: 'total_incidents' }, w: 3, h: 3 },
 { key: 'resolved_incidents', title: 'Resolved', kind: 'Metric', icon: Hash, type: 'metric_card', config: { metric: 'resolved_incidents' }, w: 3, h: 3 },
 { key: 'avg_impact', title: 'Avg Impact', kind: 'Metric', icon: Hash, type: 'metric_card', config: { metric: 'avg_impact' }, w: 3, h: 3 },
 { key: 'slos_tracked', title: 'SLOs Tracked', kind: 'Metric', icon: Hash, type: 'metric_card', config: { metric: 'slos_tracked' }, w: 3, h: 3 },
 { key: 'clusters_last_run', title: 'Clusters (last run)', kind: 'Metric', icon: Hash, type: 'metric_card', config: { metric: 'clusters_last_run' }, w: 3, h: 3 },
 { key: 'runs_total', title: 'Analysis Runs', kind: 'Metric', icon: Hash, type: 'metric_card', config: { metric: 'runs_total' }, w: 3, h: 3 },
 { key: 'incidents_per_day', title: 'Incidents / day', kind: 'Chart', icon: Activity, type: 'time_series', w: 6, h: 4 },
 { key: 'top_domains', title: 'Top Domains', kind: 'Chart', icon: BarChart2, type: 'bar_chart', w: 6, h: 4 },
 { key: 'severity_split', title: 'Severity Split', kind: 'Chart', icon: PieChartIcon, type: 'pie_chart', config: { by: 'severity' }, w: 4, h: 4 },
 { key: 'resolved_ratio', title: 'Resolved Ratio', kind: 'Chart', icon: Gauge, type: 'gauge', w: 4, h: 4 },
 { key: 'incident_heatmap', title: 'Incident Heatmap', kind: 'Chart', icon: Grid3x3, type: 'heatmap', config: { days: 30 }, w: 6, h: 4 },
 { key: 'recent_incidents', title: 'Recent Incidents', kind: 'Feed', icon: List, type: 'incident_feed', w: 6, h: 5 },
 { key: 'notes', title: 'Notes', kind: 'Markdown', icon: FileText, type: 'markdown', config: { content: '## Notes\n\nWrite anything here.' }, w: 4, h: 3 },
];

const DAY_LABELS = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];

function EmptyWidget({ label }: { label: string }) {
 return (
 <div className="h-full flex items-center justify-center">
 <span className="eyebrow">{label}</span>
 </div>
 );
}

export default function DashboardsPage() {
 const { toast } = useToast();
 const [dashboards, setDashboards] = useState<any[]>([]);
 const [loading, setLoading] = useState(true);
 
 const [selectedDashboard, setSelectedDashboard] = useState<any>(null);
 const [isEditing, setIsEditing] = useState(false);
 const [showCreateModal, setShowCreateModal] = useState(false);
 const [newDashName, setNewDashName] = useState('');
 const [widgetDataCache, setWidgetDataCache] = useState<Record<string, any>>({});

 const [confirmOpen, setConfirmOpen] = useState(false);
 const [confirmTitle, setConfirmTitle] = useState('');
 const [confirmMessage, setConfirmMessage] = useState('');
 const [confirmCallback, setConfirmCallback] = useState<(() => void) | null>(null);

 const [globalTimeRange, setGlobalTimeRange] = useState('1h');
 const [autoRefreshInterval, setAutoRefreshInterval] = useState(0);
 const [showWidgetConfigModal, setShowWidgetConfigModal] = useState(false);
 const [showCatalog, setShowCatalog] = useState(false);
 const [editingWidget, setEditingWidget] = useState<any>(null);

 useEffect(() => {
 // eslint-disable-next-line react-hooks/immutability
 fetchDashboards();
 }, []);

 useEffect(() => {
 if (autoRefreshInterval > 0 && selectedDashboard && !isEditing) {
 const interval = setInterval(() => {
 selectedDashboard.widgets.forEach((w: any) => fetchWidgetData(selectedDashboard.id, w.id));
 }, autoRefreshInterval);
 return () => clearInterval(interval);
 }
 }, [autoRefreshInterval, selectedDashboard, isEditing]);

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

 async function fetchWidgetData(dashId: number, widgetId: string) {
 try {
 const data = await apiFetch(`/dashboards/${dashId}/widgets/${widgetId}/data?start_time=${globalTimeRange}`);
 setWidgetDataCache(prev => ({ ...prev, [widgetId]: data }));
 } catch (e: any) {
 console.error('Failed to fetch widget data', e);
 }
 };

 const createDashboard = async (e: React.FormEvent) => {
 e.preventDefault();
 try {
 const newDash = await apiPost('/dashboards', { 
 name: newDashName, 
 layout: [], 
 widgets: [], 
 is_shared: false,
 default_time_range: '1h',
 template_variables: []
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

 const deleteDashboard = (id: number) => {
 setConfirmTitle('Delete Dashboard');
 setConfirmMessage('Delete this dashboard?');
 setConfirmCallback(() => async () => {
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
 });
 setConfirmOpen(true);
 };

 const updateDashboardState = (updatedDash: any) => {
 setSelectedDashboard(updatedDash);
 setDashboards(prev => prev.map(d => d.id === updatedDash.id ? updatedDash : d));
 };

 const handleSaveWidget = async (widgetData: any) => {
 if (!selectedDashboard) return;
 
 const isExisting = selectedDashboard.widgets.some((w: any) => w.id === widgetData.id);
 let updatedWidgets;
 let updatedLayout = selectedDashboard.layout || [];
 
 if (isExisting) {
 // Update existing
 updatedWidgets = selectedDashboard.widgets.map((w: any) => w.id === widgetData.id ? widgetData : w);
 } else {
 // Add new
 updatedWidgets = [...selectedDashboard.widgets, widgetData];
 
 // If this is a new widget, we MUST add a layout entry so react-grid-layout renders it
 const maxY = updatedLayout.length > 0 ? Math.max(...updatedLayout.map((l: any) => l.y + l.h)) : 0;
 updatedLayout = [
 ...updatedLayout, 
 { i: widgetData.id, x: 0, y: maxY, w: 4, h: 4, minW: 2, minH: 3 }
 ];
 }

 const updatedDash = { ...selectedDashboard, widgets: updatedWidgets, layout: updatedLayout };
 
 try {
 await apiPut(`/dashboards/${selectedDashboard.id}`, { widgets: updatedWidgets, layout: updatedLayout });
 updateDashboardState(updatedDash);
 fetchWidgetData(selectedDashboard.id, widgetData.id);
 setShowWidgetConfigModal(false);
 setEditingWidget(null);
 } catch (e: any) {
 toast({ title: 'Failed to save widget', type: 'error' });
 }
 };

 const openWidgetConfig = (type: string) => {
 setEditingWidget({ type }); // initial data structure for new widget
 setShowWidgetConfigModal(true);
 };

 // Catalog entries already know their type + config, so they go straight onto the
 // board rather than through the generic config modal.
 const addFromCatalog = async (item: CatalogItem) => {
 if (!selectedDashboard) return;
 const id = `w_${item.key}_${selectedDashboard.widgets.length + 1}`;
 const widget = { id, type: item.type, title: item.title, config: item.config || {} };
 const widgets = [...selectedDashboard.widgets, widget];
 const layout = selectedDashboard.layout || [];
 const maxY = layout.length ? Math.max(...layout.map((l: any) => l.y + l.h)) : 0;
 const nextLayout = [...layout, { i: id, x: 0, y: maxY, w: item.w, h: item.h, minW: 2, minH: 2 }];
 try {
 await apiPut(`/dashboards/${selectedDashboard.id}`, { widgets, layout: nextLayout });
 updateDashboardState({ ...selectedDashboard, widgets, layout: nextLayout });
 fetchWidgetData(selectedDashboard.id, id);
 setShowCatalog(false);
 } catch (e: any) {
 toast({ title: 'Failed to add widget', description: e.message, type: 'error' });
 }
 };

 const onLayoutChange = async (layout: any) => {
 if (!selectedDashboard || !isEditing) return;
 
 const updatedDash = { ...selectedDashboard, layout };
 updateDashboardState(updatedDash);
 
 try {
 await apiPut(`/dashboards/${selectedDashboard.id}`, { layout });
 } catch (e: any) {
 console.error('Failed to save layout', e);
 }
 };

 const removeWidget = async (widgetId: string) => {
 if (!selectedDashboard) return;
 
 const updatedWidgets = selectedDashboard.widgets.filter((w: any) => w.id !== widgetId);
 const updatedDash = { ...selectedDashboard, widgets: updatedWidgets };
 
 try {
 await apiPut(`/dashboards/${selectedDashboard.id}`, { widgets: updatedWidgets });
 updateDashboardState(updatedDash);
 } catch (e: any) {
 toast({ title: 'Failed to remove widget', type: 'error' });
 }
 };

 const renderWidget = (w: any) => {
 const data = widgetDataCache[w.id];
 // Dashboards created earlier store legacy type names. The API normalizes them,
 // so the client has to as well — otherwise the data arrives fine and the widget
 // still renders "unsupported" because it switched on the raw type.
 const type = WIDGET_ALIAS[w.type] ?? w.type;

 // Markdown is authored in the widget config — it has nothing to fetch, so it
 // must render before the data guard or it would sit on "Loading" forever.
 if (type === 'markdown') {
 return <MarkdownWidget content={w.config?.content || ''} />;
 }

 if (!data) {
 return (
 <div className="h-full flex flex-col justify-center gap-2 px-1">
 <div className="shimmer-bg h-2.5 w-1/3 rounded-[2px]" />
 <div className="shimmer-bg h-2.5 w-2/3 rounded-[2px]" />
 </div>
 );
 }

 if (type === 'metric_card') {
 const tone = data.tone === 'crit' ? 'var(--signal-crit)'
 : data.tone === 'warn' ? 'var(--signal-warn)'
 : data.tone === 'ok' ? 'var(--signal-ok)'
 : 'var(--text-primary)';
 return (
 <div className="flex flex-col justify-center h-full">
 <div className="text-[34px] leading-none font-semibold tnum mono" style={{ color: tone }}>
 {typeof data.value === 'number' ? data.value.toLocaleString() : (data.value ?? 0)}
 </div>
 <div className="eyebrow mt-2">{data.label || w.title}</div>
 </div>
 );
 }

 if (type === 'time_series') {
 const series = data.series?.[0]?.data || [];
 if (!series.length) return <EmptyWidget label="No incidents in range" />;

 const max = Math.max(...series.map((d: any) => d.value), 1);
 const points = series.map((d: any, i: number) => {
 const x = (i / Math.max(1, series.length - 1)) * 100;
 const y = 100 - (d.value / max) * 100;
 return `${x},${y}`;
 }).join(' ');
 const latest = series[series.length - 1]?.value ?? 0;

 return (
 <div className="flex flex-col h-full w-full">
 <div className="flex items-baseline gap-2">
 <span className="text-[20px] font-semibold tnum mono text-[var(--text-primary)]">{latest}</span>
 <span className="eyebrow">peak {max}</span>
 </div>
 <svg className="flex-1 w-full mt-1" preserveAspectRatio="none" viewBox="0 0 100 100">
 <polyline points={points} fill="none" stroke="var(--primary)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
 </svg>
 </div>
 );
 }

 if (type === 'gauge') {
 const pct = Math.round((data.value ?? 0) * 100);
 const target = Math.round((data.target ?? 0.8) * 100);
 const hit = pct >= target;
 const color = hit ? 'var(--signal-ok)' : pct >= target * 0.6 ? 'var(--signal-warn)' : 'var(--signal-crit)';
 // Semicircular arc: r=52, so the sweep is π·52 ≈ 163.4 units long.
 const LEN = Math.PI * 52;
 const targetAngle = Math.PI * (Math.min(100, target) / 100);
 return (
 <div className="flex flex-col items-center justify-center h-full">
 <svg viewBox="0 0 120 68" className="w-full max-w-[180px]">
 <path d="M 8 60 A 52 52 0 0 1 112 60" fill="none" stroke="var(--bg-track)" strokeWidth="8" strokeLinecap="round" />
 <path
 d="M 8 60 A 52 52 0 0 1 112 60"
 fill="none" stroke={color} strokeWidth="8" strokeLinecap="round"
 strokeDasharray={`${(Math.min(100, pct) / 100) * LEN} ${LEN}`}
 />
 {/* target tick — a ratio without its target is just a number */}
 <line
 x1={60 - 57 * Math.cos(targetAngle)} y1={60 - 57 * Math.sin(targetAngle)}
 x2={60 - 47 * Math.cos(targetAngle)} y2={60 - 47 * Math.sin(targetAngle)}
 stroke="var(--text-secondary)" strokeWidth="1.5"
 />
 <text x="60" y="56" textAnchor="middle" className="mono" fontSize="19" fontWeight="600" fill={color}>{pct}%</text>
 </svg>
 <div className="eyebrow mt-1">{data.resolved ?? 0}/{data.total ?? 0} · target {target}%</div>
 </div>
 );
 }

 if (type === 'heatmap') {
 const cells: any[] = data.cells || [];
 if (!cells.length) return <EmptyWidget label="No incidents in range" />;
 const max = data.max || 1;
 const at = (d: number, h: number) => cells.find((c) => c.day === d && c.hour === h)?.value ?? 0;
 return (
 <div className="flex flex-col h-full gap-1 overflow-auto">
 <span className="eyebrow">Last {data.days ?? 30}d · by weekday × hour</span>
 <div className="flex flex-col gap-[2px]">
 {DAY_LABELS.map((label, d) => (
 <div key={d} className="flex items-center gap-[3px]">
 <span className="eyebrow w-3 shrink-0">{label}</span>
 <div className="flex gap-[2px]">
 {Array.from({ length: 24 }, (_, h) => {
 const v = at(d, h);
 return (
 <span
 key={h}
 title={`${label} ${h}:00 — ${v} incident${v === 1 ? '' : 's'}`}
 className="w-2 h-2 rounded-[1px]"
 style={{
 background: v ? 'var(--signal-crit)' : 'var(--bg-track)',
 opacity: v ? 0.25 + 0.75 * (v / max) : 1,
 }}
 />
 );
 })}
 </div>
 </div>
 ))}
 </div>
 </div>
 );
 }

 if (type === 'incident_feed') {
 // The backend serves this from the incident feed, so read `incidents` and
 // fall back to `logs` for any dashboard still shaped the old way.
 const rows = data.incidents || data.logs || [];
 if (!rows.length) return <EmptyWidget label="Nothing to show" />;

 return (
 <div className="overflow-auto h-full mono text-[11px]">
 <table className="w-full text-left">
 <tbody className="divide-y divide-[var(--border-subtle)]">
 {rows.map((r: any, i: number) => {
 const open = (r.status || '').toUpperCase() === 'OPEN';
 return (
 <tr key={r.id ?? i} className="hover:bg-[var(--bg-surface-hover)]">
 <td className="py-1.5 pr-2 whitespace-nowrap text-[var(--text-dimmed)]">
 {r.created_at || r.timestamp
 ? new Date(r.created_at || r.timestamp).toLocaleTimeString()
 : '—'}
 </td>
 <td className="py-1.5 pr-2">
 <span
 className="px-1.5 py-0.5 rounded-[2px] text-[9px] uppercase tracking-wider"
 style={{
 color: open ? 'var(--signal-crit)' : 'var(--signal-ok)',
 background: open ? 'var(--signal-crit-dim)' : 'var(--signal-ok-dim)',
 }}
 >
 {r.status || r.level || 'INFO'}
 </span>
 </td>
 <td className="py-1.5 text-[var(--text-secondary)] truncate max-w-[220px]">
 {r.title || r.message}
 </td>
 </tr>
 );
 })}
 </tbody>
 </table>
 </div>
 );
 }

 if (type === 'pie_chart') {
 // Real distribution from /widgets/{id}/data — this used to render a
 // hardcoded array, so every pie showed the same invented numbers.
 const slices = data.slices || [];
 if (!slices.length) return <EmptyWidget label="No incidents in range" />;

 return (
 <div className="flex flex-col items-center justify-center h-full w-full">
 <ResponsiveContainer width="100%" height="100%">
 <PieChart>
 <Pie
 data={slices}
 cx="50%"
 cy="50%"
 innerRadius={38}
 outerRadius={72}
 paddingAngle={2}
 dataKey="value"
 stroke="var(--bg-card)"
 strokeWidth={2}
 >
 {slices.map((entry: any, index: number) => (
 <Cell key={`cell-${index}`} fill={SLICE_COLORS[index % SLICE_COLORS.length]} />
 ))}
 </Pie>
 <RechartsTooltip contentStyle={CHART_TOOLTIP} />
 </PieChart>
 </ResponsiveContainer>
 </div>
 );
 }

 if (type === 'bar_chart') {
 // Top domains by incident count — was a hardcoded Mon..Sun array.
 const bars = data.bars || [];
 if (!bars.length) return <EmptyWidget label="No incidents in range" />;

 return (
 <div className="flex flex-col h-full w-full">
 <ResponsiveContainer width="100%" height="100%">
 <BarChart data={bars} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
 <XAxis dataKey="label" tick={{ fill: 'var(--text-muted)', fontSize: 10 }} axisLine={false} tickLine={false} />
 <YAxis tick={{ fill: 'var(--text-muted)', fontSize: 10 }} axisLine={false} tickLine={false} allowDecimals={false} />
 <RechartsTooltip contentStyle={CHART_TOOLTIP} cursor={{ fill: 'var(--bg-surface-hover)' }} />
 <Bar dataKey="value" fill="var(--signal-crit)" radius={[2, 2, 0, 0]} />
 </BarChart>
 </ResponsiveContainer>
 </div>
 );
 }

 return <EmptyWidget label={`Unsupported widget: ${w.type}`} />;
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
 isEditing ? 'bg-[var(--primary)] text-white hover:bg-[var(--primary)]' : 'border border-[var(--border)] text-[var(--text-primary)] hover:bg-[var(--bg-card-hover)]'
 }`}
 >
 <Edit2 size={16} /> {isEditing ? 'Done Editing' : 'Edit Dashboard'}
 </button>
 </div>
 </div>

 {isEditing && (
 <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-[3px] p-3 flex items-center gap-3">
 <span className="eyebrow">Editing</span>
 <button
 onClick={() => setShowCatalog(true)}
 className="flex items-center gap-1.5 px-3 h-8 rounded-[3px] bg-[var(--primary)] text-black text-[13px] hover:bg-[var(--primary-hover)] transition-colors border-none cursor-pointer"
 >
 <Plus size={14} /> Add widget
 </button>
 <span className="text-[12px] text-[var(--text-muted)]">Drag to move, drag a corner to resize. Layout saves automatically.</span>
 </div>
 )}

 {/* Widget catalog — pick the thing you want to see, not the chart type it
 happens to be drawn with. Each entry carries the config the API needs. */}
 {showCatalog && (
 <div className="fixed inset-0 bg-black/70 z-[100] flex items-center justify-center p-6" onClick={() => setShowCatalog(false)}>
 <div className="bg-[var(--bg-modal)] border border-[var(--border)] rounded-[4px] w-[720px] max-h-[80vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
 <div className="flex items-start justify-between px-5 py-4 border-b border-[var(--border-subtle)]">
 <div>
 <div className="eyebrow mb-1">Widget catalog</div>
 <h2 className="text-[16px] font-semibold text-[var(--text-primary)]">Add a widget</h2>
 </div>
 <button onClick={() => setShowCatalog(false)} className="text-[var(--text-muted)] hover:text-[var(--text-primary)] bg-transparent border-none cursor-pointer">
 <X size={18} />
 </button>
 </div>
 <div className="p-4 overflow-y-auto grid grid-cols-2 gap-2">
 {WIDGET_CATALOG.map((item) => {
 const Icon = item.icon;
 return (
 <button
 key={item.key}
 onClick={() => addFromCatalog(item)}
 className="flex items-center gap-3 p-3 rounded-[3px] border border-[var(--border-subtle)] bg-[var(--bg-card)] hover:border-[var(--primary-line)] hover:bg-[var(--bg-surface-hover)] transition-colors text-left cursor-pointer"
 >
 <span className="w-9 h-9 shrink-0 rounded-[3px] flex items-center justify-center border border-[var(--primary-line)] bg-[var(--primary-dim)]">
 <Icon size={16} className="text-[var(--primary)]" />
 </span>
 <span className="min-w-0">
 <span className="block text-[13px] text-[var(--text-primary)] truncate">{item.title}</span>
 <span className="eyebrow">{item.kind}</span>
 </span>
 </button>
 );
 })}
 </div>
 </div>
 </div>
 )}

 <DashboardHeader 
 globalTimeRange={globalTimeRange}
 setGlobalTimeRange={(val) => {
 setGlobalTimeRange(val);
 if (selectedDashboard) {
 selectedDashboard.widgets.forEach((w: any) => fetchWidgetData(selectedDashboard.id, w.id));
 }
 }}
 autoRefreshInterval={autoRefreshInterval}
 setAutoRefreshInterval={setAutoRefreshInterval}
 onRefresh={() => {
 if (selectedDashboard) {
 selectedDashboard.widgets.forEach((w: any) => fetchWidgetData(selectedDashboard.id, w.id));
 }
 }}
 isEditing={isEditing}
 />

 <div className="flex-1 w-full min-h-[400px]">
 {selectedDashboard.widgets.length === 0 ? (
 <div className="flex flex-col items-center justify-center h-[300px] bg-[var(--bg-card)] border border-[var(--border)] rounded-lg text-[var(--text-secondary)]">
 <LayoutGrid size={32} className="mb-2 opacity-50" />
 <p>No widgets added yet.</p>
 {!isEditing && (
 <button onClick={() => setIsEditing(true)} className="mt-4 text-[var(--primary)] hover:underline text-sm font-medium">Edit dashboard to add widgets</button>
 )}
 </div>
 ) : (
 <ResponsiveGridLayout
 className="layout"
 layouts={{
 lg: (selectedDashboard.layout && selectedDashboard.layout.length > 0)
 ? selectedDashboard.layout 
 : selectedDashboard.widgets.map((w: any, i: number) => ({
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
 <div key={w.id} className={`bg-[var(--bg-card)] border ${isEditing ? 'border-[var(--primary)] border-dashed hover:border-[var(--primary)]' : 'border-[var(--border)]'} rounded-lg overflow-hidden flex flex-col group h-full shadow-sm`}>
 {isEditing && (
 <button 
 onClick={(e) => { e.stopPropagation(); removeWidget(w.id); }}
 className="absolute top-2 right-2 p-1.5 bg-red-500/10 text-red-500 rounded hover:bg-red-500 hover:text-white transition-colors z-10 opacity-0 group-hover:opacity-100"
 >
 <X size={14} />
 </button>
 )}
 <div className={`p-3 border-b border-[var(--border)] bg-[var(--bg-app)] flex justify-between items-center ${isEditing ? 'cursor-move' : ''}`}>
 <span className="text-sm font-semibold text-[var(--text-primary)]">{w.title}</span>
 {isEditing && (
 <button onClick={(e) => { e.stopPropagation(); setEditingWidget(w); setShowWidgetConfigModal(true); }} className="text-[var(--text-secondary)] hover:text-[var(--primary)]">
 <Edit2 size={14} />
 </button>
 )}
 </div>
 <div className="flex-1 p-4 overflow-hidden relative">
 {renderWidget(w)}
 </div>
 </div>
 ))}
 </ResponsiveGridLayout>
 )}
 <ConfirmModal
 isOpen={confirmOpen}
 onClose={() => setConfirmOpen(false)}
 onConfirm={confirmCallback || (() => {})}
 title={confirmTitle}
 message={confirmMessage}
 />

 {showWidgetConfigModal && (
 <WidgetConfigModal 
 initialData={editingWidget}
 onClose={() => { setShowWidgetConfigModal(false); setEditingWidget(null); }}
 onSave={handleSaveWidget}
 />
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
 className="bg-[var(--primary)] hover:bg-[var(--primary)] text-white px-4 py-2 rounded font-medium flex items-center gap-2 transition-colors"
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
 <button onClick={() => setShowCreateModal(true)} className="text-[var(--primary)] hover:underline text-sm font-medium">Create Dashboard</button>
 </div>
 ) : (
 dashboards.map(dash => (
 <div key={dash.id} className="bg-[var(--bg-card)] border border-[var(--border)] hover:border-[var(--primary)] rounded-lg p-6 transition-colors group cursor-pointer" onClick={() => loadDashboard(dash)}>
 <div className="flex justify-between items-start mb-4">
 <h3 className="text-lg font-bold text-[var(--text-primary)] group-hover:text-[var(--primary)] transition-colors">{dash.name}</h3>
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
 className="w-full bg-[var(--bg-app)] border border-[var(--border)] rounded-md py-2 px-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
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
 className="bg-[var(--primary)] hover:bg-[var(--primary)] text-white px-4 py-2 rounded text-sm font-medium transition-colors"
 >
 Create
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

 {showWidgetConfigModal && (
 <WidgetConfigModal 
 initialData={editingWidget}
 onClose={() => { setShowWidgetConfigModal(false); setEditingWidget(null); }}
 onSave={handleSaveWidget}
 />
 )}
 </div>
 );
}
