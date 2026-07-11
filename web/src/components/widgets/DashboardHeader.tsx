import React from 'react';
import { Clock, RefreshCw } from 'lucide-react';

interface DashboardHeaderProps {
 globalTimeRange: string;
 setGlobalTimeRange: (val: string) => void;
 autoRefreshInterval: number;
 setAutoRefreshInterval: (val: number) => void;
 onRefresh: () => void;
 isEditing: boolean;
}

export function DashboardHeader({ 
 globalTimeRange, 
 setGlobalTimeRange, 
 autoRefreshInterval, 
 setAutoRefreshInterval,
 onRefresh,
 isEditing 
}: DashboardHeaderProps) {
 
 return (
 <div className="flex items-center gap-4 bg-[var(--bg-card)] border border-[var(--border)] px-4 py-2 rounded-lg mb-4">
 <div className="flex items-center gap-2 border-r border-[var(--border)] pr-4">
 <Clock size={16} className="text-[var(--text-secondary)]" />
 <select 
 value={globalTimeRange} 
 onChange={(e) => setGlobalTimeRange(e.target.value)}
 disabled={isEditing}
 className="bg-transparent text-sm font-medium text-[var(--text-primary)] outline-none cursor-pointer disabled:opacity-50"
 >
 <option value="15m">Past 15 Minutes</option>
 <option value="1h">Past 1 Hour</option>
 <option value="4h">Past 4 Hours</option>
 <option value="1d">Past 1 Day</option>
 <option value="7d">Past 1 Week</option>
 </select>
 </div>

 <div className="flex items-center gap-2">
 <RefreshCw size={16} className="text-[var(--text-secondary)]" />
 <select 
 value={autoRefreshInterval} 
 onChange={(e) => setAutoRefreshInterval(Number(e.target.value))}
 disabled={isEditing}
 className="bg-transparent text-sm font-medium text-[var(--text-primary)] outline-none cursor-pointer disabled:opacity-50"
 >
 <option value={0}>Off</option>
 <option value={10000}>10s</option>
 <option value={30000}>30s</option>
 <option value={60000}>1m</option>
 <option value={300000}>5m</option>
 </select>
 </div>

 <button 
 onClick={onRefresh}
 disabled={isEditing}
 className="ml-auto px-3 py-1 text-xs font-semibold bg-[var(--bg-surface-hover)] border border-[var(--border)] rounded text-[var(--text-primary)] hover:bg-[var(--border)] transition-colors disabled:opacity-50"
 >
 Refresh Now
 </button>
 </div>
 );
}
