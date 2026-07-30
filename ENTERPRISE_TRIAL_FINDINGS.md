# SemanticOS — Fortune 500 Evaluation Report

**Evaluation date:** 2026-07-26
**Build:** branch `feat/enterprise-sso-syslog-otlp` @ `f1ea471`, API v2.0.0

> **Remediation status: all 28 tasks in §5 are implemented.** Every finding
> below has been closed and verified by re-running the original probe that
> found it, plus a regression test in `tests/test_enterprise_hardening.py`
> (59 tests) that reproduces the defect and asserts it no longer occurs.
> Suite: **709 passing**. See §8 for the verification record.
>
> The findings text is left in the past tense as written during the trial, so
> the report still reads as the evidence it was.
>
> **A second pass on 2026-07-27** asked what the first did not: what happens
> when one deployment serves two *different companies*. It found six further
> defects, all on the identity side of the boundary rather than the data side.
> They are recorded in §9, and are also closed. Suite: **740 passing**.

**Method:** clean SQLite DB, fresh migration chain, live Redis/ClickHouse/Redpanda/Postgres/MinIO.
Two tenants provisioned (`acme-corp`, `globex-industries`) with ADMIN/ANALYST/VIEWER personas,
then probed as a hostile co-tenant, a compliance auditor, and an SRE running a P0.

---

## 1. Executive summary

SemanticOS is a genuinely capable platform with a well-built security *core* — the JWT keyring,
RBAC, query parameterization, and production config validator are better than most products at
this stage. **The core is sound; the perimeter around it is not yet closed.**

The blocking issue is that multi-tenancy is enforced *per-subsystem* rather than as a platform
invariant. Every resource backed by a SQLAlchemy model is correctly scoped. Every resource that
is *not* — the alert-destination registry, the audit log, and the `data/` file namespace — leaks
completely across tenants. A second paying customer on the same instance can today read another
customer's Slack credentials, delete their alert routing, read their audit trail, and delete
their uploaded logs.

The second blocking issue is that the "privacy-first" claim does not hold at rest. Redaction runs
only on the clustering path; raw PII is persisted verbatim and served back through the product's
own search API. The two compliance toggles in the Settings UI (`Auto-Redact PII`, `Store Raw Logs`)
have no backend consumer at all.

**Verdict: not deployable for regulated multi-tenant workloads today. Single-tenant, on-prem,
non-regulated deployment is viable now.** The 28 tasks in §5 close the gap; §5.1 (10 tasks) is
the minimum bar for a production security review.

---

## 2. Where the platform excels

These held up under deliberate attack and deserve to be protected by regression tests.

| Area | Evidence |
|---|---|
| **Test & migration health** | 631/631 tests pass in 56s. Alembic chain applied cleanly from an empty DB through 9 revisions — no `create_all` drift. |
| **Query-language safety** | 8 injection payloads (`' OR 1=1--`, `UNION SELECT`, `SELECT sleep(3)`, field-name injection, null bytes, fullwidth-unicode) all returned 0 rows with no error. Parameterization via `{p0:String}` is real, and the `FIELD_NAME` guard correctly demotes hostile field names to free text. |
| **Pipeline robustness** | 15 pathological inputs — empty, whitespace-only, 500 identical lines, 400 unique lines, a **10 MB single line**, raw bytes 0–255, embedded null bytes, emoji/RTL/zero-width, 200-level nested JSON, 5 000-key JSON, ANSI escapes, CRLF/lone-CR — produced **zero crashes**. Empty sources fail cleanly with `No logs found at source(s)`. |
| **Production config validator** | Refuses to boot on weak JWT secret, `CORS=*`, `SSO_ALLOW_MOCK=true`, SQLite, and the known-insecure default key. This is the single best-designed piece of the codebase. |
| **RBAC** | All 11 privilege-boundary probes returned 403 — VIEWER/ANALYST correctly barred from user creation, credential status, key rotation, settings, and billing. |
| **Unauthenticated surface** | All 26 probed endpoints returned 401/403. No accidental public routes. |
| **Tenant scoping on DB-backed resources** | Monitors, notebooks, dashboards, incidents, runs, issues, SLOs, saved queries, integrations, deployments — cross-tenant read *and* delete correctly denied; Acme's resources survived Globex's delete attempts. |
| **Identity plumbing** | JWT keyring with `kid`-stamped rotation and verify-overlap, single-use rotating refresh tokens, `jti` revocation list, refresh-token-as-access rejection, deactivated-user rejection. |
| **Brute-force throttle** | 5 failures → 429, correctly enforced against the (IP, email) pair with a Redis sliding window and in-memory fallback. |
| **Error hygiene** | No stack traces, SQL, file paths, or dependency names leaked in any error response. |
| **Secret hygiene in VCS** | `.env` never committed; no AWS/GitHub/Slack/Stripe key patterns anywhere in tracked files. |
| **Frontend** | `tsc --noEmit` clean. |

