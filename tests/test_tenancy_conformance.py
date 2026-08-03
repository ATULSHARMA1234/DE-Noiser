"""One rule, applied to every tenant-scoped resource, generated rather than written.

The isolation bugs found in this codebase were never a *missing* idea. Tenancy
was understood; it was just re-implemented per feature, so each new resource got
a fresh chance to get it wrong — and several did. Hand-written isolation tests
have the same shape as the bug: they cover the resources someone remembered.

So this suite is generated from a registry. Adding a tenant-scoped model without
adding it here fails `test_every_tenant_scoped_model_is_registered`, and adding
it here without scoping its routes fails the behavioural tests. The only way to
ship an unscoped resource is to delete a test that says you cannot, which is a
much louder thing to do than forgetting.

This is the suite that would have caught the `/users` directory leak: it was
probed for *role* boundaries during the enterprise audit ("can a VIEWER create
users?" — no, 403) and never for *tenant* boundaries, so it looked covered.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from denoiser.api.main import app
from denoiser.storage import db as models
from denoiser.storage.db import SessionLocal, Tenant, User

PASSWORD = "ConformanceTest!2026"
LEFT = "left-admin@conformance.test"
RIGHT = "right-admin@conformance.test"


# ── The registry ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Resource:
    """One tenant-scoped resource and how to reach it over HTTP."""

    #: Router prefix, e.g. "dashboards" for /dashboards/{id}.
    path: str
    #: The mapped model that carries the tenant_id.
    model: str
    #: Builds one row belonging to `tenant_id`.
    build: Callable[[int], Any]
    #: Methods the resource exposes on /{path}/{id}. GET is assumed.
    writes: tuple[str, ...] = ()
    #: Body to send for PUT/PATCH, when the resource has one.
    write_body: dict = field(default_factory=dict)
    #: Set when the collection route is not GET /{path}.
    lists: bool = True
    #: Suffix of the readable detail route, appended to /{path}/{id}. ``None``
    #: means the resource has no read-by-id route at all — which is a fact worth
    #: recording, because "there is no route" and "the route is unscoped" look
    #: identical from the outside and only one of them is acceptable.
    detail: str | None = ""


RESOURCES: list[Resource] = [
    Resource(
        path="dashboards", model="Dashboard",
        build=lambda t: models.Dashboard(tenant_id=t, name="Their board", layout=[], widgets=[]),
        writes=("PUT", "DELETE"), write_body={"name": "hijacked"},
    ),
    Resource(
        path="monitors", model="Monitor",
        build=lambda t: models.Monitor(tenant_id=t, name="Their monitor", query="level:ERROR"),
        writes=("PUT", "DELETE"), write_body={"name": "hijacked"},
    ),
    Resource(
        path="notebooks", model="Notebook",
        build=lambda t: models.Notebook(tenant_id=t, title="Their notebook", cells=[]),
        writes=("PUT", "DELETE"), write_body={"title": "hijacked"},
    ),
    Resource(
        path="runbooks", model="Runbook",
        build=lambda t: models.Runbook(tenant_id=t, name="Their runbook"),
        writes=("PUT", "DELETE"), write_body={"name": "hijacked"},
        detail=None,  # runbooks are read through the collection only
    ),
    Resource(
        path="slos", model="ServiceLevelObjective",
        build=lambda t: models.ServiceLevelObjective(
            tenant_id=t, name="Their SLO", service="billing",
            sli_type="availability", target_percentage=99.9,
        ),
        writes=("DELETE",),
        detail="/status",  # the only per-SLO read route
    ),
    Resource(
        path="issues", model="LogIssue",
        build=lambda t: models.LogIssue(tenant_id=t, fingerprint=f"fp-{t}", title="Their issue"),
        writes=("PATCH",), write_body={"state": "RESOLVED"},
    ),
    Resource(
        path="query/saved", model="SavedQuery",
        build=lambda t: models.SavedQuery(
            tenant_id=t, name="Their saved query", query_text="level:ERROR",
        ),
        writes=("DELETE",),
        detail=None,  # saved queries are read through the collection only
    ),
    Resource(
        path="integrations", model="Integration",
        build=lambda t: models.Integration(tenant_id=t, provider="slack", name="Their integration"),
        writes=("PUT", "DELETE"), write_body={"name": "hijacked"},
        detail=None,  # integrations are read through the collection only
    ),
]

#: Models whose rows are reachable only through a route registered elsewhere, or
#: not over HTTP at all. Listed explicitly so the registry check below stays a
#: real check rather than a rubber stamp — each entry is a claim someone made.
NOT_ADDRESSABLE = {
    # Identity and audit: covered by their own dedicated suites, because their
    # isolation rules differ (users 404 by id; audit is list-only).
    "User", "Team", "AuditLog", "AlertLog",
    # Reached only through a parent resource, never by their own id.
    "IssueComment", "IssueEvent", "SLODataPoint", "RunbookExecution",
    # Written by workers and read in aggregate; no per-id route exists.
    "ExtractedMetric", "MetricRule", "BillingMeter", "Span", "DeploymentMarker",
    "AnalysisRun", "Incident", "Webhook",
    # An organisation's IdP configuration is reached only through the
    # platform-operator router (/platform/tenants/{id}/idp), which is gated by
    # the operator token rather than by tenant scope — a customer's own ADMIN
    # must not be able to read or move it, because whoever controls the SAML
    # issuer controls which organisation an assertion is routed to. Its
    # isolation is asserted in tests/test_per_org_idp.py.
    "TenantIdentityProvider",
}


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def two_organisations():
    """Two organisations with an ADMIN each — the shape a shared deployment has."""
    from denoiser.api.auth import get_password_hash
    from denoiser.storage.db import init_db

    init_db()
    db = SessionLocal()
    try:
        made = {}
        for name, email in (("conformance-left", LEFT), ("conformance-right", RIGHT)):
            tenant = db.query(Tenant).filter(Tenant.name == name).first()
            if tenant is None:
                tenant = Tenant(name=name, tier="enterprise")
                db.add(tenant)
                db.commit()
                db.refresh(tenant)
            user = db.query(User).filter(User.email == email).first()
            if user is None:
                db.add(User(
                    email=email, hashed_password=get_password_hash(PASSWORD),
                    role="ADMIN", tenant_id=tenant.id, environment_access=["*"],
                ))
                db.commit()
            made[name] = tenant.id
        yield made["conformance-left"], made["conformance-right"]
    finally:
        db.close()


def _client_for(email: str) -> TestClient:
    client = TestClient(app)
    client.__enter__()
    resp = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    client.headers.update({"Authorization": f"Bearer {resp.json()['access_token']}"})
    return client


@pytest.fixture(scope="module")
def intruder(two_organisations):
    """Signed in as the *right* organisation, looking at the left's rows."""
    client = _client_for(RIGHT)
    yield client
    client.__exit__(None, None, None)


