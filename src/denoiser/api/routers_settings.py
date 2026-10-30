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
    HTTPException,
    Request,
)

from denoiser.api.auth import (
    require_role,
)
from denoiser.api.platform_settings import (
    DEFAULT_SETTINGS,
    PLATFORM_SETTING_KEYS,
    TENANT_SETTING_KEYS,
    load_tenant_settings,
    save_tenant_settings,
)
from denoiser.api.platform_settings import load_settings as _load_settings
from denoiser.api.schemas import SettingsUpdate
from denoiser.logging import get_logger
from denoiser.storage.db import User

logger = get_logger(__name__)

router = APIRouter(tags=["Settings"])


# ─── SETTINGS — Persistent configuration ─────────────────────────────────────
#
# What a tenant sees is its own settings plus the platform-wide ones, the
# latter read-only (`platform_managed` names them; the operator changes them at
# `/platform/settings`). This was one deployment-wide document that any
# tenant's ADMIN could rewrite for everyone.

@router.get("/settings")
def get_settings(current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    return settings_view(current_user.tenant_id)


@router.put("/settings")
def update_settings(
    new_settings: SettingsUpdate,
    request: Request,
    current_user: User = Depends(require_role(["ADMIN"])),
):
    from denoiser.api.audit import diff_fields, record_changes

    tenant_id = current_user.tenant_id
    if tenant_id is None:
        raise HTTPException(status_code=400, detail="Settings belong to an organisation; this account has none")

    updates = drop_echoed_masks(new_settings.model_dump(exclude_unset=True))

    # The page sends every field back, platform ones included, so an
    # unchanged platform value is ignored. A changed one is refused rather
    # than dropped: a 200 would tell the ADMIN it took effect.
    platform = _platform_values()
    refused = sorted(
        k for k, v in updates.items()
        if k in PLATFORM_SETTING_KEYS and v != platform.get(k)
    )
    if refused:
        raise HTTPException(
            status_code=403,
            detail=f"Managed by the platform operator, not per organisation: {', '.join(refused)}",
        )
    updates = {k: v for k, v in updates.items() if k in TENANT_SETTING_KEYS}

    # Capture what moved before the write. Settings govern retention and
    # redaction, so "someone changed settings and got a 200" is not a usable
    # audit record — the previous value is the part an investigation needs.
    changes = diff_fields(load_tenant_settings(tenant_id), updates)
    _redact_secret_changes(changes)
    record_changes(request, changes)

    if updates:
        save_tenant_settings(tenant_id, updates)
    return settings_view(tenant_id)


def settings_view(tenant_id: int | None) -> dict:
    view = {**_platform_values(), **load_tenant_settings(tenant_id)}
    view = _mask_secrets(view)
    view["platform_managed"] = list(PLATFORM_SETTING_KEYS)
    view["llm"] = llm_status()
    return view


def llm_status() -> dict:
    """The narrator this deployment actually uses, read from its configuration.

    Settings offered a model dropdown (GPT-4o, Claude, ...) that nothing read;
    the model is SLD_LLM_MODEL, against SLD_LLM_BASE_URL. Shown as it is, so
    the page cannot promise a model the deployment is not running.
    """
    from urllib.parse import urlsplit

    from denoiser.config import settings as intelligence
    from denoiser.settings import _LOCAL_LLM_HOSTS

    if not (intelligence.llm_api_key or intelligence.llm_base_url):
        return {"configured": False, "model": None, "location": None}
    host = (urlsplit(intelligence.llm_base_url or "").hostname or "").lower()
    return {
        "configured": True,
        "model": intelligence.llm_model,
        # No base URL means the provider's hosted default.
        "location": "local" if host in _LOCAL_LLM_HOSTS else "external",
    }


def _platform_values() -> dict:
    platform = _load_settings()
    return {k: platform.get(k, DEFAULT_SETTINGS.get(k)) for k in PLATFORM_SETTING_KEYS}


def drop_echoed_masks(updates: dict) -> dict:
    """The settings page sends back every field it was given, including the
    mask in place of each secret. That means "unchanged", not "set it to the
    mask"."""
    return {k: v for k, v in updates.items() if not (k in _SECRET_SETTING_KEYS and v == SECRET_MASK)}


#: Settings whose values are credentials — recorded as changed, never with the
#: value itself, or the audit log becomes a place to read secrets from.
_SECRET_SETTING_KEYS = ("s3_secret_key", "s3_access_key", "slack_webhook_url", "sso_client_id")


#: Stands in for a secret that is set. Every role can read settings, and a
#: VIEWER able to read the S3 keys could read the archive they unlock.
SECRET_MASK = "********"


def _mask_secrets(settings: dict) -> dict:
    masked = dict(settings)
    for key in _SECRET_SETTING_KEYS:
        if masked.get(key):
            masked[key] = SECRET_MASK
    return masked


def _redact_secret_changes(changes: dict) -> None:
    for key in list(changes):
        if key in _SECRET_SETTING_KEYS:
            changes[key] = {"from": "<redacted>", "to": "<redacted>"}
