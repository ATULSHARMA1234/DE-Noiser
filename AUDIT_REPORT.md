# SemanticOS — Pre-Launch Technical Audit

**Date:** 2026-08-05
**Branch audited:** `feat/enterprise-sso-syslog-otlp` @ `97ad799` + uncommitted working tree
**Scope:** Phases 0–8 as briefed. Read/run/analyze only — no code was modified.

---

## 1. Verdict

**Do not ship as a paid product yet. Ship as a free/design-partner deployment today if you want.**

The engineering craft here is well above average for a solo-built platform. Multi-tenancy is enforced through a single, correct abstraction (`denoiser.api.scope`), SSRF has a real guard, production config is validated at boot and refuses to start when unsafe, and 1014 tests pass with zero failures. That is a genuinely defensible foundation.

But you asked what breaks when *paying* users touch it, and the answer is blunt: **there is no billing system.** Not "billing has bugs" — there is no payment provider, no subscription state, no invoice, no webhook, no proration, no dunning. `BillingMeter` is a usage table, and the job that fills it is scheduled at exactly the moment its own query window is empty, so it has been recording zeros. Separately, every span written through the documented OTLP endpoint lands with `tenant_id = NULL`, so trace usage meters as zero for every customer regardless. You cannot invoice from this data.

On top of that: the runbook execution engine will POST to any URL a tenant analyst supplies, with no SSRF guard and with Jira credentials attached — the guard exists, it just was never wired into that path. And free-tier customer logs are hard-deleted from ClickHouse four hours before the job that would have archived them runs.

Fix the eight launch blockers below and this is a credible commercial product. The gap is not depth of engineering; it is that the commercial layer was never built and two data paths quietly bypass work that was done correctly elsewhere.

---

## 2. Launch blockers

1. **C-1** — Build a billing system, or do not charge. There is none.
2. **C-2** — Usage metering runs at 00:00 UTC and queries `toDate(now())`, so it meters ~0 seconds of the *new* day and never meters the completed one. Every `BillingMeter` row is effectively zero.
3. **C-3** — `/v1/traces` never stamps `tenant_id` on persisted spans. Trace usage meters as 0 for every tenant; spans are also unattributed for isolation and erasure.
4. **C-4** — Runbook engine has no SSRF guard and forwards Jira credentials to a tenant-supplied host. Any ANALYST can reach cloud metadata and internal services, and exfiltrate the Jira token.
5. **C-5** — `total_bytes_ingested` is a 32-bit `Integer`. Any tenant ingesting >2.1 GB/day makes the whole metering pass fail for that tenant.
6. **H-1** — Retention (00:00) hard-deletes free-tier ClickHouse data before archival (04:00) can write it to S3. Silent customer data loss.
7. **H-2** — `/v1/logs` stores log bodies **unredacted**; `/ingest` redacts. The documented enterprise path is the one that keeps PII at rest.
8. **H-3** — No security headers anywhere (app, nginx, or Caddy). No HSTS, CSP, `X-Frame-Options`, or `X-Content-Type-Options` on a credentialed API and dashboard.

---

## 3. Findings table

| ID | Severity | Area | Location | Summary |
|----|----------|------|----------|---------|
| C-1 | CRITICAL | Commercial | *(absent)* | No billing system exists at all — no provider, subscriptions, invoices, or webhooks |
| C-2 | CRITICAL | Billing | `src/denoiser/workers/analysis_worker.py:406` | Metering scheduled at 00:00 UTC but queries `toDate(now())` — meters an empty day |
| C-3 | CRITICAL | Data / Billing | `src/denoiser/api/otlp.py:112` | Persisted spans never stamped with `tenant_id`; trace metering always 0 |
| C-4 | CRITICAL | Security | `src/denoiser/automation/engine.py:44` | Runbook webhook/Jira actions bypass the SSRF guard and leak credentials |
| C-5 | CRITICAL | Billing | `src/denoiser/storage/db.py:461` | `total_bytes_ingested` is 32-bit; >2.1 GB/day breaks the tenant's metering |
| H-1 | HIGH | Data | `src/denoiser/api/scheduler.py:125` | Retention deletes free-tier data 4h before archival runs |
| H-2 | HIGH | Privacy | `src/denoiser/api/otlp.py:53` | OTLP log ingest stores bodies unredacted; `/ingest` redacts |
| H-3 | HIGH | Security | `src/denoiser/api/main.py:52` | No security-response-header middleware, and none in nginx or Caddy |
| H-4 | HIGH | Correctness | `src/denoiser/api/otlp.py:150` | Postgres commit precedes the 503, so exporter retries duplicate spans |
| H-5 | HIGH | Compliance | `src/denoiser/api/platform_admin.py:278` | Erasure is tenant-wide only; no per-data-subject deletion (GDPR Art. 17) |
| M-1 | MEDIUM | Reliability | `src/denoiser/integrations/slack.py:60` | `requests.post` with no timeout — worker thread can hang indefinitely |
| M-2 | MEDIUM | Data | `src/denoiser/storage/db.py:454` | `billing_meters` has no unique constraint on `(tenant_id, date)` |
| M-3 | MEDIUM | Performance | `src/denoiser/api/tracing.py:259` | N+1: one extra root-span query per trace in the Postgres fallback |
| M-4 | MEDIUM | Reliability | `src/denoiser/storage/archiver.py:97` | Archiver loads every expired span into memory with `.all()` |
| M-5 | MEDIUM | Correctness | `src/denoiser/api/routers_users.py:89` | Global email uniqueness leaks cross-tenant existence and blocks legitimate signups |
| M-6 | MEDIUM | Licensing | `pyproject.toml:47` | `psycopg2-binary` is LGPL; Redpanda image is BSL 1.1 — both need a decision |
| M-7 | MEDIUM | Correctness | `src/denoiser/intelligence/llm.py:39` | New `_normalise_payload` silently discards a non-dict payload, skipping the incident |
| L-1 | LOW | Reliability | `src/denoiser/api/otlp.py:84` | `/v1/traces` builds unbounded in-memory lists from one request body |
| L-2 | LOW | Ops | `Caddyfile:20` | Production hostname is a hardcoded `nip.io` address of a specific IP |

---

## 4. Detailed findings

### CRITICAL

---

#### C-1 — There is no billing system

**Severity:** CRITICAL · **Area:** Commercial readiness · **Effort:** L

