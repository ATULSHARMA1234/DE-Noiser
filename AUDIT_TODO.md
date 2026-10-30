# Audit Remediation — Task List

Derived from `AUDIT_REPORT.md` (2026-08-05). Every task is atomic: one discrete change, independently verifiable, one commit.

**ID key:** `WT` working tree · `SEC` security · `BILL` billing/metering · `DATA` data integrity · `PRIV` privacy · `REL` reliability · `PERF` performance · `OPS` operations · `LIC` licensing · `VER` verification · `DEC` decision (no code) · `COM` commercial layer

**Effort:** S = under an hour · M = half day to a day · L = multi-day

Dependencies are listed where a task cannot start until another lands.

---

## Block 0 — Working tree, before you commit

The uncommitted diff contains two defects. These block the commit, not the launch.

- [ ] **WT-1** · S · In `ingest_otlp_traces` ([otlp.py:147-167](src/denoiser/api/otlp.py#L147)), move the ClickHouse `insert_traces` call above `db.commit()`, and return 503 before the commit runs.
- [ ] **WT-2** · S · Add `UniqueConstraint("tenant_id", "trace_id", "span_id", name="uq_spans_identity")` to the `Span` model ([db.py:186](src/denoiser/storage/db.py#L186)). *Depends on WT-4.*
- [ ] **WT-3** · S · Write an Alembic migration that deletes duplicate `spans` rows, keeping the lowest `id` per `(tenant_id, trace_id, span_id)`.
- [ ] **WT-4** · S · Extend that migration to add the unique constraint after the dedupe. *Depends on WT-3.*
- [ ] **WT-5** · S · Change the Postgres span write in `ingest_otlp_traces` to an upsert (`ON CONFLICT DO NOTHING`). *Depends on WT-2.*
- [ ] **WT-6** · S · In `_normalise_payload` ([llm.py:41](src/denoiser/intelligence/llm.py#L41)), replace `return {}` with the local heuristic fallback so a non-dict payload never silently skips incident creation.
- [ ] **WT-7** · S · Test: a `/v1/traces` POST that 503s leaves no duplicate rows in `spans` on retry.
- [ ] **WT-8** · S · Test: a non-dict LLM payload produces a fallback summary, not an empty dict.

Nothing else in the diff needs changing. The `failure_domain` coercion and migration `d0a6b41e73c5` are correct as written.

---

## Block 1 — Day one. Closes every CRITICAL except billing.

Ordered. SEC-1 first because it is a live breach path.

### C-4 — Runbook SSRF and credential exfiltration

- [ ] **SEC-1** · S · Call `validate_destination(url)` at the top of the `webhook` branch in `execute_runbook_step` ([engine.py:41](src/denoiser/automation/engine.py#L41)).
- [ ] **SEC-2** · S · Call `validate_destination(slack_url)` in the `slack_notification` branch ([engine.py:51](src/denoiser/automation/engine.py#L51)).
- [ ] **SEC-3** · S · Call `validate_destination(jira_url)` in the `jira_issue` branch **before** `HTTPBasicAuth(...)` is constructed ([engine.py:67](src/denoiser/automation/engine.py#L67)), so a rejected host never sees the token.
- [ ] **SEC-4** · S · Confirm no branch writes `response.text` into `execution_logs`; if any does, replace with status code only.
- [ ] **SEC-5** · S · Call `validate_destination` on step URLs in `create_runbook` ([runbooks.py:56](src/denoiser/api/runbooks.py#L56)) for a fast save-time failure.
- [ ] **SEC-6** · S · Same in `update_runbook` ([runbooks.py:82](src/denoiser/api/runbooks.py#L82)).
- [ ] **SEC-7** · S · Test: a runbook step targeting `169.254.169.254` is refused at execution.
- [ ] **SEC-8** · S · Test: a `jira_issue` step with a blocked `jira_url` sends no request and no credential.

### C-3 — Spans not attributed to a tenant

- [ ] **BILL-1** · S · Normalise `verify_ingest_auth` ([auth.py:267](src/denoiser/api/auth.py#L267)) to return `int` on every branch — it currently returns `str` at line 295 and `int` at 280 and 305.
- [ ] **BILL-2** · S · Update the `insert_logs` / `insert_traces` call sites that assumed a `str` tenant id. *Depends on BILL-1.*
- [ ] **BILL-3** · S · Set `tenant_id=tenant_id` on the `Span(...)` construction ([otlp.py:112](src/denoiser/api/otlp.py#L112)). *Depends on BILL-1.*
- [ ] **BILL-4** · S · Migration: delete existing `spans` rows with `tenant_id IS NULL` — they are unattributable and must not be guessed.
- [ ] **BILL-5** · S · Test: after an OTLP trace POST, the persisted `Span` row has a non-NULL `tenant_id` matching the API key's tenant.
- [ ] **BILL-6** · S · Test: `aggregate_billing` reports non-zero `total_traces_ingested` for a tenant that posted traces.

### C-5 — 32-bit overflow on usage counters

- [ ] **BILL-7** · S · Change `total_logs_ingested`, `total_bytes_ingested`, `total_traces_ingested` to `BigInteger` on the `BillingMeter` model ([db.py:454](src/denoiser/storage/db.py#L454)).
- [ ] **BILL-8** · S · Migration: `ALTER COLUMN ... TYPE BIGINT` for all three. *Depends on BILL-7.*
- [ ] **BILL-9** · S · Move the `db.commit()` at [billing_worker.py:132](src/denoiser/workers/billing_worker.py#L132) inside the per-tenant loop so one tenant's failure cannot discard every other tenant's meter.
- [ ] **BILL-10** · S · Test: a tenant metering above 2^31 bytes commits without error and does not affect other tenants' rows.

### C-2 — Metering window is empty

- [ ] **BILL-11** · S · Add a `day` parameter to `aggregate_billing` ([billing_worker.py:39](src/denoiser/workers/billing_worker.py#L39)), defaulting to yesterday.
- [ ] **BILL-12** · S · Bind the ClickHouse query to that day: `toDate(timestamp) = {day:Date}` ([billing_worker.py:83](src/denoiser/workers/billing_worker.py#L83)). *Depends on BILL-11.*
- [ ] **BILL-13** · S · Bound the `Span` query to `[day_start, day_start + 1 day)` ([billing_worker.py:93](src/denoiser/workers/billing_worker.py#L93)). *Depends on BILL-11.*
- [ ] **BILL-14** · S · Move the beat schedule to `crontab(minute=15, hour=0)` ([analysis_worker.py:406](src/denoiser/workers/analysis_worker.py#L406)) so late writes for the closed day have settled.
- [ ] **BILL-15** · S · Add a CLI entry point to re-run metering for an arbitrary past day. *Depends on BILL-11.*
- [ ] **BILL-16** · S · Test: data ingested "yesterday" produces a non-zero meter. This is the test whose absence let C-2 ship.

### Quick wins

- [ ] **REL-1** · S · Add `timeout=10` to `requests.post` in [slack.py:60](src/denoiser/integrations/slack.py#L60).
- [ ] **REL-2** · S · Add a CI grep that fails on `requests.(get|post|put)\(` without a `timeout` argument.

### H-3 — Security headers

- [ ] **OPS-1** · S · Write `SecurityHeadersMiddleware` setting `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`.
- [ ] **OPS-2** · S · Register it as the outermost middleware in [main.py](src/denoiser/api/main.py#L52). *Depends on OPS-1.*
- [ ] **OPS-3** · S · Add a `Content-Security-Policy` in report-only mode. *Depends on OPS-1.*
- [ ] **OPS-4** · M · Review CSP reports, then switch to enforcing. *Depends on OPS-3.*
- [ ] **OPS-5** · S · Mirror the same headers as `add_header` directives in `nginx/nginx.conf`.
- [ ] **OPS-6** · S · Redirect `:80` to HTTPS in the `Caddyfile` instead of serving the app from it.
- [ ] **OPS-7** · S · Test asserting every header is present on an API response.

---

## Block 2 — Week one

### H-1 — Retention destroys data before archival can save it

- [ ] **DATA-1** · S · Call `S3ArchiverEngine.run_archival()` at the start of `aggregate_billing`, before any `cleanup_old_data` call.
- [ ] **DATA-2** · S · Remove the 04:00 `trigger_sso_s3_db_archival` job from [scheduler.py:125](src/denoiser/api/scheduler.py#L125). *Depends on DATA-1.*
- [ ] **DATA-3** · S · Test: logs crossing the retention boundary appear in the archive before they are deleted from ClickHouse.

### H-2 — Unredacted PII on the OTLP path

- [ ] **PRIV-1** · M · Apply `build_redactor()` inside `insert_logs` ([clickhouse_store.py](src/denoiser/storage/clickhouse_store.py)) so every ingest path inherits it.
- [ ] **PRIV-2** · S · Remove the now-redundant per-route redaction from [routers_ingest.py:58](src/denoiser/api/routers_ingest.py#L58). *Depends on PRIV-1.*
- [ ] **PRIV-3** · S · Redact before the raw-log-sink write at [otlp.py:44](src/denoiser/api/otlp.py#L44).
- [ ] **PRIV-4** · S · Redact before the Redis publish at [otlp.py:67](src/denoiser/api/otlp.py#L67).
- [ ] **PRIV-5** · S · Test: an email address POSTed to `/v1/logs` is absent from what reaches the store.
- [ ] **PRIV-6** · S · Test: the same for the Kafka consumer path ([ingestion_worker.py:253](src/denoiser/workers/ingestion_worker.py#L253)).

### M-2 — Duplicate billing rows

- [ ] **DATA-4** · S · Migration: delete duplicate `billing_meters` rows, keeping the highest `id` per `(tenant_id, date)`.
- [ ] **DATA-5** · S · Migration: add `UniqueConstraint("tenant_id", "date")`. *Depends on DATA-4.*
- [ ] **DATA-6** · S · Switch the meter write to a Postgres upsert (`on_conflict_do_update`). *Depends on DATA-5.*

### Verification — highest value on this page

- [ ] **VER-1** · M · Run the full suite against live ClickHouse, Kafka, Redis, and Postgres containers. Record what fails. Every ClickHouse finding in the report is read, not observed.
- [ ] **VER-2** · M · Perform a full backup and restore into a clean environment. Confirm the restored system serves requests.
- [ ] **VER-3** · S · Read `.github/pip-audit-ignore.txt`; confirm each entry still has a valid reason and an unexpired review date.
- [ ] **VER-4** · S · Run `npm ci && npm run build && npm run dev` in `web/`. Confirm the app builds and loads.
- [ ] **VER-5** · M · Complete Phase 8 against the running frontend: error messages, keyboard/contrast basics, mobile breakpoints, onboarding dead ends, broken links, README accuracy. *Depends on VER-4.*
- [ ] **VER-6** · S · Confirm the `SLD_LLM_API_KEY` in the local `.env` is not a production or shared key. Rotate if it has ever left this machine.
- [ ] **VER-7** · S · Confirm what `LLM_BASE_URL` points at in every real deployment. If it is not a local model, customer log content is leaving the customer's infrastructure.
- [ ] **VER-8** · S · Cross-check `ENTERPRISE_TRIAL_FINDINGS.md` and `SCALE_READINESS_TODO.md` against this list; remove anything already tracked there.

---

## Block 3 — Before the first paying customer

### DEC — Decisions that block work. No code.

- [ ] **DEC-1** · Decide the pricing model. Per-GB-ingested is the honest one — it is what you already meter and what your costs track. *Blocks the whole COM block.*
- [ ] **DEC-2** · Decide hosted or on-prem. ~~This determines whether Redpanda's BSL 1.1 is acceptable. *Blocks LIC-2.*~~ It no longer blocks LIC-2 — the default broker is Apache Kafka either way. Still open for Redis 7.4+ (RSALv2/SSPLv1) and MinIO (AGPL-3.0).
- [ ] **DEC-3** · Decide which features are paid. Today every feature is available to a `free` tenant, so there is nothing to sell. *Blocks COM-10.*

### C-1 — Billing system

- [ ] **COM-1** · M · Add a `Plan` model: name, included volume, overage price, currency, minor units as integers. *Depends on DEC-1.*
- [ ] **COM-2** · M · Add a `Subscription` model: tenant, provider customer id, provider subscription id, plan, status, period start/end. *Depends on DEC-1.*
- [ ] **COM-3** · S · Migration for both. *Depends on COM-1, COM-2.*
- [ ] **COM-4** · M · Add a `ProcessedWebhookEvent` table keyed on the provider's event id, for replay safety.
- [ ] **COM-5** · M · Stripe Checkout flow for signup. *Depends on COM-3.*
- [ ] **COM-6** · M · Stripe Billing Portal link for plan change and cancellation. *Depends on COM-3.*
- [ ] **COM-7** · L · Webhook endpoint handling subscription lifecycle events, guarded by COM-4. *Depends on COM-4.*
- [ ] **COM-8** · M · Push metered usage to Stripe from the metering pass. *Depends on COM-3, BILL-11.*
- [ ] **COM-9** · M · Entitlement middleware keyed on `Subscription.status`, not `Tenant.tier`, enforced at the router dependency. *Depends on COM-3.*
- [ ] **COM-10** · M · Apply feature gates to the routers chosen in DEC-3. Server-side only. *Depends on COM-9, DEC-3.*
- [ ] **COM-11** · S · Test: a replayed webhook event is a no-op.
- [ ] **COM-12** · S · Test: a subscription in `past_due` loses access to gated routes.
- [ ] **COM-13** · S · Test: a cancelled subscription retains read access through the end of its paid period.

### H-5 — Data-subject erasure

Do PRIV-1 first. If PII never lands unredacted, most of this problem disappears.

- [ ] **PRIV-7** · M · Scope what identifiable data survives redaction, in ClickHouse `semantic_logs` and in `spans.attributes`. *Depends on PRIV-1.*
- [ ] **PRIV-8** · L · Subject-erasure endpoint taking a tenant plus a subject identifier, running `ALTER TABLE ... UPDATE replaceAll(...)` scoped to that tenant. *Depends on PRIV-7.*
- [ ] **PRIV-9** · M · Equivalent erasure over `spans.attributes`. *Depends on PRIV-7.*
- [ ] **PRIV-10** · S · Record subject erasures in `ErasureRecord` using the existing certificate mechanism. *Depends on PRIV-8.*
- [ ] **PRIV-11** · M · Write the DPA: retention periods, sub-processor list (S3/MinIO, and the LLM endpoint if not local), assist-with-DSR clause. *Depends on VER-7.*

### LIC — Licensing

- [ ] **LIC-1** · S · Generate and commit `THIRD_PARTY_LICENSES.md` from `uv export` + `pip-licenses`, plus the frontend tree. Procurement asks for it by name and you currently satisfy no attribution requirement.
- [x] **LIC-2** · M · ~~If DEC-2 says hosted:~~ Apache Kafka (`apache/kafka:3.9.0`, KRaft) is now the default broker in `docker-compose.yml`; Redpanda moved to an opt-in `docker-compose.redpanda.yml`. Done **without** DEC-2, deliberately: a default that is only correct on-prem makes the licensing decision for the deployer and expires silently if a hosted tier ever appears. Verified by running it — broker healthy, aiokafka produce/consume round-trip, topic auto-created.
- [x] **LIC-3** · S · Document the Redpanda BSL constraint in `DEPLOY.md`. — plus the switch-back command and a drain-before-you-switch procedure for existing installs.
- [ ] **LIC-4** · S · Record the `psycopg2-binary` LGPL position — dynamically linked, hosted service, no obligation triggered — so the answer exists before someone asks.

---

## Block 4 — Backlog. Real, not urgent.

- [ ] **PERF-1** · S · Replace the per-trace root-span lookup in [tracing.py:259](src/denoiser/api/tracing.py#L259) with one `IN` query into a dict.
- [ ] **PERF-2** · S · Add `scope.predicate(Span)` to both root-span queries at [tracing.py:259-262](src/denoiser/api/tracing.py#L259). *Depends on BILL-3.*
- [ ] **REL-3** · S · Batch the archiver's span load with `yield_per`, writing and pruning per batch ([archiver.py:97](src/denoiser/storage/archiver.py#L97)).
- [ ] **REL-4** · M · Move archival out of the API process into the Celery worker, so a memory spike costs a worker rather than the serving path. *Depends on REL-3, DATA-1.*
- [ ] **REL-5** · S · Cap the span count per `/v1/traces` request and return 413 above it ([otlp.py:88](src/denoiser/api/otlp.py#L88)).
- [x] **DATA-7** · M · Scope the email existence check in `create_user` to the caller's tenant ([routers_users.py:89](src/denoiser/api/routers_users.py#L89)).
- [x] **DATA-8** · M · Change the `users.email` constraint to `UniqueConstraint("tenant_id", "email")`, with migration. *Depends on DATA-7.* — migration `e1b52c8a904f`; a partial unique index keeps the old global rule for rows with no tenant.
- [x] **DATA-9** · M · Update login, SSO, and SCIM to resolve users by `(tenant, email)` rather than email alone. *Depends on DATA-8.* — tokens carry a `tid` claim; `/auth/login` takes an optional `tenant` and only needs it when one address *and* one password match twice; the quota middleware buckets on `tid`.
- [x] **DATA-10** · S · Test: two tenants can each hold a user with the same email address. *Depends on DATA-9.* — `tests/test_shared_email_across_orgs.py`, 11 cases.
- [ ] **OPS-8** · S · Templatise the `Caddyfile` hostname from an environment variable instead of the hardcoded `20.2.90.156.nip.io`.
- [ ] **OPS-9** · S · Move the real deployment instructions into `DEPLOY.md`. *Depends on OPS-8.*

---

## Counts

| Block | Tasks | Rough effort |
|---|---|---|
| 0 — Working tree | 8 | Half a day |
| 1 — Day one | 33 | 1–2 days |
| 2 — Week one | 17 | 3–4 days |
| 3 — Before first customer | 28 | 3–4 weeks |
| 4 — Backlog | 12 | 1 week |

Blocks 0 and 1 close every CRITICAL except billing. Block 3 is the launch.
