# SemanticOS — Scale & Operability Gap Analysis

**Date:** 2026-08-03
**Scope:** what breaks when a Fortune-500 runs this, *excluding* what
`ENTERPRISE_TRIAL_FINDINGS.md` already found and closed (tenant data isolation,
PII redaction, offboarding, SSO/SCIM scoping).

That audit asked *"can customer A see customer B's data?"* and answered it well.
This one asks three questions it never asked:

1. **Does it scale past one box?**
2. **Does it survive a bad day?** (node drain, ClickHouse down, pod restart, rollback)
3. **Can a team of 20 ship it safely for three years?** (API contract, supply chain, test gates)

The honest summary: the *security* posture is that of a product that has been
audited. The *availability and delivery* posture is that of a single-author
project. A large customer fails the second and third review, not the first.

---

## Findings

### A. Hard scaling ceiling — the API cannot run more than one replica

`deploy/helm/semanticos/values.yaml:16-19` says it outright: the API writes
local state to its data volume, so `>1` replica needs ReadWriteMany. The writers:

| State | Location |
|---|---|
| `live_stream.log` (+ rotation) | `src/denoiser/api/main.py:1085`, `api/compat.py:41`, `api/otlp.py:38` |
| Uploaded sources | `src/denoiser/api/main.py:1001`, `api/sources.py:31` |
| Query reads log files off disk | `src/denoiser/api/query.py:47` |

Consequences at enterprise scale:

- **No horizontal scale.** One pod is the whole ingest and query tier.
- **No zero-downtime deploy.** One replica means a rolling update is an outage.
- **RWX is not a fix.** NFS/EFS under a rotating append-log is a corruption and
  latency problem, not a scale-out plan.

`Dockerfile:70` mitigates with `--workers 4`, which multiplies the *next* bug.

### B. Per-process state that four uvicorn workers silently break

`--workers 4` means four independent Python processes, each with its own memory.
Three pieces of security-relevant state are process-local:

- **Login lockout** — `src/denoiser/api/main.py:278` `_login_attempts: dict`.
  Brute-force budget is multiplied by workers × replicas. 4 workers = 4× the
  attempts before lockout, and lockout does not survive a restart.
- **SAML assertion replay guard** — `src/denoiser/api/saml.py:136`. The docstring
  accepts "once per replica"; with `--workers 4` the real number is once per
  *worker*, four times larger than the documented risk.
- **OIDC discovery/JWKS cache** — `src/denoiser/api/oidc.py:36-37`. Key rotation
  at the IdP is picked up at different times per worker; a rotation window
  produces intermittent 401s that are unreproducible by design.

The rate limiter got this right (Redis-backed, `api/middleware.py:194`). These
three did not.

### C. The login path blocks the event loop

`src/denoiser/api/main.py:364` — `async def login(...)` takes a **synchronous**
SQLAlchemy `Session` via `Depends(get_db)`, and passlib/bcrypt hashing runs in
the same coroutine. A bcrypt verify is ~100 ms of pure CPU. In an `async def`
route that is 100 ms during which the worker serves **nothing** — not health
checks, not ingest, not the websocket fan-out.

At an enterprise login storm (Monday 9am, or an IdP-initiated re-auth), throughput
collapses to `workers / 0.1s` ≈ 40 logins/sec per pod, with every other request
queued behind it. `/auth/refresh` (`main.py:433`) is correctly sync (`def`), so
it runs in the threadpool — the inconsistency is the tell that this was not deliberate.

### D. Kubernetes chart has no availability primitives

`grep` across `deploy/helm/`: **no** `HorizontalPodAutoscaler`, **no**
`PodDisruptionBudget`, **no** `NetworkPolicy`, no pod anti-affinity, no
topology spread.

- No HPA → capacity is a manual `helm upgrade` during an incident.
- No PDB → a routine node drain (cluster upgrade, spot reclaim) takes the tier
  down. Combined with finding A (`replicaCount: 1`), a node drain is a **guaranteed
  outage**, not a risk.
- No NetworkPolicy → every pod in the namespace can reach ClickHouse and Postgres
  directly. A flat network inside the blast radius fails a modern network-segmentation
  control review.

Resource limits *are* set (`values.yaml:125-139`), but the API gets `1Gi` while
running 4 uvicorn workers — verify against real RSS before trusting it; the ML
imports are not free.

### E. Unauthenticated metrics endpoint

`/internal/metrics` (`api/main.py:838`) has no `Depends` and is quota-exempt
(`api/middleware.py:335`). It renders the platform's own request-rate/error/latency
series. Anyone who can reach the pod gets a live map of route names, traffic
volume and error rates. With no NetworkPolicy (finding D) that is namespace-wide.

Prometheus scrape endpoints being open is a common default — it is still a finding
in an enterprise review, and the fix is cheap (bind to a second port, or require
a scrape token).