---

## 3. Critical findings

### C-1 — Alert destinations have no tenant isolation (and no persistence)

`alert_router` is a process-global singleton; `WebhookConfig` has no `tenant_id` field, and none
of the six `/webhooks*` routes filter by tenant. Reproduced end to end:

```
Acme registers: https://hooks.slack.com/services/T00ACME/B00ACME/xoxbAcmeSuperSecret...
Globex GET    /webhooks          → 200, Acme's full secret URL returned
Globex PUT    /webhooks/{id}     → 200 {"status":"updated","name":"pwned"}
Globex POST   /webhooks/{id}/test→ 200 (fired Acme's destination)
Globex DELETE /webhooks/{id}     → 200 {"status":"deleted"}
Acme   GET    /webhooks          → 200 []      ← Acme's paging is now silently gone
```

A Slack/PagerDuty webhook URL **is a bearer credential**. This is credential disclosure plus
destructive cross-tenant write. Separately, the registry is in-memory only: every alert
destination is lost on restart.

### C-2 — `/analyze` reads arbitrary filesystem paths

`AnalysisRequest.source` is an unvalidated string passed to `LogReader.read()`, which does
`Path(path).resolve()` and opens it. Any ANALYST or ADMIN can read any file the process can:

```
POST /analyze {"source":"/etc/passwd"}  → 200 queued
POST /analyze {"source":".env"}         → 200 queued
```

Confirmed via `LogReader` directly — the live `.env` was read back including
`SLD_LLM_API_KEY=AQ.Ab8RN6I3d86...` (a working Google API key). Contents land in run results and
are retrievable via `/runs/{id}`. **Rotate that key; it is exposed to every analyst account.**

### C-3 — `data/` is a global namespace shared by all tenants

`/sources/upload` writes to a flat `data/` directory keyed only by basename. Path traversal *is*
correctly blocked, but tenancy is absent:

```
Acme uploads  acme-confidential-a6c35b.log  (contains PAN + SSN)
Globex GET    /sources        → 200, Acme's filename listed
Globex POST   /analyze        → 200, accepted against Acme's file
Globex DELETE /sources/{name} → 200 {"status":"deleted"}   ← Acme's evidence destroyed
```

Also: `/ingest` writes every tenant's logs into one shared `data/live_stream.log`.

### C-4 — PII is never redacted at rest

`Redactor` is applied in `analysis_worker.py:219` only to build `normalized_text` for embedding.
`_index_logs` writes `record["raw_text"]` — unredacted — to ClickHouse, and `/ingest` writes raw
lines to disk and ClickHouse with no redaction on that path at all. End-to-end, with
`redact_pii=true` set:

```
POST /ingest        SSN=123-45-6789 card=4111111111111111 email=jane@acme.com
                    ip=203.0.113.9 password=superSecret123 ghp_16C7e42F...
POST /v1/logs/query → all six values returned VERBATIM
grep data/live_stream.log → SSN present in cleartext on disk
```

### C-5 — The two compliance toggles are non-functional

`redact_pii` and `store_raw_logs` are defined in `platform_settings.py`, exposed in `SettingsUpdate`,
and rendered in the Settings UI with explicit promises — *"Mask sensitive data before passing to
local LLM or clustering"*, *"Keep a local copy of ingested raw logs"*. **No backend code reads
either value.** `Redactor(enabled=True)` is hardcoded. Setting `store_raw_logs=false` still wrote
raw logs to disk (verified). The UI additionally promises IP redaction, which the engine does not
implement at all.

A compliance control that reports success without acting is worse than an absent one — it will be
cited in a DPIA.

