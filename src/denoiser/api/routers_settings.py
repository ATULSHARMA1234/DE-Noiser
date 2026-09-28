"""Persistent platform configuration.

Split out of `api.main`, which held eleven unrelated concerns in 1,500 lines:
every parallel feature branch touched that one file and every one of them
conflicted. A pure move — no handler below is changed, and the routes are the
same paths at the same methods.
"""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    Request,
)

from denoiser.api.auth import (
    require_role,
)
from denoiser.api.platform_settings import load_settings as _load_settings
from denoiser.api.platform_settings import save_settings as _save_settings
from denoiser.api.schemas import SettingsUpdate
from denoiser.logging import get_logger
from denoiser.storage.db import User

logger = get_logger(__name__)

router = APIRouter(tags=["Settings"])


# ─── SETTINGS — Persistent configuration ─────────────────────────────────────

@router.get("/settings")
def get_settings(current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    return _load_settings()


@router.put("/settings")
def update_settings(
    new_settings: SettingsUpdate,
    request: Request,
    current_user: User = Depends(require_role(["ADMIN"])),
):
    from denoiser.api.audit import diff_fields, record_changes

    current = _load_settings()
    updates = new_settings.model_dump(exclude_unset=True)

    # Capture what moved before the write. Settings govern retention and
    # redaction, so "someone changed settings and got a 200" is not a usable
    # audit record — the previous value is the part an investigation needs.
    changes = diff_fields(current, updates)
    _redact_secret_changes(changes)
    record_changes(request, changes)

    current.update(updates)
    _save_settings(current)
    return current


#: Settings whose values are credentials — recorded as changed, never with the
#: value itself, or the audit log becomes a place to read secrets from.
_SECRET_SETTING_KEYS = ("s3_secret_key", "s3_access_key", "slack_webhook_url", "sso_client_id")


def _redact_secret_changes(changes: dict) -> None:
    for key in list(changes):
        if key in _SECRET_SETTING_KEYS:
            changes[key] = {"from": "<redacted>", "to": "<redacted>"}