### F. Dead-letter queue is written to ephemeral local disk

`src/denoiser/workers/ingestion_worker.py:27` → `data/dlq/ingestion_dlq.jsonl`.

The worker is otherwise the best-engineered part of the system — manual offset
commits, per-topic linger, backpressure ceiling, retry budget, poison-pill
quarantine. Then it quarantines to a **local file**:

- Worker pods have no PVC in the chart → **DLQ is lost on every restart.** The
  "quarantine" is a delete.
- **No replay tooling.** Nothing in `src/` or `scripts/` reads the DLQ back.
- **No alerting on DLQ depth.** Silent data loss has no signal.

For a customer whose logs are a compliance record, "we dropped it and told no one"
is the single worst outcome in the system.

### G. No API versioning

148 routes, all at the root (`/users`, `/incidents`, `/runs`, …). Only 5 references
to `/v1` exist, and they are the OTLP spec paths, not ours.

There is no way to make a breaking change without breaking every integrated
customer simultaneously, and no deprecation policy to point them at. Enterprise
procurement asks for the API stability commitment before signing; there is
currently no artifact that answers it.

### H. Supply chain and release gates are advisory, not blocking

- `pip-audit` — `continue-on-error: true` (`ci.yml:97`)
- `npm audit` — `continue-on-error: true` (`ci.yml:128`)
- Full-package `mypy` — `continue-on-error: true` (`ci.yml:63`); strict mypy is
  enforced on **6 files** out of ~200.
- No SBOM, no image signing (cosign), no provenance attestation.
- `values.yaml:5` defaults `tag: "latest"` — the comment says pin it, the default
  doesn't. Defaults are what gets deployed.

Net: a known-CVE dependency cannot fail a release. That is the specific control a
vendor-security questionnaire asks about by name.

### I. Frontend has no test gate

`web/e2e/` holds 7 Playwright specs (smoke, dashboards, explore, traces, alerts,
report, monkey) — real coverage, genuinely useful. CI never runs them: the
`frontend-test` job stops at `tsc`, `eslint`, `next build`. There are also zero
component/unit tests (`web/src`: no `*.test.*`).

A build that compiles is the entire frontend quality bar today.

### J. Test coverage is thinnest exactly where risk is highest

Coverage floor is 60% (`ci.yml:93`). The last run put `storage/clickhouse_store.py`
at **32%** — the module that owns every write of customer data, the tenant filter
on every read, and the retention deletes.

718 test functions is a real suite. It is pointed at the wrong place.

### K. Self-observability stops at metrics

`/internal/metrics` exists and is decent. Missing, for a platform whose whole
pitch is observability:

- No OpenTelemetry self-instrumentation (the code parses *other people's* OTLP;
  it emits no spans of its own) → no distributed trace of a slow query through
  API → ClickHouse.
- No shipped Grafana dashboards or Prometheus alert rules.
- No SLO defined for SemanticOS itself. The product computes error budgets for
  the customer's services and none for its own.
- Audit log has **no SIEM export** (`api/audit.py`: no syslog/CEF/webhook sink).
  Enterprise SOC requires audit events land in Splunk/Sentinel, not in a
  Postgres table behind a UI.

### L. DR is documented, never exercised

`docs/operations.md:257-305` has correct backup and restore-order instructions.
What is absent: automation (nothing schedules a backup), a restore drill, and any
stated **RPO/RTO**. Untested restores are the standard way companies discover
their backups don't work.

Also still open from the prior audit and unchanged: ClickHouse erasure is async,
so a GDPR deletion certificate is issued against an unconfirmed mutation.

### M. Analysis tier does not scale horizontally

`analysis/pipeline.py:44-47` caps a run at 500k lines and holds them in memory;
clustering + UMAP run in one process (`clustering/hdbscan_clusterer.py`). A large
enterprise emits that in minutes, not per run. There is no sharding, no partial/
incremental clustering, and no queue of analysis work across workers — the cap
protects the process, but it silently means **most of the data is never analysed**.
The cap is logged (`pipeline.py:226`), which is honest, but a customer paying for
"denoise everything" will read that log line as a defect.

### N. Codebase shape blocks a team

- `api/main.py` — 1441 lines / 58 KB holding auth, users, health, admin, metrics,
  vitals, ingest, websocket. Every parallel feature branch touches it; every one
  conflicts.
- 14 bare `print()` calls in library code (`src/denoiser`, excluding CLI) — output
  that bypasses structured logging, has no correlation id, and cannot be filtered
  in production.
- No `CODEOWNERS`, no PR template.
- `pyproject.toml:3` — `version = "0.1.0"`, classifier `Development Status :: 3 - Alpha`,
  on a product marketed as enterprise. No release tags.

---

## TODO — atomic tasks