**Location:** absent from the codebase. Verified by search across `src/`, `web/app/`, `web/components/`, and `tests/` for `stripe|subscription|invoice|billing`. The only hits are the usage-metering table and worker:

```
src/denoiser/workers/billing_worker.py   # usage aggregation only
src/denoiser/storage/db.py:454           # class BillingMeter — counters, no money
```

`BillingMeter` records `total_logs_ingested`, `total_bytes_ingested`, `total_traces_ingested`. That is it. There is no payment provider integration, no `Subscription` or `Plan` model, no price, no currency, no invoice, no webhook handler, no idempotency key, no dunning, no proration, no cancellation, no refund path.

Entitlements are equally thin. `Tenant.tier` (`free|pro|enterprise`, `src/denoiser/storage/db.py:428`) controls exactly two things:

- request quota — `src/denoiser/api/middleware.py:411`
- retention days — `src/denoiser/workers/billing_worker.py:31-36`

Every *feature* — SSO, SCIM, tracing, runbooks, notebooks, SLOs, CI correlation — is available to a `free` tenant. There is nothing to sell that a free account does not already have.

**Failure scenario:** You sign your first paying customer. There is no mechanism to take their money, no record that they are entitled to anything a free account isn't, and no usage figure you could put on an invoice (see C-2, C-3, C-5). Manual invoicing from `BillingMeter` produces a bill for zero.

**Impact:** No revenue. Any "Pro" or "Enterprise" pricing page you publish today is unbacked by enforcement.

**Recommended fix:** Decide the model first — per-GB-ingested is the honest one given what you meter. Then:
1. Add `Subscription` (tenant_id, provider_customer_id, provider_subscription_id, plan, status, current_period_start/end) and `Plan` (name, included_gb, overage_price_per_gb, currency).
2. Integrate Stripe: Checkout for signup, Billing Portal for plan changes/cancellation, metered usage records pushed from the metering pass.
3. Webhook endpoint with an idempotency table keyed on Stripe's `event.id` — Stripe redelivers, and every handler must be replay-safe.
4. Server-side entitlement middleware keyed on `Subscription.status`, not `Tenant.tier`, so a failed payment actually degrades access. Gate at the router dependency, never in the frontend.
5. Money as `Numeric(12,2)` or integer minor units. Never `Float`.

---

#### C-2 — Usage metering runs at the one moment its query window is empty

**Severity:** CRITICAL · **Area:** Billing · **Effort:** S

**Location:** `src/denoiser/workers/analysis_worker.py:405-408` and `src/denoiser/workers/billing_worker.py:56,83,93-95`

```python
# analysis_worker.py:405
    # Usage metering + tier retention, daily at midnight UTC.
    sender.add_periodic_task(
        crontab(minute=0, hour=0), aggregate_billing.s(), name='aggregate_billing_daily'
    )
```

```python
# billing_worker.py:56
        today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
...
# billing_worker.py:82
                        "SELECT count(), sum(length(message)) FROM semantic_logs "
                        f"WHERE {where} AND toDate(timestamp) = toDate(now())",
...
# billing_worker.py:93
                    db.query(func.count(func.distinct(Span.trace_id)))
                    .filter(Span.tenant_id == tenant.id, Span.start_time >= today)
```

The task fires at 00:00:00 UTC. At that instant `now()` is the *new* day and `today` is the *new* midnight. `toDate(timestamp) = toDate(now())` therefore matches only rows written in the handful of seconds since midnight, and `Span.start_time >= today` matches the same sliver. The day that just ended — the day you would bill for — is never queried.

**Failure scenario:** A tenant ingests 400 GB across 2026-08-04. At 00:00:00 on 2026-08-05 the pass runs and writes `BillingMeter(date=2026-08-05, total_bytes_ingested≈0)`. Every subsequent day repeats. At month end you have 31 rows of approximately zero and no record of the 12 TB they actually sent.

**Impact:** Total revenue loss on any usage-based plan. Also unfalsifiable: the rows exist and look plausible, so nothing alerts.

**Recommended fix:** Meter the *previous* full day, and make the window explicit rather than implicit in `now()`:

```python
def aggregate_billing(db=None, *, day=None, enforce_retention=True):
    day = day or (utcnow().date() - timedelta(days=1))
```
then bind `toDate(timestamp) = {day:Date}` and `Span.start_time >= day_start, Span.start_time < day_start + 1 day`. Take `day` as a parameter so a missed run can be backfilled. Shift the schedule to ~00:15 UTC so late-arriving writes for the closed day have settled. Add a test that ingests a row "yesterday" and asserts the meter is non-zero — the current suite would not have caught this.

---

#### C-3 — Spans persisted by `/v1/traces` are never attributed to a tenant

**Severity:** CRITICAL · **Area:** Data / Billing / Isolation · **Effort:** S

**Location:** `src/denoiser/api/otlp.py:112-124`

```python
                span = Span(
                    trace_id=span_data.get("traceId"),
                    span_id=span_data.get("spanId"),
                    parent_span_id=span_data.get("parentSpanId"),
                    service_name=service_name,
                    operation_name=span_data.get("name"),
                    start_time=start_dt,
                    end_time=end_dt,
                    duration_ms=duration_ms,
                    status_code=status_code,
                    attributes=attributes,
                    events=span_data.get("events", [])
                )
```

`tenant_id` is not set. The handler *has* the tenant — it is the `tenant_id` parameter at line 79, and it is passed to ClickHouse at line 156 — but the Postgres row is built without it. `Span.tenant_id` is `nullable=True` (`src/denoiser/storage/db.py:190`), so this fails silently.

The archiver's *restore* path already fixed exactly this bug and left a comment saying so (`src/denoiser/storage/archiver.py:243-247`):

```python
                            span = Span(
                                # Restored without this, every rehydrated span
                                # came back unattributed — archived under one
                                # customer and returned belonging to nobody.
                                tenant_id=item.get("tenant_id"),
```

The primary ingest path never got the same treatment.

**Failure scenario:**
1. Customer configures their OTel collector to export to `/v1/traces` with their API key. Traces appear in the UI (read via ClickHouse, which *is* scoped) — everything looks correct.
2. Metering runs `Span.tenant_id == tenant.id` (`billing_worker.py:93-95`) and gets 0. The comment on line 89-90 claims this was just fixed from a hardcoded zero; functionally it is still zero.
3. Tenant offboarding / erasure deletes by `tenant_id`, so these spans survive deletion of the customer that produced them.
4. Any Postgres-backed span read scoped with `tenant_predicate` (`src/denoiser/api/scope.py:56`) resolves NULL-owned rows to the *unassigned* bucket, which is what a user with `tenant_id = None` sees — so an unassigned account sees every tenant's spans.

