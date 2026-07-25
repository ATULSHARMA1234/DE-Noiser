# API Reference

Base URL: `http://localhost:8000`. Interactive docs (OpenAPI/Swagger) are served
at `/docs` when the API is running.

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

IdP-driven user lifecycle. Auth: `Authorization: Bearer <SCIM_BEARER_TOKEN>`
(endpoints return `403` until the token is configured).

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
| GET | `/health/ready` | Readiness — probes DB, Redis, ClickHouse, Kafka; `503` if a critical dep is down. |
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
| POST | `/integrations/{id}/sync` | ANALYST+ | Pull provider metadata in. For GitHub this imports deployments as markers for deploy↔anomaly correlation (needs `config.repo` = `owner/name`). |

## Sources & connectors

| Method | Path | Role | Description |
|--------|------|------|-------------|
| GET/POST/DELETE | `/sources` … | varies | List / upload / delete log files. |
| GET/POST | `/connectors/{k8s,aws,docker}/…` | varies | Discover and fetch logs. Returns `"simulated"` sample data when the backend is absent. |

## Administration

| Method | Path | Role | Notes |
|--------|------|------|-------|
| GET/POST/DELETE | `/users` … | ADMIN | Manage operators. `GET` is **paginated**. |
| GET/PUT | `/settings` | VIEWER+/ADMIN | Read / update platform settings. |
| WS | `/stream?token=` | any | Live per-tenant log tail (Redis pub/sub). |

## Conventions

- **Pagination**: list endpoints accept `limit` (1–1000, default 200) and `offset` (≥0).
- **Errors**: JSON `{error, detail, request_id}`; every response carries `X-Request-ID`.
- **Time**: `from_ts` / `to_ts` are epoch milliseconds.
