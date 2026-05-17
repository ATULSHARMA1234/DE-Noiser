# SemanticOS — Unified Enterprise & Competitive Readiness Tasklist

> **Objective:** Transform SemanticOS from an Enterprise MVP into a production-grade, Zebrium-competitive observability platform — privacy-first, zero-cost, and architecturally equivalent to the industry giants.

> **Rule:** Each task is **atomic** and **sequentially ordered** with no overlapping dependencies. Complete top-to-bottom.

---

## Phase 1: Core Backend Hardening (Tasks 1–8)
*Goal: Make the Python backend bulletproof, testable, and production-safe.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 1 | **Structured logging with correlation IDs** | Replace ad-hoc logger calls with `denoiser.logging`. Attach a unique `request_id` (UUID) to every API request for end-to-end tracing. | `api/main.py`, `logging.py` |
| 2 | **Pydantic input validation** | Replace raw `dict` request bodies with strongly-typed Pydantic `BaseModel` classes for `/analyze`, `/settings`, `/ingest`. | `api/main.py`, new: `api/schemas.py` |
| 3 | **Global exception handler middleware** | Catch all unhandled errors, log with correlation ID, return clean `{"error": "...", "request_id": "..."}` instead of raw 500 tracebacks. | `api/main.py` |
| 4 | **Rate limiting on `/ingest`** | Install `slowapi`, apply 100 req/min per IP to prevent misconfigured FluentBit agents from flooding the server. | `api/main.py`, `pyproject.toml` |
| 5 | **PostgreSQL migration** | Update SQLAlchemy `DATABASE_URL` to support both SQLite (dev) and PostgreSQL (prod). Add `psycopg2-binary`. | `storage/db.py`, `pyproject.toml`, `.env` |
| 6 | **Alembic database migrations** | Initialize Alembic, create initial migration from current models. Version all schema changes. | New: `alembic/`, `alembic.ini` |
| 7 | **Unit tests for analysis pipeline** | Pytest tests for `LogReader`, `Normalizer`, `HDBSCANClusterer.fit_predict()`, `PII Redactor`. Target 80%+ coverage. | New: `tests/test_ingestion.py`, `tests/test_clustering.py`, `tests/test_redaction.py` |
| 8 | **Integration tests for API endpoints** | Using `httpx.AsyncClient`, test every endpoint and assert correct status codes and response shapes. | New: `tests/test_api.py` |

---

## Phase 2: Cross-Service Causal Correlation (Tasks 9–13)
*Goal: Automatically detect causal chains across multiple services — the #1 feature gap vs Zebrium.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 9 | **Universal timestamp extractor** | Create a `TimestampExtractor` that parses ISO 8601, Unix epoch, syslog, AWS CloudWatch, and Docker compose timestamp formats into UTC epoch milliseconds. | New: `preprocessing/timestamp.py` |
| 10 | **Multi-source batch analysis** | Extend `/analyze` to accept `sources: List[str]`. Ingest all sources into a single Polars DataFrame with a `source_label` column, then cluster together. | `api/main.py`, `api/schemas.py` |
| 11 | **Temporal proximity causal scorer** | After clustering, compare cluster pairs across different sources. If they spike within a 500ms window, assign a causal correlation score. | New: `detection/causal_scorer.py` |
| 12 | **Service topology graph UI** | Build `/app/topology` page with an interactive force-directed graph (`react-flow`) showing services as nodes and causal links as weighted edges. Clicking an edge shows correlated clusters side-by-side. | New: `web/src/app/app/topology/page.tsx` |
| 13 | **LLM causal chain narration** | Feed correlated cluster pairs to the LLM: *"Service A errored at T1, Service B errored at T1+200ms. Explain the causal chain."* Output: plain-English forensic narrative. | `intelligence/llm.py` |

---

