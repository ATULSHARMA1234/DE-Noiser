'use client';

import React from 'react';
import Link from 'next/link';
import { ArrowRight, Network, Radio, ShieldCheck, Terminal } from 'lucide-react';

const PILLARS = [
  {
    icon: Network, k: 'Cluster', title: 'Semantic clustering',
    body: 'A hybrid Agglomerative + HDBSCAN pipeline collapses millions of raw lines into a handful of pattern templates — most of the noise is gone before a human ever looks.',
  },
  {
    icon: Terminal, k: 'Triage', title: 'Local root-cause',
    body: 'A causal proximity scorer ranks the patterns, and a local LLM writes the incident narrative and remediation hints. On your hardware; nothing leaves the building.',
  },
  {
    icon: Radio, k: 'Watch', title: 'Live nervous system',
    body: 'Tail live streams, track SLO error budgets, route alerts to Slack or PagerDuty, and auto-remediate with runbooks the moment drift appears.',
  },
];

const RAW = [
  '10:42:01 api-7f2  ERROR upstream timeout after 30000ms',
  '10:42:01 api-3a9  ERROR upstream timeout after 30000ms',
  '10:42:02 api-b21  ERROR upstream timeout after 30000ms',
  '10:42:02 worker-1 WARN  retry backoff 4s attempt 3',
  '10:42:03 api-c04  ERROR upstream timeout after 30000ms',
  '10:42:03 cache-2  INFO  evict 1204 keys ttl expired',
];

