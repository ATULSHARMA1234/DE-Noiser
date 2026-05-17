# SemanticOS — Enterprise Production Readiness Tasklist

> **Objective:** Transform the current Enterprise MVP into a fully production-ready, deployable, and auditable observability platform that meets SOC2/enterprise compliance standards.

> [!IMPORTANT]
> Each task is **atomic** — it can be completed independently in a single session. Tasks are **sequentially ordered** with no overlapping dependencies. Complete them top-to-bottom.

---

## Phase 1: Core Backend Hardening
*Goal: Make the Python backend bulletproof, testable, and production-safe.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 1 | **Add structured logging with correlation IDs** | Replace all `print()` and ad-hoc `logger` calls in `api/main.py` with the existing `denoiser.logging` module. Every API request should generate a unique `request_id` (UUID) that is attached to all log lines for that request, enabling end-to-end tracing. | `api/main.py`, `logging.py` |
| 2 | **Add input validation with Pydantic models** | Replace all raw `dict` request bodies in the API (e.g., `/analyze`, `/settings`, `/ingest`) with strongly-typed Pydantic `BaseModel` classes. This prevents malformed payloads from crashing the server. | `api/main.py` (new file: `api/schemas.py`) |
| 3 | **Add global exception handler middleware** | Create a FastAPI exception handler that catches all unhandled errors, logs them with the correlation ID from Task 1, and returns a clean JSON error response `{"error": "...", "request_id": "..."}` instead of a raw 500 traceback. | `api/main.py` |
| 4 | **Add rate limiting to the `/ingest` endpoint** | Install `slowapi` and apply a rate limit (e.g., 100 requests/minute per IP) to the `/ingest` webhook to prevent abuse from misconfigured FluentBit agents flooding the server. | `api/main.py`, `pyproject.toml` |
| 5 | **Migrate database from SQLite to PostgreSQL** | Update the SQLAlchemy `DATABASE_URL` to read from the `.env` file. Replace the `check_same_thread` SQLite-specific arg with a conditional check. Add `psycopg2-binary` to `pyproject.toml`. Test with both SQLite (local dev) and PostgreSQL (production). | `storage/db.py`, `pyproject.toml`, `.env` |
| 6 | **Add Alembic database migrations** | Initialize Alembic in the project root. Create an initial migration from the current `Incident` and `AnalysisRun` models. This replaces the manual `ALTER TABLE` scripts and ensures schema changes are versioned and reproducible across environments. | New: `alembic/`, `alembic.ini` |
| 7 | **Write unit tests for the analysis pipeline** | Create a `tests/` directory. Write pytest tests for: (a) `LogReader` ingestion, (b) `Normalizer` preprocessing, (c) `HDBSCANClusterer.fit_predict()` with a small sample dataset, (d) `PII Redactor` with known sensitive strings. Target: 80%+ coverage on core modules. | New: `tests/test_ingestion.py`, `tests/test_clustering.py`, `tests/test_redaction.py` |
| 8 | **Write integration tests for all API endpoints** | Using `httpx.AsyncClient` and FastAPI's `TestClient`, write tests that hit every endpoint (`/health`, `/sources`, `/analyze`, `/incidents`, `/settings`, `/ingest`) and assert correct status codes and response shapes. | New: `tests/test_api.py` |

---