## Phase 3: System Telemetry & Auto-Metrics (Tasks 14–17)
*Goal: Capture host-level metrics automatically — closing the APM gap vs Datadog.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 14 | **psutil system metrics collector** | Background agent collecting CPU, memory, disk I/O, network drops every 5s. Writes structured JSON to `data/metrics_stream.jsonl`. | New: `telemetry/metrics_collector.py`, `pyproject.toml` |
| 15 | **Metrics correlation engine** | During analysis, load the metrics stream alongside log clusters. For each incident cluster, find the ±30s metrics window and attach context (e.g., "CPU at 98%"). | New: `detection/metrics_correlator.py` |
| 16 | **Dashboard system vitals panel** | Add real-time sparkline charts for CPU, Memory, Disk I/O, and Network to the Command Center dashboard. | `web/src/app/app/page.tsx` |
| 17 | **eBPF kernel tracing (Linux-only, optional)** | Use `bcc`/`bpftrace` to capture TCP retransmits, DNS latency, OOM kills. Write as structured events into the ingestion pipeline. Requires root. | New: `telemetry/ebpf_collector.py` |

---

## Phase 4: Authentication & Authorization (Tasks 18–25)
*Goal: Lock down the platform so only authorized users can access it.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 18 | **User model and password hashing** | SQLAlchemy `User` model with `id`, `email`, `hashed_password`, `role` (ADMIN/ANALYST/VIEWER). Use `passlib[bcrypt]`. Seed script for first admin. | `storage/db.py`, new: `api/auth.py` |
| 19 | **JWT authentication** | Install `python-jose[cryptography]`. Create `/auth/login` and `/auth/me` endpoints. Build a `get_current_user` FastAPI dependency. | `api/auth.py`, `api/main.py`, `pyproject.toml` |
| 20 | **Protect all API routes** | Apply `get_current_user` to every endpoint except `/health` and `/auth/login`. `/ingest` accepts JWT or static API key. | `api/main.py` |
| 21 | **Role-based access control (RBAC)** | `require_role()` dependency. VIEWER=read-only, ANALYST=resolve incidents, ADMIN=delete/settings/users. 403 for unauthorized. | `api/auth.py`, `api/main.py` |
| 22 | **Login page** | New `/login` route with email/password fields. Store JWT in `localStorage`, redirect to `/app`. Match dark glassmorphic theme. | New: `web/src/app/login/page.tsx` |
| 23 | **Auth token in all API calls** | Update `api.ts` to attach `Authorization: Bearer` header. Add global 401 interceptor → redirect to `/login`. | `web/src/lib/api.ts` |
| 24 | **Route guard** | `AuthProvider` context that checks for valid JWT on mount. Wrap `/app` layout. | New: `web/src/context/AuthContext.tsx`, `web/src/app/app/layout.tsx` |
| 25 | **User management page (Admin)** | New `/app/users` page: list users, create new users with role, deactivate. Sidebar link. | New: `web/src/app/app/users/page.tsx` |

---

## Phase 5: Alerting & Notifications (Tasks 26–30)
*Goal: Push critical incidents automatically — don't wait for dashboard checks.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 26 | **Wire Slack webhook to analysis** | Connect existing `SlackNotifier` to `/analyze`. Auto-send formatted Block Kit message when `impact_score > 0.7`. | `api/main.py`, `integrations/slack.py` |
| 27 | **Slack webhook config UI** | Add Slack Webhook URL input to Settings page with "Test Notification" button. | `web/src/app/app/settings/page.tsx` |
| 28 | **Email alerting via SMTP** | `EmailNotifier` class sending HTML incident alerts. Trigger alongside Slack. | New: `integrations/email.py`, `api/main.py` |
| 29 | **PagerDuty integration** | `PagerDutyNotifier` using Events API v2 for CRITICAL severity incidents. | New: `integrations/pagerduty.py`, `api/main.py` |
| 30 | **Alerts history page** | New `/app/alerts` showing chronological log of every sent notification with status/timestamp. New `AlertLog` DB table. | New: `web/src/app/app/alerts/page.tsx`, `storage/db.py` |

---

