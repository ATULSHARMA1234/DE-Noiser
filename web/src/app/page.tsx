'use client';

import React from 'react';
import Link from 'next/link';
import { Button, Card, Grid, Badge } from '@tremor/react';
import { Zap, Database, ArrowRight, BrainCircuit } from 'lucide-react';

export default function MarketingHome() {
 return (
 <div className="min-h-screen bg-[#020617] text-[#f8fafc] font-sans selection:bg-cyan-500/30 overflow-hidden">
 {/* Navbar */}
 <nav className="flex items-center justify-between p-6 px-12 border-b border-white/5 bg-slate-950/50 backdrop-blur-md sticky top-0 z-50">
 <div className="flex items-center gap-3">
 <div className="w-8 h-8 from-cyan-500 to-emerald-500 rounded-lg flex items-center justify-center">
 <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-white"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
 </div>
 <span className="font-black text-xl tracking-tighter">SemanticOS</span>
 </div>
 <div className="hidden md:flex gap-8 text-sm font-bold text-slate-400">
 <Link href="/product" className="hover:text-white transition-colors">Product</Link>
 <Link href="/security" className="hover:text-white transition-colors">Security & Privacy</Link>
 <Link href="/docs" className="hover:text-white transition-colors">Documentation</Link>
 </div>
 <Link href="/app">
 <Button className="bg-cyan-600 hover:bg-cyan-500 border-none font-bold">Launch Command Center</Button>
 </Link>
 </nav>

 {/* Hero Section */}
 <main className="max-w-6xl mx-auto px-6 pt-32 pb-24 text-center relative">
 <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-cyan-500/20 blur-[120px] rounded-full pointer-events-none"></div>
 <Badge color="emerald" className="mb-8 font-black uppercase tracking-widest text-[10px]">Private by Default • 100% Local</Badge>
 <h1 className="text-6xl md:text-8xl font-black tracking-tighter leading-[1.1] mb-8">
 The Autonomous <br/><span className="text-transparent bg-clip-text from-cyan-400 to-emerald-400">SRE Copilot</span>
 </h1>
 <p className="text-xl text-slate-400 max-w-3xl mx-auto mb-12 font-medium leading-relaxed">
 Collapse millions of logs into a handful of behaviors. Diagnose incidents locally with Llama 3.3. No cloud upload. No ingestion bill.
 </p>
 <div className="flex items-center justify-center gap-6">
 <Link href="/app">
 <Button size="xl" className="bg-cyan-600 hover:bg-cyan-500 border-none font-black text-lg px-8 py-4 shadow-[0_0_40px_rgba(6,182,212,0.4)]" icon={ArrowRight} iconPosition="right">
 Launch Command Center
 </Button>
 </Link>
 <Button size="xl" variant="secondary" className="bg-white/5 hover:bg-white/10 border-white/10 text-white font-bold text-lg px-8 py-4">
 Read the Docs
 </Button>
 </div>
 </main>

 {/* Pillars Section */}
 <section className="bg-slate-950/50 py-32 border-t border-white/5 relative z-10">
 <div className="max-w-7xl mx-auto px-6">
 <div className="text-center mb-20">
 <h2 className="text-4xl font-black tracking-tighter mb-4">The Three Enterprise Pillars</h2>
 <p className="text-slate-400 text-lg">Built from the ground up for massive scale and absolute privacy.</p>
 </div>
 <Grid numItemsMd={3} className="gap-8">
 <Card className="bg-slate-900/40 border border-white/5 rounded-3xl p-10 hover:border-cyan-500/30 transition-colors">
 <div className="w-14 h-14 bg-cyan-500/10 rounded-2xl flex items-center justify-center text-cyan-400 mb-8 border border-cyan-500/20"><BrainCircuit size={28} /></div>
 <h3 className="text-2xl font-black text-white mb-4">Neural Engine</h3>
 <p className="text-slate-400 leading-relaxed">Polars burst ingestion, semantic vectors, HDBSCAN noise annihilation, and intelligent sampling for sub-minute runs.</p>
 </Card>
 <Card className="bg-slate-900/40 border border-white/5 rounded-3xl p-10 hover:border-emerald-500/30 transition-colors">
 <div className="w-14 h-14 bg-emerald-500/10 rounded-2xl flex items-center justify-center text-emerald-400 mb-8 border border-emerald-500/20"><Zap size={28} /></div>
 <h3 className="text-2xl font-black text-white mb-4">Nervous System</h3>
 <p className="text-slate-400 leading-relaxed">Agent mode tails live streams with multi-source extensibility. Watch your infrastructure health via the live pulse feed.</p>
 </Card>
 <Card className="bg-slate-900/40 border border-white/5 rounded-3xl p-10 hover:border-blue-500/30 transition-colors">
 <div className="w-14 h-14 bg-blue-500/10 rounded-2xl flex items-center justify-center text-blue-400 mb-8 border border-blue-500/20"><Database size={28} /></div>
 <h3 className="text-2xl font-black text-white mb-4">Persistent Memory</h3>
 <p className="text-slate-400 leading-relaxed">Every incident is permanently recorded. Search historical snapshots, track recurrences, and build an organizational memory bank.</p>
 </Card>
 </Grid>
 </div>
 </section>
 
 {/* Footer */}
 <footer className="border-t border-white/5 py-12 text-center text-slate-500 text-sm font-bold uppercase tracking-widest">
 SemanticOS © 2026 • Built for the Privacy-First Enterprise
 </footer>
 </div>
 );
}
