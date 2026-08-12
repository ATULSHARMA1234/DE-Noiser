"""Platform settings, stored in the database rather than on a local disk.

These lived in ``data/settings.json`` on the API's own filesystem, which made
the API stateful: a second replica could not see a setting the first one wrote,
so running more than one required a ReadWriteMany volume and the two replicas
could still race each other's writes. The database is already shared by every
replica and already backed up, so that is where this belongs.

The legacy file is imported once on first read and then left alone, so an
existing deployment keeps its configuration across the upgrade.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from denoiser.logging import get_logger
from denoiser.storage.db import PlatformSetting, SessionLocal
from denoiser.utils.time import utcnow

logger = get_logger(__name__)

# One row holds the whole document. These are deployment-wide operator settings,
# not per-tenant ones, so there is exactly one of them.
SETTINGS_ROW_ID = 1

DEFAULT_SETTINGS: dict[str, Any] = {
    "store_raw_logs": True,
    "redact_pii": True,
    "llm_model": "llama-3.3-70b",
    "confidence_threshold": 70,
    "retention_days": 30,
    "sampling_threshold": 50000,
    "auto_analyze": False,
    "s3_enabled": False,
    "s3_endpoint": os.getenv("S3_ENDPOINT", "http://localhost:9000"),
    "s3_bucket": os.getenv("S3_BUCKET", "semanticos-logs"),
    # No credentials baked into source — supplied via env or the settings UI.
    "s3_access_key": os.getenv("S3_ACCESS_KEY", ""),
    "s3_secret_key": os.getenv("S3_SECRET_KEY", ""),
}


def _legacy_file() -> Path:
    return Path(os.getenv("SEMANTICOS_DATA_DIR", "data")) / "settings.json"


def _import_legacy_file() -> dict[str, Any] | None:
    """Read the pre-migration settings.json, if one is still lying around."""
    path = _legacy_file()
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text())
        if isinstance(loaded, dict):
            logger.info(f"Importing legacy settings from {path} into the database")
            return loaded
    except (OSError, ValueError) as e:
        logger.warning(f"Could not read legacy settings file {path}: {e}")
    return None


def load_settings(db=None) -> dict[str, Any]:
    """Current settings, with defaults filled in for keys added since they were saved."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        row = db.query(PlatformSetting).filter(PlatformSetting.id == SETTINGS_ROW_ID).first()
        stored = dict(row.data or {}) if row else None

        if stored is None:
            stored = _import_legacy_file()
            if stored is not None:
                save_settings(stored, db=db)

        # Defaults underneath, so a new setting appears without a migration and
        # an operator's saved value always wins.
        return {**DEFAULT_SETTINGS, **(stored or {})}
    except Exception as e:
        logger.error(f"Failed to load platform settings: {e}")
        return DEFAULT_SETTINGS.copy()
    finally:
        if owns_session:
            db.close()


def save_settings(settings: dict[str, Any], db=None) -> dict[str, Any]:
    """Persist settings for every replica. Returns what was stored."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        row = db.query(PlatformSetting).filter(PlatformSetting.id == SETTINGS_ROW_ID).first()
        if row is None:
            row = PlatformSetting(id=SETTINGS_ROW_ID, data=dict(settings))
            db.add(row)
        else:
            row.data = dict(settings)
        row.updated_at = utcnow()
        db.commit()
        return dict(settings)
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to save platform settings: {e}")
        raise
    finally:
        if owns_session:
            db.close()


# ── Compliance switches ─────────────────────────────────────────────────────
#
# `redact_pii` and `store_raw_logs` were stored, exposed in the Settings API and
# rendered in the UI with explicit promises attached — "Mask sensitive data",
# "Keep a local copy of ingested raw logs" — but nothing read them. Toggling
# either changed nothing, which is worse than not offering the control: a
# compliance officer who switches redaction on has been told it is on.
#
# These helpers are the read side. Every write path that persists log content
# consults them.

def redaction_enabled() -> bool:
    """Whether ingested log content must be redacted before it is stored."""
    try:
        return bool(load_settings().get("redact_pii", True))
    except Exception:
        # Fail closed: if the setting cannot be read, redact.
        return True


def raw_log_storage_enabled() -> bool:
    """Whether raw ingested lines may be written to the local data directory."""
    try:
        return bool(load_settings().get("store_raw_logs", True))
    except Exception:
        # Fail closed: if the setting cannot be read, do not write to disk.
        return False


def build_redactor():
    """A Redactor configured from current platform settings.

    Constructed per call rather than cached, so an operator toggling redaction
    takes effect on the next batch instead of at the next restart.
    """
    from denoiser.preprocessing.redaction import Redactor

    return Redactor(enabled=redaction_enabled())


def redact_batch(logs: list) -> list:
    """Redact a batch of ingested records, whatever shape they are in.

    Call this once at the ingest boundary, before the batch reaches *any* sink.
    An ingest request fans out to four of them — the raw object-store copy, the
    Kafka topic, ClickHouse, and the Redis stream the live console reads — and
    redacting at only some of them means the data is still there for anyone
    looking in the right place.

    That is what happened: `/ingest` redacted, `/v1/logs` did not, and the OTLP
    endpoint is the one the README points enterprises at. Its records reached
    the object store, the search index and the live stream verbatim.

    One function, called at each entrance, rather than the rule written out per
    router — the same correction this codebase already made for tenant scoping.
    """
    if not logs:
        return logs

    from denoiser.preprocessing.redaction import redact_value

    redactor = build_redactor()
    return [redact_value(entry, redactor) for entry in logs]