### C-6 — Audit log is not tenant-scoped

`AuditLog` has no `tenant_id` column and `/audit/` applies no tenant filter. Globex's admin:

```
GET /audit/?limit=300 → 200, total=86 rows spanning ALL tenants
distinct user_ids visible: [2,3,4,5,6]     ← other customers' users
paths visible: /admin/tenant/api-key/rotate, /settings, /ingest, /analyze, ...
```

Globex can reconstruct Acme's security-operations timeline, including when they rotated keys.

---

## 4. High and medium findings

### High

- **H-1 — Redaction regex is broken by a double-escape.** In `_PATTERNS`, `PASSWORD`, `SECRET`, and
  `TOKEN` use `[^\\s,;\"']+` inside a raw string. In a character class `\\s` means *literal
  backslash or literal `s`* — not whitespace. Any value containing `s` is truncated or missed:
  - `password="superSecret123"` → **not redacted at all**
  - `token=asdf1234` → `token=<TOKEN>sdf1234` (one char redacted, rest leaked)
  - `client_secret: sk_live_abc` → `client_secret:<SECRET>sk_live_abc`
  Fix is `\s`. This silently defeats three of the nine rules.

- **H-2 — Redaction coverage gaps.** 17 of 25 sensitive-value cases leaked. Missing entirely:
  GitHub PAT (`ghp_`), Slack tokens (`xoxb-`), Stripe keys (`sk_live_`), PEM private-key blocks,
  `aws_secret_access_key`, HTTP Basic credentials, **IPv4/IPv6** (personal data under GDPR and
  promised by the UI), E.164 phone numbers, IBAN, national insurance numbers, date of birth,
  passport numbers, MAC addresses.

- **H-3 — Credit-card rule over-redacts and destroys debuggability.** `\b(?:\d[ -]*?){13,16}\b`
  has no Luhn check. It silently ate an order ID, a byte counter, a k8s UID, a build number, and
  part of a latency histogram in testing — replacing them with `<CREDIT_CARD>`. Engineers will
  lose the exact fields they need during an incident, with no indication why.

- **H-4 — SSRF via alert destinations.** No allowlist, blocklist, or scheme restriction. The server
  issued requests to `169.254.169.254` (cloud IMDS), `metadata.google.internal`, RFC1918 space, and
  its own loopback admin port. The upstream response body is echoed back in the `error` field
  (`"HTTP 405: {\"detail\":\"Method Not Allowed\"}"`), making this a *readable* SSRF, not a blind one.
  Combined with C-1, any tenant can drive it.

- **H-5 — The UI never refreshes tokens.** `web/src/lib/api.ts` stores only `access_token` and, on
  any 401, clears storage and hard-redirects to `/login`. `/auth/refresh` is never called. With a
  30-minute access token, **every operator is force-logged-out every 30 minutes**, including
  mid-incident. The backend's single-use rotating refresh implementation is complete, tested, and
  entirely unused by the product.

- **H-6 — JWTs in `localStorage`.** Any XSS yields a durable token. Enterprise reviews expect
  `httpOnly; Secure; SameSite` cookies.

- **H-7 — Negative pagination is unvalidated.** `/issues?limit=-1` and `?offset=-5` return 200.
  `limit` has `le=` but no `ge=`; a negative LIMIT means *unbounded* in both SQLite and Postgres,
  so `limit=-1` is a full-table fetch on demand.

- **H-8 — Several list endpoints have no maximum page size.** `/alerts/?limit=99999999`,
  `/audit/?limit=100000000`, `/webhooks/log?limit=99999999` all accepted. `/telemetry/kernel-events`
  and `/admin/usage` clamp correctly — the validation is inconsistent rather than absent.

### Medium

- **M-1 — No timestamp sanity bounds.** `9999-12-31` → `253402300799000` and `0001-01-01` →
  `-62135596800000` are both accepted. Issue folding takes `max(last_seen)`, so **one log line from
  a container with a bad clock permanently pins an issue's `last_seen` ~7 973 years into the
  future**, defeating every recency filter, trend sparkline, and SLO window keyed on it.

- **M-2 — Epoch-nanosecond and leap-second timestamps are unparsed.** `1785068722410270200` (the
  native OTLP resolution) and `2026-06-30T23:59:60Z` both return `None`, silently dropping the
  event's time.

