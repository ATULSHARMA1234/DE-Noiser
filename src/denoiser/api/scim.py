"""
SCIM 2.0 provisioning (RFC 7643/7644).

Lets an IdP (Okta, Azure AD, OneLogin, …) automatically create, update, and — most
importantly for a large workforce — **de-provision** users and manage team
membership, without an admin touching the platform. De-provisioning sets
``is_active = False``, which the auth layer already rejects, so a departing
employee loses access the moment the IdP pushes the change.

Auth: a static bearer token (``SCIM_BEARER_TOKEN``) presented by the IdP. The
endpoints are disabled (403) until that token is configured.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.orm import Session

from denoiser.api.auth import get_password_hash, revoke_token  # noqa: F401  (revoke_token kept for symmetry)
from denoiser.logging import get_logger
from denoiser.settings import get_settings
from denoiser.storage.db import Team, Tenant, User, get_db

logger = get_logger(__name__)

router = APIRouter(prefix="/scim/v2", tags=["SCIM"])

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


def require_scim_auth(authorization: str | None = Header(None)) -> bool:
    """Validate the SCIM bearer token. 403 when SCIM is not configured."""
    configured = get_settings().scim_bearer_token
    if not configured:
        raise HTTPException(status_code=403, detail="SCIM provisioning is not enabled")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing SCIM bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if token != configured:
        raise HTTPException(status_code=401, detail="Invalid SCIM bearer token")
    return True


def _default_tenant_id(db: Session) -> int | None:
    tenant = db.query(Tenant).order_by(Tenant.id).first()
    return tenant.id if tenant else None


# ── Serialization ────────────────────────────────────────────────────────────

def _user_to_scim(u: User) -> dict[str, Any]:
    return {
        "schemas": [USER_SCHEMA],
        "id": str(u.id),
        "externalId": u.external_id,
        "userName": u.email,
        "name": {"formatted": u.email},
        "emails": [{"value": u.email, "primary": True}],
        "active": bool(u.is_active),
        "meta": {"resourceType": "User", "location": f"/scim/v2/Users/{u.id}"},
    }


def _team_to_scim(t: Team, members: list[User]) -> dict[str, Any]:
    return {
        "schemas": [GROUP_SCHEMA],
        "id": str(t.id),
        "externalId": t.external_id,
        "displayName": t.name,
        "members": [{"value": str(m.id), "display": m.email} for m in members],
        "meta": {"resourceType": "Group", "location": f"/scim/v2/Groups/{t.id}"},
    }


def _parse_username_filter(flt: str | None) -> str | None:
    """Parse the SCIM ``userName eq "x"`` filter IdPs send before create."""
    if not flt:
        return None
    parts = flt.split(" ", 2)
    if len(parts) == 3 and parts[0].lower() == "username" and parts[1].lower() == "eq":
        return parts[2].strip().strip('"')
    return None


# ── Users ────────────────────────────────────────────────────────────────────

@router.get("/Users")
def list_users(
    filter: str | None = Query(None),
    _: bool = Depends(require_scim_auth),
    db: Session = Depends(get_db),
):
    username = _parse_username_filter(filter)
    q = db.query(User)
    if username:
        q = q.filter(User.email == username)
    users = q.order_by(User.id).limit(200).all()
    return {
        "schemas": [LIST_SCHEMA],
        "totalResults": len(users),
        "startIndex": 1,
        "itemsPerPage": len(users),
        "Resources": [_user_to_scim(u) for u in users],
    }


@router.get("/Users/{user_id}")
def get_user(user_id: int, _: bool = Depends(require_scim_auth), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_scim(user)


@router.post("/Users", status_code=201)
def create_user(payload: dict, _: bool = Depends(require_scim_auth), db: Session = Depends(get_db)):
    import uuid

    email = payload.get("userName")
    if not email:
        raise HTTPException(status_code=400, detail="userName is required")

    emails = payload.get("emails") or []
    primary_email = next((e.get("value") for e in emails if e.get("primary")), None)
    email = primary_email or email

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        # SCIM: return 409 so the IdP switches to update instead of duplicating.
        raise HTTPException(status_code=409, detail="User already exists")

    user = User(
        email=email,
        hashed_password=get_password_hash(str(uuid.uuid4())),  # provisioned users have no local password
        role="VIEWER",
        tenant_id=_default_tenant_id(db),
        is_active=bool(payload.get("active", True)),
        external_id=payload.get("externalId"),
        teams=[],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(f"SCIM provisioned user {email} (id={user.id})")
    return _user_to_scim(user)


def _apply_user_patch(user: User, payload: dict) -> None:
    """Apply a SCIM PATCH (used by Okta/Azure to toggle `active`)."""
    for op in payload.get("Operations", []):
        action = (op.get("op") or "").lower()
        path = (op.get("path") or "").lower()
        value = op.get("value")
        if action in ("replace", "add"):
            if path == "active":
                user.is_active = _as_bool(value)
            elif isinstance(value, dict) and "active" in value:
                user.is_active = _as_bool(value["active"])
            elif path == "username" and isinstance(value, str):
                user.email = value


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


@router.patch("/Users/{user_id}")
def patch_user(user_id: int, payload: dict, _: bool = Depends(require_scim_auth), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    _apply_user_patch(user, payload)
    db.commit()
    db.refresh(user)
    return _user_to_scim(user)


@router.put("/Users/{user_id}")
def replace_user(user_id: int, payload: dict, _: bool = Depends(require_scim_auth), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.get("userName"):
        user.email = payload["userName"]
    if payload.get("externalId"):
        user.external_id = payload["externalId"]
    user.is_active = bool(payload.get("active", user.is_active))
    db.commit()
    db.refresh(user)
    return _user_to_scim(user)


@router.delete("/Users/{user_id}", status_code=204)
def deprovision_user(user_id: int, _: bool = Depends(require_scim_auth), db: Session = Depends(get_db)):
    """De-provision: deactivate rather than hard-delete so audit history survives.

    ``is_active = False`` is already rejected by the auth layer, so the user
    loses all access immediately.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = False
    db.commit()
    logger.info(f"SCIM de-provisioned user {user.email} (id={user.id})")
    return Response(status_code=204)


