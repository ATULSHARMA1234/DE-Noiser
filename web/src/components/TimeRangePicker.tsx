'use client';

import React, { useState, useRef, useEffect } from 'react';
import { useTimeRange, TimePreset } from '@/context/TimeRangeContext';
import { Clock, Calendar, ChevronDown } from 'lucide-react';

const PRESETS: { value: TimePreset; label: string }[] = [
 { value: '15m', label: 'Last 15 minutes' },
 { value: '1h', label: 'Last 1 hour' },
 { value: '4h', label: 'Last 4 hours' },
 { value: '1d', label: 'Last 1 day' },
 { value: '7d', label: 'Last 7 days' },
 { value: '30d', label: 'Last 30 days' },
];

export function TimeRangePicker() {
 const { timeRange, setTimePreset, setTimeRange } = useTimeRange();
 const [isOpen, setIsOpen] = useState(false);
 const dropdownRef = useRef<HTMLDivElement>(null);

 // Close dropdown when clicking outside
 useEffect(() => {
 function handleClickOutside(event: MouseEvent) {
 if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
 setIsOpen(false);
 }
 }
 document.addEventListener('mousedown', handleClickOutside);
 return () => document.removeEventListener('mousedown', handleClickOutside);
 }, []);

 const currentLabel = timeRange.preset === 'custom' 
 ? 'Custom' 
 : PRESETS.find(p => p.value === timeRange.preset)?.label || 'Time Range';

 return (
 <div className="relative" ref={dropdownRef}>
 <button
 onClick={() => setIsOpen(!isOpen)}
 className="flex items-center gap-2 bg-[var(--bg-app)] border border-[var(--border)] hover:border-[var(--border-hover)] px-3 py-1.5 rounded-md text-sm text-[var(--text-primary)] transition-colors"
 >
 <Clock size={14} className="text-[var(--text-secondary)]" />
 <span className="font-medium">{currentLabel}</span>
 <ChevronDown size={14} className="text-[var(--text-secondary)]" />
 </button>

 {isOpen && (
 <div className="absolute right-0 top-full mt-2 w-56 bg-[var(--bg-modal)] border border-[var(--border)] rounded-md shadow-xl overflow-hidden z-50">
 <div className="p-2 border-b border-[var(--border)] text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wider">
 Presets
 </div>
 <div className="p-1">
 {PRESETS.map((preset) => (
 <button
 key={preset.value}
 onClick={() => {
 setTimePreset(preset.value);
 setIsOpen(false);
 }}
 className={`w-full text-left px-3 py-2 text-sm rounded transition-colors ${
 timeRange.preset === preset.value
 ? 'bg-blue-500/10 text-blue-400 font-medium'
 : 'text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)]'
 }`}
 >
 {preset.label}
 </button>
 ))}
 </div>
 {/* Custom range picker could be added here in the future */}
 <div className="p-2 border-t border-[var(--border)]">
 <button
 onClick={() => setIsOpen(false)}
 className="w-full flex items-center justify-center gap-2 text-left px-3 py-2 text-sm rounded text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)] transition-colors"
 >
 <Calendar size={14} /> Custom range...
 </button>
 </div>
 </div>
 )}
 </div>
 );
}