export default function MarketingHome() {
  return (
    <div className="min-h-screen bg-[var(--bg-app)] text-[var(--text-primary)] app-grid-bg">
      {/* Nav */}
      <nav className="flex items-center justify-between px-6 md:px-10 h-14 border-b border-[var(--border-subtle)] bg-[var(--bg-app)]/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="flex items-center gap-2.5">
          <span className="w-6 h-6 rounded-[3px] border border-[var(--primary-line)] bg-[var(--primary-dim)] flex items-center justify-center">
            <span className="mono text-[13px] font-bold text-[var(--primary)] leading-none">S</span>
          </span>
          <span className="text-[15px] font-semibold tracking-tight">SemanticOS</span>
        </div>
        <div className="hidden md:flex gap-7 text-[13px] text-[var(--text-secondary)]">
          <Link href="/product" className="hover:text-[var(--text-primary)] transition-colors">Product</Link>
          <Link href="/security" className="hover:text-[var(--text-primary)] transition-colors">Security</Link>
          <Link href="/docs" className="hover:text-[var(--text-primary)] transition-colors">Docs</Link>
        </div>
        <Link href="/app" className="inline-flex items-center gap-1.5 h-8 px-3.5 rounded-[3px] bg-[var(--primary)] text-black text-[13px] font-medium hover:bg-[var(--primary-hover)] transition-colors">
          Launch console <ArrowRight size={14} />
        </Link>
      </nav>

      {/* Hero — the thesis is the collapse itself, so show it. */}
      <main className="max-w-6xl mx-auto px-6 md:px-10 pt-16 md:pt-24 pb-20 grid lg:grid-cols-[1.05fr_1fr] gap-12 items-center">
        <div>
          <div className="inline-flex items-center gap-2 eyebrow mb-6">
            <span className="w-1.5 h-1.5 rounded-full bg-[var(--status-green)]" /> Private by default · 100% local
          </div>
          <h1 className="text-4xl md:text-6xl font-semibold tracking-tight leading-[1.05] mb-5" style={{ textWrap: 'balance' } as React.CSSProperties}>
            Turn a million log lines into a handful of causes.
          </h1>
          <p className="text-[15px] md:text-[16px] text-[var(--text-secondary)] leading-relaxed max-w-xl mb-8">
            SemanticOS clusters noisy log streams into pattern templates, scores them by causal proximity, and writes the root-cause narrative with a local LLM. No cloud upload. No per-gigabyte ingestion bill.
          </p>
          <div className="flex items-center gap-3">
            <Link href="/app" className="inline-flex items-center gap-2 h-10 px-5 rounded-[3px] bg-[var(--primary)] text-black text-[14px] font-medium hover:bg-[var(--primary-hover)] transition-colors">
              Launch Command Center <ArrowRight size={16} />
            </Link>
            <Link href="/docs" className="inline-flex items-center h-10 px-5 rounded-[3px] border border-[var(--border)] text-[14px] text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)] transition-colors">
              Read the docs
            </Link>
          </div>
          <div className="flex flex-wrap gap-x-8 gap-y-2 mt-10 pt-6 border-t border-[var(--border-subtle)]">
            {[['99%', 'noise reduced'], ['0', 'bytes to cloud'], ['<1min', 'analysis runs'], ['HDBSCAN', 'clustering core']].map(([n, l]) => (
              <div key={l}>
                <div className="text-[20px] font-semibold tnum text-[var(--text-primary)]">{n}</div>
                <div className="eyebrow mt-0.5">{l}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Denoise motif */}
        <div className="border border-[var(--border-subtle)] rounded-[5px] bg-[var(--bg-card)] overflow-hidden">
          <div className="flex items-center gap-2 px-3 h-8 border-b border-[var(--border-subtle)]">
            <span className="w-2.5 h-2.5 rounded-full bg-[var(--status-red)]/60" />
            <span className="w-2.5 h-2.5 rounded-full bg-[var(--status-yellow)]/60" />
            <span className="w-2.5 h-2.5 rounded-full bg-[var(--status-green)]/60" />
            <span className="eyebrow ml-2">semanticos · denoise</span>
          </div>
          <div className="p-3 grid gap-3">
            <div>
              <div className="eyebrow mb-1.5">Raw stream · 1,284,551 lines</div>
              <div className="bg-[var(--bg-inset)] rounded-[3px] p-2.5 mono text-[10.5px] leading-relaxed text-[var(--text-muted)] space-y-0.5 overflow-hidden">
                {RAW.map((l) => <div key={l} className="truncate">{l}</div>)}
              </div>
            </div>
            <div className="flex items-center gap-2 eyebrow text-[var(--primary)]">
              <ArrowRight size={12} /> cluster · score · triage
            </div>
            <div>
              <div className="eyebrow mb-1.5">3 patterns · 1 root cause</div>
              <div className="space-y-1.5">
                <ClusterRow tone="red" id="C1" label="upstream timeout after 30000ms" count="41,208" score="0.94" />
                <ClusterRow tone="yellow" id="C2" label="retry backoff attempt N" count="3,417" score="0.52" />
                <ClusterRow tone="blue" id="C3" label="cache evict keys ttl expired" count="912" score="0.11" />
              </div>
            </div>
          </div>
        </div>
      </main>

      {/* How it works */}
      <section className="border-t border-[var(--border-subtle)] bg-[var(--bg-app)]">
        <div className="max-w-6xl mx-auto px-6 md:px-10 py-20">
          <div className="eyebrow mb-2">How it works</div>
          <h2 className="text-2xl md:text-3xl font-semibold tracking-tight mb-12" style={{ textWrap: 'balance' } as React.CSSProperties}>
            Ingest, cluster, triage — end to end, on your infrastructure.
          </h2>
          <div className="grid md:grid-cols-3 gap-4">
            {PILLARS.map(({ icon: Icon, k, title, body }) => (
              <div key={k} className="border border-[var(--border-subtle)] rounded-[3px] bg-[var(--bg-card)] p-6 hover:border-[var(--border-hover)] transition-colors">
                <div className="flex items-center justify-between mb-5">
                  <span className="w-9 h-9 rounded-[3px] bg-[var(--primary-dim)] border border-[var(--primary-line)] flex items-center justify-center text-[var(--primary)]">
                    <Icon size={18} />
                  </span>
                  <span className="eyebrow">{k}</span>
                </div>
                <h3 className="text-[15px] font-medium text-[var(--text-primary)] mb-2">{title}</h3>
                <p className="text-[13px] text-[var(--text-secondary)] leading-relaxed">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="border-t border-[var(--border-subtle)]">
        <div className="max-w-6xl mx-auto px-6 md:px-10 py-16 flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
          <div>
            <h2 className="text-xl md:text-2xl font-semibold tracking-tight mb-1.5">Run it on your own logs.</h2>
            <p className="text-[14px] text-[var(--text-secondary)]">Point it at a file or a live cluster and watch the noise collapse.</p>
          </div>
          <Link href="/app" className="inline-flex items-center gap-2 h-10 px-5 rounded-[3px] bg-[var(--primary)] text-black text-[14px] font-medium hover:bg-[var(--primary-hover)] transition-colors shrink-0">
            <ShieldCheck size={16} /> Launch Command Center
          </Link>
        </div>
      </section>

      <footer className="border-t border-[var(--border-subtle)] py-8 text-center eyebrow">
        SemanticOS © 2026 · privacy-first log intelligence
      </footer>
    </div>
  );
}

function ClusterRow({ tone, id, label, count, score }: { tone: 'red' | 'yellow' | 'blue'; id: string; label: string; count: string; score: string }) {
  const color = `var(--status-${tone})`;
  return (
    <div className="flex items-center gap-2.5 bg-[var(--bg-inset)] rounded-[3px] px-2.5 py-2">
      <span className="mono text-[10px] px-1.5 h-5 flex items-center rounded-[2px]" style={{ color, background: 'var(--bg-surface-hover)' }}>{id}</span>
      <span className="mono text-[11px] text-[var(--text-secondary)] truncate flex-1">{label}</span>
      <span className="mono text-[10px] text-[var(--text-muted)] tnum">{count}</span>
      <span className="mono text-[10px] tnum" style={{ color }}>{score}</span>
    </div>
  );
}
