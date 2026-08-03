"""
Drop-in ingestion compatibility for the two largest installed bases:

  * **Elasticsearch Bulk API** (`POST /_bulk`) — lets an existing Filebeat /
    Logstash / Beats `elasticsearch` output ship to SemanticOS with no client
    change beyond the URL.
  * **Splunk HTTP Event Collector** (`POST /services/collector`) — lets existing
    Splunk HEC forwarders do the same.

Both parse their native wire format, normalize to the standard log-record shape,
and persist through the same path as every other source. Preflight/health probes
those clients make before sending are answered so the shippers consider the
endpoint healthy.
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from denoiser import runtime
from denoiser.api.auth import verify_ingest_auth
from denoiser.logging import get_logger
from denoiser.settings import is_testing
from denoiser.storage.db import Tenant, get_db

logger = get_logger(__name__)

router = APIRouter(tags=["Compat"])


# ── Shared persistence (same fan-out as the OTLP path) ───────────────────────

async def _persist(records: list[dict[str, Any]], tenant_id: str) -> int:
    if not records:
        return 0

    # Through the shared sink, not a local file: see denoiser.storage.raw_log_sink.
    # Off the event loop because both implementations block (disk or a PUT).
    from denoiser.api.platform_settings import raw_log_storage_enabled

    if raw_log_storage_enabled():
        await run_in_threadpool(
            runtime.raw_log_sink().write, tenant_id, [json.dumps(r) for r in records]
        )

    if runtime.clickhouse_store().client:
        runtime.clickhouse_store().insert_logs(records, tenant_id=tenant_id)

    try:
        async with runtime.redis_client().pipeline(transaction=False) as pipe:
            for r in records:
                pipe.publish(f"log_stream:{tenant_id}", json.dumps(r))
            await pipe.execute()
    except Exception as e:
        logger.debug(f"compat: redis publish skipped: {e}")

    return len(records)


def _resolve_tenant(api_key: str, db: Session) -> str:
    tenant = db.query(Tenant).filter(Tenant.api_key == api_key).first()
    if tenant:
        return str(tenant.id)
    static = os.getenv("INGEST_API_KEY") or ("semanticos-ingest-key-123" if is_testing() else None)
    if static and api_key == static:
        default = db.query(Tenant).order_by(Tenant.id).first()
        return str(default.id) if default else "default_tenant"
    raise HTTPException(status_code=401, detail="Invalid token")


# ── Elasticsearch Bulk API ───────────────────────────────────────────────────

def parse_bulk(raw: bytes) -> list[dict[str, Any]]:
    """Parse Elastic NDJSON bulk: alternating action/source lines."""
    lines = raw.decode("utf-8", errors="replace").splitlines()
    docs: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        try:
            action = json.loads(line)
        except json.JSONDecodeError:
            i += 1
            continue
        if not isinstance(action, dict) or not action:
            i += 1
            continue
        op = next(iter(action))
        if op in ("index", "create", "update"):
            if i + 1 < len(lines):
                try:
                    doc = json.loads(lines[i + 1])
                except json.JSONDecodeError:
                    doc = None
                if isinstance(doc, dict):
                    # `update` wraps the payload under "doc".
                    docs.append(doc.get("doc", doc) if op == "update" else doc)
            i += 2
        elif op == "delete":
            i += 1  # delete has no source line
        else:
            i += 1
    return docs


@router.get("/")
def elastic_root() -> dict[str, Any]:
    """Version stub so an Elastic client's preflight check passes."""
    return {
        "name": "semanticos",
        "cluster_name": "semanticos",
        "version": {
            "number": "8.11.0",
            "build_flavor": "default",
            "lucene_version": "9.8.0",
            "minimum_wire_compatibility_version": "7.17.0",
            "minimum_index_compatibility_version": "7.0.0",
        },
        "tagline": "You Know, for Search",
    }


@router.post("/_bulk")
@router.post("/{index}/_bulk")
async def elastic_bulk(request: Request, tenant_id: str = Depends(verify_ingest_auth)):
    """Elasticsearch `_bulk` ingestion. Returns a bulk-shaped response so Beats/
    Logstash treat the write as successful."""
    raw = await request.body()
    docs = parse_bulk(raw)
    ingested = await _persist(docs, tenant_id)
    return {
        "took": 0,
        "errors": False,
        "items": [{"index": {"_index": "semanticos", "status": 201, "result": "created"}} for _ in range(ingested)],
    }


# ── Splunk HTTP Event Collector ──────────────────────────────────────────────

def parse_hec(raw: bytes, is_raw: bool = False) -> list[dict[str, Any]]:
    """Parse a Splunk HEC body.

    The event endpoint accepts one or more JSON event objects concatenated
    (whitespace-separated, *not* a JSON array). The raw endpoint is plain text,
    one log per line.
    """
    text = raw.decode("utf-8", errors="replace")
    if is_raw:
        return [{"message": line, "source": "splunk-hec-raw"} for line in text.splitlines() if line.strip()]

    logs: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    idx, n = 0, len(text)
    while idx < n:
        while idx < n and text[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            break
        idx = end
        logs.append(_hec_event_to_log(obj))
    return logs


def _hec_event_to_log(e: Any) -> dict[str, Any]:
    if not isinstance(e, dict):
        return {"message": str(e), "source": "splunk-hec"}
    event = e.get("event")
    log: dict[str, Any] = dict(event) if isinstance(event, dict) else {"message": "" if event is None else str(event)}
    if "time" in e:
        log.setdefault("timestamp", e["time"])
    # Splunk metadata → the platform's source dimension.
    if "source" not in log:
        log["source"] = e.get("source") or e.get("sourcetype") or e.get("host") or "splunk-hec"
    for k in ("host", "sourcetype", "index"):
        if k in e:
            log.setdefault(k, e[k])
    return log


def verify_hec_auth(authorization: str | None = Header(None), db: Session = Depends(get_db)) -> str:
    """Validate `Authorization: Splunk <token>` and resolve the tenant."""
    if not authorization or not authorization.lower().startswith("splunk "):
        raise HTTPException(status_code=401, detail="Missing Splunk HEC token")
    token = authorization.split(" ", 1)[1].strip()
    return _resolve_tenant(token, db)


@router.get("/services/collector/health")
def hec_health() -> dict[str, Any]:
    """HEC health probe (unauthenticated, as Splunk forwarders expect)."""
    return {"text": "HEC is available and accepting input", "code": 17}


@router.post("/services/collector")
@router.post("/services/collector/event")
async def hec_event(request: Request, tenant_id: str = Depends(verify_hec_auth)):
    raw = await request.body()
    logs = parse_hec(raw, is_raw=False)
    await _persist(logs, tenant_id)
    return {"text": "Success", "code": 0}


@router.post("/services/collector/raw")
async def hec_raw(request: Request, tenant_id: str = Depends(verify_hec_auth)):
    raw = await request.body()
    logs = parse_hec(raw, is_raw=True)
    await _persist(logs, tenant_id)
    return {"text": "Success", "code": 0}