Each task is independently completable: one owner, one concern, disjoint files.
No task blocks another, so any subset can be done in any order or in parallel.
Ordering within a priority is by value, not dependency.

### P0 — a large customer's deployment fails without these

- [ ] **T1. Move `live_stream.log` writes behind the existing store seam.**
  Files: `api/main.py:1085-1093`, `api/compat.py:41-46`, `api/otlp.py:38-39`.
  Replace direct file appends with the Redis/ClickHouse path already used by
  ingest. Acceptance: no API route opens a file under `data/` for write.

- [ ] **T2. Move uploaded sources to object storage.**
  Files: `api/main.py:1001`, `api/sources.py`, `api/query.py:47`.
  Write uploads to S3/MinIO (the archiver already has a client) and read them back
  by key. Acceptance: an upload on pod A is queryable on pod B.

- [ ] **T3. Set `api.replicaCount: 2` and add rolling-update strategy.**
  File: `deploy/helm/semanticos/values.yaml`, `templates/api-deployment.yaml`.
  `maxUnavailable: 0`, `maxSurge: 1`. Acceptance: `helm upgrade` serves traffic
  throughout. (Correct only once T1+T2 land — but the chart edit itself is
  independent and can be prepared and reviewed now.)

- [ ] **T4. Move login lockout counters to Redis.**
  File: `api/main.py:278-361`. Reuse the `SlidingWindowCounter` in
  `api/middleware.py:194`. Acceptance: a test proves the lockout threshold holds
  across two app instances sharing one Redis.

- [ ] **T5. Move the SAML replay guard to Redis.**
  File: `api/saml.py:125-159`. `SET NX` keyed on assertion id, TTL = assertion
  expiry. Acceptance: second use of an assertion id is refused by a *different*
  process; delete the "meaningful 90%" docstring caveat.

- [ ] **T6. Make `login` synchronous.**
  File: `api/main.py:363-407`. Change `async def` → `def` so FastAPI runs the
  bcrypt verify and sync `Session` in the threadpool. Acceptance: a load test at
  50 concurrent logins keeps `/health/live` under 50 ms.

- [ ] **T7. Give the ingestion worker a durable DLQ.**
  File: `workers/ingestion_worker.py:23-45`. Write quarantined batches to a Kafka
  `*.dlq` topic (or S3), not local disk. Acceptance: a worker pod restart loses
  no dead-lettered record.

- [ ] **T8. Add a PodDisruptionBudget for every deployment.**
  File: new `deploy/helm/semanticos/templates/pdb.yaml`.
  `minAvailable: 1` for api/web/ingestion/syslog. Acceptance: `kubectl drain`
  does not take the tier to zero.

- [ ] **T9. Require auth on `/internal/metrics`.**
  Files: `api/main.py:838-842`, `api/middleware.py:335`. Accept a scrape bearer
  token (`METRICS_TOKEN`) or bind metrics to a separate port not in the Service.
  Acceptance: an unauthenticated GET returns 401.

### P1 — required before signing an enterprise contract

- [ ] **T10. Introduce `/v1` and a deprecation policy.**
  Files: all of `src/denoiser/api/*.py` (router prefix), `web/src/lib/api*`,
  `docs/api.md`. Mount every existing router under `/v1`, keep unprefixed paths
  as permanent aliases, document the support window. Acceptance: `docs/api.md`
  states the versioning and deprecation commitment.

- [ ] **T11. Make dependency scanning blocking.**
  File: `.github/workflows/ci.yml:95-100, 126-129`. Drop `continue-on-error`, add
  an explicit dated ignore-list for accepted CVEs. Acceptance: a seeded vulnerable
  dependency fails the build.

- [ ] **T12. Publish an SBOM and sign images.**
  File: `.github/workflows/ci.yml` `build-and-push`. Add syft SBOM + cosign
  keyless signing + provenance attestation. Acceptance: `cosign verify` passes
  against a published tag.

- [ ] **T13. Stop defaulting to `:latest`.**
  File: `deploy/helm/semanticos/values.yaml:5`, `Chart.yaml` `appVersion`.
  Default the tag to the released version. Acceptance: a fresh `helm install`
  with no `--set` pulls an immutable tag.

- [ ] **T14. Add a NetworkPolicy set.**
  File: new `deploy/helm/semanticos/templates/networkpolicy.yaml`. Default-deny
  ingress; allow only web→api, api/worker→datastores, scraper→metrics port.
  Acceptance: an unrelated pod in the namespace cannot open a ClickHouse socket.

- [ ] **T15. Export audit events to a SIEM.**
  File: `api/audit.py`. Add a sink (syslog/CEF or webhook) fired on every audit
  write, configured per deployment. Acceptance: a configured endpoint receives an
  event within one second of a privileged action.