# ── Groups (→ Teams) ─────────────────────────────────────────────────────────

def _members_of(db: Session, team: Team) -> list[User]:
    # teams is a JSON column; membership is checked in Python (portable across
    # SQLite/Postgres without dialect-specific JSON operators).
    return [u for u in db.query(User).all() if team.name in (u.teams or [])]


@router.get("/Groups")
def list_groups(_: bool = Depends(require_scim_auth), db: Session = Depends(get_db)):
    teams = db.query(Team).order_by(Team.id).limit(200).all()
    return {
        "schemas": [LIST_SCHEMA],
        "totalResults": len(teams),
        "startIndex": 1,
        "itemsPerPage": len(teams),
        "Resources": [_team_to_scim(t, _members_of(db, t)) for t in teams],
    }


@router.post("/Groups", status_code=201)
def create_group(payload: dict, _: bool = Depends(require_scim_auth), db: Session = Depends(get_db)):
    name = payload.get("displayName")
    if not name:
        raise HTTPException(status_code=400, detail="displayName is required")
    team = Team(name=name, external_id=payload.get("externalId"), tenant_id=_default_tenant_id(db))
    db.add(team)
    db.commit()
    db.refresh(team)

    # Seed initial members if provided.
    for m in payload.get("members", []):
        _add_member(db, team, m.get("value"))
    db.commit()
    return _team_to_scim(team, _members_of(db, team))


@router.get("/Groups/{group_id}")
def get_group(group_id: int, _: bool = Depends(require_scim_auth), db: Session = Depends(get_db)):
    team = db.query(Team).filter(Team.id == group_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Group not found")
    return _team_to_scim(team, _members_of(db, team))


def _add_member(db: Session, team: Team, user_id: Any) -> None:
    if user_id is None:
        return
    user = db.query(User).filter(User.id == int(user_id)).first()
    if user and team.name not in (user.teams or []):
        user.teams = [*(user.teams or []), team.name]


def _remove_member(db: Session, team: Team, user_id: Any) -> None:
    if user_id is None:
        return
    user = db.query(User).filter(User.id == int(user_id)).first()
    if user and team.name in (user.teams or []):
        user.teams = [t for t in user.teams if t != team.name]


@router.patch("/Groups/{group_id}")
def patch_group(group_id: int, payload: dict, _: bool = Depends(require_scim_auth), db: Session = Depends(get_db)):
    team = db.query(Team).filter(Team.id == group_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Group not found")

    for op in payload.get("Operations", []):
        action = (op.get("op") or "").lower()
        path = (op.get("path") or "").lower()
        value = op.get("value")
        if path == "members" or (isinstance(value, dict) and "members" in value):
            members = value if isinstance(value, list) else (value or {}).get("members", [])
            for m in members or []:
                mid = m.get("value") if isinstance(m, dict) else m
                if action == "remove":
                    _remove_member(db, team, mid)
                else:
                    _add_member(db, team, mid)
        elif action == "replace" and path == "displayname" and isinstance(value, str):
            team.name = value

    db.commit()
    db.refresh(team)
    return _team_to_scim(team, _members_of(db, team))


@router.delete("/Groups/{group_id}", status_code=204)
def delete_group(group_id: int, _: bool = Depends(require_scim_auth), db: Session = Depends(get_db)):
    team = db.query(Team).filter(Team.id == group_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Group not found")
    # Drop membership references, then remove the group.
    for u in _members_of(db, team):
        u.teams = [t for t in (u.teams or []) if t != team.name]
    db.delete(team)
    db.commit()
    return Response(status_code=204)
