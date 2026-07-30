# SemanticOS

SemanticOS is a privacy-first, hyperscale, on-premise log analysis and observability platform. It uses semantic clustering (HDBSCAN), a causal proximity scorer, and a local LLM to automatically denoise millions of logs, find root causes, and trigger automated runbooks — all without your data ever leaving your infrastructure.

![Command Center Dashboard](assets/semanticos_dashboard_mockup.png)

## Why SemanticOS?
Modern enterprise observability tools (like Datadog, Splunk, or New Relic) are expensive and require you to send sensitive PII and infrastructure data to third-party clouds. SemanticOS provides the same advanced AIOps features for free, running entirely on your own hardware.

### Key Features
- **Semantic Clustering:** Automatically groups similar log lines into unique pattern templates using a hybrid Agglomerative/HDBSCAN pipeline over local sentence embeddings.
- **Issue Tracking:** Folds each run's clusters into durable issues keyed on the log pattern itself, so one failing pattern keeps a single identity across runs — first/last seen, an occurrence trend, tag prevalence, samples, triage state, assignee, comments, and the deploy that most likely introduced it. Recurrence after resolution reopens the issue as a regression.
- **Causal Root-Cause Analysis:** Correlates clustered events across services within a sliding time window to surface directed, cross-service causal links.
- **Predictive AI & SLOs:** Computes availability/latency SLIs from real ingested logs, tracks error budgets, and uses Holt-Winters forecasting to predict when an SLO will breach.
- **Distributed Tracing (OTLP):** Ingests and stores OpenTelemetry spans in ClickHouse. A separate optional **eBPF agent** captures kernel-level process-execution telemetry on Linux (via bcc/libbpf) — this is host telemetry, not span-level distributed tracing.
- **Streaming Ingestion:** A Redpanda/Kafka → worker → ClickHouse pipeline with at-least-once delivery and multi-tenant retention tiering. (Throughput depends on your hardware and broker sizing; no specific rate is guaranteed out of the box.)
- **Local LLM Incident Narratives:** Generates human-readable root-cause summaries using a local, OpenAI-compatible model (e.g. Llama 3 via Ollama). Falls back to a heuristic summary when no model is configured.
- **Log Query Language:** A custom query DSL compiled to parameterized ClickHouse SQL.
- **Automated Runbooks:** Triggers automated workflows and multi-channel alerts (Slack, PagerDuty, Teams, generic webhooks) on anomalies.
- **Enterprise Identity:** Real OIDC (Authorization Code + JWKS) *and* real SAML 2.0 SSO — assertions are signature-verified against the IdP certificate, with audience, recipient, validity-window and replay checks — plus SCIM 2.0 provisioning, per-tenant API quotas, and JWT signing-key rotation with an overlap window (no forced sign-out).
- **CI Correlation:** Pulls GitHub Actions workflow logs and deployment/release metadata, so a failing pipeline or a deploy lands in the same timeline as the incident it caused.

> **Sandbox mode.** In **development**, the Kubernetes, AWS CloudWatch, and Docker log connectors fall back to clearly-labeled `"simulated"` sample data when no cluster/credentials/socket is detected, and a mock SSO IdP is available. In **production** both are off: the connectors return a real `502` (unless `ALLOW_SIMULATED_CONNECTORS` is explicitly set) and SSO requires a configured OIDC or SAML IdP. Simulated paths are labeled as such in every API response.

## Architecture

```mermaid
graph TD
    A[Logs / eBPF Traces] --> B[FastAPI Ingestion]
    B --> C[Redpanda / Kafka]
    C --> D[Ingestion Worker]
    D --> E[(ClickHouse)]
    E --> F[Analysis Worker (Clustering & Scoring)]
    F --> G[Local LLM]
    F --> H[(PostgreSQL)]
    H --> I[Next.js Frontend]
```

## Quickstart

### Prerequisites
- Docker & Docker Compose
- Node.js v20+ (required by Next.js 16 / React 19)
- Python 3.12+
- `uv` package manager

### 1. Start Infrastructure
Start Redpanda, ClickHouse, Redis, and PostgreSQL:
```bash
docker-compose up -d
```

### 2. Start Backend
Run the FastAPI application:
```bash
uv run python -m uvicorn denoiser.api.main:app --host 0.0.0.0 --port 8000
```

### 3. Start Frontend
Run the Next.js React frontend:
```bash
cd web
npm install
npm run dev
```

The Command Center will be available at [http://localhost:3000/app](http://localhost:3000/app).

## Documentation
- [Contributing Guidelines](CONTRIBUTING.md)
- [API Reference](docs/api.md)
- [Architecture Details](docs/architecture.md)
- [Operations Runbook (deploy, backup/restore, checklist)](docs/operations.md)
- [Helm chart](deploy/helm/semanticos)
- [Changelog](CHANGELOG.md)
- Load testing: `python scripts/loadtest.py --help`

## License
SemanticOS is licensed under the [MIT License](LICENSE).
