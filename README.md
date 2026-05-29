# SemanticOS

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Status](https://img.shields.io/badge/Status-Beta-purple.svg)]()

**SemanticOS** is an open-source, privacy-first observability platform designed to automatically denoise high-volume logs, reduce alert fatigue, and generate root-cause intelligence locally—without sending your data to a third-party cloud.

It combines the power of OpenTelemetry, ClickHouse, and local large language models (LLMs) to provide an enterprise-grade experience at zero cost.

## Features

- 🧠 **AI-Powered Log Denoising**: Uses HDBSCAN and semantic clustering to reduce millions of raw logs into a handful of actionable patterns.
- 🕵️ **Local Root Cause Analysis**: Leverages a local LLM to analyze correlated traces and logs, outputting a human-readable incident summary and remediation steps.
- ⚡ **High-Performance Storage**: Built on ClickHouse for blazingly fast queries and high-throughput ingestion.
- 🔍 **Log Query Language (LQL)**: A secure, AST-based search language for rapid ad-hoc log querying and filtering.
- 📊 **SLO & Metrics**: Automatic Log-to-Metrics conversion to track error budgets and Service Level Objectives.
- 🤖 **Automated Runbooks**: Execute remediation workflows (webhooks, scripts) automatically upon SLO breach or P0 incident detection.
- 🏢 **Multi-Tenancy**: Built-in row-level data isolation and JWT role-based access control (RBAC).

## Architecture

1. **Ingestion**: OTLP traces and structured logs are ingested via the FastAPI gateway.
2. **Storage**: Data is durably persisted into ClickHouse.
3. **Analysis Worker**: A Celery worker periodically clusters logs, evaluates SLOs, and extracts metrics.
4. **Intelligence**: When anomalies are detected, logs are embedded via LanceDB and summarized via the LLM.
5. **Runbooks**: Incidents trigger automated, user-defined runbooks.

## Quickstart

### Prerequisites
- Docker & Docker Compose
- Python 3.11+
- Node.js 18+

### Running Locally

1. **Start the Infrastructure** (ClickHouse, Redis)
   ```bash
   docker-compose up -d
   ```

2. **Start the API & Worker**
   ```bash
   # Terminal 1
   uv run python -m uvicorn denoiser.api.main:app --reload

   # Terminal 2
   uv run celery -A denoiser.workers.analysis_worker.celery_app worker --loglevel=info
   ```

3. **Start the Frontend**
   ```bash
   cd web
   npm install
   npm run dev
   ```

4. **Visit SemanticOS**
   Open [http://localhost:3000](http://localhost:3000) in your browser. Log in with the default credentials (`admin@semanticos.local` / `admin`).

## Community & Support

- **Bug Reports**: Please use the GitHub Issue Tracker.
- **Discussions**: Join our Discord community (link coming soon).

## License

SemanticOS is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.