**Impact:** Trace usage is unbillable. Erasure certificates issued under `platform_admin` are inaccurate. Isolation guarantee has a hole for unassigned accounts.

**Recommended fix:** Set `tenant_id=int(tenant_id)` on the `Span(...)` construction. Note the type: `verify_ingest_auth` returns `str` on one branch (`src/denoiser/api/auth.py:295`) and `int` on others (line 280, 305) — normalise that function to always return `int` and fix the ClickHouse call sites to match. Then backfill: existing NULL-tenant spans cannot be attributed retroactively and should be deleted, not guessed. Add a test asserting `Span.tenant_id` is non-NULL after an OTLP trace POST.

---

#### C-4 — Runbook engine bypasses the SSRF guard and forwards credentials

**Severity:** CRITICAL · **Area:** Security · **Effort:** S

**Location:** `src/denoiser/automation/engine.py:41-82`

```python
        if action_type == "webhook":
            url = step.get("url")
            if not url:
                raise ValueError("Webhook URL is missing.")
            execution_logs.append(f"[{utcnow().isoformat()}] Sending POST request to {url}")
            response = requests.post(url, json=incident_payload, timeout=10)
```

```python
        elif action_type == "jira_issue":
            jira_url = step.get("jira_url")
            jira_user = step.get("jira_user")
            jira_token = step.get("jira_api_token")
...
            url = f"{jira_url.rstrip('/')}/rest/api/2/issue"
            auth = HTTPBasicAuth(jira_user, jira_token)
...
            response = requests.post(url, json=payload, auth=auth, timeout=15)
```

The project has a correct SSRF guard — `src/denoiser/integrations/net_guard.py:77 validate_destination()`, which blocks private, loopback, and link-local addresses and re-resolves DNS at send time. It is wired into exactly two places:

```
src/denoiser/integrations/alert_router.py:395:            validate_destination(cfg.url)
src/denoiser/api/webhooks.py:66:        validate_destination(body.url)
src/denoiser/api/webhooks.py:100:            validate_destination(body.url)
```

The runbook engine is not one of them. `slack_notification` (line 51) has the same gap.

Authorization required: `ANALYST` or `ADMIN` can create a runbook and fire it synchronously —
`src/denoiser/api/runbooks.py:56` (`create_runbook`, ANALYST/ADMIN) and `:101` (`run_runbook_now`, ANALYST/ADMIN).

**Failure scenario:** A tenant analyst — any customer's employee, not a platform admin — creates a runbook with a `jira_issue` step whose `jira_url` is `https://attacker.example`, and a `webhook` step pointing at `http://169.254.169.254/latest/meta-data/iam/security-credentials/`. They call `POST /runbooks/{id}/run`. The platform POSTs from inside your VPC to the metadata service, and separately sends `HTTPBasicAuth(jira_user, jira_token)` to the attacker's host in a `Authorization: Basic` header. Execution status is written to `execution_logs`, which the analyst can read back via `GET /runbooks/executions` (`runbooks.py:156`).

**Impact:** Reachability of every internal service and cloud metadata endpoint from a tenant-controlled input, plus direct exfiltration of any credential a runbook step carries. On a multi-tenant deployment this is a breach path from a low-privilege customer role.

**Recommended fix:** Call `validate_destination(url)` at the top of every outbound branch in `execute_runbook_step` — `webhook`, `slack_notification`, and `jira_issue` (validate `jira_url` *before* building the auth header, so a rejected destination never sees the credential). Validate again at runbook save time in `create_runbook`/`update_runbook` for a fast failure, but the send-time check is the one that matters because DNS is mutable — `net_guard`'s own docstring makes this point (`net_guard.py:14`). Do not echo `response.text` into `execution_logs`; `alert_router.py:443` already learned that lesson.

---

#### C-5 — `total_bytes_ingested` overflows at 2.1 GB/day

**Severity:** CRITICAL · **Area:** Billing / Data · **Effort:** S

**Location:** `src/denoiser/storage/db.py:454-462`, materialised in `alembic/versions/773789731d39_baseline_schema_from_models.py:69`

```python
class BillingMeter(Base):
    __tablename__ = "billing_meters"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, index=True, nullable=False)
    date = Column(DateTime, nullable=False)
    total_logs_ingested = Column(Integer, default=0)
    total_bytes_ingested = Column(Integer, default=0)
```

```python
    sa.Column('total_bytes_ingested', sa.Integer(), nullable=True),
```

`sa.Integer()` is `INTEGER` on Postgres — signed 32-bit, maximum 2,147,483,647. That is 2.0 GiB. `total_logs_ingested` has the same ceiling at 2.1 billion records.

This is a log platform whose README describes itself as "hyperscale" and ships a 1M-line stress test fixture. 2 GB/day is a small customer.

**Failure scenario:** Once C-2 is fixed and metering actually reads a full day, a tenant ingesting 3 GB triggers `psycopg2.errors.NumericValueOutOfRange: integer out of range` on the `db.commit()` at `billing_worker.py:132`. That commit is outside the per-tenant `try` (which ends at line 130), so it lands in the outer handler at line 138 — `db.rollback()`, and **every tenant's meter for that day is discarded**, not just the large one. The pass returns `{"error": ...}` and nothing retries it.

**Impact:** A single large customer erases the entire deployment's usage record for that day. Revenue loss scaled to your best customer.

**Recommended fix:** Migrate both counters to `sa.BigInteger()`. On Postgres this is `ALTER TABLE billing_meters ALTER COLUMN total_bytes_ingested TYPE BIGINT` — a full table rewrite, but `billing_meters` is one row per tenant per day, so it is trivially small. Add `total_traces_ingested` to the same change. Separately, move the `db.commit()` inside the per-tenant loop (or add a savepoint) so one tenant's failure cannot discard the rest.

---

### HIGH

---

#### H-1 — Retention deletes free-tier data two hours before archival could save it

**Severity:** HIGH · **Area:** Data · **Effort:** S

**Location:** `src/denoiser/api/scheduler.py:125` vs `src/denoiser/workers/analysis_worker.py:406`

