"""The user directory: one organisation's membership list.

Split out of `api.main`. A pure move — the handlers, the tenant scoping and the
404-not-403 rule below are unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from denoiser.api.auth import get_password_hash, require_role
from denoiser.api.pagination import ResourceId
from denoiser.api.schemas import UserCreate, UserResponse
from denoiser.api.scope import tenant_predicate
from denoiser.logging import get_logger
from denoiser.storage.db import User, get_db

logger = get_logger(__name__)

router = APIRouter(tags=["Users"])


# The user directory is the membership list of one organisation. Every lookup
# below is filtered by the caller's tenant, so an admin can only ever see and
# manage their own colleagues. Unfiltered, these four endpoints let one
# customer's admin enumerate, delete and deactivate another customer's staff.
def _same_tenant(tenant_id: int | None):
    """Predicate matching the users belonging to ``tenant_id``.

    Unassigned users form their own bucket: an admin without a tenant manages
    the users without one. The NULL handling that makes that work lives in
    `denoiser.api.scope`, which is where every other router gets it — this was
    a third independent copy of the same rule.
    """
    return tenant_predicate(User, tenant_id)


#: The actor every unattributed audit row is written against. Deleting or
#: deactivating it would break attribution for the whole deployment, so it is
#: protected unconditionally rather than relying on tenant scoping to hide it.
SYSTEM_AUDIT_EMAIL = "system-audit@semanticos.io"


def _tenant_user(db: Session, user_id: int, current_user: User) -> User:
    """Fetch a user *from the caller's own organisation*, or 404.

    Returning 404 rather than 403 for someone else's user is deliberate: a 403
    would confirm that the id exists, which is enough to enumerate another
    organisation's headcount.
    """
    # Looked up unscoped first, purely to enforce the platform-wide protection
    # below. The only fact this can reveal is which id belongs to a fixed,
    # seeded service account — not anything about another organisation.
    user = db.query(User).filter(User.id == user_id).first()
    if user and user.email == SYSTEM_AUDIT_EMAIL:
        raise HTTPException(status_code=400, detail="Cannot modify the system-audit user")

    if user is None or not _in_tenant(user, current_user.tenant_id):
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _in_tenant(user: User, tenant_id: int | None) -> bool:
    return user.tenant_id == tenant_id


@router.get("/users", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"])),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List the operators in the caller's organisation (paginated)."""
    return (
        db.query(User)
        .filter(_same_tenant(current_user.tenant_id))
        .order_by(User.id)
        .limit(limit)
        .offset(offset)
        .all()
    )


@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    """Provision a new operator inside the caller's organisation."""
    # Scoped to the caller's own organisation. Unscoped, this was an existence
    # oracle: an admin at one company could post a competitor's address and read
    # the 400 as "that person has an account on this deployment" — precisely the
    # disclosure the 404-not-403 rule in `_tenant_user` exists to prevent,
    # reachable by a different route.
    exists = (
        db.query(User)
        .filter(User.email == payload.email, _same_tenant(current_user.tenant_id))
        .first()
    )
    if exists:
        raise HTTPException(status_code=400, detail="User with this email already exists")

    user = User(
        email=payload.email,
        hashed_password=get_password_hash(payload.password),
        role=payload.role,
        # Inherited from the admin creating them, never taken from the request:
        # a client-supplied tenant would let an admin plant an account inside
        # another organisation. Without it the new account was orphaned with a
        # null tenant and could not see its own colleagues' work.
        tenant_id=current_user.tenant_id,
        department=payload.department,
        environment_access=payload.environment_access,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # `users.email` is unique per organisation, so this can now only be two
        # admins of the *same* organisation creating the same address at once —
        # the check above passed for both, and one of them lost the insert. It
        # is no longer reachable by another customer already employing the same
        # person; that is the whole point of the constraint change.
        db.rollback()
        raise HTTPException(status_code=400, detail="User with this email already exists")
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(user_id: ResourceId, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    """Delete an operator from the caller's organisation."""
    user = _tenant_user(db, user_id, current_user)

    if user.email == current_user.email:
        raise HTTPException(status_code=400, detail="Cannot delete currently logged in admin user")

    db.delete(user)
    db.commit()
    return {"status": "deleted", "id": user_id}


@router.put("/users/{user_id}/deactivate")
def deactivate_user(user_id: ResourceId, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"]))):
    """Deactivate an operator in the caller's organisation (soft deactivation)."""
    user = _tenant_user(db, user_id, current_user)

    if user.email == current_user.email:
        raise HTTPException(status_code=400, detail="Cannot deactivate currently logged in admin user")

    user.is_active = False
    db.commit()
    db.refresh(user)
    return {"status": "deactivated", "id": user_id, "is_active": user.is_active}
