import React, { useState } from 'react';
import { X } from 'lucide-react';

interface WidgetConfigModalProps {
 onClose: () => void;
 onSave: (widgetData: any) => void;
 initialData?: any;
}

export function WidgetConfigModal({ onClose, onSave, initialData }: WidgetConfigModalProps) {
 const [formData, setFormData] = useState({
 title: initialData?.title || '',
 type: initialData?.type || 'time_series',
 query: initialData?.config?.query || '',
 aggregation: initialData?.config?.aggregation || 'count',
 content: initialData?.config?.content || '' // for markdown
 });

 const handleSave = (e: React.FormEvent) => {
 e.preventDefault();
 onSave({
 id: initialData?.id || `w_${Date.now()}`,
 type: formData.type,
 title: formData.title,
 config: {
 query: formData.query,
 aggregation: formData.aggregation,
 content: formData.content
 }
 });
 };

 return (
 <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
 <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-xl shadow-2xl w-full max-w-lg flex flex-col">
 <div className="p-5 border-b border-[var(--border)] flex justify-between items-center">
 <h2 className="text-lg font-bold text-[var(--text-primary)]">{initialData ? 'Edit Widget' : 'Add Widget'}</h2>
 <button type="button" onClick={onClose} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors p-1 rounded-md hover:bg-[var(--bg-app)]">
 <X size={20} />
 </button>
 </div>
 
 <form onSubmit={handleSave} className="flex flex-col flex-1 overflow-hidden">
 <div className="p-6 overflow-y-auto space-y-5">
 <div>
 <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">Widget Title</label>
 <input 
 type="text" 
 required
 value={formData.title}
 onChange={e => setFormData({...formData, title: e.target.value})}
 placeholder="e.g. Total Errors"
 className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-all"
 />
 </div>
 
 <div>
 <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">Widget Type</label>
 <select 
 value={formData.type}
 onChange={e => setFormData({...formData, type: e.target.value})}
 className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-all appearance-none"
 >
 <option value="time_series">Time Series</option>
 <option value="metric_card">Metric Card</option>
 <option value="log_table">Log Table</option>
 <option value="pie_chart">Pie Chart</option>
 <option value="bar_chart">Bar Chart</option>
 <option value="markdown">Markdown Notes</option>
 </select>
 </div>

 {formData.type === 'markdown' ? (
 <div>
 <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">Markdown Content</label>
 <textarea 
 required
 value={formData.content}
 onChange={e => setFormData({...formData, content: e.target.value})}
 placeholder="# Notes\n\nAdd your dashboard notes here..."
 rows={5}
 className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded px-3 py-2 text-sm font-mono text-[var(--text-primary)] focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-all resize-none"
 />
 </div>
 ) : (
 <>
 <div>
 <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">Query</label>
 <input 
 type="text" 
 value={formData.query}
 onChange={e => setFormData({...formData, query: e.target.value})}
 placeholder="e.g. level:ERROR"
 className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded px-3 py-2 text-sm font-mono text-[var(--text-primary)] focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-all"
 />
 </div>
 {formData.type !== 'log_table' && (
 <div>
 <label className="block text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-1.5">Aggregation</label>
 <select 
 value={formData.aggregation}
 onChange={e => setFormData({...formData, aggregation: e.target.value})}
 className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded px-3 py-2 text-sm text-[var(--text-primary)] focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none transition-all appearance-none"
 >
 <option value="count">Count</option>
 <option value="avg">Average</option>
 <option value="sum">Sum</option>
 <option value="max">Max</option>
 </select>
 </div>
 )}
 </>
 )}
 </div>

 <div className="p-5 border-t border-[var(--border)] bg-[var(--bg-app)] flex justify-end gap-3 rounded-b-xl">
 <button 
 type="button" 
 onClick={onClose}
 className="px-4 py-2 text-sm font-medium text-[var(--text-primary)] border border-[var(--border)] hover:bg-[var(--bg-card-hover)] rounded transition-colors"
 >
 Cancel
 </button>
 <button 
 type="submit" 
 className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded transition-colors"
 >
 Save Widget
 </button>
 </div>
 </form>
 </div>
 </div>
 );
}