## Phase 2: Authentication & Authorization
*Goal: Lock down the platform so only authorized users can access it.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 9 | **Add a User model and password hashing** | Create a `User` SQLAlchemy model with fields: `id`, `email`, `hashed_password`, `role` (enum: `ADMIN`, `ANALYST`, `VIEWER`), `created_at`. Use `passlib[bcrypt]` for password hashing. Add a CLI command or seed script to create the first admin user. | `storage/db.py`, new: `api/auth.py` |
| 10 | **Implement JWT authentication on the backend** | Install `python-jose[cryptography]`. Create `/auth/login` (returns JWT token) and `/auth/me` (returns current user) endpoints. Create a `get_current_user` FastAPI dependency that extracts and validates the JWT from the `Authorization: Bearer` header. | `api/auth.py`, `api/main.py`, `pyproject.toml` |
| 11 | **Protect all API routes with auth middleware** | Apply the `get_current_user` dependency to every endpoint except `/health` and `/auth/login`. The `/ingest` endpoint should accept either a JWT or a static API key (for machine-to-machine like FluentBit). | `api/main.py` |
| 12 | **Add role-based access control (RBAC)** | Create a `require_role()` dependency. Apply it so: `VIEWER` can only GET data; `ANALYST` can resolve/reopen incidents; `ADMIN` can delete sources, change settings, and manage users. Return `403 Forbidden` for unauthorized actions. | `api/auth.py`, `api/main.py` |
| 13 | **Build the Login page on the frontend** | Create a new `/login` route in Next.js with email/password fields. On submit, call `POST /auth/login`, store the returned JWT in `localStorage`, and redirect to `/app`. Style it to match the existing dark glassmorphic theme. | New: `web/src/app/login/page.tsx` |
| 14 | **Add auth token to all frontend API calls** | Update `src/lib/api.ts` to automatically attach the `Authorization: Bearer <token>` header to every request. Add a global `401` interceptor that redirects to `/login` if the token expires. | `web/src/lib/api.ts` |
| 15 | **Add a route guard to protect the dashboard** | Create a React context provider (`AuthProvider`) that checks for a valid JWT on mount. If no token exists, redirect to `/login`. Wrap the `/app` layout with this provider. | New: `web/src/context/AuthContext.tsx`, `web/src/app/app/layout.tsx` |
| 16 | **Build the User Management page (Admin only)** | Create a new `/app/users` page visible only to `ADMIN` role. It should list all users, allow creating new users (with role assignment), and allow deactivating existing users. Add a "Users" link to the sidebar. | New: `web/src/app/app/users/page.tsx`, `web/src/app/app/layout.tsx` |

---

## Phase 3: Alerting & Notification Integrations
*Goal: Push critical incidents to engineers automatically, don't wait for them to check the dashboard.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 17 | **Connect Slack webhook to the API** | The `SlackNotifier` class already exists in `integrations/slack.py`. Wire it into the `/analyze` endpoint so that when an analysis completes with `impact_score > 0.7`, it automatically sends a formatted Slack Block Kit message to the configured webhook URL. Read the webhook URL from settings. | `api/main.py`, `integrations/slack.py` |
| 18 | **Add a Slack webhook configuration UI** | Add a "Slack Webhook URL" input field to the Settings page. When saved, it persists to `settings.json` via the existing `/settings` API. Add a "Test Notification" button that sends a sample message. | `web/src/app/app/settings/page.tsx` |
| 19 | **Add email alerting via SMTP** | Create an `EmailNotifier` class that sends HTML-formatted incident alerts via SMTP (configurable host/port/credentials in settings). Trigger it alongside Slack when `impact_score > threshold`. | New: `integrations/email.py`, `api/main.py` |
| 20 | **Add PagerDuty integration** | Create a `PagerDutyNotifier` class that triggers a PagerDuty incident via their Events API v2 when a `CRITICAL` severity incident is detected. Read the routing key from settings. | New: `integrations/pagerduty.py`, `api/main.py` |
| 21 | **Build an Alerts History page** | Create a new `/app/alerts` page that shows a chronological log of every notification sent (Slack, Email, PagerDuty) with status (delivered/failed), timestamp, and the linked incident ID. Store alert records in a new `AlertLog` database table. | New: `web/src/app/app/alerts/page.tsx`, `storage/db.py` |

---

