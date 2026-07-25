# Changelog

All notable changes to SemanticOS are documented here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Security
- **Tenant isolation is now fail-closed** (audit H1). The ClickHouse store
  rejected an empty/falsy `tenant_id` instead of silently running an unscoped,
  cross-tenant query; every read/write is now scoped or refused. Regression
  tests cover the guard and the injected predicate.
- **Local password login is gated in production** (audit M2). It is off by
  default in production (MFA is enforced by the IdP through SSO) and on in
  development, with an explicit `SEMANTICOS_ALLOW_LOCAL_LOGIN` break-glass
  override.

### Fixed
- **OIDC JWKS rotation** (audit M1): the JWKS cache now has a TTL and force-
  refreshes when a token's `kid` is missing, so a provider key rotation no
  longer locks every user out until restart. Removed the arbitrary
  "pick the first key" fallback.

### Changed
- **Analysis input is capped** (audit M3) at `SEMANTICOS_MAX_ANALYSIS_LINES`
  (default 500k, per-request override) so a huge source can't OOM a worker; the
  run result reports `truncated`. Added a capacity/load-test procedure to the
  operations runbook.
- Raised test coverage on enterprise-critical edges (audit M4): SCIM
  de-provisioning actually cutting token access, plus syslog TLS handshake and
  TCP framing edge cases.

## [0.1.0] - 2026-07-25

### Added
- **Packaging & operability**:
  - **Hardened Docker image** — runs as a non-root user (uid 1001), adds a
    `HEALTHCHECK`, OCI labels, and a `.dockerignore` (the build context no longer
    leaks `.git`/`.venv`/`node_modules`/`data`/secrets).
  - **Production Helm chart** — API, analysis worker, ingestion worker, web, and
    the syslog connector as separate workloads; liveness/readiness probes wired to
    the real health endpoints; resource requests/limits; pod hardening
    (non-root, dropped capabilities, seccomp); a **pre-install/upgrade migration
    Job** (so replicas don't race to auto-migrate); a full app Secret with
    `existingSecret` support; and a **ServiceAccount + RBAC** for the in-cluster
    Kubernetes log connector.
  - **Operations runbook** (`docs/operations.md`) — Helm install, a production
    readiness checklist, scaling notes, and tested Postgres/ClickHouse/object-store
    backup & restore procedures.
- **Real Kubernetes connector** — pod discovery + timestamped log reading via the
  Kubernetes API (in-cluster ServiceAccount or local kubeconfig), a polling
  `KubernetesLogCollector` that streams recent logs into the ingestion sink, and
  per-line level inference. The `/connectors/k8s/*` endpoints now use it, and the
  **simulated fallback is gated to dev** (`ALLOW_SIMULATED_CONNECTORS`) — in
  production an unreachable cluster returns a real `502`, not fake pods.
- **Elasticsearch & Splunk drop-in connectors** — an Elasticsearch Bulk API
  (`/_bulk`, with a version-stub preflight) and Splunk HEC (`/services/collector`,
  event + raw + health) so existing Filebeat/Logstash and Splunk forwarders can
  ship to SemanticOS with only a URL change. Both normalize to the standard log
  record shape and persist through the shared path.
- **OTLP logs connector** — `/v1/logs` now accepts the OpenTelemetry **protobuf**
  encoding (the exporter default) as well as JSON, via a dependency-free OTLP
  protobuf decoder. `severityNumber` maps to the platform severity vocabulary and
  `service.name` becomes the source; records flow through the same pipeline as
  every other input.
- **Syslog ingestion connector** (RFC 5424 + RFC 3164/BSD) over UDP and TCP
  (octet-counted and newline framing) with optional TLS, running as its own
  `syslog` service on port 514. The highest-breadth source connector — one
  listener serves firewalls, routers, appliances, and legacy Unix hosts. Parsed
  records flow through the same pipeline resolvers as HTTP ingest.
- **Enterprise identity (large-workforce readiness)**:
  - **Real OIDC SSO** — Authorization Code flow with discovery, code exchange, and
    JWKS signature/issuer/audience validation of the ID token; group claims map to
    role + teams (JIT provisioning). The mock IdP remains, gated for dev only.
  - **SCIM 2.0** (`/scim/v2/Users`, `/scim/v2/Groups`) — automated user
    create/update/**de-provision** and group→team membership, so a departing
    employee loses access the moment the IdP pushes the change.
  - **Teams** model + `users.external_id`/`users.teams` (migration), exposed on
    the user profile.
- **Self-observability**: `/internal/metrics` Prometheus endpoint (request rate,
  error rate, latency histograms) via a metrics middleware.
- **Readiness/liveness split**: `/health/live` (cheap) and `/health/ready`
  (probes DB, Redis, ClickHouse, Kafka; returns `503` when a critical dependency
  is down). The old `/health` was a static, always-"healthy" stub.
- **JWT revocation**: every token now carries a `jti`; `/auth/logout` records it
  in a `revoked_tokens` table so a token can be invalidated before it expires.
- **Login brute-force throttle**: `/auth/login` locks out after 5 failures per
  IP+email within 5 minutes.
- **Pagination** on `/incidents`, `/runs`, and `/users` (`limit`/`offset`).
- **Kafka dead-letter queue**: unparseable messages and batches that fail to
  flush after repeated retries are quarantined instead of dropped or wedging the
  partition.
- **Load-test harness** (`scripts/loadtest.py`) to measure real `/ingest`
  throughput and latency.
- **Docs**: `docs/architecture.md`, `docs/api.md`, this changelog.
- **CI**: enforced mypy on a strict-clean allowlist (ratchet), `npm audit`, and a
  Dependabot config (pip / npm / github-actions).

### Changed
- **SLOs & metrics now use real data.** `evaluate_slos` computes availability/
  latency SLIs from actual ingested logs via the (previously unused) SLO engine,
  and records nothing when a service has no traffic. `extract_metrics` aggregates
  real matches in ClickHouse. Both previously fabricated values with `random`.
- **`/ingest` batching**: Kafka sends are pipelined and awaited together (was one
  blocking round-trip per message); Redis publishes are pipelined; the log file
  is written in one call.
- **README** reworded to match delivered behavior: OTLP vs eBPF distinguished,
  the "millions/sec" claim softened, sandbox features labeled, prereqs corrected
  (Node 20+, Python 3.12+).

### Fixed
- Runbook automation was crashing at runtime: added the missing `Incident.severity`
  column (+ migration), fixed `/alerts/trigger` writing non-existent columns, and
  corrected an `execute_runbook_step()` call with the wrong arity.
- ABAC PII-isolation rule was dead code (`impact_score > 80` on a 0–1 scale);
  corrected to `> 0.8`.
- P2-severity incidents never alerted (priority-selection loop couldn't pick P2).
- Rate limiter keyed all proxied traffic to one bucket; now reads `X-Forwarded-For`.
- SQL injection surface in `cleanup_old_data` and the SLO engine — parameterized.
- Removed stray debug scripts from the repository root.
- Replaced deprecated `datetime.utcnow()`/`utcfromtimestamp()` (removed in a
  future Python) with a naive-UTC helper (`denoiser.utils.time`), preserving the
  exact previous timestamp semantics.
