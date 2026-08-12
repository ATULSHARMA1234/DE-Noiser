"""
SCIM 2.0 provisioning (RFC 7643/7644).

Lets an IdP (Okta, Azure AD, OneLogin, …) automatically create, update, and — most
importantly for a large workforce — **de-provision** users and manage team
membership, without an admin touching the platform. De-provisioning sets
``is_active = False``, which the auth layer already rejects, so a departing
employee loses access the moment the IdP pushes the change.

Auth: a bearer token presented by the IdP. Each customer gets their own token
(``POST /admin/tenant/scim-token/rotate``), and *which* token authenticates
decides which organisation the IdP may provision into — so two companies can
point their own Okta tenants at one deployment without either being able to see
or de-provision the other's staff. A deployment-wide ``SCIM_BEARER_TOKEN`` is
still honoured for single-customer installs; it is refused once any customer has
registered an email domain, because at that point it names no one organisation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.orm import Session

from denoiser.api.auth import get_password_hash, revoke_token  # noqa: F401  (revoke_token kept for symmetry)
from denoiser.api.scope import tenant_predicate
from denoiser.logging import get_logger
from denoiser.settings import get_settings
from denoiser.storage.db import Team, Tenant, User, get_db

logger = get_logger(__name__)

router = APIRouter(prefix="/scim/v2", tags=["SCIM"])

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


def scim_tenant(
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
) -> int | None:
    """Authenticate the IdP and return the organisation it may manage.

    Every endpoint below depends on this and filters by the value it returns, so
    an IdP holding one customer's token cannot read, patch or de-provision
    another customer's users — which, with a single shared token, it could.
    """
    from denoiser.api.credentials import matches_static_secret, secrets_match
    from denoiser.api.tenancy import domain_routing_configured, tenant_for_scim_token

    if not authorization or not authorization.lower().startswith("bearer "):
        # 403 when the feature is off entirely, so an operator can tell "not
        # enabled" from "wrong credential".
        if not get_settings().scim_bearer_token and not _any_tenant_token(db):
            raise HTTPException(status_code=403, detail="SCIM provisioning is not enabled")
        raise HTTPException(status_code=401, detail="Missing SCIM bearer token")

    token = authorization.split(" ", 1)[1].strip()

    tenant = tenant_for_scim_token(db, token)
    if tenant is not None:
        return tenant.id

    configured = get_settings().scim_bearer_token
    if not configured and not _any_tenant_token(db):
        raise HTTPException(status_code=403, detail="SCIM provisioning is not enabled")

    # Compared in constant time, and SCIM_BEARER_TOKEN_PREVIOUS is accepted
    # during a rotation — otherwise changing the token means the IdP's next
    # provisioning run fails until someone updates it there too.
    if configured and (secrets_match(token, configured) or matches_static_secret(token, "SCIM_BEARER_TOKEN")):
        if domain_routing_configured(db):
            raise HTTPException(
                status_code=403,
                detail=(
                    "The deployment-wide SCIM token cannot be used once organisations "
                    "are registered by email domain, because it does not identify one "
                    "of them. Issue a per-organisation token instead."
                ),
            )
        return _default_tenant_id(db)

    raise HTTPException(status_code=401, detail="Invalid SCIM bearer token")


def _any_tenant_token(db: Session) -> bool:
    return db.query(Tenant).filter(Tenant.scim_token.isnot(None)).first() is not None


def _default_tenant_id(db: Session) -> int | None:
    tenant = db.query(Tenant).order_by(Tenant.id).first()
    return tenant.id if tenant else None


def _scoped(query, model, tenant_id: int | None):
    """Restrict a query to one organisation.

    SCIM resolves its tenant from a bearer token rather than a logged-in user,
    so it cannot take a `TenantScope` — but the rule it applies is the same one,
    and it now comes from the same place. This used to be a second, independent
    implementation, right down to a copy of the ``IS NULL`` rationale.
    """
    return query.filter(tenant_predicate(model, tenant_id))


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
    tenant_id: int | None = Depends(scim_tenant),
    db: Session = Depends(get_db),
):
    username = _parse_username_filter(filter)
    q = _scoped(db.query(User), User, tenant_id)
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
def get_user(user_id: int, tenant_id: int | None = Depends(scim_tenant), db: Session = Depends(get_db)):
    user = _tenant_user(db, user_id, tenant_id)
    return _user_to_scim(user)


def _tenant_user(db: Session, user_id: int, tenant_id: int | None) -> User:
    """One user from the authenticated organisation, or 404.

    404 rather than 403 for somebody else's user: a 403 would confirm the id
    exists, which is enough to enumerate another customer's headcount.
    """
    user = _scoped(db.query(User).filter(User.id == user_id), User, tenant_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _tenant_team(db: Session, group_id: int, tenant_id: int | None) -> Team:
    team = _scoped(db.query(Team).filter(Team.id == group_id), Team, tenant_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Group not found")
    return team


@router.post("/Users", status_code=201)
def create_user(payload: dict, tenant_id: int | None = Depends(scim_tenant), db: Session = Depends(get_db)):
    import uuid

    email = payload.get("userName")
    if not email:
        raise HTTPException(status_code=400, detail="userName is required")

    emails = payload.get("emails") or []
    primary_email = next((e.get("value") for e in emails if e.get("primary")), None)
    email = primary_email or email

    # Scoped to the organisation whose token authenticated this call. An address
    # is unique inside one organisation, so a person already provisioned by
    # another customer's IdP is not a conflict here — they are a different
    # account, and refusing to create it used to leave that customer unable to
    # provision their own employee.
    existing = _scoped(db.query(User).filter(User.email == email), User, tenant_id).first()
    if existing:
        # SCIM: return 409 so the IdP switches to update instead of duplicating.
        raise HTTPException(status_code=409, detail="User already exists")

    user = User(
        email=email,
        hashed_password=get_password_hash(str(uuid.uuid4())),  # provisioned users have no local password
        role="VIEWER",
        tenant_id=tenant_id,
        is_active=bool(payload.get("active", True)),
        external_id=payload.get("externalId"),
        teams=[],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(f"SCIM provisioned user {email} (id={user.id})")
    return _user_to_scim(user)


def _set_user_attr(user: User, attr: str, value: Any) -> None:
    """Map one SCIM attribute onto the User model. Unknown attributes ignored."""
    attr = attr.lower()
    if attr == "active":
        user.is_active = _as_bool(value)
    elif attr in ("username", "emails") and isinstance(value, str):
        user.email = value
    elif attr == "emails" and isinstance(value, list):
        primary = next((e.get("value") for e in value if e.get("primary")), None)
        if primary:
            user.email = primary
    elif attr == "externalid":
        user.external_id = value
    elif attr in ("role", "roles") and value:
        # Okta/Azure may send roles as a list of {value: ...}; take the first.
        if isinstance(value, list):
            value = (value[0].get("value") if isinstance(value[0], dict) else value[0]) if value else None
        if value:
            user.role = str(value).upper()


def _apply_user_patch(user: User, payload: dict) -> None:
    """Apply a SCIM PATCH. Supports the two shapes IdPs send:

    - path-scoped:  ``{"op":"replace","path":"active","value":false}``
    - no-path dict: ``{"op":"replace","value":{"active":false,"userName":"x"}}``

    Covers active (de/re-provision), userName/emails, externalId, and role.
    """
    for op in payload.get("Operations", []):
        action = (op.get("op") or "").lower()
        if action not in ("replace", "add"):
            continue
        path = (op.get("path") or "").lower()
        value = op.get("value")
        if path:
            _set_user_attr(user, path, value)
        elif isinstance(value, dict):
            for attr, val in value.items():
                _set_user_attr(user, attr, val)


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


@router.patch("/Users/{user_id}")
def patch_user(user_id: int, payload: dict, tenant_id: int | None = Depends(scim_tenant), db: Session = Depends(get_db)):
    user = _tenant_user(db, user_id, tenant_id)
    _apply_user_patch(user, payload)
    db.commit()
    db.refresh(user)
    return _user_to_scim(user)


@router.put("/Users/{user_id}")
def replace_user(user_id: int, payload: dict, tenant_id: int | None = Depends(scim_tenant), db: Session = Depends(get_db)):
    user = _tenant_user(db, user_id, tenant_id)
    if payload.get("userName"):
        user.email = payload["userName"]
    if payload.get("externalId"):
        user.external_id = payload["externalId"]
    user.is_active = bool(payload.get("active", user.is_active))
    db.commit()
    db.refresh(user)
    return _user_to_scim(user)


@router.delete("/Users/{user_id}", status_code=204)
def deprovision_user(user_id: int, tenant_id: int | None = Depends(scim_tenant), db: Session = Depends(get_db)):
    """De-provision: deactivate rather than hard-delete so audit history survives.

    ``is_active = False`` is already rejected by the auth layer, so the user
    loses all access immediately.
    """
    user = _tenant_user(db, user_id, tenant_id)
    user.is_active = False
    db.commit()
    logger.info(f"SCIM de-provisioned user {user.email} (id={user.id})")
    return Response(status_code=204)


# ── Groups (→ Teams) ─────────────────────────────────────────────────────────

def _members_of(db: Session, team: Team) -> list[User]:
    # teams is a JSON column; membership is checked in Python (portable across
    # SQLite/Postgres without dialect-specific JSON operators). Restricted to the
    # team's own organisation so that two customers who both happen to have an
    # "sre" team do not appear in each other's membership lists.
    users = _scoped(db.query(User), User, team.tenant_id).all()
    return [u for u in users if team.name in (u.teams or [])]


@router.get("/Groups")
def list_groups(tenant_id: int | None = Depends(scim_tenant), db: Session = Depends(get_db)):
    teams = _scoped(db.query(Team), Team, tenant_id).order_by(Team.id).limit(200).all()
    return {
        "schemas": [LIST_SCHEMA],
        "totalResults": len(teams),
        "startIndex": 1,
        "itemsPerPage": len(teams),
        "Resources": [_team_to_scim(t, _members_of(db, t)) for t in teams],
    }


@router.post("/Groups", status_code=201)
def create_group(payload: dict, tenant_id: int | None = Depends(scim_tenant), db: Session = Depends(get_db)):
    name = payload.get("displayName")
    if not name:
        raise HTTPException(status_code=400, detail="displayName is required")
    team = Team(name=name, external_id=payload.get("externalId"), tenant_id=tenant_id)
    db.add(team)
    db.commit()
    db.refresh(team)

    # Seed initial members if provided.
    for m in payload.get("members", []):
        _add_member(db, team, m.get("value"))
    db.commit()
    return _team_to_scim(team, _members_of(db, team))


@router.get("/Groups/{group_id}")
def get_group(group_id: int, tenant_id: int | None = Depends(scim_tenant), db: Session = Depends(get_db)):
    team = _tenant_team(db, group_id, tenant_id)
    return _team_to_scim(team, _members_of(db, team))


def _add_member(db: Session, team: Team, user_id: Any) -> None:
    """Add a user to a team, silently ignoring anyone outside its organisation.

    The member ids come from the IdP, so they are attacker-influenced input as
    far as this service is concerned: without the scope check, one customer's
    IdP could bind another customer's employee into its own team and inherit
    whatever that team grants.
    """
    if user_id is None:
        return
    user = _scoped(db.query(User).filter(User.id == int(user_id)), User, team.tenant_id).first()
    if user and team.name not in (user.teams or []):
        user.teams = [*(user.teams or []), team.name]


def _remove_member(db: Session, team: Team, user_id: Any) -> None:
    if user_id is None:
        return
    user = _scoped(db.query(User).filter(User.id == int(user_id)), User, team.tenant_id).first()
    if user and team.name in (user.teams or []):
        user.teams = [t for t in user.teams if t != team.name]


@router.patch("/Groups/{group_id}")
def patch_group(group_id: int, payload: dict, tenant_id: int | None = Depends(scim_tenant), db: Session = Depends(get_db)):
    team = _tenant_team(db, group_id, tenant_id)

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
def delete_group(group_id: int, tenant_id: int | None = Depends(scim_tenant), db: Session = Depends(get_db)):
    team = _tenant_team(db, group_id, tenant_id)
    # Drop membership references, then remove the group.
    for u in _members_of(db, team):
        u.teams = [t for t in (u.teams or []) if t != team.name]
    db.delete(team)
    db.commit()
    return Response(status_code=204)