## Phase 6: Audit Trail & Compliance (Tasks 31–34)
*Goal: Meet SOC2 and enterprise compliance requirements.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 31 | **AuditLog database model** | Table: `id`, `user_id`, `action`, `resource_type`, `resource_id`, `details` (JSON), `ip_address`, `timestamp`. | `storage/db.py` |
| 32 | **Audit logging middleware** | Auto-write `AuditLog` entry for every mutating action (POST/PUT/DELETE) with authenticated user info. | `api/main.py`, new: `api/audit.py` |
| 33 | **Audit log viewer (Admin)** | New `/app/audit` page: searchable, filterable table. Filters by user, action type, date range. | New: `web/src/app/app/audit/page.tsx` |
| 34 | **Data retention policy** | Background task (APScheduler) deleting old `AnalysisRun` and `AuditLog` records past `retention_days`. | `api/main.py`, new: `api/scheduler.py` |

---

## Phase 7: Storage, Data Lifecycle & Distributed Scale (Tasks 35–40)
*Goal: Handle petabyte-scale log volumes — closing the infrastructure gap vs Splunk.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 35 | **S3/MinIO object storage** | Replace local `data/` with S3-compatible object store. `boto3` for AWS, `minio` client for self-hosted. Infinite scale at $0.023/GB/month. | New: `storage/object_store.py`, `api/main.py` |
| 36 | **Wire S3 into retention scheduler** | Compress logs older than 7 days → upload to S3 → delete local. Add Storage section to Settings page. | `api/scheduler.py`, `web/src/app/app/settings/page.tsx` |
| 37 | **Log file rotation** | Auto-rotate `live_stream.log` at 100MB. Rename to `live_stream_<timestamp>.log`, create fresh file. Old files archived by S3. | `api/main.py` |
| 38 | **Persistent vector database (LanceDB)** | Store all embeddings persistently instead of discarding after analysis. Enables semantic search across all historical logs. | New: `storage/vector_store.py`, modify clustering pipeline |
| 39 | **Redis/Celery async job queue** | Replace synchronous `/analyze` with async jobs. API submits to Redis, workers process independently. Horizontal scaling. | New: `workers/analysis_worker.py`, `api/main.py`, `pyproject.toml` |
| 40 | **ClickHouse integration (advanced)** | Columnar analytics DB for billion-row SQL queries in seconds. All ingested logs dual-written to ClickHouse. Replaces Splunk's search. | New: `storage/clickhouse_store.py`, `pyproject.toml` |

---

## Phase 8: Containerization & Deployment (Tasks 41–45)
*Goal: Deployable anywhere with a single command.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 41 | **Backend Dockerfile** | Multi-stage: build deps with `uv`, copy to slim image. `gunicorn` + `uvicorn` workers. Port 8000. | New: `Dockerfile` |
| 42 | **Frontend Dockerfile** | Multi-stage: `npm run build` → `next start`. Port 3000. | New: `web/Dockerfile` |
| 43 | **docker-compose.yml** | 4 services: `api`, `web`, `db` (PostgreSQL), `redis`. Health checks, volumes, shared network. | New: `docker-compose.yml` |
| 44 | **HTTPS with Nginx reverse proxy** | Add `nginx` service to compose. TLS termination. Self-signed certs for dev, real certs for prod. | New: `nginx/nginx.conf`, `docker-compose.yml` |
| 45 | **Redis pub/sub for WebSocket scaling** | Replace in-process WebSocket with Redis pub/sub channel. `/ingest` publishes, all WS connections subscribe. Enables horizontal API scaling. | `api/main.py`, `pyproject.toml` |

---

## Phase 9: CI/CD & Quality Gates (Tasks 46–49)
*Goal: Automated testing and deployment on every git push.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 46 | **GitHub Actions CI pipeline** | On push/PR: install deps → `pytest` with coverage → `ruff` linter → fail if coverage <80%. | New: `.github/workflows/ci.yml` |
| 47 | **Frontend linting & type-checking** | Extend CI: `npx tsc --noEmit` + `npx eslint .`. Fail on errors. | `.github/workflows/ci.yml` |
| 48 | **Docker image build & push** | On merge to `main`: build both images, tag with git SHA, push to `ghcr.io`. | `.github/workflows/ci.yml` |
| 49 | **Kubernetes Helm chart** | Templates: Deployment, Service, Ingress (TLS), ConfigMap, Secret, PVC. | New: `deploy/helm/semanticos/` |