- **M-3 — No ingest batch or body-size limit.** 100 000 log entries accepted in a single request
  (3.3 s). A 20 MB single log was rejected only by an accidental Kafka `MessageSizeTooLargeError`,
  whose internals leaked into the client response. Without Kafka, that path writes straight to
  ClickHouse unbounded.

- **M-4 — Ingest accepts non-log values.** `[1, 2, null, true, [1,2]]` → `{"ingested": 6}`. Nulls
  and scalars are stored as log records.

- **M-5 — Correlation ID is lost in error responses.** 500s return
  `{"request_id": "no-request"}` — the `ContextVar` set by `CorrelationIDMiddleware` does not
  propagate into the exception handler. A customer-reported error ID cannot be traced to a server log.

- **M-6 — Oversized integer path param → 500.** `GET /incidents/99999999999999999999` returns
  `500 Internal server error` instead of 404/422.

- **M-7 — Deeply nested query → 500.** 2 000 `AND` terms overflow `compile_to_sql`'s recursion.
  No term-count or depth cap.

- **M-8 — Read access is never audited.** `AuditMiddleware` skips everything that isn't
  POST/PUT/DELETE. For a system holding SSNs and PANs, *read* access to PII is precisely what
  SOC 2 CC7.2 and HIPAA §164.312(b) require logging.

- **M-9 — Audit records carry no before/after state.** `details` holds only `{"status_code": …}`.
  The log cannot answer "who reduced retention from 90 days to 1, and what was it before?"

- **M-10 — Account-lockout denial of service.** Knowing a user's email is enough to lock them out
  for 5 minutes with 5 bad passwords. No CAPTCHA, progressive delay, or admin unlock path.

- **M-11 — `/sources/upload` reads the whole file into memory.** `content = await file.read()`
  with no size cap — a large upload OOMs the API process.

---

## 5. Remediation task list

Tasks are atomic and scope-disjoint: no two tasks modify the same concern, so they can be assigned
to different engineers and merged independently. Where a hard ordering exists it is stated as
*Requires*; that is a sequencing note, not shared scope.

### 5.1 — Blocking for a production security review

**T-01 · Add a `webhooks` table and migration**
Create a `Webhook` SQLAlchemy model (`id`, `tenant_id` indexed, `name`, `channel_type`, `url`,
`min_priority`, `enabled`, `extra`, `created_at`) plus an Alembic revision. Schema only — no
route or router changes.
*Done when:* `alembic upgrade head` creates the table on a clean DB and `alembic downgrade` reverses it.

**T-02 · Back `AlertRouter` with the `webhooks` table**
Replace the in-process dict in `integrations/alert_router.py` with reads/writes against the model
from T-01, so destinations survive a restart. Keep the existing method signatures.
*Requires T-01. Done when:* a destination registered before a process restart is still present after it.

**T-03 · Tenant-scope every `/webhooks*` route**
Add `tenant_id` filtering to list/get/update/delete/test in `api/main.py`, sourced from
`current_user.tenant_id`. Cross-tenant access returns 404.
*Requires T-01. Done when:* the C-1 reproduction returns 404 at every step and Acme's destination survives.

**T-04 · Encrypt webhook URLs at rest and mask them in responses**
Store `url` encrypted; return `https://hooks.slack.com/services/T00ACME/…/****` in all API
responses. Full value used only at delivery time.
*Requires T-01. Done when:* `GET /webhooks` never returns a complete secret URL.

**T-05 · Confine `/analyze` sources to an allowlisted root**
Resolve `AnalysisRequest.source`/`sources` and reject anything outside the configured data root
with 400. Applies to `LogReader.read()` as defence in depth.
*Done when:* `{"source":"/etc/passwd"}` and `{"source":".env"}` both return 400.

**T-06 · Namespace uploaded sources per tenant**
Write uploads to `data/tenants/{tenant_id}/`, and scope `/sources` list and `/sources/{filename}`
delete to the caller's tenant.
*Done when:* Globex cannot see, analyse, or delete Acme's uploaded file.