```python
# scheduler.py:125 — APScheduler, in the API process. This is the job that
# calls S3ArchiverEngine.run_archival(); the hour=2 job on the line above it
# sweeps log *files* off local disk and is unrelated.
scheduler.add_job(single_instance("trigger_sso_s3_db_archival")(trigger_sso_s3_db_archival), 'cron', hour=4, minute=0)
```

```python
# analysis_worker.py:406 — Celery beat, in the worker process
    sender.add_periodic_task(
        crontab(minute=0, hour=0), aggregate_billing.s(), name='aggregate_billing_daily'
    )
```

`aggregate_billing` calls `ch_store.cleanup_old_data(tenant.id, days_to_keep)` (`billing_worker.py:117-121`), which issues `ALTER TABLE semantic_logs DELETE` (`clickhouse_store.py:417`). Free tier keeps 7 days (`billing_worker.py:32`). The archiver's default cutoff is also 7 days (`archiver.py:89`: `archive_days = settings.get("s3_archive_days", 7)`).

Two independent schedulers, two different processes, same threshold, and the destructive one runs first — by four hours.

**Failure scenario:** A free-tier tenant's logs cross the 7-day boundary. At 00:00 the retention pass hard-deletes them from ClickHouse. At 04:00 the archiver runs `SELECT * FROM semantic_logs WHERE timestamp < cutoff` and finds nothing. The data is gone and was never written to S3. The customer, or you during an incident review, later asks for it and it does not exist — with no error anywhere in the logs, because both jobs succeeded.

**Impact:** Silent, permanent customer data loss on the tier most likely to be evaluating you. Also undermines the archive-and-restore story in `PARTNER_HANDOVER.md`.

**Recommended fix:** Make archival a precondition of retention rather than a parallel job. Either (a) call `run_archival()` at the start of `aggregate_billing`, before `cleanup_old_data`, and drop the 04:00 APScheduler job; or (b) have `cleanup_old_data` refuse to delete rows newer than the last successful archive watermark, recorded per tenant. Option (a) is simpler and removes a cross-process ordering dependency that nothing currently enforces. Either way, assert the ordering in a test.

---

#### H-2 — OTLP log ingest stores bodies unredacted

**Severity:** HIGH · **Area:** Privacy / Compliance · **Effort:** S

**Location:** `src/denoiser/api/otlp.py:49-53` vs `src/denoiser/api/routers_ingest.py:51-59`

The `/ingest` path redacts:

```python
# routers_ingest.py:51
        # back. Everything downstream of this point sees redacted content.
        from denoiser.api.platform_settings import (
            build_redactor,
...
        from denoiser.preprocessing.redaction import redact_value

        redactor = build_redactor()
        body = [redact_value(entry, redactor) for entry in body]
```

The OTLP path does not:

```python
# otlp.py:49
    # Insert to ClickHouse
    clickhouse_configured = bool(runtime.clickhouse_store().client)
    clickhouse_inserted = False
    if clickhouse_configured:
        clickhouse_inserted = runtime.clickhouse_store().insert_logs(extracted_logs, tenant_id=tenant_id)
```

No `redact` call appears anywhere in `otlp.py`. The same is true of the Kafka consumer path (`src/denoiser/workers/ingestion_worker.py:253`).

Note that the raw-log sink write at `otlp.py:44-47` also stores the unredacted body to S3/MinIO, and the Redis publish at line 67 broadcasts it unredacted to the live stream.

**Failure scenario:** A customer follows the README's OTLP instructions — the path you market to enterprises — and ships application logs containing customer email addresses and bearer tokens. `SLD_REDACT_BY_DEFAULT=True` is set in `.env`, the operator believes redaction is on, and it is: for the `/ingest` path only. PII is written to ClickHouse and to the S3 raw sink in the clear, and stays there for the tenant's full retention period.

**Impact:** Your primary product claim is "privacy-first, your data never leaves your infrastructure" — but *within* the infrastructure, the documented ingest path stores raw PII. That is a GDPR data-minimisation problem and a direct contradiction of `README.md:1-10`.

**Recommended fix:** Move redaction below the fork. `insert_logs` in `clickhouse_store.py` is the single choke point every path passes through — apply `build_redactor()` there, or in a thin wrapper all three callers use. Doing it per-router is what produced this drift in the first place; the fix should make the unredacted path the one that doesn't exist. Add a test that POSTs an email address to `/v1/logs` and asserts it is not present in what reaches the store.

---

#### H-3 — No security response headers anywhere

**Severity:** HIGH · **Area:** Security · **Effort:** S

**Location:** `src/denoiser/api/main.py:52-83` (middleware stack), `nginx/nginx.conf`, `Caddyfile`

The middleware stack is:

```python
app.add_middleware(CORSMiddleware, ...)
app.add_middleware(RateLimitMiddleware, ...)
app.add_middleware(TenantQuotaMiddleware)
app.add_middleware(CSRFMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(CorrelationIDMiddleware)
app.add_middleware(AuditMiddleware)
app.add_middleware(VersionPrefixMiddleware, fastapi_app=app)
app.add_middleware(MetricsMiddleware)
```

Grep for `Strict-Transport-Security|Content-Security-Policy|X-Frame-Options|X-Content-Type-Options|Referrer-Policy|Permissions-Policy` across `src/` returns nothing. `nginx/nginx.conf` contains no `add_header` directives (TLS config only, lines 29-35). The `Caddyfile` is bare `reverse_proxy` blocks.

**Failure scenario:** The dashboard at `web:3000` is served from the same origin as the API through Caddy. With no `X-Frame-Options`/`frame-ancestors`, an attacker frames the dashboard and clickjacks an ADMIN into a destructive action (`DELETE /users/{id}`, `POST /runbooks/{id}/run`). With no HSTS, the `:80` block in `Caddyfile:5` serves the app over plaintext and a session cookie can be stripped on first contact. With no `X-Content-Type-Options: nosniff`, a log message echoed into a JSON error response can be MIME-sniffed as HTML by older browsers.

**Impact:** Fails the first security questionnaire and the first automated scan. Enterprise buyers check these with a curl.

**Recommended fix:** Add a small `SecurityHeadersMiddleware` as the outermost layer in `main.py` setting `Strict-Transport-Security: max-age=31536000; includeSubDomains`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and a `Content-Security-Policy` (start report-only, then enforce — Next.js needs `'unsafe-inline'` for styles unless you wire nonces). Mirror the same headers in `nginx/nginx.conf` so a deployment that bypasses the app still gets them. Redirect `:80` to HTTPS in the `Caddyfile` rather than serving from it.

