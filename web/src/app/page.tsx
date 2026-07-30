'use client';

/* ═══════════════════════════════════════════════════════════════════════════
   Landing / profile — "the engineer, rendered as telemetry".

   The product this site fronts is an observability platform, so the profile
   borrows that vocabulary rather than the usual portfolio one: the hero is a
   boot log, competence is a set of signal meters, projects are services with
   metrics attached, and the timeline is an event stream. Same Signal theme as
   the console (near-black, one amber accent) so the two never look like two
   different products.
   ═══════════════════════════════════════════════════════════════════════════ */

import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import {
  ArrowRight, ArrowUpRight, Award, BadgeCheck, Boxes, BrainCircuit, Cpu,
  GitBranch, GraduationCap, Leaf, Mail, MapPin, Network, Phone, Radio,
  ShieldCheck, Sparkles, Terminal, Users, Workflow,
} from 'lucide-react';

/* ── Data ─────────────────────────────────────────────────────────────────── */

const ROLES = [
  'AIOps & observability systems',
  'ML model compression',
  'event-driven platform backends',
  'solution architecture',
];

const BOOT: { t: string; lvl: 'ok' | 'info' | 'warn'; msg: string }[] = [
  { t: '00.004', lvl: 'info', msg: 'profile: atul sharma · b.tech cse · srm ist ghaziabad' },
  { t: '00.011', lvl: 'ok', msg: 'education   cgpa 9.20/10 · class xii 90% · class x 96.5%' },
  { t: '00.019', lvl: 'info', msg: 'loading services [semanticos, greenformer, radiance]' },
  { t: '00.031', lvl: 'ok', msg: 'semanticos   1,284,551 lines → 3 patterns · 1 root cause' },
  { t: '00.044', lvl: 'ok', msg: 'greenformer  int8 + distill → 6.8× inference energy cut' },
  { t: '00.052', lvl: 'ok', msg: 'radiance     agentic sql copilot · react-flow canvas' },
  { t: '00.061', lvl: 'warn', msg: 'certs        aws data engineer · aws ai practitioner' },
  { t: '00.066', lvl: 'info', msg: 'source       github.com/ATULSHARMA1234' },
  { t: '00.070', lvl: 'ok', msg: 'all systems nominal — open to 2027 grad roles' },
];

const METERS = [
  { label: 'AI / ML systems', value: 92, note: 'transformers, compression, RAG-adjacent retrieval' },
  { label: 'Backend & distributed', value: 88, note: 'FastAPI, Redpanda, workers, event pipelines' },
  { label: 'Data & observability', value: 90, note: 'ClickHouse, Postgres, LanceDB, SLOs' },
  { label: 'Frontend engineering', value: 82, note: 'Next.js, React, TypeScript, Tailwind' },
  { label: 'Cloud & delivery', value: 78, note: 'Docker, Kubernetes, AWS, CI hygiene' },
];

const STACK: { group: string; icon: React.ElementType; items: string[] }[] = [
  {
    group: 'Languages', icon: Terminal,
    items: ['Python', 'TypeScript', 'Java', 'C++', 'C', 'SQL', 'HTML', 'CSS'],
  },
  {
    group: 'AI / ML', icon: BrainCircuit,
    items: ['PyTorch', 'HuggingFace', 'Sentence Transformers', 'ONNX Runtime', 'scikit-learn', 'HDBSCAN', 'CodeCarbon'],
  },
  {
    group: 'Backend & data', icon: Boxes,
    items: ['FastAPI', 'Node.js', 'Express', 'Flask', 'PostgreSQL', 'ClickHouse', 'Redis', 'Redpanda', 'LanceDB', 'MongoDB', 'Prisma'],
  },
  {
    group: 'Frontend', icon: Sparkles,
    items: ['Next.js', 'React', 'Tailwind CSS', 'React Flow', 'Socket.IO', 'Recharts'],
  },
  {
    group: 'Platform', icon: Cpu,
    items: ['Docker', 'Kubernetes', 'AWS', 'Git', 'REST APIs', 'TensorBoard'],
  },
];