**T-07 · Redact on the write path**
Apply `Redactor` in `_index_logs` before the ClickHouse insert and in the `/ingest` handler before
the disk write and ClickHouse insert. Do not touch the existing embedding-path redaction.
*Done when:* the C-4 reproduction returns no verbatim SSN/PAN/email/token from `/v1/logs/query`,
and `data/live_stream.log` contains none.

**T-08 · Make `redact_pii` and `store_raw_logs` functional**
Have the ingest and indexing paths read both settings: `redact_pii=false` skips redaction,
`store_raw_logs=false` suppresses the raw disk write. Removing the toggles instead is an
acceptable alternative — silently ignoring them is not.
*Requires T-07. Done when:* toggling each setting produces an observable change in stored data.

**T-09 · Add `tenant_id` to `AuditLog` and scope `/audit/`**
Add the column and migration, populate it in `AuditMiddleware` from `request.state`, and filter
`/audit/` by `current_user.tenant_id`.
*Done when:* Globex's admin sees only Globex rows.

**T-10 · Fix the `\\s` character-class bug**
Change `[^\\s,;\"']+` to `[^\s,;\"']+` in the `PASSWORD`, `SECRET`, and `TOKEN` patterns.
*Done when:* `password="superSecret123"`, `token=asdf1234`, and `client_secret: sk_live_abc` are
each fully masked, with a regression test per pattern.

### 5.2 — Required before a regulated or multi-tenant rollout

**T-11 · Extend redaction to the missing secret formats**
Add patterns for `ghp_`/`gho_`/`ghs_`, `xox[baprs]-`, `sk_live_`/`pk_live_`, PEM `-----BEGIN … PRIVATE KEY-----`
blocks, `aws_secret_access_key`, and HTTP Basic credentials.
*Done when:* each has a masking test.

**T-12 · Add configurable personal-data patterns**
Add IPv4, IPv6, E.164 phone, IBAN, DOB, passport, and MAC patterns behind individually
toggleable config flags (IP redaction breaks network debugging for some customers, so it must be
switchable rather than always-on).
*Done when:* each pattern masks when enabled and is inert when disabled.

**T-13 · Luhn-validate credit-card matches**
Gate the `CREDIT_CARD` substitution on a Luhn checksum.
*Done when:* `4111111111111111` is masked while the order ID, byte counter, k8s UID, and build
number from H-3 pass through untouched.

**T-14 · Restrict outbound alert-delivery destinations**
Validate every webhook URL at registration and before delivery: HTTPS only, and reject
loopback, link-local (`169.254.0.0/16`), and RFC1918 targets unless explicitly allowlisted.
*Done when:* all seven SSRF probes are rejected at registration.

**T-15 · Stop echoing upstream response bodies**
Record the upstream status code and a fixed reason string in `AlertDeliveryRecord.error`; never
the response body.
*Done when:* a failing delivery returns no upstream content to the caller.

**T-16 · Implement token refresh in the web client**
On 401, have `apiFetch` call `/auth/refresh` once with the stored refresh token, retry the
original request, and only redirect to `/login` if the refresh itself fails. Store the refresh
token alongside the access token.
*Done when:* a session survives access-token expiry without a visible logout.

**T-17 · Move session tokens to httpOnly cookies**
Issue access and refresh tokens as `httpOnly; Secure; SameSite=Lax` cookies and drop
`localStorage` token storage. CSRF protection is part of this task.
*Requires T-16. Done when:* no token is reachable from `document`/JS.

**T-18 · Add `ge=` bounds to every pagination parameter**
Constrain `limit`/`offset`/`skip` to `ge=1` / `ge=0` across `/issues`, `/alerts/`, `/audit/`,
`/incidents`, `/webhooks/log`, and `/query/saved`.
*Done when:* `limit=-1` and `offset=-5` both return 422.

**T-19 · Apply a uniform maximum page size**
Add `le=1000` (or a shared constant) to the same set, matching the clamping
`/telemetry/kernel-events` already does.
*Requires T-18. Done when:* `limit=99999999` returns 422 on every list endpoint.

**T-20 · Clamp ingest batch size and request body size**
Reject `/ingest` batches over a configured entry count and requests over a configured byte size
with 413, before Kafka is reached.
*Done when:* a 100 000-entry batch and a 20 MB body are both rejected with 413 and no Kafka
internals in the response.

### 5.3 — Correctness and operability hardening