@pytest.fixture
def owned_row(request, two_organisations):
    """One row belonging to the left organisation, removed afterwards."""
    left, _ = two_organisations
    resource: Resource = request.param
    model = getattr(models, resource.model)

    db = SessionLocal()
    try:
        row = resource.build(left)
        db.add(row)
        db.commit()
        db.refresh(row)
        row_id = row.id
    finally:
        db.close()

    yield resource, row_id

    db = SessionLocal()
    try:
        db.query(model).filter(model.id == row_id).delete()
        db.commit()
    finally:
        db.close()


def _ids(resources):
    return [r.path for r in resources]


# ── The conformance rules ────────────────────────────────────────────────────

@pytest.mark.parametrize("owned_row", RESOURCES, indirect=True, ids=_ids(RESOURCES))
def test_another_organisations_row_is_not_readable(owned_row, intruder):
    """404, not 403: a 403 confirms the id exists, which is enough to enumerate."""
    resource, row_id = owned_row
    if resource.detail is None:
        pytest.skip(f"/{resource.path} has no read-by-id route")
    resp = intruder.get(f"/{resource.path}/{row_id}{resource.detail}")
    assert resp.status_code == 404, (
        f"GET /{resource.path}/{{id}}{resource.detail} returned {resp.status_code} "
        f"for another organisation's row; expected 404. Body: {resp.text[:200]}"
    )


@pytest.mark.parametrize("owned_row", RESOURCES, indirect=True, ids=_ids(RESOURCES))
def test_another_organisations_row_is_not_writable(owned_row, intruder):
    resource, row_id = owned_row
    for method in resource.writes:
        resp = intruder.request(
            method, f"/{resource.path}/{row_id}",
            json=resource.write_body if method in ("PUT", "PATCH") else None,
        )
        assert resp.status_code == 404, (
            f"{method} /{resource.path}/{{id}} returned {resp.status_code} for "
            f"another organisation's row; expected 404."
        )


