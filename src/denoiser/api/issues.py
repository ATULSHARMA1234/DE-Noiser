"""
Issue tracking API.

Runs and clusters answer "what did this analysis find?". Issues answer the
questions a team actually works from: is this new, is it getting worse, who owns
it, has anyone looked at it. Every endpoint here is tenant-scoped through the
authenticated user.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from denoiser.analysis.issues import suspect_deployment
from denoiser.api.auth import User, get_current_user, require_role
from denoiser.api.pagination import ResourceId
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.storage.db import IssueComment, IssueEvent, LogIssue, Team, get_db
from denoiser.storage.db import User as DBUser
from denoiser.utils.time import iso_utc, utcnow

router = APIRouter(prefix="/issues", tags=["issues"])

STATES = ("FOR_REVIEW", "REVIEWED", "IGNORED", "RESOLVED")
SEVERITIES = ("P0", "P1", "P2", "P3")

# Sort keys the list endpoint accepts, mapped to their column and direction.
SORTS = {
    "last_seen": (LogIssue.last_seen, "desc"),
    "first_seen": (LogIssue.first_seen, "desc"),
    "events": (LogIssue.total_events, "desc"),
    "severity": (LogIssue.severity, "asc"),   # P0 sorts before P3 lexically
    "anomaly": (LogIssue.anomaly_score, "desc"),
}


class IssueUpdateSchema(BaseModel):
    state: str | None = None
    severity: str | None = None
    assignee_id: int | None = None
    team_id: int | None = None


class CommentSchema(BaseModel):
    body: str


def _sparkline(histogram: list | None, buckets: int = 48) -> list[dict]:
    """The last ``buckets`` hourly points, gap-filled.

    A list row draws this at ~120px; handing it a sparse series makes a quiet
    hour indistinguishable from a missing one, which is the difference between
    "recovered" and "we stopped looking".
    """
    points = {p["ts"]: int(p.get("count") or 0) for p in (histogram or []) if p.get("ts")}
    if not points:
        return []

    end = utcnow().replace(minute=0, second=0, microsecond=0)
    series = []
    for i in range(buckets - 1, -1, -1):
        ts = (end - timedelta(hours=i)).isoformat() + "+00:00"
        series.append({"ts": ts, "count": points.get(ts, 0)})
    return series


def _issue_summary(issue: LogIssue, users: dict[int, DBUser], teams: dict[int, Team]) -> dict[str, Any]:
    assignee = users.get(issue.assignee_id) if issue.assignee_id else None
    team = teams.get(issue.team_id) if issue.team_id else None
    return {
        "id": issue.id,
        "fingerprint": issue.fingerprint,
        "title": issue.title,
        "service": issue.service,
        "severity": issue.severity,
        "state": issue.state,
        "is_noise": bool(issue.is_noise),
        "first_seen": iso_utc(issue.first_seen),
        "last_seen": iso_utc(issue.last_seen),
        "total_events": issue.total_events or 0,
        "run_count": issue.run_count or 0,
        "anomaly_score": issue.anomaly_score or 0.0,
        "assignee": {"id": assignee.id, "email": assignee.email} if assignee else None,
        "team": {"id": team.id, "name": team.name} if team else None,
        "sparkline": _sparkline(issue.histogram),
        "representative_log": issue.representative_log,
    }


def _lookup_maps(scope: TenantScope) -> tuple[dict, dict]:
    users = {u.id: u for u in scope.query(DBUser).all()}
    teams = {t.id: t for t in scope.query(Team).all()}
    return users, teams


def _base_query(scope: TenantScope):
    return scope.query(LogIssue)


@router.get("")
def list_issues(
    state: str | None = None,
    severity: str | None = None,
    service: str | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
    sort: str = "last_seen",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
):
    """Issues for the tenant, with the counts the list header needs.

    ``counts`` is deliberately computed over the *unfiltered* set for state, so
    the status tabs keep showing how much work sits behind each one — a tab that
    only counts what the current filter already matched cannot be navigated by.
    """
    query = _base_query(scope)

    if state and state.upper() != "ALL":
        query = query.filter(LogIssue.state == state.upper())
    if severity:
        query = query.filter(LogIssue.severity.in_([s.strip().upper() for s in severity.split(",") if s.strip()]))
    if service:
        query = query.filter(LogIssue.service.in_([s.strip() for s in service.split(",") if s.strip()]))
    if assignee_id:
        query = query.filter(LogIssue.assignee_id == assignee_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            LogIssue.title.ilike(like)
            | LogIssue.representative_log.ilike(like)
            | LogIssue.template.ilike(like)
        )

    total = query.count()
    column, direction = SORTS.get(sort, SORTS["last_seen"])
    query = query.order_by(column.desc() if direction == "desc" else column.asc())
    issues = query.offset(max(0, offset)).limit(limit).all()

    users, teams = _lookup_maps(scope)

    state_counts = dict(
        db.query(LogIssue.state, func.count(LogIssue.id))
        .filter(scope.predicate(LogIssue))
        .group_by(LogIssue.state)
        .all()
    )

    return {
        "issues": [_issue_summary(i, users, teams) for i in issues],
        "total": total,
        "offset": offset,
        "limit": limit,
        "counts": {s: int(state_counts.get(s, 0)) for s in STATES},
    }


@router.get("/facets")
def issue_facets(
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
):
    """Value counts for the left rail: service, severity, state, assignee."""

    def counts_for(column):
        rows = (
            db.query(column, func.count(LogIssue.id))
            .filter(scope.predicate(LogIssue))
            .group_by(column)
            .all()
        )
        return [
            {"value": str(value), "count": int(count)}
            for value, count in sorted(rows, key=lambda r: -r[1])
            if value is not None
        ]

    users, _ = _lookup_maps(scope)
    assignees = []
    for row in (
        db.query(LogIssue.assignee_id, func.count(LogIssue.id))
        .filter(scope.predicate(LogIssue), LogIssue.assignee_id.isnot(None))
        .group_by(LogIssue.assignee_id)
        .all()
    ):
        user = users.get(row[0])
        if user:
            assignees.append({"value": user.email, "id": user.id, "count": int(row[1])})

    return {
        "facets": {
            "service": counts_for(LogIssue.service),
            "severity": counts_for(LogIssue.severity),
            "state": counts_for(LogIssue.state),
            "assignee": assignees,
        }
    }


def _get_issue(scope: TenantScope, issue_id: ResourceId) -> LogIssue:
    return scope.get_or_404(LogIssue, issue_id, "Issue not found")


@router.get("/{issue_id}")
def get_issue(
    issue_id: ResourceId,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
):
    """Everything the detail panel renders, in one round trip."""
    tenant_id = scope.tenant_id
    issue = _get_issue(scope, issue_id)
    users, teams = _lookup_maps(scope)

    comments = (
        db.query(IssueComment)
        .filter(IssueComment.issue_id == issue_id, scope.predicate(IssueComment))
        .order_by(IssueComment.created_at.asc())
        .all()
    )
    events = (
        db.query(IssueEvent)
        .filter(IssueEvent.issue_id == issue_id, scope.predicate(IssueEvent))
        .order_by(IssueEvent.created_at.desc())
        .limit(50)
        .all()
    )

    detail = _issue_summary(issue, users, teams)
    detail.update({
        "template": issue.template,
        "tags": issue.tags or {},
        "histogram": issue.histogram or [],
        "samples": issue.samples or [],
        "last_run_id": issue.last_run_id,
        "last_cluster_id": issue.last_cluster_id,
        "suspect_deployment": suspect_deployment(db, tenant_id, issue),
        "comments": [
            {
                "id": c.id,
                "body": c.body,
                "author_email": c.author_email,
                "created_at": iso_utc(c.created_at),
            }
            for c in comments
        ],
        "activity": [
            {
                "id": e.id,
                "kind": e.kind,
                "detail": e.detail or {},
                "actor_email": e.actor_email,
                "created_at": iso_utc(e.created_at),
            }
            for e in events
        ],
    })
    return detail


@router.patch("/{issue_id}")
def update_issue(
    issue_id: ResourceId,
    payload: IssueUpdateSchema,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"])),
):
    """Triage an issue. Every accepted change is recorded on the activity feed."""
    tenant_id = scope.tenant_id
    issue = _get_issue(scope, issue_id)

    if payload.state is not None:
        state = payload.state.upper()
        if state not in STATES:
            raise HTTPException(status_code=400, detail=f"state must be one of {', '.join(STATES)}")
        if state != issue.state:
            db.add(IssueEvent(
                tenant_id=tenant_id, issue_id=issue.id, user_id=current_user.id,
                actor_email=current_user.email, kind="state",
                detail={"from": issue.state, "to": state},
            ))
            issue.state = state

    if payload.severity is not None:
        severity = payload.severity.upper()
        if severity not in SEVERITIES:
            raise HTTPException(status_code=400, detail=f"severity must be one of {', '.join(SEVERITIES)}")
        if severity != issue.severity:
            db.add(IssueEvent(
                tenant_id=tenant_id, issue_id=issue.id, user_id=current_user.id,
                actor_email=current_user.email, kind="severity",
                detail={"from": issue.severity, "to": severity},
            ))
            issue.severity = severity

    if payload.assignee_id is not None and payload.assignee_id != issue.assignee_id:
        # Unassign is assignee_id 0; a real id must belong to this tenant, or an
        # issue could be assigned to somebody in another organisation.
        if payload.assignee_id:
            assignee = (
                db.query(DBUser)
                .filter(DBUser.id == payload.assignee_id, scope.predicate(DBUser))
                .first()
            )
            if assignee is None:
                raise HTTPException(status_code=400, detail="Unknown assignee")
            issue.assignee_id = assignee.id
            detail = {"to": assignee.email}
        else:
            issue.assignee_id = None
            detail = {"to": None}
        db.add(IssueEvent(
            tenant_id=tenant_id, issue_id=issue.id, user_id=current_user.id,
            actor_email=current_user.email, kind="assignee", detail=detail,
        ))

    if payload.team_id is not None and payload.team_id != issue.team_id:
        if payload.team_id:
            team = db.query(Team).filter(Team.id == payload.team_id, scope.predicate(Team)).first()
            if team is None:
                raise HTTPException(status_code=400, detail="Unknown team")
            issue.team_id = team.id
        else:
            issue.team_id = None

    issue.updated_at = utcnow()
    db.commit()
    db.refresh(issue)

    users, teams = _lookup_maps(scope)
    return _issue_summary(issue, users, teams)


@router.post("/{issue_id}/comments")
def add_comment(
    issue_id: ResourceId,
    payload: CommentSchema,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"])),
):
    tenant_id = scope.tenant_id
    issue = _get_issue(scope, issue_id)

    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=400, detail="Comment cannot be empty")

    comment = IssueComment(
        tenant_id=tenant_id, issue_id=issue.id, user_id=current_user.id,
        author_email=current_user.email, body=body,
    )
    db.add(comment)
    db.add(IssueEvent(
        tenant_id=tenant_id, issue_id=issue.id, user_id=current_user.id,
        actor_email=current_user.email, kind="comment", detail={"preview": body[:120]},
    ))
    db.commit()
    db.refresh(comment)
    return {
        "id": comment.id,
        "body": comment.body,
        "author_email": comment.author_email,
        "created_at": iso_utc(comment.created_at),
    }


@router.get("/{issue_id}/assignees")
def assignable_users(
    issue_id: ResourceId,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(get_current_user),
):
    """Who this issue can be handed to — active users in the same tenant."""
    _get_issue(scope, issue_id)
    users = (
        db.query(DBUser)
        .filter(scope.predicate(DBUser), DBUser.is_active.is_(True))
        .order_by(DBUser.email.asc())
        .all()
    )
    return {"users": [{"id": u.id, "email": u.email, "role": u.role} for u in users]}