**T-21 · Clamp parsed timestamps to a sane window**
Reject or floor/ceil timestamps outside `[now - 10y, now + 1d]` in `TimestampExtractor`, and
record a counter when it fires.
*Done when:* a `9999-12-31` line cannot move an issue's `last_seen` beyond tomorrow.

**T-22 · Parse epoch-nanosecond and leap-second timestamps**
Add magnitude-based epoch detection (s/ms/µs/ns) and accept `:60` leap seconds.
*Done when:* `1785068722410270200` and `2026-06-30T23:59:60Z` both parse.

**T-23 · Validate ingest entry shape**
Require each entry to be a string or an object; reject scalars, nulls, and arrays with 422.
*Done when:* `[1, 2, null, true, [1,2]]` returns 422.

**T-24 · Propagate the correlation ID into error responses**
Fix `request_id_ctx` propagation (set the ContextVar inside the request scope, or read the ID from
`request.state`) so handled and unhandled errors carry the real ID.
*Done when:* a forced 500 returns a request ID matching the `X-Request-ID` response header.

**T-25 · Bound path-parameter and query-complexity inputs**
Return 422 for out-of-range integer path params (`/incidents/99999999999999999999`) and cap query
term count/AST depth in `parse_query`.
*Done when:* both M-6 and M-7 reproductions return 422 rather than 500.

**T-26 · Audit reads of log data and record before/after state**
Extend `AuditMiddleware` to log GET on the log/issue/incident/run read paths, and capture changed
field values in `details` for settings and retention mutations.
*Done when:* the audit log answers "who read this tenant's logs" and "what was the previous
retention value".

**T-27 · Stream uploads to disk with a size cap**
Replace `await file.read()` in `/sources/upload` with chunked streaming and reject over a
configured maximum with 413.
*Done when:* a file larger than the cap is rejected without a memory spike.

**T-28 · Add progressive login backoff and an admin unlock path**
Replace the flat 5-attempt lockout with exponential backoff and give ADMINs an endpoint to clear
a lockout.
*Done when:* a locked-out account can be released by an admin without waiting out the window.

---

## 6. Immediate action, independent of the backlog

The working `.env` in this checkout holds a live `SLD_LLM_API_KEY`. It was never committed to git,
but T-05's absence means every ANALYST account can read it through `/analyze`. **Rotate it now**,
before or alongside T-05.

---

## 7. Reproduction assets

Harnesses used for this evaluation (kept outside the repo, in the session scratchpad):
`harness.py` (56 tenancy/RBAC/auth probes), `probe_webhook.py`, `probe_ssrf.py`,
`probe_sources.py`, `probe_pipeline.py` (15 pathological inputs), `probe_redaction.py`
(25 leak cases + 6 over-redaction cases), `probe_e2e_pii.py`, `probe_ops.py`, `probe_time.py`.
Each finding above cites output produced by one of these.

---

## 8. Verification record

Each row was confirmed by re-running the probe that originally found the
defect, against a freshly-migrated database, plus a regression test.