- [ ] **T16. Run Playwright in CI.**
  File: `.github/workflows/ci.yml` `frontend-test`. Boot API + web, run
  `npm run test:e2e`, upload the report artifact. Acceptance: a deliberately
  broken route fails the PR.

- [ ] **T17. Raise `clickhouse_store.py` to 80% coverage.**
  Files: `tests/test_clickhouse_store.py` (new), `storage/clickhouse_store.py`.
  Cover insert batching, tenant filter on every read, retention delete, and
  connection-failure paths. Acceptance: module coverage ≥80% and the CI floor
  raised to 65%.

- [ ] **T18. Add an HPA for api, worker and ingestion.**
  File: new `deploy/helm/semanticos/templates/hpa.yaml`, gated on
  `autoscaling.enabled`. CPU target 70%, min 2 / max 10. Acceptance: a load test
  triggers a scale-out event.

- [ ] **T19. Automate backups and prove a restore.**
  Files: new `deploy/helm/semanticos/templates/backup-cronjob.yaml`,
  `docs/operations.md:257`. Nightly `pg_dump` + `clickhouse-backup` to object
  storage; document measured RPO/RTO from one real restore drill. Acceptance:
  `docs/operations.md` records a dated, successful restore.

- [ ] **T20. Alert on DLQ depth and consumer lag.**
  Files: new `deploy/prometheus/alerts.yaml`, `api/observability.py`. Export
  `dlq_records_total` and Kafka consumer lag; alert on non-zero DLQ growth.
  Acceptance: dead-lettering a record pages within five minutes.

### P2 — sustainability; do these before the team grows

- [ ] **T21. Split `api/main.py` into routers.**
  File: `api/main.py` → `api/routers/{auth,users,health,admin,vitals,ingest,ws}.py`.
  Pure move, no behaviour change. Acceptance: no module over 400 lines; suite green.

- [ ] **T22. Replace the 14 `print()` calls with structured logging.**
  Files: `src/denoiser/**` excluding `cli/` (start at `clustering/hdbscan_clusterer.py:77`).
  Acceptance: `grep -rn "print(" src/denoiser --exclude-dir=cli` returns nothing;
  add a ruff rule (`T20`) to keep it that way.

- [ ] **T23. Grow the strict-mypy allowlist to the whole `api/` package.**
  File: `.github/workflows/ci.yml:53-60`. Add modules as they reach zero errors;
  target `api/` complete. Acceptance: the enforced list covers `src/denoiser/api/*.py`.

- [ ] **T24. Instrument the platform with OpenTelemetry.**
  Files: new `src/denoiser/telemetry/otel.py`, `api/main.py` lifespan. Emit spans
  for API → Postgres → ClickHouse; export to the configured OTLP endpoint.
  Acceptance: a slow query shows as one trace across all three tiers.

- [ ] **T25. Ship Grafana dashboards and an SLO for SemanticOS itself.**
  Files: new `deploy/grafana/*.json`, `docs/operations.md:306`. Availability and
  latency SLO for `/ingest` and `/query`, with error-budget alerts.
  Acceptance: `helm install` yields a working dashboard with no hand-editing.

- [ ] **T26. Shard the analysis pipeline.**
  Files: `analysis/pipeline.py:44-47, 223-226`, `workers/analysis_worker.py`.
  Partition a run by service/time window across N workers and merge cluster
  results, so the 500k cap is per-shard rather than per-run.
  Acceptance: a 5M-line run completes with no `max_lines` truncation warning.

- [ ] **T27. Add `CODEOWNERS`, a PR template, and release tagging.**
  Files: new `.github/CODEOWNERS`, `.github/pull_request_template.md`;
  `pyproject.toml:3` version + `Development Status :: 5 - Production/Stable`.
  Acceptance: `main` is tagged and the version matches the published image tag.

- [ ] **T28. Confirm ClickHouse erasure before certifying it.**
  File: `api/platform_admin.py` (tenant purge). Record the mutation id, expose a
  status endpoint, and only issue the erasure certificate once
  `system.mutations.is_done = 1`. Acceptance: the purge response carries a
  verifiable mutation reference. *(Closes the last "Still open" item in
  `ENTERPRISE_TRIAL_FINDINGS.md`.)*

- [ ] **T29. Support one IdP per organisation.**
  Files: `api/oidc.py`, `api/saml.py`, `api/sso.py`, `storage/db.py` (new
  `tenant_idp_config` table + migration). Move IdP config from deployment env
  vars into per-organisation rows, routed by the existing email-domain mapping.
  Acceptance: two organisations authenticate through two different IdPs in one
  deployment. *(Closes the other "Still open" item.)*

- [ ] **T30. Gate performance in CI.**
  Files: `scripts/loadtest.py`, new `.github/workflows/perf.yml`. Nightly run
  against an ephemeral stack; fail on p95 regression beyond a recorded baseline.
  Acceptance: an artificial 2× slowdown fails the nightly job.