## Phase 4: Audit Trail & Compliance
*Goal: Meet SOC2 and enterprise compliance requirements.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 22 | **Create an AuditLog database model** | Add an `AuditLog` table with fields: `id`, `user_id`, `action` (e.g., `INCIDENT_RESOLVED`, `SOURCE_DELETED`, `SETTINGS_CHANGED`), `resource_type`, `resource_id`, `details` (JSON), `ip_address`, `timestamp`. | `storage/db.py` |
| 23 | **Add audit logging middleware** | Create a FastAPI middleware or utility function that automatically writes an `AuditLog` entry for every mutating action (POST, PUT, DELETE). It should capture the authenticated user (from Task 10), the action performed, and the affected resource. | `api/main.py`, new: `api/audit.py` |
| 24 | **Build the Audit Log viewer page (Admin only)** | Create a new `/app/audit` page that displays the full audit trail in a searchable, filterable table. Filters: by user, by action type, by date range. Only visible to `ADMIN` role. | New: `web/src/app/app/audit/page.tsx` |
| 25 | **Add data retention policy enforcement** | Create a background task (using FastAPI's `BackgroundTasks` or APScheduler) that runs daily and deletes `AnalysisRun` and `AuditLog` records older than the configured `retention_days` setting. Log the purge action to the audit trail. | `api/main.py`, new: `api/scheduler.py` |

---

## Phase 5: Storage & Data Lifecycle
*Goal: Handle terabyte-scale log volumes without filling up the server disk.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 26 | **Add S3 cold storage archival** | Create an `S3Archiver` class that compresses (`gzip`) log files older than 7 days and uploads them to an S3 bucket. After successful upload, delete the local file. Read the S3 bucket name and credentials from `.env`. | New: `integrations/s3_archiver.py` |
| 27 | **Wire S3 archival into the retention scheduler** | Extend the scheduler from Task 25 to run the S3 archival before deleting local files. Add a "Storage" section to the Settings page showing local disk usage and archived file count. | `api/scheduler.py`, `web/src/app/app/settings/page.tsx` |
| 28 | **Add log file size limits and rotation** | For the `/ingest` endpoint's `live_stream.log` file: implement automatic rotation when the file exceeds 100MB. Rotate by renaming to `live_stream_<timestamp>.log` and creating a fresh file. Old rotated files are picked up by the S3 archiver. | `api/main.py` |

---

## Phase 6: Containerization & Deployment
*Goal: Make the platform deployable anywhere with a single command.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 29 | **Create a Dockerfile for the Python backend** | Write a multi-stage Dockerfile: Stage 1 builds dependencies with `uv`, Stage 2 copies the built environment into a slim Python image. Expose port 8000. Use `gunicorn` with `uvicorn` workers for production. | New: `Dockerfile` |
| 30 | **Create a Dockerfile for the Next.js frontend** | Write a multi-stage Dockerfile: Stage 1 runs `npm run build`, Stage 2 serves the production bundle with `next start`. Expose port 3000. | New: `web/Dockerfile` |
| 31 | **Create a `docker-compose.yml`** | Define a compose file with 4 services: `api` (Python backend), `web` (Next.js frontend), `db` (PostgreSQL), and `redis` (for WebSocket pub/sub in Task 33). Include health checks, volume mounts for persistent data, and a shared network. | New: `docker-compose.yml` |
| 32 | **Add HTTPS with Nginx reverse proxy** | Add an `nginx` service to the docker-compose stack that terminates TLS and proxies requests to the `api` and `web` services. Include a self-signed certificate generator for local dev and instructions for mounting real certs in production. | New: `nginx/nginx.conf`, `docker-compose.yml` |
| 33 | **Add Redis pub/sub for multi-replica WebSockets** | Replace the in-process WebSocket stream with a Redis-backed pub/sub channel. When the `/ingest` endpoint receives logs, it publishes them to a Redis channel. All WebSocket connections subscribe to that channel. This allows the API to scale horizontally. | `api/main.py`, `pyproject.toml` |

---

## Phase 7: CI/CD & Quality Gates
*Goal: Automated testing and deployment on every git push.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 34 | **Create a GitHub Actions CI pipeline** | Create a workflow that triggers on every push/PR to `main`. Steps: (1) Checkout code, (2) Install Python deps with `uv`, (3) Run `pytest` with coverage report, (4) Run `ruff` linter, (5) Fail the build if coverage < 80% or linter errors exist. | New: `.github/workflows/ci.yml` |
| 35 | **Add frontend linting and type-checking to CI** | Extend the CI pipeline to: (1) Install Node deps, (2) Run `npx tsc --noEmit` for TypeScript type checking, (3) Run `npx eslint .` for code quality. Fail the build on any errors. | `.github/workflows/ci.yml` |
| 36 | **Add Docker image build & push to CI** | Extend the CI pipeline with a deployment stage that: (1) Builds both Docker images, (2) Tags them with the git SHA, (3) Pushes them to GitHub Container Registry (`ghcr.io`). Only runs on merges to `main`, not on PRs. | `.github/workflows/ci.yml` |
| 37 | **Create a Kubernetes Helm chart** | Create a Helm chart (`deploy/helm/semanticos/`) with templates for: Deployment (API + Web), Service, Ingress (with TLS), ConfigMap (for settings), Secret (for API keys), and PersistentVolumeClaim (for data). | New: `deploy/helm/semanticos/` |

---

## Phase 8: Dashboard Polish & Advanced UX
*Goal: Final UI/UX refinements to make the dashboard feel truly premium.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 38 | **Add dark/light theme toggle** | Create a `ThemeProvider` context that stores the user's preference in `localStorage`. Add a sun/moon toggle button to the top header bar. Update all CSS variables to support both modes. | New: `web/src/context/ThemeContext.tsx`, `web/src/app/globals.css`, `web/src/app/app/layout.tsx` |
| 39 | **Add keyboard shortcuts** | Implement global keyboard shortcuts: `Cmd+K` opens a command palette (search), `Cmd+R` triggers a new analysis run, `Cmd+L` navigates to Live Pulse, `Escape` closes any open modal. | `web/src/app/app/layout.tsx` |
| 40 | **Add toast notifications** | Replace all `alert()` calls across the frontend with a toast notification system (e.g., `react-hot-toast`). Show success toasts for actions like "Incident Resolved" and error toasts for failed API calls. | All frontend pages, `pyproject.toml` (web) |
| 41 | **Add a global loading skeleton** | When any page is fetching data, show animated skeleton placeholders (shimmer effect) instead of blank space or spinner icons. This creates a perceived performance boost. | All frontend pages |
| 42 | **Add responsive mobile layout** | Add responsive breakpoints so the dashboard is usable on tablets and phones. The sidebar should collapse into a hamburger menu on screens < 768px. Tables should become scrollable card lists. | `web/src/app/app/layout.tsx`, all pages |
| 43 | **Build an onboarding wizard for first-time users** | When the database has zero analysis runs, show a step-by-step wizard: (1) "Upload your first log file", (2) "Run your first analysis", (3) "Review your incidents". Store completion state in `localStorage`. | New: `web/src/components/OnboardingWizard.tsx` |

---

## Phase 9: Documentation & Launch
*Goal: Make the project presentable for open-source release or enterprise demo.*

| # | Task | Description | Files Affected |
|---|------|-------------|----------------|
| 44 | **Write a comprehensive README.md** | Rewrite the README with: project banner image, feature screenshots, architecture diagram (Mermaid), quick start guide (Docker + manual), API reference table, environment variable reference, and contributing guidelines. | `README.md` |
| 45 | **Add API documentation with Swagger** | FastAPI auto-generates Swagger docs at `/docs`. Ensure all endpoints have proper docstrings, request/response model examples, and tags (e.g., "Analysis", "Incidents", "Auth"). Verify the docs page is clean and complete. | `api/main.py`, `api/schemas.py` |
| 46 | **Create a demo video / GIF** | Record a 60-second screen recording showing: uploading a log file → running analysis → viewing the Command Center results → drilling into an incident → resolving it. Export as GIF for the README. | New: `docs/demo.gif` |
| 47 | **Add a CONTRIBUTING.md and CODE_OF_CONDUCT.md** | Write contributor guidelines covering: how to set up the dev environment, branch naming conventions, PR template, and code style rules. Add a standard code of conduct. | New: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` |
| 48 | **Add a LICENSE file** | Add an MIT license file (or Apache 2.0 if you prefer) to the repository root. | New: `LICENSE` |

---

## Summary

| Phase | Tasks | Focus Area |
|-------|-------|------------|
| **Phase 1** | Tasks 1–8 | Backend hardening, testing, PostgreSQL |
| **Phase 2** | Tasks 9–16 | Authentication, RBAC, login UI |
| **Phase 3** | Tasks 17–21 | Slack, Email, PagerDuty alerting |
| **Phase 4** | Tasks 22–25 | Audit trail, compliance, retention |
| **Phase 5** | Tasks 26–28 | S3 archival, log rotation |
| **Phase 6** | Tasks 29–37 | Docker, Kubernetes, CI/CD |
| **Phase 7** | Tasks 34–37 | GitHub Actions, Helm charts |
| **Phase 8** | Tasks 38–43 | UI polish, mobile, onboarding |
| **Phase 9** | Tasks 44–48 | Documentation, demo, launch |

> [!TIP]
> **Recommended order for maximum impact:** Start with Phase 1 (hardening) → Phase 6 (Docker) → Phase 2 (Auth) → Phase 9 (docs). This gives you a deployable, secure product with documentation fastest. Phases 3–5 and 7–8 are "depth" features you layer on after the core is solid.