| Finding | Original behaviour | Verified after remediation |
|---|---|---|
| **C-1** webhooks | Globex read Acme's Slack URL, renamed, fired and deleted it | URL masked to `https://hooks.slack.com/services/••••••••`; every cross-tenant `GET/PUT/DELETE/test` → **404**; destination survives restart (now a `webhooks` table, URL encrypted at rest) |
| **C-2** `/analyze` path read | `/etc/passwd`, `.env` accepted (`200 queued`); `.env` read back incl. live API key | All six traversal payloads → **404** `"was not found in this workspace"`; rejected synchronously at the API *and* in the worker |
| **C-3** shared `data/` | Globex listed, analysed and **deleted** Acme's confidential log | Filename not listed; analyse → 404; delete → 404. Uploads land in `data/tenants/{id}/` |
| **C-4** PII at rest | SSN, PAN, email, IP, password, GitHub PAT all returned verbatim by `/v1/logs/query` | All six **redacted** — `SSN=<SSN> card=<CREDIT_CARD> email=<EMAIL> ip=<IPV4>` |
| **C-5** dead settings | `store_raw_logs=false` still wrote to disk | 0 occurrences written; both toggles now have a backend consumer |
| **C-6** audit log | Globex saw all 86 rows across every tenant | acme 26 / globex 22, disjoint user ids; before/after captured (`retention_days: 44 → 55`); read access audited |
| **H-1** `\\s` regex bug | `password="superSecret123"` untouched | Fully masked, with a regression test per affected pattern |
| **H-2** coverage gaps | 17 / 25 sensitive values leaked | 2 / 25 — and both are non-defects: the probe's UK NINO sample is invalid per the official prefix rules, and free-text names/addresses need NER, not regex (documented limitation) |
| **H-3** card over-redaction | Order ids, byte counters, k8s uids destroyed | All survive; every real card format (contiguous, 4-4-4-4, Amex 4-6-5) still masked. Gated on issuer prefix **and** grouping **and** Luhn |
| **H-4** SSRF | Server fetched IMDS, RFC1918, loopback | All 7 payloads **400 at registration**; re-checked before every delivery |
| **H-5** no refresh | Hard logout every 30 min | `apiFetch` refreshes once and replays; refresh works from the cookie alone |
| **H-6** localStorage | Token durably XSS-stealable | `httpOnly` cookies; no token in `localStorage` anywhere in `web/src` |
| **H-7/H-8** pagination | `limit=-1` → full-table scan | Every probed endpoint **422** on negative *and* oversized values |
| **M-1** timestamps | Year 9999 pinned `last_seen` 7 973 years ahead | Rejected at extraction and again at issue folding |
| **M-2** epoch-ns | OTLP-native `1785068722410270200` → `None` | Parses; leap second `23:59:60` folds to `:59.999` |
| **M-3/M-4** ingest | 100 k batch accepted; `[1,2,null,true,[1,2]]` → 6 "logs" | **422** with an actionable message |
| **M-5** correlation id | Every 500 returned `"no-request"` | Real id round-trips; resolver prefers `request.state` |
| **M-6/M-7** input bounds | Oversized int and 2 000-term query → **500** | **422** / **400** |
| **M-8/M-9** audit depth | Reads unaudited; no before/after | Both implemented |
| **M-10** lockout DoS | 5 strikes → 5 min flat lockout of any known email | Progressive backoff (5s→15s→60s→300s→900s), cleared on success, plus a tenant-scoped admin unlock endpoint |
| **M-11** upload OOM | Whole file read into memory | Streamed in 1 MB chunks, aborts past the cap with **413** |

**Non-regression.** The strengths in §2 were re-verified, not assumed: the same
15 pathological inputs (raw bytes, 10 MB single line, null bytes, 200-deep
JSON, emoji/RTL, CRLF) still produce **zero crashes**, and the 8 query-injection
payloads still return 0 rows.

### Deviations from the task list as written

Two tasks were implemented differently from their stated acceptance criterion,
for reasons that only became visible during the work:

* **T-05** specified `400` for a source outside the data root. It returns
  **404**. `resolve_source` deliberately gives one indistinguishable answer for
  "outside the root", "another tenant's file" and "no such file", so the
  endpoint cannot be used to probe for the existence of either; a single
  not-found is the honest status for that single message. A request with *some*
  readable sources still proceeds, preserving the existing multi-source
  contract.
* **T-17** keeps returning tokens in the login response body. Removing them
  would break the CLI, log shippers and CI, which cannot use a cookie jar. The
  security property the task was after — no durable credential reachable from
  page script — is met by the web client no longer storing them.

### Residual risks, accepted and documented

* **DNS rebinding on webhook delivery.** A host that answers NXDOMAIN at
  registration and a private address at connect time defeats the guard. Closing
  it fully requires pinning the connection to a pre-validated IP, a
  transport-level change. Unresolvable hosts are allowed through deliberately so
  the guard does not depend on working DNS (air-gapped installs, CI).
* **IPv4 vs. version strings.** `1.2.3.4` is indistinguishable from a dotted
  quad and is masked when IP redaction is on. Unresolvable by pattern; this is
  why the category is switchable.
* **Names and postal addresses** are not detected. Regex cannot do it; this
  needs named-entity recognition and is out of scope for the redaction engine.

---

## 9. Second pass — hosting more than one customer

**Date:** 2026-07-27. Prompted by a question the original trial did not ask:
what happens when *two different companies* are served by one deployment, each
with their own staff who need to work together internally?

