# Changelog

All notable changes to SemanticOS are documented here. The format is loosely
based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Issue tracking.** A cluster only existed inside the run that produced it —
  HDBSCAN renumbers cluster ids each run — so the same failing pattern was
  reported as brand new every time and could carry no history, state or owner.
  Every run now folds its clusters into `log_issues`, keyed on a fingerprint of
  the normalized template (with a template-hash fallback so a drifting
  representative does not fork the issue). Each issue carries first/last seen, a
  merged hourly occurrence histogram, tag prevalence, samples, triage state
  (`FOR_REVIEW`/`REVIEWED`/`IGNORED`/`RESOLVED`), assignee, comments and an
  activity feed; recurrence after resolution reopens the issue as a regression.
  Served by `/issues` (list, facets, detail, PATCH, comments) and rendered on a
  new **Issues** page with a facet rail, status tabs and a detail panel that
  includes the suspect deployment — the last deploy marker before the issue was
  first seen.
- **Real SAML 2.0 SSO.** `/auth/sso/saml/acs` verifies the assertion's XML
  signature against the configured IdP certificate and checks issuer, audience,
  `Destination`/`Recipient`, the validity window (with configurable skew) and
  single-use assertion ids. Attributes are read only from the signature-covered
  subtree, and a response carrying more than one assertion, a `DOCTYPE`, or an
  `EncryptedAssertion` is refused. Adds SP-initiated login and SP metadata
  (`/auth/sso/saml/login`, `/auth/sso/saml/metadata`). Configure with
  `SAML_IDP_ENTITY_ID`, `SAML_IDP_SSO_URL`, `SAML_IDP_X509_CERT`,
  `SAML_SP_ENTITY_ID`, `SAML_SP_ACS_URL`.
- **Per-tenant API quotas.** A sliding window keyed on the tenant (from
  `X-API-Key` or the Bearer subject) now applies across every route, with
  per-tier ceilings (`TENANT_QUOTA_FREE`/`_PRO`/`_ENTERPRISE`). The previous
  per-IP `/ingest` limiter did not bound a workspace shipping from many pods.
  Health, metrics and auth routes stay exempt.
- **JWT signing-key rotation.** `JWT_SECRET_KEY_PREVIOUS` keeps retired keys
  verifying while their tokens drain, so rotating no longer signs every user
  out; tokens carry a `kid` header. `GET /admin/signing-keys` reports which key
  a replica is signing with. Any setting may be supplied as `<VAR>_FILE` from a
  mounted secret, re-read within `JWT_KEYRING_REFRESH_SECONDS`.
- **GitHub Actions log ingestion and deployment sync.** `fetch_logs` pulls and
  flattens workflow-run log archives (filterable by workflow, branch or
  conclusion; bounded by `GITHUB_MAX_WORKFLOW_RUNS` /
  `GITHUB_MAX_LINES_PER_RUN`) and `sync_metadata` returns real deployments and
  releases. Both previously raised `NotImplementedError`.

- **Organisation onboarding and offboarding** (`/platform/*`, gated by
  `SEMANTICOS_PLATFORM_TOKEN`). Create a customer with their own API key, SCIM
  token and registered email domains; list and update them; and delete one,
  which purges their relational rows, ClickHouse logs and traces, embeddings,
  uploaded sources and cold archives, reporting what was removed from each store
  and naming any store it could not reach. There was previously no way to remove
  a customer at all. The endpoints are gated by an operator credential rather
  than the ADMIN role, because ADMIN belongs to a customer's own administrator
  and must not be able to move the boundary that separates two customers.

### Changed
- **Tenant ownership is decided in one module.** `TenantScope`
  (`denoiser.api.scope`) replaces four disagreeing dialects spread over 58 call
  sites. Ten of those sites guarded the check behind `if current_user.tenant_id`,
  so an unassigned account walked past it; three treated a NULL-tenant *row* as
  shared, making one organisation's legacy notebooks and metric rules readable
  and writable by another. Cross-tenant access is now uniformly **404**, not the
  mix of 404 and 403 it was — a 403 confirms the id exists, which is enough to
  enumerate another customer's resources. Migration `f7a2c04b91de` adopts rows
  that predate tenant scoping into the first organisation, so ownership is a
  plain equality with no legacy special case.
- **A generated tenancy conformance suite** (`tests/test_tenancy_conformance.py`)
  drives every registered resource through the same cross-organisation
  assertions. A tenant-scoped model with no entry fails the suite, so isolation
  coverage can no longer quietly stop growing when a feature is added — which is
  how the `/users` directory leak survived an audit that probed it for role
  boundaries but never for tenant boundaries.
- **Process-wide handles moved to `denoiser.runtime`.** `api.main` owned the
  Redis client, ClickHouse store, Kafka producer and data directory, so four
  modules — including `storage/archiver.py` — imported the HTTP application from
  inside function bodies to break the cycle. Importing any router opened a Redis
  connection and issued two ClickHouse `CREATE TABLE` statements as a side
  effect; it now opens nothing, and substituting a store in tests is one seam
  rather than a patch per import path.
- **Path parameters are bounded** on every scoped router (`ResourceId`), so an
  out-of-range id is a 422 instead of reaching the database driver.

