# Operations Runbook

How to deploy, harden, back up, and restore a self-hosted SemanticOS instance.

## Deploying with Helm

The chart lives in [`deploy/helm/semanticos`](../deploy/helm/semanticos). It deploys
the API, analysis worker, ingestion worker, web UI, and the syslog connector,
runs database migrations as a pre-install/upgrade Job, and creates a
ServiceAccount + RBAC so the in-cluster Kubernetes connector can read pod logs.

Datastores (Postgres, Redis, ClickHouse, Redpanda, object store) are **external
by design** — point the chart at managed/operator-run instances.

```bash
helm install semanticos deploy/helm/semanticos \
  --namespace semanticos --create-namespace \
  --set secrets.jwtSecretKey="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" \
  --set secrets.adminPassword="$ADMIN_PW" \
  --set secrets.ingestApiKey="$INGEST_KEY" \
  --set secrets.scimBearerToken="$SCIM_TOKEN" \
  --set postgres.host=pg.internal --set postgres.password="$PGPASS" \
  --set clickhouse.host=clickhouse.internal --set clickhouse.password="$CHPASS" \
  --set redis.url=redis://redis.internal:6379/0 \
  --set kafka.broker=redpanda.internal:9092
```

Prefer a **pre-created Secret** managed by Vault / Sealed Secrets / External
Secrets Operator, then `--set secrets.existingSecret=my-secret` (keys:
`database-url`, `jwt-secret-key`, `admin-password`, `ingest-api-key`,
`scim-bearer-token`, `clickhouse-password`).

Migrations run automatically via the Job; deployments set
`SEMANTICOS_AUTO_MIGRATE=0` so replicas never race to migrate.

## Production readiness checklist

- [ ] **Secrets** supplied via a managed Secret (`secrets.existingSecret`), not `--set`.
- [ ] `JWT_SECRET_KEY` is ≥32 random chars (the app refuses to boot otherwise in prod).
- [ ] `SEMANTICOS_ENV=production` (enables the strict startup safety checks).
- [ ] TLS terminated at the ingress; `CORS_ALLOWED_ORIGINS` set to real https origins.
- [ ] Datastores are managed/HA (Postgres with replicas + PITR, ClickHouse cluster, Redpanda).
- [ ] Resource requests/limits reviewed for the analysis worker (embedding model is memory-heavy).
- [ ] Images pinned to an immutable tag/digest, pulled from your private registry.
- [ ] Pods run as non-root (default), capabilities dropped (default).
- [ ] Monitoring scrapes `GET /internal/metrics`; alerting on `GET /health/ready` != 200.
- [ ] Backups scheduled and a restore has been **tested** (see below).
- [ ] Log retention / data-tiering configured per tenant (`cleanup_old_data`).

## Secrets: sourcing and rotation

Every setting may be supplied as a **file** instead of an environment variable:
set `<VAR>_FILE` to a mounted path (`JWT_SECRET_KEY_FILE`,
`SCIM_BEARER_TOKEN_FILE`, `DATABASE_URL_FILE`, …). That is the shape Vault
Agent, External Secrets and the AWS/GCP secret CSI drivers already mount, and
unlike an env var a mounted file can be updated under a running process. An
explicit env var always wins over the file.

### Rotating the JWT signing key with no forced sign-out

The signing key rotates through an **overlap window**: `JWT_SECRET_KEY` signs
new tokens, `JWT_SECRET_KEY_PREVIOUS` (comma-separated, most recent first) is
still accepted while tokens signed with it drain. Every token carries a `kid`
header identifying its key, so verification picks the right one.

1. Move the current key to the retired list and install the new one:
   ```bash
   JWT_SECRET_KEY=<new 32+ char random>
   JWT_SECRET_KEY_PREVIOUS=<the key being retired>
   ```
   With file-backed secrets, write the two files — replicas pick the change up
   within `JWT_KEYRING_REFRESH_SECONDS` (default 30s) without a restart.