The original trial provisioned two tenants and probed cross-tenant access on
dashboards, monitors, notebooks, SLOs, webhooks, runbooks and saved queries.
That was the right test for **data**. It was not a test of **identity**, and
every finding below is on that side of the line — which is why the first pass
missed them.

### M-1 — The user directory was not tenant-scoped

All four `/users` endpoints ran unfiltered. One company's admin could enumerate
every other customer's staff (emails, roles, membership), and delete or
deactivate them. `create_user` never set `tenant_id`, so new accounts were
orphaned and could not see their own colleagues' work.

Probed only for *role* boundaries in the first pass — "can a VIEWER create
users?" returned `403`, so the endpoint looked covered.

**Closed.** All four scoped; a foreign user id returns `404`, not `403`, so the
endpoint cannot be used to count another company's headcount.

### M-2 — SSO and SCIM assigned every federated user to the lowest-id tenant

`sso.py` and `scim.py` both took `SELECT * FROM tenants ORDER BY id LIMIT 1`.
Correct for one customer; for two, an employee of the second company signing in
through their own IdP was seated inside the first company's data.

**Closed.** Organisations register the email domains they own; identities route
by domain, and an unregistered domain is refused rather than guessed at. A
deployment where nobody has registered a domain keeps the old fallback, so
single-customer installs are unaffected.

### M-3 — One shared SCIM token could manage every customer's staff

Every SCIM endpoint queried across the whole deployment. An IdP holding the
single `SCIM_BEARER_TOKEN` could list, patch, re-role and de-provision *any*
customer's users — the sharpest form being a denial-of-service on a competitor
by deactivating their entire workforce.

**Closed.** Per-organisation tokens; the authenticating token selects the
organisation and every read and write is filtered to it. Group membership can
only bind users from the group's own organisation. The deployment-wide token is
refused once domains are registered, because it then names no one organisation.

### M-4 — The vector store commingled every customer's log templates

`log_embeddings` had no tenant column. Latent rather than live — `search()` had
no callers — but templates are not innocuous: they are log lines with only the
*variable* parts stripped, so they carry table names, endpoints and internal
hostnames verbatim.

**Closed.** Rows are stamped with their owner, `search()` takes a required
`tenant_id`, and a write without one is refused. A pre-existing table is
migrated in place and its rows quarantined as unattributed.

### M-5 — Cold archives put two companies' logs in one object

The archiver wrote one gzip per run containing every tenant's rows. Two
problems: two customers' logs in a single object, and no way to delete one
customer's archived data without destroying everyone else's.

**Closed.** One object per customer per run, under
`archive/{logs,traces}/tenant=<id>/`. Rehydration also restores `tenant_id`,
which it previously dropped — every archived span came back belonging to nobody.

### M-6 — There was no way to offboard a customer

No tenant deletion existed anywhere. A departing customer's data stayed in five
stores indefinitely, which is a straightforward GDPR Article 17 problem.

**Closed.** `DELETE /platform/tenants/{id}` purges relational rows (including
three child tables that carry no `tenant_id` of their own and would otherwise
have been missed), ClickHouse, embeddings, uploaded sources and cold archives.
It reports per store, and names any store it could not reach rather than
claiming a completed erasure. A test asserts every mapped model is classified as
customer-owned, child-owned or deliberately deployment-wide, so a table added
later cannot be silently left behind.

### Verification

Suite: **740 passing** (715 before this pass). 25 new tests in
`tests/test_multi_org_isolation.py`, each a reproduction of the defect above it.
Migration `e6b3d80f5a24` verified up, down and up again.

### Still open

* **One IdP per deployment.** OIDC and SAML configuration is read from
  deployment-wide environment variables, so a shared deployment offers one
  identity provider. Domain routing fixes *which organisation* an assertion
  lands in; it does not let two companies each bring their own Okta for
  interactive login. SCIM provisioning is per-organisation and does not have
  this limitation.
* **ClickHouse deletion is asynchronous.** `ALTER TABLE … DELETE` is accepted,
  not applied, when the purge returns. `mutations_sync` is deliberately not set
  — a final purge should not hold an HTTP request open for the length of a large
  table rewrite — so an erasure certificate should be issued against the
  mutation's completion, not this endpoint's response.