---

#### H-4 — Trace ingest commits to Postgres, then returns 503, so retries duplicate

**Severity:** HIGH · **Area:** Correctness / Idempotency · **Effort:** M
**Note:** introduced by the uncommitted change in the working tree.

**Location:** `src/denoiser/api/otlp.py:147-167`

```python
    # Save to local SQLite database
    for span in db_spans:
        db.add(span)
    db.commit()

    # Insert to ClickHouse
    clickhouse_configured = bool(runtime.clickhouse_store().client)
    clickhouse_inserted = False
    if clickhouse_configured and clickhouse_rows:
        clickhouse_inserted = runtime.clickhouse_store().insert_traces(clickhouse_rows, tenant_id=tenant_id)

    if clickhouse_configured and clickhouse_rows and not clickhouse_inserted:
        raise HTTPException(
            status_code=503,
            detail="Spans could not be written to the trace store; retry the batch.",
        )
```

The 503 is the right call — an OTLP exporter must retry a lost batch. But the Postgres commit at line 150 has already succeeded, and the `spans` table has no uniqueness constraint (`src/denoiser/storage/db.py:186-201` — `span_id` is `index=True`, not `unique=True`).

**Failure scenario:** ClickHouse is down for 10 minutes. A collector with the default OTLP retry policy (5s initial, exponential, 5-minute max elapsed) resends each batch ~6 times. Every attempt commits a fresh copy of every span to Postgres and then 503s. A tenant sending 5,000 spans/minute writes ~300,000 duplicate rows over the outage. When ClickHouse returns, the UI (ClickHouse-backed) is correct, but the Postgres fallback path (`tracing.py:337`) reports span counts inflated ~6×, and the archiver at `archiver.py:97` later loads all of them into memory.

**Impact:** Postgres bloat proportional to outage length × traffic, wrong span counts in the fallback view, and a slow-motion amplification of exactly the failure the 503 was added to handle.

**Recommended fix:** Do the ClickHouse insert *first*, and only commit Postgres if it succeeded — ClickHouse is the system of record for spans per the comment at line 158. If Postgres must stay as a mirror, add `UniqueConstraint("tenant_id", "trace_id", "span_id")` and use an upsert (`ON CONFLICT DO NOTHING`) so retries are idempotent. Note the same shape exists in `/v1/logs`: the raw-log sink write at line 44-47 happens before the 503 at line 58, so retries duplicate S3 objects too — lower impact, same fix pattern.

---

#### H-5 — Erasure is tenant-wide only; no per-data-subject deletion

**Severity:** HIGH · **Area:** Compliance · **Effort:** L

**Location:** `src/denoiser/api/platform_admin.py:278-309`, `src/denoiser/storage/db.py:262-284`

```python
    # Record the erasure so it can be certified later. This outlives the tenant
...
    record = ErasureRecord(
```

What exists is genuinely good: tenant offboarding deletes Postgres rows, ClickHouse partitions, LanceDB vectors, and S3 archives, and issues a certificate backed by a re-read of the store (`clickhouse_store.py:444-515`). Tests cover it (`tests/test_multi_org_isolation.py::test_offboarding_removes_the_customers_data`).

What does not exist is deletion of *one person's* data. Search for `erasure`, `gdpr`, `right_to_be_forgotten`, `data_export` returns only tenant-scoped machinery. There is no endpoint that takes a data-subject identifier and removes their PII from ingested log bodies.

**Failure scenario:** Your customer, a SaaS company, receives a GDPR Art. 17 request from one of *their* end users whose email appears in application logs they shipped to you. They forward it to you as processor. Your only tool is deleting their entire tenant. You cannot comply, and per H-2 the OTLP path stored that email unredacted in the first place.

**Impact:** Legal exposure as a data processor. A DPA you sign with an EU customer will contain an assist-with-data-subject-requests clause you cannot currently satisfy. Also blocks the enterprise security review.

**Recommended fix:** Two-part. (1) Make redaction-at-ingest complete (H-2) so the PII largely isn't there — this is the real answer and it is much cheaper than deletion. (2) For what remains, add a subject-erasure endpoint that runs `ALTER TABLE semantic_logs UPDATE message = replaceAll(...)` scoped to tenant + matched identifier, plus the equivalent over `spans.attributes`, recorded in `ErasureRecord` with the same certificate mechanism you already built. Document retention periods and the third-party processor list (S3/MinIO, and the LLM endpoint if it is not local) in a DPA before your first EU customer.

---

### MEDIUM

---

#### M-1 — Slack sender has no timeout

**Severity:** MEDIUM · **Area:** Reliability · **Effort:** S

**Location:** `src/denoiser/integrations/slack.py:60-64`

```python
            response = requests.post(
                self.webhook_url,
                data=json.dumps(slack_data),
                headers={'Content-Type': 'application/json'}
            )
```

Every other outbound call in the codebase sets one — `engine.py:47` (`timeout=10`), `engine.py:82` (`timeout=15`), `alert_router.py:415` (`AsyncClient(timeout=10)`), `oidc.py:45` (`Client(timeout=10)`). This one does not, so it inherits `requests`' default of *no timeout*.

**Failure scenario:** Slack has a partial outage where connections are accepted but never answered. An analysis run posts its report, the worker thread blocks indefinitely, and the Celery worker's concurrency pool drains one slot per run until analysis stops entirely. Nothing times out and nothing alerts, because from Celery's perspective the tasks are still running.

**Impact:** Full analysis pipeline stall from a third-party outage, requiring a manual worker restart.

**Recommended fix:** `timeout=10`. Consider a lint rule — `ruff` does not check this by default, but a grep in CI for `requests.(get|post)\(` without `timeout` is cheap and would have caught it.

---

#### M-2 — `billing_meters` has no unique constraint on `(tenant_id, date)`

**Severity:** MEDIUM · **Area:** Data integrity · **Effort:** S

**Location:** `src/denoiser/storage/db.py:454-463`; `alembic/versions/773789731d39_...py:64-75` creates only non-unique indexes.

`billing_worker.py:98-115` does a read-then-write to stay idempotent:

```python
                meter = db.query(BillingMeter).filter(
                    BillingMeter.tenant_id == tenant.id,
                    BillingMeter.date == today
                ).first()

                if not meter:
                    meter = BillingMeter(...)
                    db.add(meter)
```