2. Confirm every replica is signing with the new key:
   ```bash
   curl -H "Authorization: Bearer $ADMIN_TOKEN" https://api.your-host/admin/signing-keys
   # {"active_kid": "...", "retired_kids": ["..."], "accepts_retired_tokens": true}
   ```
3. Wait out the longest token lifetime — `REFRESH_TOKEN_EXPIRE_MINUTES`
   (default 7 days) — so no valid token is still signed with the retired key.
4. Drop `JWT_SECRET_KEY_PREVIOUS`. Tokens signed with the old key now fail.

The startup checks refuse production if the retired list contains the active key
(the rotation never took effect) or the publicly known development default.

To force an *immediate* invalidation instead — a suspected key compromise — skip
step 3: rotate with no `JWT_SECRET_KEY_PREVIOUS` at all. Every outstanding token
dies and all users re-authenticate, which is the correct trade under compromise.

### Rotating a tenant's API key

The tenant API key is what customers paste into their log shippers, so rotating
it naively breaks every agent at the same instant. It rotates through the same
overlap shape as the signing key: the superseded key keeps authenticating for a
bounded window while shippers are updated one at a time.

1. Issue the new key. It is returned **once** and is not retrievable again:
   ```bash
   curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
        -H 'Content-Type: application/json' -d '{"overlap_hours": 24}' \
        https://api.your-host/admin/tenant/api-key/rotate
   ```
2. Roll the new key out to your shippers. Both keys authenticate during the
   window; requests on the old key are logged with the overlap's end time.
3. Close the window as soon as the fleet is updated, rather than waiting for it
   to lapse:
   ```bash
   curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
        https://api.your-host/admin/tenant/api-key/revoke-previous
   ```

For a leaked key, rotate with `{"overlap_hours": 0}` — the old key stops working
immediately, which is the correct trade under compromise.

### Rotating the shared ingest and SCIM tokens

`INGEST_API_KEY` (unattended shippers) and `SCIM_BEARER_TOKEN` (the IdP's
provisioning calls) follow the same convention as the signing key: set
`<VAR>_PREVIOUS` to the value being retired — comma-separated, most recent
first — roll the new value out to the shippers or the IdP, then drop the
`_PREVIOUS` entry. Both accept `<VAR>_FILE` for a mounted secret.

`GET /admin/credentials` reports the rotation state of everything above —
whether each secret is configured, whether a superseded value is still being
accepted, and when this tenant's API key was last rotated. It never returns a
secret's value.

## Tenant API quotas

Each tenant gets a sliding-window request ceiling across the whole API, keyed on
the tenant (resolved from `X-API-Key` or the Bearer subject) rather than the
client IP — a workspace shipping from a hundred pods is one bucket, not a
hundred. Defaults per 60s window, tunable without a code change:

| Tier | Requests / window | Override |
|------|-------------------|----------|
| free | 600 | `TENANT_QUOTA_FREE` |
| pro | 6,000 | `TENANT_QUOTA_PRO` |
| enterprise | 60,000 | `TENANT_QUOTA_ENTERPRISE` |

Window length is `TENANT_QUOTA_WINDOW_SECONDS`; `TENANT_QUOTA_ENABLED=false`
disables enforcement. Responses carry `X-RateLimit-Limit` / `-Remaining`, and a
breach returns 429 with `Retry-After`. Health, metrics and auth routes are
exempt so a quota breach cannot lock an operator out of their own workspace.
The window lives in Redis so it is shared across replicas; if Redis is down it
degrades to a per-replica in-memory window rather than failing open entirely.

## The ingestion consumer is on the write path

`POST /ingest` returns 200 as soon as a record is handed to Kafka. If the
ingestion consumer is not running, the topic fills and **nothing reaches
ClickHouse** — successful writes, and no logs to query.

Readiness checks this. The consumer publishes a heartbeat (with its current
lag) to Redis every few seconds, and `GET /health/ready` reports it as
`ingestion_consumer`. A missing, stale, or badly-lagging consumer fails
readiness with 503:

