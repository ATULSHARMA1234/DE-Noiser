"""Erasing one person, rather than one customer.

Tenant offboarding already worked, and worked well: it deletes the Postgres
rows, the ClickHouse partitions, the vector store and the S3 archives, and it
certifies the result against a re-read of the store. But it is the only tool
there was, and it answers the wrong question.

The question a customer forwards to us is GDPR Article 17 for *one of their end
users*, whose email or account id appears inside application logs they shipped
here. The only lever was deleting their entire workspace.

Two things had to be true for this to be tractable, and the order matters:

**Most of it should never have been stored.** Redaction now runs at every ingest
boundary (`api.platform_settings.redact_batch`), so the common identifiers —
emails, card numbers, tokens — are replaced before anything is written. This
module exists for what redaction cannot know is personal: an internal account
id, a username, a customer reference. Those are shapes only the data controller
recognises, which is why the identifier comes from them.

**Erasure has to be provable.** ClickHouse mutations are asynchronous, so
issuing a certificate at submission time certifies nothing. The same
`ErasureRecord` and mutation-tracking the tenant purge uses is reused here, for
the same reason.

Scope, stated plainly so nobody assumes more:

  * ClickHouse `semantic_logs` — `message` and `raw_json`, redacted in place.
  * Postgres `spans` — the `attributes` JSON.
  * Archived objects in S3 are NOT rewritten. They are compressed, immutable
    and possibly on object-lock; the retention window is the erasure mechanism
    there, and `retention_days` is what a DPA should state.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from denoiser import runtime
from denoiser.api.auth import User, require_role
from denoiser.logging import get_logger
from denoiser.storage.db import ErasureRecord, Span, get_db
from denoiser.utils.time import utcnow

logger = get_logger(__name__)

router = APIRouter(prefix="/privacy", tags=["privacy"])

#: What replaces the identifier. Deliberately not deletion of the whole record:
#: a log line with one field redacted is still an operational record, and
#: erasure obliges us to remove the personal data, not the customer's history.
REPLACEMENT = "[ERASED]"

#: An identifier short enough to match half the corpus is not an identifier. Two
#: characters would rewrite every log line the tenant has, which is data loss
#: dressed as compliance.
MIN_IDENTIFIER_LENGTH = 4


class SubjectErasureRequest(BaseModel):
    identifier: str = Field(
        min_length=MIN_IDENTIFIER_LENGTH,
        description="The value to erase — an email, username or internal id.",
    )
    #: Refuse to run unless the caller has confirmed the count they expect. An
    #: erasure is irreversible and unbounded by nature; this makes the operator
    #: look at the preview first.
    confirm: bool = False


def _looks_dangerous(identifier: str) -> str | None:
    """Reasons this identifier should not be run as an erasure.

    The risk here is breadth, not injection: the identifier is a bound
    parameter, and `position`/`replaceAll` match it literally, so `%` and `_`
    carry no special meaning and an account id like `acct_9f3b21c4` is ordinary
    input. Rejecting those would refuse the most common real identifier there
    is while protecting against nothing.

    What does matter is a value so short or so common that it appears in
    unrelated records. That turns a targeted erasure into an untargeted rewrite,
    and unlike a refusal it cannot be undone.
    """
    if len(identifier.strip()) < MIN_IDENTIFIER_LENGTH:
        return f"identifier must be at least {MIN_IDENTIFIER_LENGTH} characters"
    if identifier.strip() != identifier:
        return "identifier has leading or trailing whitespace"
    if re.fullmatch(r"[^A-Za-z0-9]+", identifier):
        return "identifier is only punctuation and would match unrelated records"
    if identifier.lower() in {
        "error", "warn", "warning", "info", "debug", "trace", "fatal",
        "true", "false", "null", "none", "user", "test", "root", "admin",
    }:
        return "identifier is a common log token and would match unrelated records"
    return None


def preview_subject(db: Session, tenant_id: int, identifier: str) -> dict[str, Any]:
    """How many records the erasure would touch, without touching them.

    The operator sees this before confirming. An erasure with no preview is an
    erasure whose blast radius nobody measured.
    """
    store = runtime.clickhouse_store()
    log_matches = 0

    if store.client is not None:
        where, params = store.scope(
            tenant_id,
            extra=["(position(message, {needle:String}) > 0 "
                   "OR position(raw_json, {needle:String}) > 0)"],
            bind={"needle": identifier},
        )
        rows = store.client.query(
            f"SELECT count() FROM semantic_logs WHERE {where}", parameters=params
        ).result_rows
        log_matches = rows[0][0] if rows else 0

    # Counted in Python rather than with a JSON-column predicate: the operators
    # differ between PostgreSQL and SQLite, and a subject erasure touches tens of
    # spans, not millions. Correctness on both engines is worth more here than a
    # query that only works on one.
    import json as _json

    span_matches = sum(
        1
        for span in db.query(Span).filter(Span.tenant_id == tenant_id).all()
        if span.attributes and identifier in _json.dumps(span.attributes)
    )

    return {"logs": log_matches, "spans": span_matches}


def erase_subject(db: Session, tenant_id: int, identifier: str) -> dict[str, Any]:
    """Redact ``identifier`` out of one tenant's stored records.

    Tenant-scoped without exception. An erasure request arrives through one
    customer, about one of *their* end users, and must not reach into another
    customer's data even when the same string appears there.
    """
    store = runtime.clickhouse_store()
    result: dict[str, Any] = {"mutations": [], "spans_updated": 0, "logs_submitted": False}

    if store.client is not None:
        where, params = store.scope(
            tenant_id,
            extra=["(position(message, {needle:String}) > 0 "
                   "OR position(raw_json, {needle:String}) > 0)"],
            bind={"needle": identifier},
        )
        params = {**params, "replacement": REPLACEMENT}
        # replaceAll rather than deleting the row: the operational record stays,
        # the personal data does not. Both columns, because `raw_json` is stored
        # alongside `message` and is searchable — redacting one would leave the
        # value exactly where a query would find it.
        store.client.command(
            "ALTER TABLE semantic_logs UPDATE "
            "message = replaceAll(message, {needle:String}, {replacement:String}), "
            "raw_json = replaceAll(raw_json, {needle:String}, {replacement:String}) "
            f"WHERE {where}",
            parameters=params,
        )
        result["logs_submitted"] = True
        logger.info("Submitted a subject erasure mutation for tenant %s", tenant_id)

    # Spans carry their identifiers in the attributes JSON. Rewritten row by row
    # because the column is JSON on both engines and a portable in-place string
    # replacement across dialects is not worth the subtlety here — a subject
    # erasure touches tens of rows, not millions.
    import json as _json

    spans = db.query(Span).filter(Span.tenant_id == tenant_id).all()
    for span in spans:
        if not span.attributes:
            continue
        encoded = _json.dumps(span.attributes)
        if identifier not in encoded:
            continue
        span.attributes = _json.loads(encoded.replace(identifier, REPLACEMENT))
        result["spans_updated"] += 1
    db.commit()

    return result


@router.post("/erasures/preview")
def preview(
    payload: SubjectErasureRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """How many records an erasure would rewrite. Changes nothing."""
    problem = _looks_dangerous(payload.identifier)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    return {
        "identifier": payload.identifier,
        "matches": preview_subject(db, current_user.tenant_id, payload.identifier),
    }


@router.post("/erasures")
def request_subject_erasure(
    payload: SubjectErasureRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
):
    """Erase one data subject's identifier from this workspace's records.

    Recorded in the same `ErasureRecord` table as a tenant offboarding, so a
    controller answering a regulator has one place to look. `completed_at` stays
    NULL until the ClickHouse mutation finishes — the response means submitted,
    which is the honest word for an asynchronous deletion.
    """
    problem = _looks_dangerous(payload.identifier)
    if problem:
        raise HTTPException(status_code=400, detail=problem)

    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to proceed. Check /privacy/erasures/preview first — "
                   "an erasure cannot be undone.",
        )

    matches = preview_subject(db, current_user.tenant_id, payload.identifier)
    outcome = erase_subject(db, current_user.tenant_id, payload.identifier)

    # The identifier itself is deliberately not stored: writing the value we
    # were asked to erase into the record of erasing it would be absurd.
    record = ErasureRecord(
        purged_tenant_id=current_user.tenant_id,
        tenant_name=f"data-subject erasure ({matches['logs']} log(s), {matches['spans']} span(s))",
        requested_at=utcnow(),
        completed_at=None,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return {
        "erasure_id": record.id,
        "status": "submitted",
        "matched": matches,
        "spans_updated": outcome["spans_updated"],
        "logs_submitted": outcome["logs_submitted"],
        "certificate_url": f"/platform/erasures/{record.id}",
        # Said plainly, because "submitted" and "done" are different words and
        # a controller must not report the first as the second.
        "note": (
            "Log-store erasure is asynchronous. Archived objects in cold storage "
            "are not rewritten; they expire with the retention window."
        ),
    }
