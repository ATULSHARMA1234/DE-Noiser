# SemanticOS

SemanticOS is a privacy-first, hyperscale, on-premise log analysis and observability platform. It uses semantic clustering (HDBSCAN), a causal proximity scorer, and a local LLM to automatically denoise millions of logs, find root causes, and trigger automated runbooks — all without your data ever leaving your infrastructure.

![Command Center Dashboard](assets/semanticos_dashboard_mockup.png)

## Why SemanticOS?
Modern enterprise observability tools (like Datadog, Splunk, or New Relic) are expensive and require you to send sensitive PII and infrastructure data to third-party clouds. SemanticOS provides the same advanced AIOps features for free, running entirely on your own hardware.

### Key Features
- **Semantic Clustering:** Automatically groups millions of similar log lines into unique pattern templates using an agglomerative HDBSCAN pipeline.
- **Predictive AI & SLOs:** Defines error budgets and uses forecasting models to predict when an SLO will breach.
- **Distributed Tracing (eBPF):** High-performance kernel-level tracing without instrumenting application code.
- **Hyperscale Ingestion:** Powered by Redpanda and ClickHouse to ingest and store millions of events per second with multi-tenant data tiering.
- **Local LLM Incident Narratives:** Generates human-readable root cause analyses using local models (e.g. Llama 3 via Ollama).
- **Log Query Language:** A custom query DSL to search structured and unstructured logs efficiently.
- **Automated Runbooks:** Triggers automated workflows and multi-channel alerts (Slack, PagerDuty) on anomalies.

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
- Node.js v18+
- Python 3.11+
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
- [API Reference](docs/api.md) *(coming soon)*
- [Architecture Details](docs/architecture.md) *(coming soon)*

## License
SemanticOS is licensed under the [MIT License](LICENSE).
