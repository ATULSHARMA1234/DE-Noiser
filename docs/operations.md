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

## Scaling

| Component | Scaling |
|-----------|---------|
| Analysis worker, ingestion worker, syslog | Stateless — scale replicas freely. |
| API | Writes local state (`settings.json`, `live_stream.log`) to its data volume; running >1 replica needs a ReadWriteMany volume. |
| Web | Stateless. |

## Backup & restore

**What holds state:** PostgreSQL (users, tenants, runs, incidents, SLOs,
runbooks, audit), ClickHouse (logs + traces), and the object store (archives).
Redis and Redpanda are transient. The API's PVC holds only regenerable local
files and does not need backup.

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
- **Prometheus metrics:** `GET /internal/metrics` (request rate, errors, latency)
- **Load testing:** `python scripts/loadtest.py --help`
