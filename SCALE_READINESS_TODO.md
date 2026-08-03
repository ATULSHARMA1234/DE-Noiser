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

## TODO — status

All 30 tasks were worked. **29 landed complete, 1 needed no change** — T4 was
based on a finding that turned out to be wrong; see the bottom of this section.

Suite: **1004 passing** (860 at the start of this work). Lint and the enforced
type-check gates are green; both were red beforehand.

### P0 — the deployment blockers

| | Task | Status |
|---|---|---|
| T1 | `live_stream.log` behind a shared sink | done |
| T2 | Uploaded sources in object storage, disk as cache | done |
| T3 | API defaults to 2 replicas, `maxUnavailable: 0`, anti-affinity | done |
| T4 | Login lockout on Redis | **already correct** — see below |
| T5 | SAML replay guard on Redis | done |
| T6 | Login off the event loop | done |
| T7 | Dead-letter queue on a Kafka topic | done |
| T8 | PodDisruptionBudgets | done |
| T9 | `/internal/metrics` requires a scrape token | done |

### P1 — before signing a contract

| | Task | Status |
|---|---|---|
| T10 | `/v1` on every route + deprecation policy | done |
| T11 | Dependency scanning is blocking | done |
| T12 | SBOM, cosign signing, provenance | done |
| T13 | No `:latest`, in the pipeline or the chart | done |
| T14 | NetworkPolicy (opt-in) | done |
| T15 | Audit events to a SIEM (syslog/CEF) | done |
| T16 | Playwright runs in CI | done |
| T17 | `clickhouse_store` coverage | done — 69% → **95%**, floor 60 → 73 |
| T18 | HPA (opt-in) | done |
| T19 | Backup CronJob, RPO stated, drill documented | done |
| T20 | Alerts on dead-lettering and consumer lag | done |

### P2 — sustainability

| | Task | Status |
|---|---|---|
| T21 | Split `api/main.py` | done — 1,500 → **253 lines** |
| T22 | No `print()` in library code, ruff `T20` rule | done |
| T23 | Strict-mypy allowlist | done — 6 → **17 modules** |
| T24 | OpenTelemetry self-instrumentation | done |
| T25 | Grafana dashboard + platform SLO | done |
| T26 | Analysis pipeline streams instead of capping | done |
| T27 | CODEOWNERS, PR template, release versioning | done |
| T28 | Erasure certified against mutation completion | done |
| T29 | Per-organisation IdP | done |
| T30 | Nightly performance gate | done |

T28 and T29 close both items `ENTERPRISE_TRIAL_FINDINGS.md` recorded as
**"Still open"**.

---

### T4 — no change was needed

The original finding was wrong. `_login_attempts` is the *fallback* used when
Redis is unreachable, not the primary store: `_login_failures`,
`_record_login_failure` and `_clear_login_failures` were already Redis-backed
([main.py:297-339](src/denoiser/api/main.py#L297-L339)). The lockout was
already shared across workers and replicas. Left alone.

### T21 — complete

`api/main.py` went from ~1,500 lines to **253**, and is now imports, the
middleware stack, the lifespan and router registration. Twelve routers came out
of it, the largest 310 lines.

Pure moves, verified by capturing the served route table before and after: 157
routes, same paths, same methods, nothing added and nothing removed. That check
is what makes a refactor of this size reviewable — reading 1,200 relocated lines
for a transcription error is not.

Two things were *not* pure moves and are called out rather than folded in:
`_human_size` and `_estimate_lines` sat beside the query endpoint but serve the
sources listing, and went with their caller; and `if __name__ == "__main__"` had
ended up trailing the alert-trigger handler and went back to the application
module.

---

## Six defects found by actually running things

None of these were visible by reading. Each was found by executing the thing
that was previously only described — which is the argument for the verification
work in the first place.

**Writing the tests (P0/P1):**

- **The raw-log object key was not collision-proof.** Unique only if a process
  held exactly one sink — true then, and not a property worth betting a
  customer's logs on. Now carries a random suffix.
- **The CEF header escape did not strip newlines.** A crafted request path could
  terminate one audit record and begin a second, attacker-authored one inside
  the customer's SIEM.

**Rendering the Helm chart:**

- **`ingress.enabled=true` produced nothing at all.** `values.yaml` had
  documented className, hosts, paths, a per-path backend selector and TLS since
  the chart was written, and no template consumed any of it. `helm install`
  reported success and created no Ingress. `helm lint` passes on a chart whose
  values are wired to nothing.

**Running the restore drill:**

- **ClickHouse was not being backed up at all.** The CronJob dumped tables named
  `logs` and `spans`; the real tables are `semantic_logs` and
  `semantic_traces`. Both `SHOW CREATE TABLE` calls failed, hit the `||
  continue` guard, and the job reported success. The verification step counted
  the Postgres dump and passed.
- **The ClickHouse schema dump was unusable.** It used the default TabSeparated
  format, which escapes the newlines inside the single String column `SHOW
  CREATE TABLE` returns — so the saved DDL was one line containing literal
  `\n`, and ClickHouse rejects it on restore with a syntax error.

**Running the load test:**

- **`/ingest` was capped at 100 requests per minute, hardcoded.** The first run
  of the gate returned 100 successes and 5,052 rate-limited responses. Beyond
  breaking the gate, that ceiling is far too low for real shippers: one Fluent
  Bit agent flushing once a second exhausts it in under two minutes, and behind
  a proxy every shipper shares one bucket. Now configurable, defaulting to
  6,000/minute.

## Corrections to the analysis above

Three figures in the findings section were wrong when first written and are
corrected here rather than quietly edited out:

- **`clickhouse_store` was at 69%, not 32%.** The 32% came from a stale
  `.coverage` file left by a partial run.
- **There were 3 bare `print()` calls in library code, not 14.** That count
  caught `fingerprint` and Rich's `console.print`.
- **The login lockout was not process-local** (T4, above).

## Verification performed

Everything previously listed as unverified has now been run.

| | Result |
|---|---|
| **Helm chart** | `helm lint` clean. Rendered in two shapes — defaults, and every optional feature at once — and validated against real Kubernetes 1.29 schemas with `kubeconform`: **18 and 27 resources, all valid.** The three misconfiguration guards are asserted to actually refuse. CI runs all of this now (`helm-chart` job), because a template behind an `if` is one nobody renders. |
| **Restore drill** | Run against live Postgres and ClickHouse: seed, back up with the CronJob's own commands, drop both schemas, restore, verify. **1,000,000 rows restored in 1.34 s** from a 29 MB backup taken in 0.48 s. Row counts, record content and tenant attribution all verified. Recorded, dated, in `docs/operations.md`. Repeatable via `scripts/restore_drill.py`. |
| **Performance gate** | Measured end to end: **22,755 logs/s, p95 223 ms, zero errors over 1,366,000 logs.** All four gate paths verified — no baseline passes (the arming path), a matching run passes, halved throughput and tripled p95 both fail, and errors under load invalidate the run. |
| **Suite** | **1,004 passing.** Lint and the enforced type-check gates green. |

### One thing deliberately not done

**No performance baseline is committed to this repository.** A throughput figure
is a property of the machine that produced it. The 22,755 logs/s above came from
an M-series laptop; a shared CI runner is several times slower, so committing
that number would fail the nightly gate every night — and the first response to a
gate that always fails is to switch it off.

The baseline instead lives in the GitHub Actions cache, keyed by runner image.
The first nightly run on a new runner records its own and arms the gate with no
step for anyone to remember, and a runner-image upgrade re-baselines rather than
reporting a fake regression. `workflow_dispatch` with `reset_baseline`
re-records deliberately.
