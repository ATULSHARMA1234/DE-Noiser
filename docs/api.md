# API Reference

Base URL: `http://localhost:8000`. Interactive docs (OpenAPI/Swagger) are served
at `/docs` when the API is running.

## Versioning and deprecation policy

Every endpoint in this document is available at two addresses:

```
GET /incidents          # unversioned alias
GET /v1/incidents       # versioned — use this for new integrations
```

They are the same handler. Responses carry `X-API-Version: v1`.

**The commitment.**

| | |
|---|---|
| **`/v1` is frozen.** | No field is removed from a `/v1` response, no field changes type, and no request parameter becomes required. New optional fields may be added, so clients must ignore unknown fields rather than reject them. |
| **The unversioned aliases are permanent.** | They are not a deprecation window. Existing integrations keep working indefinitely; `/v1` exists so that future ones are insulated from a future `/v2`. |
| **Breaking changes get a new version.** | A change that would violate the `/v1` freeze ships as `/v2` instead. `/v1` and `/v2` then run side by side. |
| **Minimum deprecation notice: 12 months.** | Measured from the announcement in [CHANGELOG.md](../CHANGELOG.md) to the removal, and no version is removed while a supported customer is still calling it. Deprecated endpoints return a `Deprecation` and `Sunset` header ([RFC 8594](https://www.rfc-editor.org/rfc/rfc8594)) for the whole window. |
| **Security fixes are exempt.** | A change required to close a vulnerability may ship inside a version, on whatever timeline the severity demands. It will be called out in the changelog. |

**Exception — OTLP.** `POST /v1/logs` and `POST /v1/traces` are the paths the
OpenTelemetry specification defines, not ours. Their contract is OTLP's and
follows that specification's versioning, not this policy.

## Authentication

Most endpoints require a Bearer JWT. Obtain one from `/auth/login`, send it as
`Authorization: Bearer <token>`. Ingestion also accepts an `X-API-Key` header.

| Method | Path | Role | Description |
|--------|------|------|-------------|
| POST | `/auth/login` | — | Exchange email/password for a JWT. Rate-limited (5 failures / 5 min per IP+email). |
| GET | `/auth/me` | any | Current user profile. |
| POST | `/auth/logout` | any | Revoke the caller's token (cannot be reused before expiry). |
| GET | `/auth/sso/providers` | — | Which sign-in flows this deployment offers (OIDC / SAML / mock), so the login page renders only reachable buttons. |
| GET | `/auth/sso/login` | — | SSO redirect. Uses the real OIDC provider when `OIDC_ISSUER`/`OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET` are set; otherwise the gated mock IdP. |
| GET | `/auth/sso/callback` | — | OIDC callback: validates the ID token (JWKS), maps groups → role/teams, provisions the user, issues a JWT. |
| GET | `/auth/sso/saml/login` | — | SP-initiated SAML: redirects to the IdP with a deflated `AuthnRequest`. `501` until SAML is configured. |
| POST | `/auth/sso/saml/acs` | — | SAML ACS: verifies the assertion signature, audience, recipient, validity window and replay, then issues a JWT. `401` on any failure. |
| GET | `/auth/sso/saml/metadata` | — | SP metadata XML to register with the IdP. |
| GET | `/admin/signing-keys` | ADMIN | Active and retired JWT key ids, for confirming a key rotation landed. |
| GET | `/admin/credentials` | ADMIN | Rotation state of every long-lived credential. Never returns a secret's value. |
| POST | `/admin/tenant/api-key/rotate` | ADMIN | Issue a new tenant API key; the superseded one stays valid for `overlap_hours` (0 = revoke now). Returned once. |
| POST | `/admin/tenant/api-key/revoke-previous` | ADMIN | End the overlap early, once every shipper carries the new key. |

#### SAML 2.0 configuration

| Variable | Meaning |
|----------|---------|
| `SAML_IDP_ENTITY_ID` | IdP entity id; must match the assertion issuer. |
| `SAML_IDP_SSO_URL` | IdP HTTP-Redirect SSO endpoint. |
| `SAML_IDP_X509_CERT` | IdP signing certificate (PEM). `_FILE` variant supported. |
| `SAML_SP_ENTITY_ID` | This service's entity id; must appear in the assertion audience. |
| `SAML_SP_ACS_URL` | Public URL of `/auth/sso/saml/acs`. |
| `SAML_CLOCK_SKEW_SECONDS` | Tolerance on assertion validity windows (default 60). |

Assertions must be **signed** (response-level or assertion-level) and
**unencrypted** — `EncryptedAssertion` is rejected rather than skipped. All five
variables are required; with any missing, the SAML routes stay `501` instead of
falling back to anything weaker.

### SCIM 2.0 provisioning

IdP-driven user lifecycle. Auth: `Authorization: Bearer <token>` (endpoints
return `403` until a token is configured).

**Which token decides which organisation the IdP may manage.** Each organisation
has its own token, issued by `POST /platform/tenants/{id}/scim-token/rotate`;
every endpoint below is filtered to that organisation, so one customer's IdP
cannot see or de-provision another's staff. A deployment-wide
`SCIM_BEARER_TOKEN` still works for a single-customer install, and is refused
with `403` once any organisation registers an email domain — at that point it no
longer names one organisation.

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/scim/v2/Users` | List (supports `filter=userName eq "…"`) / create. |
| GET/PUT/PATCH | `/scim/v2/Users/{id}` | Read / replace / patch (e.g. `active=false`). |
| DELETE | `/scim/v2/Users/{id}` | De-provision (deactivate; access revoked immediately). |
| GET/POST | `/scim/v2/Groups` | List / create teams. |
| GET/PATCH/DELETE | `/scim/v2/Groups/{id}` | Read / manage membership / delete. |

## Health & self-observability

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` · `/health/live` | Liveness — process is serving. |
| GET | `/health/ready` | Readiness — probes DB, Redis, ClickHouse, Kafka **and the ingestion consumer**; `503` if a critical dep is down. A stopped consumer means `/ingest` accepts writes that never become queryable, so it fails readiness. |
| GET | `/internal/metrics` | Prometheus exposition (request rate, errors, latency). |

## Ingestion

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/ingest` | API key or JWT | Ingest a batch: `{"logs": [ {...}, ... ]}`. Fans out to Kafka, Redis, and (fallback) ClickHouse. |

### Syslog listener (real connector)

Runs as its own service (`denoiser.ingestion.syslog_server`, exposed on `514/udp`
and `514/tcp` via docker-compose). Accepts **RFC 5424** and **RFC 3164/BSD**
messages over UDP and TCP (both octet-counted and newline framing), with optional
TLS. Parsed records are normalized to the standard `timestamp`/`level`/`source`/
`message` shape and written to ClickHouse — identical to the HTTP path.

Config: `SYSLOG_UDP_PORT`, `SYSLOG_TCP_PORT`, `SYSLOG_HOST`, `SYSLOG_TLS_CERT`,
`SYSLOG_TLS_KEY`, `SYSLOG_TENANT_ID`.

### OTLP logs (real connector)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/v1/logs` | API key or JWT | OpenTelemetry logs. Accepts the OTLP/HTTP **protobuf** encoding (the OTel default) **and** JSON. |
| POST | `/v1/traces` | API key or JWT | OpenTelemetry spans (JSON). |

`severityNumber` (1–24) is mapped to the platform severity vocabulary, resource
`service.name` becomes the log source, and `trace_id`/`span_id` are preserved.
Point any OpenTelemetry Collector's `otlphttp` exporter at `/v1/logs`.

### Elasticsearch & Splunk compatibility (drop-in connectors)

Let existing shippers send to SemanticOS with only a URL change.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | — | Elasticsearch version stub (Beats/Logstash preflight). |
| POST | `/_bulk` · `/{index}/_bulk` | API key or JWT | Elasticsearch Bulk API (NDJSON). Point a Filebeat/Logstash `elasticsearch` output here. |
| GET | `/services/collector/health` | — | Splunk HEC health probe. |
| POST | `/services/collector[/event]` | `Splunk <token>` | Splunk HEC event ingestion (concatenated JSON events). |
| POST | `/services/collector/raw` | `Splunk <token>` | Splunk HEC raw (line-delimited) ingestion. |

## Log query (LQL)

| Method | Path | Role | Description |
|--------|------|------|-------------|
| POST | `/v1/logs/query` | VIEWER+ | Run a Log Query Language search. Body: `{query, limit, from_ts, to_ts}`. |

LQL supports `field:value`, `"quoted phrases"`, and `AND` / `OR`; it compiles to
parameterized ClickHouse SQL.

## Analysis, runs, incidents

| Method | Path | Role | Notes |
|--------|------|------|-------|
| POST | `/analyze` | ANALYST+ | Queue an analysis run (Celery; inline if broker down). |
| GET | `/tasks/{task_id}` | VIEWER+ | Poll async task status. |
| GET | `/runs` · `/analysis/runs` | VIEWER+ | List runs. **Paginated**: `?limit=&offset=` (limit ≤ 1000). |
| GET | `/runs/{run_id}` | ABAC read | Run detail. |
| GET | `/analysis/compare?run_a=&run_b=` | VIEWER+ | Drift comparison between two runs. |
| GET | `/incidents` | VIEWER+ | List incidents. **Paginated**. |
| GET | `/incidents/{id}` | ABAC read | Incident detail. |
| PUT | `/incidents/{id}/resolve` | ABAC write | Resolve/reopen. |

## Issues

An issue is one log pattern tracked across runs (fingerprint of the normalized
template, scoped to its service), which is what carries first/last seen, the
occurrence trend and triage state — a cluster cannot, because its id is
reassigned on every run.

| Method | Path | Role | Notes |
|--------|------|------|-------|
| GET | `/issues` | VIEWER+ | List. Filters: `state`, `severity`, `service`, `assignee_id`, `q`; `sort=last_seen\|first_seen\|events\|severity\|anomaly`; `limit` ≤ 200, `offset`. Returns per-state counts over the unfiltered set, for the status tabs. |
| GET | `/issues/facets` | VIEWER+ | Value counts for service, severity, state and assignee. |
| GET | `/issues/{id}` | VIEWER+ | Detail: tags with prevalence, hourly histogram, samples, comments, activity, and the suspect deployment (last marker in the 24h before first seen). |
| PATCH | `/issues/{id}` | ANALYST+ | Set `state` (`FOR_REVIEW`/`REVIEWED`/`IGNORED`/`RESOLVED`), `severity`, `assignee_id` (0 unassigns), `team_id`. Each change is written to the activity feed. |
| POST | `/issues/{id}/comments` | ANALYST+ | Add a comment. |
| GET | `/issues/{id}/assignees` | VIEWER+ | Active users in the tenant, for the assignment picker. |

## Alerting & runbooks

| Method | Path | Role | Description |
|--------|------|------|-------------|
| GET/POST/PUT/DELETE | `/webhooks` … | ADMIN | Manage alert destinations (Slack/PagerDuty/Teams/generic). |
| POST | `/webhooks/{id}/test` | ADMIN | Fire a synthetic test alert. |
| POST | `/alerts/trigger` | ANALYST+ | Ingest an alert; P0 opens an incident and runs matching runbooks. |
| POST | `/runbooks/{id}/run` | ANALYST+ | Execute a runbook now, without waiting for a matching incident. |
| PUT | `/runbooks/{id}` | ANALYST+ | Update a runbook (the enable/disable toggle). |
| POST | `/monitors/{id}/evaluate` | ANALYST+ | Run a monitor's query immediately and persist the result. |

## Integrations

| Method | Path | Role | Description |
|--------|------|------|-------------|
| GET/POST/PUT/DELETE | `/integrations` … | varies | Manage connected providers. Credentials are stored but never returned — reads show a mask. |
| POST | `/integrations/{id}/test` | ANALYST+ | Verify the stored credential actually authenticates. |
| POST | `/integrations/{id}/sync` | ANALYST+ | Pull provider metadata in. For GitHub this imports deployments as markers for deploy↔anomaly correlation (needs `config.repo` = `owner/name`). Markers are filed under `config.service` (default: the repo name) or `config.service_by_environment` — set one for a monorepo, or every service lands in a single series. |

## Sources & connectors

| Method | Path | Role | Description |
|--------|------|------|-------------|
| GET/POST/DELETE | `/sources` … | varies | List / upload / delete log files. |
| GET/POST | `/connectors/{k8s,aws,docker}/…` | varies | Discover and fetch logs. Returns `"simulated"` sample data when the backend is absent. |

## Administration

| Method | Path | Role | Notes |
|--------|------|------|-------|
| GET/POST/DELETE | `/users` … | ADMIN | Manage operators. `GET` is **paginated**. |
| GET/PUT | `/settings` | VIEWER+/ADMIN | Read / update platform settings. Stored in the database, so every API replica sees the same values. |
| GET | `/admin/usage` | ADMIN | Per-day ingest volume (logs, bytes, traces) and the tenant's retention tier. |
| POST | `/admin/usage/recalculate` | ADMIN | Re-meter today immediately. Does not apply retention. |
| GET | `/vitals` · `/metrics/current` | VIEWER+ | Vitals of the **SemanticOS host**, not the monitored fleet; the response carries `scope` and `host`. |
| GET | `/telemetry/kernel-events` | VIEWER+ | eBPF kernel events (TCP retransmits, OOM kills). Linux + `bcc` only. |
| WS | `/stream?token=` | any | Live per-tenant log tail (Redis pub/sub). |

## Platform operations (hosting several organisations)

Onboarding and offboarding whole customers. Auth: `Authorization: Bearer
<SEMANTICOS_PLATFORM_TOKEN>` — the *vendor's* credential, deliberately not the
ADMIN role, since ADMIN belongs to a customer's own administrator and must not
be able to move the boundary between two customers. Returns `403` until the
token is set.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/platform/tenants` | Every organisation, with user counts and registered domains. No secrets. |
| POST | `/platform/tenants` | Onboard. Returns `api_key` and `scim_token` **once**. |
| PATCH | `/platform/tenants/{id}` | Update `domains` / `tier`. |
| POST | `/platform/tenants/{id}/scim-token/rotate` | Re-issue the SCIM token. Returned once. |
| DELETE | `/platform/tenants/{id}` | Offboard. Body must echo `{"confirm_name": "<name>"}`. |

**Domains.** `domains` are the email domains a customer owns. SSO and SCIM route
federated identities by them, so registering one is what turns a
single-customer deployment into a shared one. A domain can be claimed by only
one organisation (`409` otherwise), and an address from an unregistered domain
is refused at login rather than assigned to a guess. With no domains registered
anywhere, SSO keeps its single-customer fallback.

**Deletion is irreversible** and spans stores nothing else cleans together:
relational rows (including child tables with no `tenant_id` of their own),
ClickHouse logs and traces, LanceDB embeddings, uploaded sources, and cold
archives. The response reports what was removed per store; if any store could
not be reached it returns `"status": "partial"` and lists the failures, and the
purge should be repeated rather than treated as a completed erasure.

## Conventions

- **Pagination**: list endpoints accept `limit` (1–1000, default 200) and `offset` (≥0).
- **Errors**: JSON `{error, detail, request_id}`; every response carries `X-Request-ID`.
- **Time**: `from_ts` / `to_ts` are epoch milliseconds.
- **SLO status**: `GET /slos/{id}/status` returns `HEALTHY` / `WARNING` / `BREACHED` / `NO_DATA`. A latency SLI is measured only over log lines carrying a duration (`duration_ms`, `latency_ms`, `elapsed_ms`, `response_time_ms`, `duration`, `latency`) against the SLO's own `latency_threshold_ms`; lines without one are excluded from both numerator and denominator, never counted as passing. When nothing in the window is measurable the status is `NO_DATA` — not a passing score.
