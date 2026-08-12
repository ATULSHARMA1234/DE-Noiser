"""Who the caller is allowed to see — decided once, for every route.

Before this module, ownership was re-decided at every call site, and the call
sites did not agree. Four dialects were in use simultaneously::

    .filter(M.tenant_id == user.tenant_id)              # 29 sites, 404 on miss
    if row.tenant_id != user.tenant_id: raise 403       # 14 sites
    if user.tenant_id and row.tenant_id != user.tenant_id: raise 403   # 10 sites
    _same_tenant(user.tenant_id)                        # 1 site, the only NULL-safe one

The third is a bypass: ``user.tenant_id`` is falsy for an unassigned account, so
the whole comparison is skipped and the row is returned. The same shape appears
with the *row* guarded (``if row.tenant_id and ...``), which makes every
unassigned row visible to every tenant.

Those were not four opinions about ownership. They were one opinion, typed out
four times, wrong in two of them. This module states it once:

**A row belongs to the caller when its ``tenant_id`` equals theirs — and NULL
equals NULL.** SQL disagrees (``NULL = NULL`` is never true), which is precisely
why the rule has to live somewhere a route cannot get it wrong by hand.

**A row that belongs to someone else does not exist.** Every miss is a 404, not
a 403: a 403 confirms the id is real, which is enough to count another
organisation's dashboards, monitors or headcount.
"""

from __future__ import annotations

from typing import Any, TypeVar

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Query, Session

from denoiser.api.auth import get_current_user
from denoiser.storage.db import User, get_db

T = TypeVar("T")


def tenant_predicate(model: type[Any], tenant_id: int | None):
    """The ownership condition for ``model``, NULL included.

    ``column == None`` compiles to ``= NULL`` and matches nothing, so an
    unassigned caller would silently see an empty database rather than the
    unassigned rows that are theirs. ``IS NULL`` is the only correct form and
    this is the only place that has to remember it.

    Exposed as a function, not just a `TenantScope` method, because two callers
    have a tenant but no request user: SCIM resolves its tenant from a bearer
    token, and the user directory resolves it before a `TenantScope` is built.
    Both had written this rule out again — correctly, as it happens, but a rule
    typed out three times is a rule with three chances to drift.
    """
    column = model.tenant_id
    return column.is_(None) if tenant_id is None else column == tenant_id


class TenantScope:
    """The caller's view of the database.

    Obtained with ``Depends(tenant_scope)``. Routes ask it for a query rather
    than building a predicate, so a route cannot accidentally be written
    unscoped — the unscoped form is ``db.query(...)``, which now reads as the
    exception it is.
    """

    __slots__ = ("db", "user")

    def __init__(self, db: Session, user: User) -> None:
        self.db = db
        self.user = user

    @property
    def tenant_id(self) -> int | None:
        return self.user.tenant_id

    # ── Reading ──────────────────────────────────────────────────────────

    def query(self, model: type[T], *entities: Any) -> Query:
        """A query over ``model`` restricted to the caller's organisation."""
        q = self.db.query(model, *entities) if entities else self.db.query(model)
        return q.filter(self.predicate(model))

    def predicate(self, model: type[Any]):
        """The ownership condition for ``model``, NULL included."""
        return tenant_predicate(model, self.tenant_id)

    def owns(self, row: Any) -> bool:
        """Whether an already-loaded row belongs to the caller."""
        return row is not None and getattr(row, "tenant_id", None) == self.tenant_id

    def get_or_404(self, model: type[T], resource_id: Any, detail: str | None = None) -> T:
        """One row of ``model`` by primary key, or 404.

        404 rather than 403 for another organisation's row — see the module
        docstring. The caller may override ``detail`` for a friendlier message,
        but not the status: making it configurable is how a 403 creeps back in.
        """
        row = self.query(model).filter(model.id == resource_id).first()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=detail or f"{_human_name(model)} not found",
            )
        return row

    # ── Writing ──────────────────────────────────────────────────────────

    def stamp(self, row: T) -> T:
        """Mark a new row as belonging to the caller.

        Creation was the other half of the problem: several routes built a row
        without setting ``tenant_id`` at all, so it landed unassigned and its
        author could not see it again.
        """
        row.tenant_id = self.tenant_id
        return row

    def add(self, row: T) -> T:
        """Stamp a new row and stage it on the session."""
        self.db.add(self.stamp(row))
        return row


def _human_name(model: type[Any]) -> str:
    """"MetricRule" -> "Metric rule", for a readable 404."""
    name = getattr(model, "__name__", "Resource")
    spaced = "".join(f" {c.lower()}" if c.isupper() else c for c in name).strip()
    return spaced[:1].upper() + spaced[1:] if spaced else "Resource"


def tenant_scope(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TenantScope:
    """FastAPI dependency yielding the caller's scope.

    ``get_current_user`` is cached per request, so a route depending on both
    this and ``require_role(...)`` resolves the user once.
    """
    return TenantScope(db=db, user=user)