@pytest.mark.parametrize("owned_row", RESOURCES, indirect=True, ids=_ids(RESOURCES))
def test_another_organisations_row_is_not_listed(owned_row, intruder):
    """The id must not appear in the collection either — scoping the detail
    route alone still leaks names, titles and counts through the list."""
    resource, row_id = owned_row
    if not resource.lists:
        pytest.skip(f"/{resource.path} has no collection route")

    resp = intruder.get(f"/{resource.path}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rows = body if isinstance(body, list) else body.get("items", body.get("issues", []))
    assert row_id not in [r.get("id") for r in rows if isinstance(r, dict)], (
        f"GET /{resource.path} listed another organisation's row {row_id}"
    )


@pytest.mark.parametrize("owned_row", RESOURCES, indirect=True, ids=_ids(RESOURCES))
def test_the_owner_can_still_reach_their_own_row(owned_row, two_organisations):
    """The other half of the rule. A scope that hides everything is not isolation.

    This is not a formality: the first attempt at NULL-safe scoping in this
    codebase compiled to `tenant_id = NULL`, which matches nothing, and made
    unassigned rows unreachable by anyone at all.
    """
    resource, row_id = owned_row
    owner = _client_for(LEFT)
    try:
        if resource.detail is None:
            listed = owner.get(f"/{resource.path}").json()
            rows = listed if isinstance(listed, list) else listed.get("items", [])
            assert row_id in [r.get("id") for r in rows if isinstance(r, dict)]
        else:
            assert owner.get(f"/{resource.path}/{row_id}{resource.detail}").status_code == 200
    finally:
        owner.__exit__(None, None, None)


# ── The structural rules ─────────────────────────────────────────────────────

def test_every_tenant_scoped_model_is_registered():
    """A model carrying tenant_id is either covered above or explicitly excused.

    Without this, the suite silently stops growing the moment someone adds a
    feature — which is exactly how the platform ended up with six independent
    isolation defects that each looked like an oversight rather than a pattern.
    """
    mapped = {
        cls.__name__ for cls in models.Base.__subclasses__() if hasattr(cls, "tenant_id")
    }
    covered = {r.model for r in RESOURCES} | NOT_ADDRESSABLE
    missing = mapped - covered
    assert not missing, (
        f"tenant-scoped models with no conformance entry: {sorted(missing)}. "
        "Add a Resource(...) above, or add it to NOT_ADDRESSABLE with a reason."
    )


def test_excusals_still_refer_to_real_models():
    """NOT_ADDRESSABLE must not accumulate names of models that no longer exist."""
    stale = {name for name in NOT_ADDRESSABLE if not hasattr(models, name)}
    assert not stale, f"NOT_ADDRESSABLE names models that no longer exist: {sorted(stale)}"


def test_scoped_routers_go_through_the_scope_module():
    """Any router handling a registered resource must import TenantScope.

    A cheap structural guard for the case the behavioural tests cannot see: a
    *new* route added to an already-scoped router, hand-rolling the predicate
    again. If the module is already importing the scope, the reviewer's question
    becomes "why doesn't this route use it?" rather than "is this scoped?".
    """
    import pathlib

    api_dir = pathlib.Path(models.__file__).parent.parent / "api"
    offenders = []
    for resource in RESOURCES:
        source = api_dir / f"{resource.path}.py"
        if not source.exists():
            continue
        text = source.read_text()
        if "tenant_scope" not in text:
            offenders.append(source.name)
    assert not offenders, (
        f"these routers own a tenant-scoped resource but do not use TenantScope: {offenders}"
    )


def test_no_router_writes_the_tenant_predicate_by_hand():
    """The rule lives in `denoiser.api.scope`. Nowhere else may restate it.

    `scope.py`'s docstring opens by listing four dialects of this predicate that
    were in use simultaneously, two of them wrong. Three of the four have since
    been replaced by `TenantScope`/`tenant_predicate`; this stops a fifth being
    typed out. A route that needs a tenant predicate has two supported ways to
    get one, and neither of them is `== current_user.tenant_id`.
    """
    import pathlib
    import re

    api_dir = pathlib.Path(models.__file__).parent.parent / "api"
    pattern = re.compile(r"tenant_id\s*==\s*(current_user|user)\.tenant_id")

    offenders = []
    for source in sorted(api_dir.rglob("*.py")):
        if source.name == "scope.py":  # where the rule is defined and documented
            continue
        for number, line in enumerate(source.read_text().splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{source.name}:{number}")

    assert not offenders, (
        "these lines re-implement the tenant predicate instead of using "
        f"TenantScope.query()/tenant_predicate(): {offenders}"
    )
