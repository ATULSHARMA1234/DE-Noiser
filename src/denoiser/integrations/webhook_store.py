"""
Tenant-scoped persistence for alert destinations.

Every function here takes a ``tenant_id`` and filters on it. That is the whole
point of the module: the previous in-memory registry had no owner column, so
"list the webhooks" meant "list *everyone's* webhooks". Routes call these
helpers instead of touching the router's internals, which makes the tenant
filter impossible to forget.

URLs are encrypted on the way in and masked on the way out. The plaintext is
only reconstructed by :func:`to_config`, which is what the delivery path uses.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from denoiser.integrations.alert_router import ChannelType, WebhookConfig
from denoiser.storage.db import Webhook
from denoiser.storage.secrets import decrypt, encrypt, mask_url
from denoiser.utils.time import iso_utc


def to_config(row: Webhook) -> WebhookConfig | None:
    """Rehydrate a stored row into a deliverable config.

    Returns ``None`` when the URL cannot be decrypted (key rotated without
    re-encryption, or a backup restored into a different environment) — the
    destination is unusable and must not be delivered to.
    """
    url = decrypt(row.url_encrypted)
    if not url:
        return None
    try:
        channel = ChannelType(row.channel_type)
    except ValueError:
        return None
    return WebhookConfig(
        id=row.id,
        name=row.name,
        channel_type=channel,
        url=url,
        min_priority=row.min_priority or "P1",
        enabled=bool(row.enabled),
        extra=dict(row.extra or {}),
        tenant_id=row.tenant_id,
    )


def to_public_dict(row: Webhook) -> dict[str, Any]:
    """The API-safe view: identifying detail, never the full credential."""
    return {
        "id": row.id,
        "name": row.name,
        "channel_type": row.channel_type,
        "url": mask_url(decrypt(row.url_encrypted)),
        "min_priority": row.min_priority or "P1",
        "enabled": bool(row.enabled),
        "extra": dict(row.extra or {}),
        "created_at": iso_utc(row.created_at),
        "updated_at": iso_utc(row.updated_at),
    }


def list_webhooks(db: Session, tenant_id: int) -> list[Webhook]:
    return (
        db.query(Webhook)
        .filter(Webhook.tenant_id == tenant_id)
        .order_by(Webhook.created_at.desc())
        .all()
    )


def get_webhook(db: Session, tenant_id: int, webhook_id: str) -> Webhook | None:
    """Fetch one destination *belonging to this tenant*.

    A row owned by another tenant returns None, so the caller raises the same
    404 it would for a genuinely missing id and the endpoint does not confirm
    that someone else's webhook exists.
    """
    return (
        db.query(Webhook)
        .filter(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id)
        .first()
    )


def create_webhook(
    db: Session,
    tenant_id: int,
    *,
    name: str,
    channel_type: str,
    url: str,
    min_priority: str = "P1",
    enabled: bool = True,
    extra: dict | None = None,
) -> Webhook:
    # The id is derived from (tenant, name, url) so two tenants registering the
    # same Slack channel do not collide on a shared primary key.
    webhook_id = WebhookConfig.make_id(f"{tenant_id}:{name}", url)
    row = db.query(Webhook).filter(Webhook.id == webhook_id).first()
    if row is None:
        row = Webhook(id=webhook_id, tenant_id=tenant_id)
        db.add(row)
    row.name = name
    row.channel_type = channel_type
    row.url_encrypted = encrypt(url)
    row.min_priority = min_priority
    row.enabled = enabled
    row.extra = extra or {}
    db.commit()
    db.refresh(row)
    return row


def update_webhook(
    db: Session,
    row: Webhook,
    *,
    name: str | None = None,
    url: str | None = None,
    min_priority: str | None = None,
    enabled: bool | None = None,
    extra: dict | None = None,
) -> Webhook:
    if name is not None:
        row.name = name
    if url is not None:
        row.url_encrypted = encrypt(url)
    if min_priority is not None:
        row.min_priority = min_priority
    if enabled is not None:
        row.enabled = enabled
    if extra is not None:
        row.extra = {**dict(row.extra or {}), **extra}
    db.commit()
    db.refresh(row)
    return row


def delete_webhook(db: Session, row: Webhook) -> None:
    db.delete(row)
    db.commit()


def destinations_for_tenant(db: Session, tenant_id: int) -> list[WebhookConfig]:
    """Every enabled, decryptable destination for a tenant.

    This is what the analysis worker dispatches against, so an alert raised for
    one tenant can only ever reach that tenant's channels.
    """
    configs = []
    for row in list_webhooks(db, tenant_id):
        if not row.enabled:
            continue
        cfg = to_config(row)
        if cfg is not None:
            configs.append(cfg)
    return configs