```json
{"status": "degraded",
 "checks": {"database": "ok", "redis": "ok", "kafka": "ok",
            "ingestion_consumer": "error: no ingestion consumer has checked in (is the worker running?)"}}
```

When no Kafka producer is configured the API writes to ClickHouse directly, the
consumer is not in the path, and the check reports `not_required`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `INGESTION_HEARTBEAT_INTERVAL` | 5s | How often the consumer checks in. |
| `INGESTION_HEARTBEAT_STALE_SECONDS` | 60s | Age at which the consumer is treated as gone. |
| `INGESTION_LAG_CRITICAL` | 500,000 | Backlog above which readiness fails. |

## Usage metering and retention

The nightly pass (midnight UTC, on the platform's own beat) meters each
tenant's ingest volume into `billing_meters` and applies their tier's retention
to ClickHouse: free 7 days, pro 30, enterprise 90.

- `GET /admin/usage?days=30` — daily logs/bytes/traces for the caller's tenant.
- `POST /admin/usage/recalculate` — re-meter today now. Retention is **not**
  applied by this call; asking for a fresh number must not delete data.

Metering needs the analysis worker's **beat** running
(`celery -A denoiser.workers.analysis_worker beat`), not just a worker.

## Scaling

| Component | Scaling |
|-----------|---------|
| Analysis worker, ingestion worker, syslog | Stateless — scale replicas freely. |
| API | Stateless for configuration (settings live in the database). `live_stream.log` is a local convenience buffer; nothing depends on it being shared. |
| Web | Stateless. |

Operator settings used to live in `data/settings.json` on the API's own disk,
which required a ReadWriteMany volume for more than one replica. They are now a
row in the database; an existing `settings.json` is imported automatically on
first boot after upgrading, and can then be deleted.

## Host telemetry vs. monitored services

`GET /vitals` and the Command Center's vitals panel report the CPU, memory,
disk and network of **the node running SemanticOS** — under Kubernetes, the API
pod. They say nothing about the services you are monitoring. Every sample is
stamped with `scope: semanticos_api_host` and its hostname so the numbers cannot
be mistaken for fleet metrics. Set `HOST_TELEMETRY_ENABLED=false` to turn the
collection off entirely.

Kernel events from the eBPF collector (TCP retransmits, OOM kills; Linux only,
requires `bcc`) are exposed at `GET /telemetry/kernel-events` and folded into
anomaly correlation, so an OOM kill next to a burst of anomalies shows up as
evidence rather than sitting unread in a file.

### Analysis input cap

A single analysis run bounds how many raw log lines it pulls into memory, so a
multi-million-line source cannot OOM a worker. The cap defaults to **500,000
lines** and is configurable:

- per request: `max_lines` in the analysis request body;
- globally: `SEMANTICOS_MAX_ANALYSIS_LINES` (worker env).

When a run hits the cap it still completes, and the result carries
`"truncated": true` with the effective `max_lines`. Raise the cap only after
confirming worker memory headroom (rough guide: ~1 KB resident per line through
the polars/dedup stage, so 500k lines ≈ a few hundred MB peak).

## Capacity & load testing

Throughput depends entirely on your hardware and broker/ClickHouse sizing — no
fixed rate is guaranteed. **Do not quote a throughput number to a customer that
you have not measured on representative hardware.** Measure it before every
capacity commitment:

```bash
# Authenticated /ingest load test (API key or JWT).
python scripts/loadtest.py --url https://api.your-host \
    --api-key "$INGEST_API_KEY" \
    --concurrency 16 --duration 60 --batch 200
```

Record results against the environment so the numbers are reproducible:

| Field | Example | Notes |
|-------|---------|-------|
| API replicas / CPU·mem | 3 × (2 vCPU, 2 GiB) | from Helm `values.yaml` |
| Broker | Redpanda 3-node | partitions per topic |
| ClickHouse | 3 shards × 2 replicas | disk type matters |
| concurrency / batch | 16 / 200 | loadtest flags |
| **Ingest throughput** | _measure_ | logs/sec sustained |
| **p50 / p95 / p99 latency** | _measure_ | from loadtest output |
| Error rate | _measure_ | should be ~0 at steady state |

Scale the API and ingestion/analysis workers horizontally (all stateless) until
ingest latency and consumer lag are steady, then record the sustained rate as
your supported ceiling for that configuration.

## Backup & restore

**What holds state:** PostgreSQL (users, tenants, runs, incidents, SLOs,
runbooks, audit), ClickHouse (logs + traces), and the object store (archives,
uploaded sources, the raw ingest copy). Redis and Redpanda are transient. The
API's PVC holds only regenerable local files and does not need backup.

### Automated backups

The chart ships a nightly CronJob. It is **off by default** because it needs a
bucket and credentials — turn it on for anything holding real data:

```yaml
backup:
  enabled: true
  schedule: "0 1 * * *"          # before the 02:00 archival job
  bucket: "semanticos-backups"
  endpoint: ""                   # set for MinIO
  retentionDays: 30
```

It dumps Postgres (`pg_dump -Fc`) and the ClickHouse log and span tables,
uploads both to the bucket, and **verifies the objects landed** before exiting
zero. A backup job that exits successfully without checking is a green
dashboard and no backup.

### Recovery objectives

| | Value | What sets it |
|---|---|---|
| **RPO** | 24 hours | The CronJob interval. Tighten by lowering `backup.schedule`, or properly with Postgres WAL archiving (PITR, ~seconds) and incremental ClickHouse snapshots. |
| **RTO** | **~1.3 s per million rows**, plus fixed pod/scheduling time | Measured, see the drill record below. Dominated by the ClickHouse `Native` reload, which is linear in row count. |

These are the defaults this chart establishes, not a recommendation. Both are
policy decisions that belong to whoever owns the data.

**Extrapolating the RTO.** The measured figure is data-restore time only. A real
recovery adds provisioning, image pulls, the migration Job and readiness —
budget 5–10 minutes of fixed cost on top, and re-measure at your own volume
rather than scaling the number below by eye. Object-store bandwidth, not CPU,
usually dominates once the dump is measured in gigabytes.

### The restore drill

**A backup nobody has restored is a hypothesis.** `scripts/restore_drill.py`
runs the whole loop against live datastores — seed, back up with the CronJob's
own commands, destroy both stores, restore, and verify:

```bash
python scripts/restore_drill.py \
  --postgres postgresql://user:pass@host:5432/semanticos \
  --clickhouse http://clickhouse:8123 \
  --rows 500000 --report drill.json
```

It verifies row counts, **content** (a known record must come back — a restore
that recreates the schema and no rows passes a count check against an empty
expectation) and **tenant attribution** (a restore that loses it deposits one
customer's data into everybody's account). Exit code 1 means the backups do not
restore.

Run it quarterly and append the result. For a full disaster-recovery rehearsal,
do the same against a scratch namespace: `helm install` into empty datastores,
restore, then confirm `GET /health/ready` is green and a `/v1/logs/query`
returns rows from before the backup.

| Date | Postgres | ClickHouse | Backup size | Backup | **Restore (RTO)** | Result |
|------|----------|------------|-------------|--------|-------------------|--------|
| 2026-08-04 | 500,000 rows | 500,000 rows | 29 MB | 0.48 s | **1.34 s** | passed — counts, content and tenant attribution all verified |

> The first run of this drill found two defects in the backup CronJob that
> reading it had not: it dumped tables named `logs` and `spans`, which do not
> exist (they are `semantic_logs` and `semantic_traces`), so **ClickHouse was
> silently not being backed up at all**; and the schema dump used the default
> TabSeparated format, which escapes the newlines inside `SHOW CREATE TABLE`
> output, so the saved DDL was one unusable line. Both are fixed. This is what
> the drill is for.

### PostgreSQL

```bash
# Backup (schema + data)
pg_dump --format=custom --no-owner "$DATABASE_URL" > semanticos-pg-$(date +%F).dump

# Restore into an empty database
pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" semanticos-pg-YYYY-MM-DD.dump
```

For production use continuous archiving / PITR (WAL-G, pgBackRest, or your
managed provider's snapshots) rather than periodic dumps alone.

### ClickHouse

```bash
# Full backup to a configured disk (recommended: the clickhouse-backup tool)
clickhouse-client --query "BACKUP DATABASE default TO Disk('backups','semanticos-$(date +%F)')"

# Restore
clickhouse-client --query "RESTORE DATABASE default FROM Disk('backups','semanticos-YYYY-MM-DD')"
```

Logs are also recoverable from Redpanda if the ingestion worker's consumer
offsets have not advanced past them, but treat ClickHouse backups as the source
of truth.

### Object store (MinIO / S3)

```bash
mc mirror local/semanticos-logs backup/semanticos-logs      # backup
mc mirror backup/semanticos-logs local/semanticos-logs      # restore
```

### Restore order (disaster recovery)

1. Restore PostgreSQL, then run `alembic upgrade head` (or let the migrate Job run).
2. Restore ClickHouse and the object store.
3. Deploy the app (`helm upgrade --install …`).
4. Verify `GET /health/ready` returns `200` with every dependency `ok`.
5. Confirm ingestion by sending a test log and querying it back via `/v1/logs/query`.

## Observability

- **Liveness:** `GET /health/live`
- **Readiness (probes DB/Redis/ClickHouse/Kafka):** `GET /health/ready`
- **Prometheus metrics:** `GET /internal/metrics` — **authenticated**, see below
- **Alert rules:** [`deploy/prometheus/alerts.yaml`](../deploy/prometheus/alerts.yaml)
- **Load testing:** `python scripts/loadtest.py --help`

### Scraping the metrics endpoint

The exposition names every route this deployment serves, with its traffic
volume and error rate — a live map of the system. It requires a bearer token,
and in production the API **refuses the scrape** when `METRICS_TOKEN` is unset
rather than publishing that map because somebody forgot a variable. In
development, an unset token leaves it open so `curl` still works.

```yaml
# prometheus.yml
scrape_configs:
  - job_name: semanticos
    metrics_path: /internal/metrics
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/semanticos-metrics-token
    static_configs:
      - targets: ["semanticos-api:8000"]
```

Set the token via `secrets.metricsToken` (or the `metrics-token` key of your
existing Secret).

### What the metrics cover

Beyond request rate, errors and latency, two series exist because nothing else
in the system reports them:

| Series | Why it matters |
|---|---|
| `semanticos_ingestion_dead_lettered_total` | Records the pipeline could not write. They were accepted with a `200` and will never be queryable. Silent data loss, previously with no signal at all. |
| `semanticos_ingestion_consumer_up` / `_lag` | `/ingest` returns `200` as soon as a record reaches Kafka. If the consumer is down, writes succeed and nothing becomes queryable. The consumer is a separate pod with no HTTP surface, so this is the only place a scraper can see it. |

### Forwarding audit events to a SIEM

Audit records are written to Postgres and, when configured, copied to your SIEM.
An audit trail that lives only inside the audited system is one an attacker with
sufficient access can edit.

```bash
SIEM_HOST=siem.internal      # unset disables forwarding
SIEM_PORT=514
SIEM_PROTOCOL=udp            # udp | tcp | tls
SIEM_FORMAT=cef              # cef (ArcSight, parsed natively by Splunk/Sentinel/QRadar) | syslog
```

Delivery is best-effort and never fails the request: the database row is the
system of record, and an unreachable collector must not become a customer-facing
outage. Failures are logged at warning level — alert on them if the SIEM copy is
a compliance requirement rather than a convenience.