That is check-then-act with no constraint behind it.

**Failure scenario:** The Celery beat is run on two replicas (a plausible HA misconfiguration — `billing_worker.py:8-12` explicitly documents that a second beat is *possible*), or a missed run is manually re-triggered while the scheduled one is still going. Both passes read "no meter exists" and both insert. The tenant now has two rows for the same day, and any `SUM` over the period double-bills them.

**Impact:** Overbilling a customer, which is worse commercially than underbilling.

**Recommended fix:** Add `UniqueConstraint("tenant_id", "date", name="uq_billing_meters_tenant_date")` and switch the write to a Postgres upsert (`insert(...).on_conflict_do_update`). The constraint is what makes the idempotency real; the read-then-write is only a hint.

---

#### M-3 — N+1 query in the Postgres trace listing fallback

**Severity:** MEDIUM · **Area:** Performance · **Effort:** S

**Location:** `src/denoiser/api/tracing.py:253-262`

```python
        results = query.group_by(Span.trace_id).order_by(func.min(Span.start_time).desc()).limit(limit).all()

        filtered = []
        for row in results:
...
            root_span = db.query(Span).filter(Span.trace_id == row.trace_id, Span.parent_span_id.is_(None)).first()
            if not root_span:
                # If no strict root span found, just pick any span for the trace
                root_span = db.query(Span).filter(Span.trace_id == row.trace_id).first()
```

One or two extra queries per returned trace. The surrounding comment acknowledges it ("To be efficient, let's just do a quick fetch...").

Secondary issue: those two queries are **unscoped** — no `scope.predicate(Span)`, unlike the aggregate above them at line 352. `trace_id` collisions across tenants are improbable (128-bit), so this is a theoretical leak of `root_service`/`root_operation` rather than a practical one — but given C-3 leaves every span NULL-owned, it should be fixed at the same time.

**Failure scenario:** ClickHouse is unavailable, the UI falls back to Postgres, and a trace list of 100 issues 201 queries. Under the load the fallback exists to survive, this makes the fallback the bottleneck.

**Impact:** Degraded performance in exactly the degraded state it was written for.

**Recommended fix:** One query for all root spans — `db.query(Span).filter(scope.predicate(Span), Span.trace_id.in_(trace_ids), Span.parent_span_id.is_(None))` — into a dict keyed by `trace_id`, then look up in the loop. Add `scope.predicate(Span)` to both queries.

---

#### M-4 — Archiver materialises every expired span in memory

**Severity:** MEDIUM · **Area:** Reliability · **Effort:** S

**Location:** `src/denoiser/storage/archiver.py:97`

```python
            old_spans = db.query(Span).filter(Span.start_time < cutoff_date).all()
```

Then every row is copied into a second full dict structure at lines 100-115 (`by_tenant`), so peak memory is roughly twice the row set.

**Failure scenario:** The archiver has not run for a week (scheduler disabled on the replica via `SEMANTICOS_SCHEDULER_ENABLED`, or the API pod was restarting through its archival window). Seven days of spans accumulate. The next run loads all of them plus a full dict copy, and the API pod — this ran *in-process* under APScheduler, not in a worker — hits its memory limit and is OOM-killed. The `Ingress`/`HPA` sees a crashlooping pod. Combined with H-4's duplicate spans, the row count can be several times what traffic alone suggests.

**Impact:** API availability loss caused by a background maintenance job sharing the API's memory.

**Recommended fix:** Batch it — `.yield_per(5000)` or an explicit `LIMIT`/loop keyed on `id`, writing and pruning each batch before loading the next. Longer term, move archival out of the API process into the Celery worker where a memory spike costs a worker, not the serving path.

---

#### M-5 — Global email uniqueness leaks cross-tenant existence and blocks signups

**Severity:** MEDIUM · **Area:** Correctness / Security · **Effort:** M

**Location:** `src/denoiser/api/routers_users.py:89-91`, backed by `src/denoiser/storage/db.py:118`

```python
    exists = db.query(User).filter(User.email == payload.email).first()
    if exists:
        raise HTTPException(status_code=400, detail="User with this email already exists")
```

```python
    email = Column(String, unique=True, index=True, nullable=False)
```

This is the one unscoped query in a router file that otherwise gets tenant isolation exactly right — the module docstring at lines 24-27 states the rule, and `_tenant_user` follows it carefully, returning 404-not-403 specifically so ids cannot be enumerated (lines 45-51).

**Failure scenario A (disclosure):** An admin at Tenant A posts `{"email": "cfo@competitor.com"}` to `/users`. A 400 means that address has an account somewhere on the deployment; a 201 means it does not. This is precisely the oracle the 404-not-403 rule elsewhere was designed to deny, reachable by a different route.

**Failure scenario B (functional):** A consultant works with two of your customers and needs an account in each tenant. The second tenant's admin cannot create one. Support has no fix short of a database edit.

**Impact:** Cross-tenant existence disclosure, and a real onboarding blocker for shared-deployment customers.

**Recommended fix:** Scope the check with `tenant_predicate(User, current_user.tenant_id)` and change the DB constraint to `UniqueConstraint("tenant_id", "email")`. This touches login, which resolves users by email — `get_current_user` and the SSO/SCIM paths will need to resolve `(tenant, email)`. That is why this is M rather than S; do it before you have many customers, because it gets harder with data.

---

#### M-6 — Copyleft and source-available dependencies need a decision

**Severity:** MEDIUM · **Area:** Licensing · **Effort:** S

**Method:** enumerated all 217 installed distributions via `importlib.metadata`, matched against `GPL|AGPL|SSPL|BUSL|Commons Clause|MPL|EPL|CDDL|Prosperity|Elastic`.

**The good news:** the ML and data stack is clean and permissive. Verified individually:

| Package | Version | License |
|---|---|---|
| hdbscan | 0.8.42 | BSD |
| umap-learn | 0.5.12 | BSD |
| sentence-transformers | 5.4.1 | Apache 2.0 |
| torch | 2.11.0 | BSD-3-Clause |
| polars | 1.40.1 | MIT |
| lancedb | 0.30.2 | Apache 2.0 |
| clickhouse-connect | 1.0.1 | Apache 2.0 |
| signxml | 5.1.0 | Apache 2.0 |
| python-jose | 3.5.0 | MIT |
| celery | 5.6.3 | BSD-3-Clause |
| statsmodels | 0.14.6 | BSD |
| pandas | 3.0.3 | BSD-3-Clause |
| kubernetes | 35.0.0 | Apache 2.0 |
| boto3 | 1.43.6 | Apache 2.0 |
| bcrypt | 5.0.0 | Apache 2.0 |
| passlib | 1.7.4 | BSD |

