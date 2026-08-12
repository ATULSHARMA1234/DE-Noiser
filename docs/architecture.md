# Architecture

SemanticOS is a privacy-first log-intelligence platform. Everything — embeddings,
clustering, the LLM, and storage — runs on infrastructure you control.

## High-level flow

```
Log shippers / eBPF agent / OTLP
            │
            ▼
   FastAPI  /ingest ───────────────► Redpanda / Kafka  ─► Ingestion worker ─► ClickHouse
      │  (auth, rate limit)                                     │ (at-least-once,
      │                                                         │  per-partition offsets,
      ▼                                                         │  dead-letter queue)
   Redis pub/sub (live tail)                                    ▼
                                                          Analysis worker (Celery)
                                                                 │
        dedup → redact → normalize → embed → cluster → anomaly → causal → LLM narrative
                                                                 │
                                                                 ▼
                                                        PostgreSQL (runs, incidents,
                                                        SLOs, runbooks, audit) ─► Next.js UI
```

## Components

| Component | Tech | Responsibility |
|-----------|------|----------------|
| API | FastAPI | Auth, ingest, query, CRUD, WebSocket live tail, `/internal/metrics` |
| Analysis worker | Celery | The denoising pipeline (dedup→cluster→anomaly→causal→LLM) |
| Ingestion worker | aiokafka | Kafka→ClickHouse with at-least-once delivery + DLQ |
| Scheduler | APScheduler | Periodic SLO evaluation, metric extraction, retention |
| Vector store | LanceDB | Persisted template embeddings |
| Log store | ClickHouse | Raw logs + traces, LQL queries, facets, histograms |
| Metadata store | PostgreSQL | Users, tenants, runs, incidents, SLOs, runbooks, audit |
| Broker / cache | Redpanda, Redis | Streaming ingest, rate limiting, live fan-out, Celery |
| eBPF agent | Go + libbpf | Kernel process-exec telemetry (Linux) |

## The denoising pipeline (analysis worker)

1. **Ingest** multi-source logs and resolve event timestamps.
2. **Redact** PII (regex-based) before anything is embedded or stored.
3. **Normalize** to templates (numbers, ids, UUIDs → placeholders).
4. **Deduplicate** identical templates, keeping counts.
5. **Embed** unique templates with a local sentence-transformer.
6. **Cluster** — Agglomerative (≤50 templates) or HDBSCAN (larger).
7. **Anomaly score** each template against a known-good baseline.
8. **Causal links** — cross-service temporal co-occurrence with time-decay.
9. **LLM narrative** — local, OpenAI-compatible model; heuristic fallback.
10. **Persist** the run + incident + severity, dispatch alerts, trigger runbooks.

## Multi-tenancy & access control

- Every domain query is scoped by `tenant_id`.
- **RBAC** — `VIEWER` / `ANALYST` / `ADMIN` via `require_role`.
- **ABAC** — environment, department, and PII attributes via `require_abac`;
  tenant isolation is enforced for every role including `ADMIN`.
- **JWT** with per-token `jti` and DB-backed revocation (`/auth/logout`).

## Configuration

- `denoiser.config` — analysis pipeline (`SLD_*`): models, thresholds, clustering.
- `denoiser.settings` — deployment (`InfraSettings`): datastores, secrets, CORS.
  `validate_for_production` refuses to boot on silently-unsafe settings.

## Observability of the platform itself

- `/health/live` — liveness (cheap).
- `/health/ready` — readiness; probes DB, Redis, ClickHouse, Kafka; returns 503 when a critical dependency is down.
- `/internal/metrics` — Prometheus exposition of request rate, errors, and latency histograms.

## Schema management

Alembic owns the schema. `bootstrap_schema` handles three states — fresh
(build from migrations), managed (upgrade), legacy (repair to baseline, then
stamp). CI runs `alembic upgrade head` + `alembic check` on both SQLite and
Postgres so models and migrations never drift.

## Sandbox mode

When a backend isn't present, the K8s/AWS/Docker connectors return clearly
labeled `"simulated"` sample data, and the mock SSO IdP is disabled in
production. These are explicitly marked in every response.