const BUILDS = [
  {
    id: 'svc-01',
    name: 'SemanticOS',
    tag: 'AIOps & Observability',
    icon: Radio,
    tone: 'var(--primary)',
    href: '/app',
    hrefLabel: 'Launch console',
    repo: 'https://github.com/ATULSHARMA1234/DE-Noiser',
    repoLabel: 'DE-Noiser',
    blurb:
      'An enterprise-grade log intelligence platform that collapses noisy streams into pattern templates, scores them by causal proximity, and has a local LLM write the incident narrative. Nothing leaves the building.',
    bullets: [
      'Hybrid Agglomerative + HDBSCAN clustering with custom neural sampling — massive streams, no memory blowups.',
      'Anomaly detection learns the baseline and routes only high-risk flags to the LLM for structured JSON forensics.',
      'Sentence-Transformer embeddings behind a hash cache: repeat analysis runs up to 98% faster.',
    ],
    metrics: [['99%', 'noise cut'], ['98%', 'faster reruns'], ['0', 'bytes to cloud']],
    stack: ['Python', 'FastAPI', 'Next.js', 'ClickHouse', 'Redpanda', 'LanceDB', 'Kubernetes'],
  },
  {
    id: 'svc-02',
    name: 'GreenFormer',
    tag: 'Sustainable AI',
    icon: Leaf,
    tone: 'var(--status-green)',
    href: null,
    hrefLabel: null,
    repo: 'https://github.com/ATULSHARMA1234/GreenFormer',
    repoLabel: 'GreenFormer',
    blurb:
      'A compression pipeline that puts a carbon number on inference. Knowledge distillation, structured pruning and INT8 quantization, benchmarked across two hardware profiles with live CO₂ tracking.',
    bullets: [
      'Distillation → pruning → INT8 quantization chain, measured end to end rather than estimated.',
      'Profiled on dual hardware targets to separate real speedups from kernel-level noise.',
      'Real-time emissions tracked via CodeCarbon and surfaced as a business-facing ROI case.',
    ],
    metrics: [['6.8×', 'energy cut'], ['2', 'hw profiles'], ['INT8', 'quantized']],
    stack: ['PyTorch', 'HuggingFace', 'ONNX Runtime', 'CodeCarbon', 'Streamlit', 'TensorBoard'],
  },
  {
    id: 'svc-03',
    name: 'RADIANCE',
    tag: 'AI-Native B2B SaaS',
    icon: Workflow,
    tone: 'var(--status-blue)',
    href: null,
    hrefLabel: null,
    repo: 'https://github.com/ATULSHARMA1234/RE-ACT',
    repoLabel: 'RE-ACT',
    blurb:
      'A full-stack CRM where the automation canvas is the product: drag-and-drop process mapping, an agentic copilot that writes and safely executes SQL, and event-driven delivery tracking underneath.',
    bullets: [
      'Agentic copilot (LLaMA 3.3 / Gemini) generates and sandboxes dynamic SQL for live stakeholder reporting.',
      'React Flow canvas serializes node/edge routing graphs into executable enterprise workflows.',
      'Decoupled microservices with state machines and webhook retries for delivery tracking over Socket.IO.',
    ],
    metrics: [['Agentic', 'SQL copilot'], ['Realtime', 'Socket.IO'], ['Visual', 'workflow DAGs']],
    stack: ['Next.js 14', 'TypeScript', 'PostgreSQL', 'Prisma', 'Express', 'Socket.IO', 'React Flow'],
  },
];

const STREAM = [
  {
    when: 'Jul 2025 — present',
    title: 'Core Member, Computer Society of India',
    kind: 'experience' as const,
    lines: [
      'Ran large-scale technical hackathons end to end — planning, logistics, execution.',
      'Coordinated cross-functional teams on resource allocation and timelines.',
    ],
  },
  {
    when: 'ongoing',
    title: 'Core Member, IETE',
    kind: 'experience' as const,
    lines: [
      'Technical seminars, workshops and peer-driven initiatives on emerging tech.',
      'Community projects and knowledge-sharing with fellow members.',
    ],
  },
  {
    when: 'Aug 2025',
    title: 'Volunteer, Gift a Smile Foundation',
    kind: 'community' as const,
    lines: ['Community development drive.'],
  },
  {
    when: '2023 — 2027',
    title: 'B.Tech CSE, SRM IST Ghaziabad',
    kind: 'education' as const,
    lines: ['CGPA 9.20 / 10.'],
  },
  {
    when: '2021 — 2023',
    title: 'St Thomas School, Khatauli',
    kind: 'education' as const,
    lines: ['ISC Class XII — 90%.  ICSE Class X — 96.5%.'],
  },
];

