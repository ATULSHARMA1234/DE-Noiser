# Data processing, retention and erasure

What SemanticOS stores, where it goes, how long it stays and how it is deleted.
This is the material a customer's DPA needs and the questions a security review
asks. It describes what the software does — the commercial terms around it are
a separate document.

## Roles

In a normal deployment the customer is the **data controller** and SemanticOS is
software they run themselves. On-premise means the vendor is not a processor at
all: no customer log data reaches the vendor.

That changes if either of two things is configured. Both are called out below,
because both are ways the on-premise property is lost without anyone deciding
to lose it.

## What is stored

| Data | Store | Contains |
|---|---|---|
| Log records | ClickHouse `semantic_logs` | Message text and the original JSON, **redacted at ingest** |
| Spans | ClickHouse `semantic_traces`, mirrored in PostgreSQL `spans` | Service, operation, timings, attributes |
| Raw copy | S3 / MinIO, when `store_raw_logs` is on | Redacted log lines |
| Cold archive | S3 / MinIO | Gzipped records past the archive window |
| Vectors | LanceDB | Embeddings of log templates |
| Control plane | PostgreSQL | Users, tenants, incidents, issues, runbooks, audit log |

### Redaction

Applied at every ingest boundary — `/ingest`, `/v1/logs`, the Kafka consumer and
the Elastic-compatible bulk endpoint — before any store sees the record, with a
second pass inside the store write as a backstop. Patterns cover emails, card
numbers, tokens, keys and similar.

It was previously applied on one path only, so records arriving over OTLP — the
documented enterprise integration — were stored verbatim. If your deployment
predates that fix, records ingested over `/v1/logs` before it may contain
unredacted values, and the subject-erasure endpoint below is the tool for them.

Redaction is pattern-based. It cannot recognise an identifier that only the
controller knows is personal: an internal account id, a username, a customer
reference. Those are what subject erasure exists for.

## Retention

Per plan, applied daily. Archival to cold storage runs immediately before the
deletion it protects, in the same pass — deleting first and archiving later, on
two separate schedules, is what this used to do.

| Plan | Retention |
|---|---|
| Free | 7 days |
| Pro | 30 days |
| Enterprise | 90 days |

Cold archives in object storage expire with the object-storage lifecycle policy,
which the operator configures. **State this window in your DPA** — it is the
erasure mechanism for archived data, which is not rewritten in place.

## Sub-processors

An on-premise deployment has none by default. Two configurations add one:

**A hosted LLM endpoint.** `SLD_LLM_BASE_URL` pointed at a hosted API sends
redacted representative log lines from each analysed run to that provider.
Production boot refuses this unless `LLM_ALLOW_EXTERNAL=true` is set. If you set
it, name that provider in your DPA.

**Managed object storage.** S3, or any hosted S3-compatible endpoint, holds the
raw copy and the cold archive. A self-hosted MinIO does not leave your
infrastructure; a cloud bucket does, and its provider is a sub-processor.

Outbound integrations — Slack, PagerDuty, Teams, Jira, GitHub, generic webhooks
— send incident metadata (title, severity, failure domain, summary) when you
configure them, not raw log content. Each is a processor for that metadata.

## Data subject rights

### Access and export

Log query and export are available per tenant through `/query` and the console.
A controller answering a subject access request searches for the identifier and
exports the matching records.

### Erasure — one data subject

```
POST /privacy/erasures/preview   {"identifier": "acct_9f3b21c4"}
POST /privacy/erasures           {"identifier": "acct_9f3b21c4", "confirm": true}
```

Preview first: it reports how many records would be rewritten and changes
nothing. The erasure replaces the identifier with `[ERASED]` in ClickHouse log
messages and JSON, and in span attributes. The surrounding record survives —
erasure removes the personal data, not the customer's operational history.

Scoped to the calling workspace without exception, and recorded in
`erasure_records` alongside tenant offboardings, so there is one place to look
when a regulator asks.

**Two limits, stated because a controller must not report "submitted" as
"done":**

- ClickHouse deletes are asynchronous. The response means submitted; the
  certificate at `/platform/erasures/{id}` carries `completed_at` once the
  mutation finishes.
- Archived objects in cold storage are **not** rewritten. They are compressed
  and immutable, possibly under object lock. The retention window is the
  erasure mechanism there.

### Erasure — an entire customer

```
DELETE /platform/tenants/{id}
```

Deletes the PostgreSQL rows, the ClickHouse partitions, the vector store and the
S3 archives, and issues a certificate verified against a re-read of the store
rather than against the delete having been accepted.

`erasure_records` deliberately survives the tenant it describes: it is the
evidence the deletion happened, and it holds an id, a name, timestamps and
mutation ids — no customer data.

## Security controls relevant to a review

- Tenant isolation enforced through a single abstraction (`denoiser.api.scope`);
  another organisation's row returns 404, never 403, so ids cannot be enumerated.
- Rotating JWT signing keys with an overlap window; single-use refresh tokens.
- Real OIDC and SAML 2.0 with signature, audience, recipient, validity-window
  and replay checks; SCIM 2.0 provisioning with a per-tenant token.
- Outbound destinations validated against private, loopback and link-local
  ranges, re-resolved at send time.
- HSTS, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`,
  `Permissions-Policy` on every response; CSP in report-only mode by default.
- Production boot refuses a known-default JWT secret, a wildcard CORS origin, a
  plaintext origin, an enabled mock IdP, SQLite, or an unacknowledged remote LLM
  endpoint.
- Blocking dependency vulnerability scan in CI, with accepted exceptions
  recorded in `.github/pip-audit-ignore.txt` with a reason and an owner.