---

## Phase 10: Dashboard Polish & Advanced UX (Tasks 50–55)
*Goal: Premium UI/UX to rival commercial observability platforms.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 50 | **Dark/light theme toggle** | `ThemeProvider` context + sun/moon toggle in header. CSS variables for both modes. | New: `web/src/context/ThemeContext.tsx`, `globals.css` |
| 51 | **Keyboard shortcuts** | `Cmd+K` command palette, `Cmd+R` new analysis, `Cmd+L` Live Pulse, `Escape` close modals. | `web/src/app/app/layout.tsx` |
| 52 | **Toast notifications** | Replace all `alert()` with `react-hot-toast`. Success/error toasts for all actions. | All frontend pages |
| 53 | **Loading skeletons** | Animated shimmer placeholders while data loads instead of blank space. | All frontend pages |
| 54 | **Responsive mobile layout** | Sidebar collapses to hamburger menu on <768px. Tables become scrollable card lists. | `web/src/app/app/layout.tsx`, all pages |
| 55 | **Onboarding wizard** | Step-by-step guide for first-time users: upload → analyze → review. Stored in `localStorage`. | New: `web/src/components/OnboardingWizard.tsx` |

---

## Phase 11: Documentation & Launch (Tasks 56–60)
*Goal: Open-source release ready.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 56 | **Comprehensive README.md** | Banner image, feature screenshots, Mermaid architecture diagram, quick start (Docker + manual), API reference, env var reference. | `README.md` |
| 57 | **Swagger API documentation** | Ensure all endpoints have docstrings, request/response examples, and tags. Verify `/docs` page is clean. | `api/main.py`, `api/schemas.py` |
| 58 | **Demo video / GIF** | 60-second recording: upload → analyze → Command Center → drill-down → resolve. Export as GIF for README. | New: `docs/demo.gif` |
| 59 | **CONTRIBUTING.md & CODE_OF_CONDUCT.md** | Dev setup guide, branch naming, PR template, code style rules. Standard code of conduct. | New: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` |
| 60 | **LICENSE file** | MIT or Apache 2.0 license. | New: `LICENSE` |

---

## Master Summary

| Phase | Tasks | Focus | Est. Effort |
|-------|-------|-------|-------------|
| **1. Backend Hardening** | 1–8 | Testing, validation, PostgreSQL | 4 days |
| **2. Causal Correlation** | 9–13 | Multi-source forensics, topology graph | 6.5 days |
| **3. System Telemetry** | 14–17 | Metrics collection, eBPF, dashboard vitals | 4-9 days |
| **4. Auth & RBAC** | 18–25 | JWT login, roles, user management | 5 days |
| **5. Alerting** | 26–30 | Slack, Email, PagerDuty, alert history | 3 days |
| **6. Audit & Compliance** | 31–34 | Audit trail, SOC2 readiness, retention | 3 days |
| **7. Scale & Storage** | 35–40 | S3, LanceDB vectors, Redis/Celery, ClickHouse | 6-11 days |
| **8. Containerization** | 41–45 | Docker, Nginx, Redis pub/sub | 4 days |
| **9. CI/CD** | 46–49 | GitHub Actions, Helm charts | 3 days |
| **10. Dashboard Polish** | 50–55 | Theme, shortcuts, toasts, mobile, onboarding | 4 days |
| **11. Docs & Launch** | 56–60 | README, Swagger, demo, license | 3 days |
| **Total** | **60 tasks** | | **~45-55 days** |

> **Recommended fast-track:** Phase 1 → Phase 2 → Phase 8 → Phase 4 → Phase 11 gives you a **deployable, differentiated, secure product with documentation** in ~23 days.
