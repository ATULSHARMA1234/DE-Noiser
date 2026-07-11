'use client';

import React, { createContext, useContext, useState, ReactNode, useEffect } from 'react';

export type TimePreset = '15m' | '1h' | '4h' | '1d' | '7d' | '30d' | 'custom';

export interface TimeRange {
 preset: TimePreset;
 from: number; // Unix timestamp ms
 to: number; // Unix timestamp ms
}

interface TimeRangeContextType {
 timeRange: TimeRange;
 setTimeRange: (range: TimeRange) => void;
 setTimePreset: (preset: TimePreset) => void;
}

const defaultTimeRange: TimeRange = {
 preset: '1h',
 from: Date.now() - 60 * 60 * 1000,
 to: Date.now(),
};

const TimeRangeContext = createContext<TimeRangeContextType | undefined>(undefined);

export function TimeRangeProvider({ children }: { children: ReactNode }) {
 const [timeRange, setTimeRange] = useState<TimeRange>(defaultTimeRange);

 const setTimePreset = (preset: TimePreset) => {
 if (preset === 'custom') return; // Cannot automatically set custom without exact times
 
 const now = Date.now();
 let from = now;
 
 switch (preset) {
 case '15m': from = now - 15 * 60 * 1000; break;
 case '1h': from = now - 60 * 60 * 1000; break;
 case '4h': from = now - 4 * 60 * 60 * 1000; break;
 case '1d': from = now - 24 * 60 * 60 * 1000; break;
 case '7d': from = now - 7 * 24 * 60 * 60 * 1000; break;
 case '30d': from = now - 30 * 24 * 60 * 60 * 1000; break;
 }
 
 setTimeRange({ preset, from, to: now });
 };

 // Periodically update the "to" time if we are on a rolling preset
 useEffect(() => {
 if (timeRange.preset === 'custom') return;

 const intervalId = setInterval(() => {
 setTimePreset(timeRange.preset);
 }, 60000); // Update every minute

 return () => clearInterval(intervalId);
 }, [timeRange.preset]);

 return (
 <TimeRangeContext.Provider value={{ timeRange, setTimeRange, setTimePreset }}>
 {children}
 </TimeRangeContext.Provider>
 );
}

export function useTimeRange() {
 const context = useContext(TimeRangeContext);
 if (context === undefined) {
 throw new Error('useTimeRange must be used within a TimeRangeProvider');
 }
 return context;
}