No GPL, AGPL, SSPL, or non-commercial license in the Python tree. Frontend (`web/package.json`) is Next.js/React/Tailwind/Recharts/ECharts — all MIT or Apache 2.0.

**Needs a decision:**

| Package | License | Why it matters |
|---|---|---|
| `psycopg2-binary` 2.9.12 | **LGPL with exceptions** (`pyproject.toml:47`) | LGPL is fine for a hosted service and fine when dynamically linked, which is how it is used. It becomes a question only if you ship a statically-linked binary distribution. Low risk; document the position rather than switching. |
| `certifi` 2026.4.22, `pathspec`, `fqdn`, `tqdm` | MPL-2.0 | File-level copyleft. Unmodified use imposes no obligation on your code. Only note it if you patch these files. |
| **Redpanda** (`docker-compose.yml:173`) | **BSL 1.1** — *not a Python dependency; a container you ship in your compose file* | Business Source License forbids offering the software as a **managed service**. If SemanticOS is on-prem software the customer runs, you are fine. If you ever host it for customers, you cannot ship Redpanda as the broker. |

**Failure scenario:** You launch a hosted SemanticOS Cloud tier. Redpanda's BSL "Additional Use Grant" prohibits providing it as a streaming service to third parties. You either negotiate a Redpanda commercial license or replatform the broker under time pressure.

**Recommended fix:** Now: add `docker-compose.yml` alternatives for Apache Kafka or a Redpanda commercial license, and note the constraint in `DEPLOY.md`. The code already talks Kafka protocol via `aiokafka`, so the swap is configuration, not code. Also generate and commit a `THIRD_PARTY_LICENSES.md` (`uv export` + `pip-licenses`) — enterprise procurement asks for it by name, and you currently satisfy no attribution requirement for the BSD/MIT/Apache components you redistribute in your Docker image.

---

#### M-7 — `_normalise_payload` discards a non-dict LLM response

**Severity:** MEDIUM · **Area:** Correctness · **Effort:** S
**Note:** in the uncommitted working tree.

**Location:** `src/denoiser/intelligence/llm.py:39-49`

```python
    if not isinstance(payload, dict):
        return {}
```

Combined with `src/denoiser/analysis/pipeline.py:677`:

```python
    incident = None
    if state.llm_payload:
        incident = Incident(
```

**Failure scenario:** The model returns a top-level JSON array — uncommon but well within what a local Llama 3 does under `response_format={"type": "json_object"}`, which is a hint, not a guarantee. `_normalise_payload` returns `{}`. That is falsy, so `pipeline.py:677` creates no `Incident`, and `pending_alert` returns `None`. The run completes, reports success, and produces no incident and no alert — indistinguishable from "nothing was wrong." Before this change, the malformed payload would at least have surfaced as an error.

**Impact:** Silently missed incidents. Directly opposed to the fix's own intent, which was to stop bad model output from reaching storage.

**Recommended fix:** Return the local heuristic fallback instead of `{}` — `self._generate_local_fallback(clusters)` already exists for exactly this case (`llm.py:143`) — or log at ERROR and let the retry loop treat it as a failed attempt. An empty dict is the one return value the caller cannot distinguish from "no analysis was requested."

The rest of this change is sound: the `failure_domain` list-to-string coercion is correct, the repair migration `d0a6b41e73c5` matches narrowly on `^\{".*"\}$` and handles escaped quotes, and `Incident.summary` is a `JSON` column (`db.py:58`) so it needs no equivalent treatment.

---

### LOW

---

#### L-1 — `/v1/traces` builds unbounded structures from one request

**Severity:** LOW · **Area:** Reliability · **Effort:** S

**Location:** `src/denoiser/api/otlp.py:83-90`

```python
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    resource_spans = body.get("resourceSpans", [])
    db_spans = []
    clickhouse_rows = []
```

`BodySizeLimitMiddleware` does bound this by `Content-Length`, and `/v1/traces` is not in `_BODY_LIMIT_EXEMPT` (`middleware.py:116`), so the exposure is capped. But a chunked request without `Content-Length` is explicitly passed through (`middleware.py:122-124`), and unlike the ingest and upload paths named in that comment, `/v1/traces` has no per-route validator of its own.

**Failure scenario:** An authenticated tenant sends a chunked POST with no `Content-Length` and a very large `resourceSpans` array. The body, the parsed JSON, `db_spans`, and `clickhouse_rows` all coexist in the API process's memory.

**Impact:** Memory pressure on the API pod from an authenticated caller. Requires a valid API key, so this is abuse rather than attack.

**Recommended fix:** Cap the span count per request (the OTLP spec expects clients to batch) and return 413 above it, matching what the ingest route already does.

---

#### L-2 — Production hostname is a hardcoded `nip.io` address

**Severity:** LOW · **Area:** Ops · **Effort:** S

**Location:** `Caddyfile:20`

```
20.2.90.156.nip.io {
    # Automatic HTTPS using Let's Encrypt for magic DNS
    tls admin@semanticos.io
```

A specific IP baked into the reverse proxy config, resolved through a third-party wildcard DNS service. Also `Caddyfile:5` serves the whole app over plain `:80` with no redirect.

**Impact:** No customer can deploy this as written; `nip.io` availability becomes a dependency of your TLS issuance. Reads as a demo artifact in a repo that otherwise looks production-ready — which is what a technical buyer reviewing the repo will notice.

**Recommended fix:** Templatise the hostname from an environment variable and redirect `:80` to HTTPS. `DEPLOY.md` should carry the real instructions.

---

## 5. Unverified — needs human confirmation