const CERTS = [
  { name: 'AWS Certified Data Engineer — Associate', by: 'Amazon Web Services', when: '' },
  { name: 'AWS Certified AI Practitioner', by: 'Amazon Web Services', when: 'Apr 2026 — Apr 2029' },
  { name: 'Natural Language Processing — IIT Kharagpur', by: 'NPTEL', when: 'May 2026' },
  { name: 'Big Data & Data Science Bootcamp', by: 'C-DAC Noida', when: 'Jan 2025' },
  { name: 'IoT and Embedded Systems', by: 'Coursera', when: 'Apr 2025' },
  { name: 'Introduction to Artificial Intelligence', by: 'Simplilearn', when: 'Apr 2025' },
  { name: 'Java Basics', by: 'HackerRank', when: 'Oct 2024' },
  { name: 'Python Basics', by: 'HackerRank', when: '' },
];

const EMAIL = 'as1211@srmist.edu.in';
const PHONE = '9027095463';
const GITHUB = 'https://github.com/ATULSHARMA1234';

/** lucide dropped its brand icons in v1, so the GitHub mark lives here. */
function GithubMark({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
    </svg>
  );
}

/* ── Hooks ────────────────────────────────────────────────────────────────── */

/** Adds `in-view` once the node has been scrolled to, and never removes it. */
function useInView<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    const io = new IntersectionObserver(
      ([e]) => { if (e.isIntersecting) { setSeen(true); io.disconnect(); } },
      { rootMargin: '-40px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [seen]);
  return [ref, seen] as const;
}

/** Cycles ROLES with a type-on / delete-off cadence. */
function useTypedRole() {
  const [i, setI] = useState(0);
  const [n, setN] = useState(0);
  const [erasing, setErasing] = useState(false);

  useEffect(() => {
    const word = ROLES[i];
    if (!erasing && n === word.length) {
      const hold = setTimeout(() => setErasing(true), 1900);
      return () => clearTimeout(hold);
    }
    if (erasing && n === 0) {
      setErasing(false);
      setI((p) => (p + 1) % ROLES.length);
      return;
    }
    const step = setTimeout(() => setN((p) => p + (erasing ? -1 : 1)), erasing ? 28 : 58);
    return () => clearTimeout(step);
  }, [i, n, erasing]);

  return ROLES[i].slice(0, n);
}

/* ── Page ─────────────────────────────────────────────────────────────────── */

export default function ProfileLanding() {
  const typed = useTypedRole();

  return (
    <div className="min-h-screen bg-[var(--bg-app)] text-[var(--text-primary)]">
      <Nav />

      {/* ── Hero ───────────────────────────────────────────────────────── */}
      <header className="relative overflow-hidden border-b border-[var(--border-subtle)]">
        <div className="absolute inset-0 hero-wash pointer-events-none" />
        <div className="absolute inset-0 app-grid-bg opacity-60 pointer-events-none" />

        <div className="relative max-w-6xl mx-auto px-6 md:px-10 pt-16 md:pt-24 pb-16 grid lg:grid-cols-[1.02fr_1fr] gap-12 lg:gap-14 items-center">
          <div>
            <div className="inline-flex items-center gap-2 eyebrow mb-7">
              <span className="w-1.5 h-1.5 rounded-full bg-[var(--status-green)]" />
              available · 2027 graduate · open to internships
            </div>

            <h1 className="text-[42px] md:text-[68px] font-semibold tracking-[-0.035em] leading-[0.98] mb-4">
              Atul Sharma
            </h1>

            <div className="mono text-[14px] md:text-[16px] text-[var(--primary)] h-6 mb-6">
              <span className="text-[var(--text-muted)]">$ whoami --focus </span>
              {typed}
              <span className="caret ml-0.5" />
            </div>

            <p className="text-[15px] md:text-[16px] text-[var(--text-secondary)] leading-relaxed max-w-xl mb-8">
              CSE undergrad who builds the unglamorous middle of AI systems — the clustering
              pipelines, the quantized models, the event-driven backends that hold a platform up
              once the demo is over. Most recently: <span className="text-[var(--text-primary)]">SemanticOS</span>,
              an AIOps platform that turns a million log lines into a handful of causes.
            </p>

            <div className="flex flex-wrap items-center gap-3">
              <Link
                href="/app"
                className="inline-flex items-center gap-2 h-10 px-5 rounded-[3px] bg-[var(--primary)] text-black text-[14px] font-medium hover:bg-[var(--primary-hover)] transition-colors"
              >
                Launch Command Center <ArrowRight size={16} />
              </Link>
              <a
                href={GITHUB}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center gap-2 h-10 px-5 rounded-[3px] border border-[var(--border)] text-[14px] hover:bg-[var(--bg-surface-hover)] hover:border-[var(--border-hover)] transition-colors"
              >
                <GithubMark size={15} /> ATULSHARMA1234
              </a>
              <a
                href={`mailto:${EMAIL}`}
                className="inline-flex items-center gap-2 h-10 px-5 rounded-[3px] border border-[var(--border)] text-[14px] hover:bg-[var(--bg-surface-hover)] hover:border-[var(--border-hover)] transition-colors"
              >
                <Mail size={15} /> Get in touch
              </a>
            </div>

            <div className="flex flex-wrap gap-x-6 gap-y-2 mt-9 pt-6 border-t border-[var(--border-subtle)] eyebrow">
              <span className="inline-flex items-center gap-1.5"><GraduationCap size={12} /> SRM IST Ghaziabad · CGPA 9.20</span>
              <span className="inline-flex items-center gap-1.5"><MapPin size={12} /> Muzaffarnagar, UP</span>
              <span className="inline-flex items-center gap-1.5"><Award size={12} /> AWS ×2 certified</span>
            </div>
          </div>

          <BootPanel />
        </div>
      </header>

      {/* ── Headline numbers ───────────────────────────────────────────── */}
      <Numbers />

      {/* ── Signal meters ──────────────────────────────────────────────── */}
      <Section id="signal" eyebrow="Signal strength" title="Where the depth actually is.">
        <div className="grid lg:grid-cols-[1.1fr_1fr] gap-10">
          <div className="space-y-5">
            {METERS.map((m, i) => <Meter key={m.label} {...m} delay={i * 90} />)}
          </div>
          <div className="border border-[var(--border-subtle)] rounded-[5px] bg-[var(--bg-card)] p-6 self-start">
            <div className="eyebrow mb-4">Also on the résumé</div>
            <dl className="space-y-3.5 text-[13px]">
              {[
                ['Technical', 'OOPS · model compression · ML deployment · solution architecture · process mapping'],
                ['Analysis', 'NumPy · Pandas · data visualization · design thinking'],
                ['Languages', 'English (professional) · Hindi (native)'],
              ].map(([k, v]) => (
                <div key={k} className="grid grid-cols-[88px_1fr] gap-3">
                  <dt className="eyebrow pt-0.5">{k}</dt>
                  <dd className="text-[var(--text-secondary)] leading-relaxed">{v}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </Section>

      {/* ── Stack ──────────────────────────────────────────────────────── */}
      <Section id="stack" eyebrow="Toolchain" title="The stack, grouped by what it's for." bordered>
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {STACK.map(({ group, icon: Icon, items }, gi) => (
            <Reveal key={group} delay={gi * 70}>
              <div className="h-full border border-[var(--border-subtle)] rounded-[3px] bg-[var(--bg-card)] p-5 hover:border-[var(--border-hover)] transition-colors">
                <div className="flex items-center gap-2 mb-4">
                  <span className="w-7 h-7 rounded-[3px] bg-[var(--primary-dim)] border border-[var(--primary-line)] flex items-center justify-center text-[var(--primary)]">
                    <Icon size={14} />
                  </span>
                  <span className="text-[13px] font-medium">{group}</span>
                  <span className="eyebrow ml-auto tnum">{items.length}</span>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {items.map((it) => (
                    <span
                      key={it}
                      className="mono text-[10.5px] px-2 py-1 rounded-[2px] bg-[var(--bg-inset)] border border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--primary)] hover:border-[var(--primary-line)] transition-colors"
                    >
                      {it}
                    </span>
                  ))}
                </div>
              </div>
            </Reveal>
          ))}
        </div>
      </Section>

      {/* ── Builds ─────────────────────────────────────────────────────── */}
      <Section
        id="builds"
        eyebrow="Deployed services"
        title="Three builds, each solving a different failure of scale."
        bordered
      >
        <div className="space-y-4">
          {BUILDS.map((b, i) => <BuildCard key={b.id} {...b} index={i} />)}
        </div>
      </Section>

      {/* ── Event stream ───────────────────────────────────────────────── */}
      <Section id="stream" eyebrow="Event stream" title="Experience, community, education." bordered>
        <div className="grid lg:grid-cols-[1.3fr_1fr] gap-10">
          <ol className="relative border-l border-[var(--border-subtle)] pl-6 space-y-7">
            {STREAM.map((e, i) => <StreamRow key={e.title} {...e} delay={i * 70} />)}
          </ol>

          <div>
            <div className="eyebrow mb-4 flex items-center gap-1.5"><BadgeCheck size={12} /> Certifications</div>
            <div className="grid sm:grid-cols-2 lg:grid-cols-1 gap-2">
              {CERTS.map((c, i) => (
                <Reveal key={c.name} delay={i * 45}>
                  <div className="flex items-start gap-3 border border-[var(--border-subtle)] rounded-[3px] bg-[var(--bg-card)] px-3.5 py-3 hover:border-[var(--border-hover)] transition-colors">
                    <span className="mt-0.5 text-[var(--primary)] shrink-0"><Award size={14} /></span>
                    <div className="min-w-0">
                      <div className="text-[12.5px] leading-snug">{c.name}</div>
                      <div className="eyebrow mt-1">{c.by}{c.when && ` · ${c.when}`}</div>
                    </div>
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
        </div>
      </Section>

      {/* ── CTA ────────────────────────────────────────────────────────── */}
      <section id="contact" className="border-t border-[var(--border-subtle)] relative overflow-hidden">
        <div className="absolute inset-0 app-grid-bg opacity-50 pointer-events-none" />
        <div className="relative max-w-6xl mx-auto px-6 md:px-10 py-20">
          <div className="eyebrow mb-3">Open channel</div>
          <h2 className="text-2xl md:text-4xl font-semibold tracking-tight mb-4 max-w-2xl leading-[1.1]">
            Hiring for AI platform, ML systems, or backend? Let&apos;s talk.
          </h2>
          <p className="text-[14px] text-[var(--text-secondary)] max-w-lg mb-8">
            Graduating 2027. Happiest where models meet infrastructure — and where somebody has to
            make the thing actually stay up.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <a
              href={`mailto:${EMAIL}`}
              className="inline-flex items-center gap-2 h-10 px-5 rounded-[3px] bg-[var(--primary)] text-black text-[14px] font-medium hover:bg-[var(--primary-hover)] transition-colors"
            >
              <Mail size={15} /> {EMAIL}
            </a>
            <a
              href={`tel:${PHONE}`}
              className="inline-flex items-center gap-2 h-10 px-5 rounded-[3px] border border-[var(--border)] text-[14px] mono hover:bg-[var(--bg-surface-hover)] hover:border-[var(--border-hover)] transition-colors"
            >
              <Phone size={15} /> {PHONE}
            </a>
            <a
              href={GITHUB}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-2 h-10 px-5 rounded-[3px] border border-[var(--border)] text-[14px] mono hover:bg-[var(--bg-surface-hover)] hover:border-[var(--border-hover)] transition-colors"
            >
              <GithubMark size={15} /> ATULSHARMA1234
            </a>
            <Link
              href="/app"
              className="inline-flex items-center gap-2 h-10 px-5 rounded-[3px] border border-[var(--border)] text-[14px] hover:bg-[var(--bg-surface-hover)] hover:border-[var(--border-hover)] transition-colors"
            >
              <ShieldCheck size={15} /> See SemanticOS live
            </Link>
          </div>
        </div>
      </section>

      <footer className="border-t border-[var(--border-subtle)] py-8 px-6 md:px-10">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 eyebrow">
          <span>Atul Sharma © 2026 · built on SemanticOS</span>
          <a
            href={GITHUB}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-1.5 hover:text-[var(--primary)] transition-colors"
          >
            <GithubMark size={11} /> ATULSHARMA1234
          </a>
          <span className="inline-flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-[var(--status-green)]" /> all systems nominal
          </span>
        </div>
      </footer>
    </div>
  );
}

/* ── Chrome ───────────────────────────────────────────────────────────────── */

function Nav() {
  const links = [
    ['Signal', '#signal'],
    ['Stack', '#stack'],
    ['Builds', '#builds'],
    ['Contact', '#contact'],
  ];
  return (
    <nav className="flex items-center justify-between px-6 md:px-10 h-14 border-b border-[var(--border-subtle)] bg-[var(--bg-app)]/85 backdrop-blur-sm sticky top-0 z-50">
      <div className="flex items-center gap-2.5">
        <span className="w-6 h-6 rounded-[3px] border border-[var(--primary-line)] bg-[var(--primary-dim)] flex items-center justify-center">
          <span className="mono text-[12px] font-bold text-[var(--primary)] leading-none">AS</span>
        </span>
        <span className="text-[15px] font-semibold tracking-tight">Atul Sharma</span>
        <span className="hidden sm:inline eyebrow ml-1.5 pl-2.5 border-l border-[var(--border-subtle)]">CSE · 2027</span>
      </div>
      <div className="hidden md:flex gap-7 text-[13px] text-[var(--text-secondary)]">
        {links.map(([label, href]) => (
          <a key={href} href={href} className="hover:text-[var(--text-primary)] transition-colors">{label}</a>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <a
          href={GITHUB}
          target="_blank"
          rel="noreferrer noopener"
          aria-label="GitHub profile"
          className="w-8 h-8 rounded-[3px] border border-[var(--border)] flex items-center justify-center text-[var(--text-secondary)] hover:text-[var(--primary)] hover:border-[var(--primary-line)] transition-colors"
        >
          <GithubMark size={15} />
        </a>
        <Link
          href="/app"
          className="inline-flex items-center gap-1.5 h-8 px-3.5 rounded-[3px] bg-[var(--primary)] text-black text-[13px] font-medium hover:bg-[var(--primary-hover)] transition-colors"
        >
          Launch console <ArrowRight size={14} />
        </Link>
      </div>
    </nav>
  );
}

/** The hero's right panel: a boot log that streams in line by line. */
function BootPanel() {
  const [shown, setShown] = useState(0);

  useEffect(() => {
    if (shown >= BOOT.length) return;
    const t = setTimeout(() => setShown((n) => n + 1), shown === 0 ? 260 : 340);
    return () => clearTimeout(t);
  }, [shown]);

  const toneOf = (l: 'ok' | 'info' | 'warn') =>
    l === 'ok' ? 'var(--signal-ok)' : l === 'warn' ? 'var(--signal-warn)' : 'var(--signal-info)';

  return (
    <div className="border border-[var(--border-subtle)] rounded-[5px] bg-[var(--bg-card)] overflow-hidden shadow-[0_0_0_1px_rgba(0,0,0,0.3)]">
      <div className="flex items-center gap-2 px-3 h-8 border-b border-[var(--border-subtle)]">
        <span className="w-2.5 h-2.5 rounded-full bg-[var(--status-red)]/60" />
        <span className="w-2.5 h-2.5 rounded-full bg-[var(--status-yellow)]/60" />
        <span className="w-2.5 h-2.5 rounded-full bg-[var(--status-green)]/60" />
        <span className="eyebrow ml-2">atul@semanticos — boot</span>
        <span className="eyebrow ml-auto tnum">{shown}/{BOOT.length}</span>
      </div>

      <div className="p-3.5 mono text-[10.5px] leading-[1.75] min-h-[268px]">
        {BOOT.slice(0, shown).map((l) => (
          <div key={l.t} className="flex gap-2.5 items-baseline reveal in-view">
            <span className="tnum text-[var(--text-dimmed)] shrink-0">[{l.t}]</span>
            <span className="shrink-0 w-9 uppercase" style={{ color: toneOf(l.lvl) }}>{l.lvl}</span>
            <span className="text-[var(--text-secondary)] break-words min-w-0">{l.msg}</span>
          </div>
        ))}
        {shown >= BOOT.length && (
          <div className="flex gap-2.5 items-baseline mt-1">
            <span className="text-[var(--primary)]">›</span>
            <span className="caret" />
          </div>
        )}
      </div>

      <div className="border-t border-[var(--border-subtle)] px-3.5 py-2.5 flex items-center gap-4 eyebrow">
        <span className="inline-flex items-center gap-1.5"><Network size={11} /> 3 services</span>
        <span className="inline-flex items-center gap-1.5"><GitBranch size={11} /> 8 certifications</span>
        <span className="inline-flex items-center gap-1.5 ml-auto text-[var(--status-green)]">
          <span className="w-1.5 h-1.5 rounded-full bg-[var(--status-green)]" /> healthy
        </span>
      </div>
    </div>
  );
}

function Numbers() {
  const STATS: [string, string, string][] = [
    ['9.20', '/ 10 CGPA', 'B.Tech CSE, 2027'],
    ['98', '% faster', 'cached embedding reruns'],
    ['6.8', '× less energy', 'quantized inference'],
    ['1.2', 'M lines', 'collapsed to 3 patterns'],
  ];
  return (
    <section className="border-b border-[var(--border-subtle)] bg-[var(--bg-card)]">
      <div className="max-w-6xl mx-auto px-6 md:px-10 grid grid-cols-2 md:grid-cols-4 divide-x divide-[var(--border-subtle)]">
        {STATS.map(([n, unit, label], i) => (
          <Reveal key={label} delay={i * 80}>
            <div className={`py-7 ${i === 0 ? 'pr-5' : 'px-5'}`}>
              <div className="flex items-baseline gap-1.5">
                <span className="text-[28px] md:text-[34px] font-semibold tnum tracking-tight leading-none">{n}</span>
                <span className="mono text-[11px] text-[var(--primary)]">{unit}</span>
              </div>
              <div className="eyebrow mt-2.5">{label}</div>
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

function Section({
  id, eyebrow, title, children, bordered,
}: {
  id: string; eyebrow: string; title: string; children: React.ReactNode; bordered?: boolean;
}) {
  return (
    <section id={id} className={`scroll-mt-14 ${bordered ? 'border-t border-[var(--border-subtle)]' : ''}`}>
      <div className="max-w-6xl mx-auto px-6 md:px-10 py-18 md:py-20">
        <div className="eyebrow mb-2.5">{eyebrow}</div>
        <h2 className="text-2xl md:text-[32px] font-semibold tracking-tight leading-[1.15] mb-11 max-w-2xl">
          {title}
        </h2>
        {children}
      </div>
    </section>
  );
}

/* ── Pieces ───────────────────────────────────────────────────────────────── */

function Reveal({ children, delay = 0 }: { children: React.ReactNode; delay?: number }) {
  const [ref, seen] = useInView<HTMLDivElement>();
  return (
    <div ref={ref} className={`reveal${seen ? ' in-view' : ''} h-full`} style={{ animationDelay: `${delay}ms` }}>
      {children}
    </div>
  );
}

function Meter({ label, value, note, delay }: { label: string; value: number; note: string; delay: number }) {
  const [ref, seen] = useInView<HTMLDivElement>();
  return (
    <div ref={ref}>
      <div className="flex items-baseline justify-between mb-2">
        <span className="text-[13.5px]">{label}</span>
        <span className="mono text-[11px] tnum text-[var(--primary)]">{seen ? value : 0}</span>
      </div>
      <div className="h-[3px] rounded-[2px] bg-[var(--bg-track)] overflow-hidden">
        <div
          className="h-full bg-[var(--primary)]"
          style={{
            width: seen ? `${value}%` : '0%',
            transition: `width 1.1s cubic-bezier(0.22, 1, 0.36, 1) ${delay}ms`,
          }}
        />
      </div>
      <div className="eyebrow mt-2">{note}</div>
    </div>
  );
}

function BuildCard({
  id, name, tag, icon: Icon, tone, href, hrefLabel, repo, repoLabel, blurb, bullets, metrics,
  stack, index,
}: (typeof BUILDS)[number] & { index: number }) {
  return (
    <Reveal delay={index * 90}>
      <article className="group border border-[var(--border-subtle)] rounded-[4px] bg-[var(--bg-card)] hover:border-[var(--border-hover)] transition-colors overflow-hidden">
        <div className="flex items-center gap-3 px-5 h-11 border-b border-[var(--border-subtle)] bg-[var(--bg-inset)]">
          <span className="mono text-[10px] text-[var(--text-dimmed)] tnum">{id}</span>
          <span
            className="w-6 h-6 rounded-[3px] flex items-center justify-center shrink-0"
            style={{ color: tone, background: 'var(--bg-surface-hover)' }}
          >
            <Icon size={13} />
          </span>
          <span className="text-[14px] font-medium">{name}</span>
          <span className="eyebrow" style={{ color: tone }}>{tag}</span>

          <div className="ml-auto flex items-center gap-4 shrink-0">
            <a
              href={repo}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-1.5 mono text-[10.5px] text-[var(--text-muted)] hover:text-[var(--primary)] transition-colors"
            >
              <GithubMark size={12} /> <span className="hidden sm:inline">{repoLabel}</span>
            </a>
            {href && (
              <Link
                href={href}
                className="inline-flex items-center gap-1 mono text-[10.5px] text-[var(--text-muted)] hover:text-[var(--primary)] transition-colors"
              >
                {hrefLabel} <ArrowUpRight size={12} />
              </Link>
            )}
          </div>
        </div>

        <div className="grid lg:grid-cols-[1.55fr_1fr]">
          <div className="p-5 lg:border-r border-[var(--border-subtle)]">
            <p className="text-[13.5px] text-[var(--text-secondary)] leading-relaxed mb-4">{blurb}</p>
            <ul className="space-y-2 mb-5">
              {bullets.map((b) => (
                <li key={b} className="flex gap-2.5 text-[12.5px] text-[var(--text-secondary)] leading-relaxed">
                  <span className="mono text-[var(--text-dimmed)] shrink-0 mt-px">›</span>
                  <span>{b}</span>
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap gap-1.5">
              {stack.map((s) => (
                <span
                  key={s}
                  className="mono text-[10px] px-1.5 py-0.5 rounded-[2px] bg-[var(--bg-inset)] border border-[var(--border-subtle)] text-[var(--text-muted)]"
                >
                  {s}
                </span>
              ))}
            </div>
          </div>

          <div className="p-5 grid grid-cols-3 lg:grid-cols-1 gap-4 content-start bg-[var(--bg-surface)]">
            {metrics.map(([n, l]) => (
              <div key={l}>
                <div className="text-[19px] font-semibold tnum tracking-tight leading-none" style={{ color: tone }}>{n}</div>
                <div className="eyebrow mt-1.5">{l}</div>
              </div>
            ))}
          </div>
        </div>
      </article>
    </Reveal>
  );
}

function StreamRow({
  when, title, kind, lines, delay,
}: (typeof STREAM)[number] & { delay: number }) {
  const meta = {
    experience: { icon: Users, tone: 'var(--primary)' },
    education: { icon: GraduationCap, tone: 'var(--status-blue)' },
    community: { icon: Sparkles, tone: 'var(--status-green)' },
  }[kind];
  const Icon = meta.icon;

  return (
    <Reveal delay={delay}>
      <li className="relative list-none">
        <span
          className="absolute -left-[31px] top-0.5 w-[18px] h-[18px] rounded-full border flex items-center justify-center bg-[var(--bg-app)]"
          style={{ borderColor: meta.tone, color: meta.tone }}
        >
          <Icon size={9} />
        </span>
        <div className="eyebrow mb-1.5">{when}</div>
        <div className="text-[14px] font-medium mb-2">{title}</div>
        <ul className="space-y-1.5">
          {lines.map((l) => (
            <li key={l} className="text-[12.5px] text-[var(--text-secondary)] leading-relaxed">{l}</li>
          ))}
        </ul>
      </li>
    </Reveal>
  );
}