### Removed
- **`api/automation.py`** — four routes on `/runbooks` that were never
  registered; `runbooks.py` owns that prefix.

### Fixed
- **Alerts, email and runbooks no longer run inside the analysis transaction.**
  `run_analysis_task` dispatched webhooks — network I/O with retries and a
  ten-second timeout per attempt — before its `db.commit()`, so one unresponsive
  Slack endpoint held a Postgres transaction and its pooled connection open for
  tens of seconds; at `pool_size=20`, enough concurrent analyses exhausted the
  pool and surfaced as unrelated API requests hanging. The run is now durable
  before anyone is told about it, which also stops an alert arriving that names
  a run not yet readable. `new_incident` and the alert payload are bound before
  the transaction rather than inside a branch of it, and the duplicate
  `tenant_id` read that shadowed the resolved one is gone.

### Security
- **An unreachable store can no longer be recorded as a measurement.**
  `aggregate_metric` returned `0.0` when ClickHouse was down and the metric
  worker committed it, once a minute, as a real observation; the billing pass
  committed a day of zero usage for every customer the same way. Stores now
  raise `StoreUnavailable`, the metric worker leaves a gap in the series, and
  billing marks the tenant unmetered instead of metering it at nothing. A
  monitor during an outage now reports `ERROR` rather than `NO_DATA`, which had
  made every alert look healthy for as long as the store was down.
- **Federated identities are routed to the right organisation.** SSO and SCIM
  both assigned every user to whichever tenant had the lowest id — correct for a
  single-customer deployment, and wrong for a shared one, where an employee of
  the second company signing in through their own IdP landed inside the first
  company's data. Organisations now register the email domains they own, and an
  address from an unregistered domain is refused rather than guessed at. A
  deployment where nobody has registered a domain keeps the previous fallback,
  so single-customer installs are unaffected.
- **SCIM is scoped to the organisation whose token authenticated.** Every SCIM
  endpoint ran unfiltered across the whole deployment, so an IdP holding the one
  shared token could list, patch, re-role and de-provision *any* customer's
  staff. Each organisation now has its own token, group membership can only bind
  users from the group's own organisation, and the deployment-wide
  `SCIM_BEARER_TOKEN` is refused once domains are registered, because at that
  point it names no one organisation.
- **The vector store is tenant-scoped.** `log_embeddings` held every customer's
  log templates in one untagged table; templates carry table names, endpoints
  and internal hostnames with only the variable parts stripped. Rows are now
  stamped with their owner, `search()` takes a required `tenant_id`, and a write
  without one is refused. A table created before this change is migrated in
  place and its existing rows are quarantined as unattributed.
- **Cold archives are partitioned by customer.** The archiver wrote one gzip per
  run containing every tenant's rows, which both commingled two companies' logs
  in a single object and made a customer's archived data impossible to delete
  without destroying everyone else's. Archives are now one object per customer
  per run under `archive/{logs,traces}/tenant=<id>/`.

- **AWS CloudWatch & Docker connectors are fail-closed in production.** Like the
  k8s connector, they now return a real `502` when the backend is unreachable
  instead of silently serving labeled `"simulated"` sample data (gated by
  `ALLOW_SIMULATED_CONNECTORS` / test mode).
- **SAML ACS can no longer mint a session from unverified input.** The endpoint
  was first made fail-closed (`501` outside the dev mock mode) and is now backed
  by real signature verification — see *Added* above. Unconfigured SAML still
  returns `501` rather than degrading to anything weaker.

### Fixed
- **Rehydrated spans keep their owner.** `hydrate_archive` did not restore
  `tenant_id`, so every span archived under a customer came back belonging to
  nobody.
- **AWS connector endpoints fail fast.** boto3 clients now carry explicit
  connect/read timeouts and bounded IMDS credential resolution, so an
  unreachable or credential-less AWS returns in ~2s instead of blocking ~20s on
  EC2 metadata-endpoint retries before the `502`.
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
- **Short-lived access tokens + rotating refresh tokens** (audit L1). Access
  tokens now default to 30 min (`ACCESS_TOKEN_EXPIRE_MINUTES`) instead of 24 h;
  login and SSO return a `refresh_token`, and `POST /auth/refresh` exchanges it
  for a new pair. Refresh tokens are single-use (revoked on rotation, so a
  stolen token works at most once) and are rejected as API access credentials.
- **Audit middleware no longer re-decodes the JWT** (audit L2). The authenticated
  identity is stamped on `request.state` by the auth dependency and read by the
  middleware, removing a per-request token decode and user lookup; a rejected
  request correctly falls back to the system-audit actor.
- **Migrated all Pydantic models from class-based `Config` to `ConfigDict`** and
  dropped the deprecated `Field(env=...)` kwarg (audit L3) — eliminates every
  Pydantic-v2 deprecation warning (27 → 0) ahead of the v3 removal.
- **SCIM PATCH now updates full attributes** (userName/emails, externalId, role)
  and both the path-scoped and no-path value-object operation shapes, not just
  the `active` toggle.
- **GitHub integration is honest**: `send_alert` creates a real issue via the
  REST API and returns actual delivery success (no more unconditional `True`);
  unimplemented log/deployment sync now raise instead of returning fabricated
  data.

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