1. **No infrastructure was exercised.** The whole suite passes without ClickHouse, Kafka, Redis, or Postgres running — it is heavily mocked. Nothing in this audit confirms the ClickHouse DDL, the Kafka consumer, or the S3 archiver behave correctly against real services. Every ClickHouse-related finding here is read from code, not observed. **Run the suite against live containers before launch.**
2. **Frontend was not built or run.** `npm ci && npm run build` was not executed (no `node_modules` present). Phase 8's accessibility, responsive, and onboarding-dead-end checks are therefore **not done**. CI does run `tsc --noEmit`, `eslint`, `next build`, and Playwright, so it is likely fine — but I did not verify it, and Phase 8 should be considered outstanding.
3. **`mypy` was not run** — no `timeout` binary on this macOS host and I did not want to risk a long-running strict-mode pass. CI runs it in enforced-allowlist mode (`ci.yml:55`) plus advisory full-package mode (`ci.yml:78`), so type coverage is partial by design. Which modules are on the allowlist was not reviewed.
4. **`pip-audit` was not run locally.** CI runs it blocking with an ignore file (`ci.yml:119`). I did not read `.github/pip-audit-ignore.txt`, so I cannot say which CVEs are currently accepted or whether their stated review dates have expired. **Check that file — a stale ignore entry is a silent vulnerability acceptance.**
5. **Backup/restore was not tested.** `deploy/helm/semanticos/templates/backup-cronjob.yaml` exists and commit `7bcbdc1` claims to fix the ClickHouse half. Whether a restore actually produces a working system is unverified — this is the single most valuable thing to test manually before you have customer data worth losing.
6. **`.env` in the working directory contains a live `SLD_LLM_API_KEY`.** It is correctly gitignored (`.gitignore:31`) and not tracked (`git ls-files` confirms only `.env.example`). I did not read the value. Confirm it is not a shared/production key, and rotate it if it has ever been pasted anywhere.
7. **LLM data flow.** `settings.llm_base_url` is configurable (`llm.py:74`). If any deployment points it at a hosted API rather than a local Ollama, customer log content leaves the customer's infrastructure — contradicting the core product claim and adding a sub-processor to your GDPR disclosures. I could not determine what real deployments are configured with.
8. **`ENTERPRISE_TRIAL_FINDINGS.md` (34 KB) and `SCALE_READINESS_TODO.md` (18 KB) were not read in full.** They may already track some of these findings. Cross-check before assigning work.

---

## 6. What's genuinely solid — don't touch

- **Tenant isolation.** `src/denoiser/api/scope.py` is the best thing in this codebase. One rule, stated once, with `NULL = NULL` handled correctly and 404-not-403 enforced structurally rather than by convention. The module docstring documenting the four incorrect dialects it replaced is exemplary. Every resource router uses it correctly. C-3 and M-5 are gaps *around* it, not in it.
- **Production configuration validation.** `settings.validate_for_production` (`settings.py:180-220`) refuses to boot on a known-default JWT secret, a wildcard CORS origin, a plaintext origin, an enabled mock IdP, or SQLite. Enforced at startup in `main.py:186-195`. This is the control that prevents most launch-day disasters and it is done right.
- **The SSRF guard itself.** `net_guard.py` blocks private/loopback/link-local, re-resolves at send time because DNS is mutable, and `alert_router.py:443` deliberately does not echo response bodies. The design is correct — C-4 is that it isn't called from one path, not that it's wrong.
- **Auth primitives.** Keyring-based JWT with `kid` rotation and an overlap window, single-use rotating refresh tokens, `type` claim separating access from refresh, per-token `jti` for revocation, and a constant-time dummy bcrypt verify to defeat user enumeration (`auth.py:88-91`). All correct.
- **CI.** Blocking `pip-audit`, blocking `ruff`, migration integrity checked on both SQLite and Postgres, coverage floor at 73%, frontend type-check + lint + build + Playwright. `ruff` currently passes clean on `src` and `tests`.
- **Test suite health.** 1014 passed, 3 skipped, 0 failed, 104s. Fast enough that developers will actually run it.
- **ClickHouse parameterisation.** Tenant ids are bound parameters throughout (`clickhouse_store.py:415`), `_require_tenant` refuses to build an unscoped clause, and the query DSL validates field names against `FIELD_NAME` before interpolating into `JSONExtractString` (`query/parser.py:143`). No SQL injection found.
- **The commit messages and code comments.** Nearly every non-obvious decision carries an explanation of what went wrong before. That is why this audit could go as deep as it did in the time available, and it is worth more than it looks.

---

## 7. Phase-by-phase summary

| Phase | Result |
|---|---|
| **0 — Recon** | 143 Python modules / 26.5k lines; FastAPI + Celery + ClickHouse/Postgres/Redis/Kafka/LanceDB/S3; Next.js 16 frontend; Helm chart. Suite: 1014 passed, 3 skipped, 0 failed. `ruff` clean. Staged deletion of `metrics/extraction.py` verified safe — zero remaining references; `analysis_worker.extract_metrics` now does the work in-store. |
| **1 — Correctness** | 4 findings. Worst: spans never tenant-stamped (C-3) and commit-then-503 duplication (H-4). |
| **2 — Security** | 3 findings. Worst: runbook SSRF + credential exfiltration (C-4). No secrets in git history. No SQL injection. No XSS sinks. Tokens are not in `localStorage`. Tenant isolation holds. |
| **3 — Data** | 4 findings. Missing unique constraints, 32-bit overflow on a money-adjacent counter, N+1, and unbounded `.all()`. Migrations are otherwise well-formed and reversible where reversal is meaningful. |
| **4 — Reliability & cost** | 2 findings. Missing timeout in `slack.py`; archiver memory. No runaway-cloud-bill vector found — the LLM path is capped at `top_n=10` clusters and defaults to a local model. |
| **5 — Commercial** | The heaviest phase. No billing system at all; metering broken twice over; no paid-feature entitlements; no per-subject GDPR erasure; no `THIRD_PARTY_LICENSES.md`. Python licenses are clean; Redpanda's BSL blocks a hosted offering. |
| **6 — Operations** | Health/readiness endpoints are thorough (`routers_health.py:33-83`, including a Kafka consumer heartbeat). Prometheus alerts and a Grafana dashboard ship in `deploy/`. Main gaps are H-3 (headers) and L-2 (hardcoded hostname). Backup restore untested — see Unverified #5. |
| **7 — Test coverage** | Not a percentage problem. The untested paths are: metering correctness (would have caught C-2 and C-5), span tenant attribution (C-3), runbook outbound destinations (C-4), redaction on the OTLP path (H-2), and retention-vs-archival ordering (H-1). Every one of those is a launch blocker. Auth, permissions, and data mutation *are* well covered. |
| **8 — Product polish** | **Incomplete** — frontend was not built or run. See Unverified #2. |
