"""
Issue tracking: durable identity for a log pattern across analysis runs.

The behaviour worth pinning down is that a second run of the same pattern
updates one issue rather than creating a second one — including when the
cluster's representative template drifts, which is the normal case because the
representative is whichever template sits closest to the centroid.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from denoiser.utils.time import utcnow


@pytest.fixture()
def db():
    from denoiser.storage.db import Base, SessionLocal, engine

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def clean_issues(db):
    """Each test starts from an empty issue table for tenant 1.

    Deployment markers go too: they are what the suspect-deploy lookup reads, so
    one left behind by an earlier test answers a later test's query.
    """
    from denoiser.storage.db import DeploymentMarker, IssueComment, IssueEvent, LogIssue

    for model in (IssueEvent, IssueComment, LogIssue, DeploymentMarker):
        db.query(model).delete()
    db.commit()
    yield


class _Record:
    """Minimal stand-in for a LogRecord — the upsert only reads these fields."""

    def __init__(self, raw_text, timestamp, metadata=None, source="app.log", line_number=1):
        self.raw_text = raw_text
        self.timestamp = timestamp
        self.metadata = metadata or {}
        self.source = source
        self.line_number = line_number


class _Cluster:
    def __init__(self, cluster_id, templates):
        self.cluster_id = cluster_id
        self.templates = templates


def _cluster_data(cluster_id=0, template="connection refused to <IP>", priority="P1", size=3):
    return {
        "cluster_id": cluster_id,
        "size": size,
        "summary": "Database connection pool exhausted",
        "source": f"payments:{cluster_id}",
        "representative_log": "connection refused to 10.0.0.4",
        "representative_template": template,
        "priority": priority,
        "anomaly_score": 0.42,
    }


class TestFingerprint:
    def test_same_pattern_same_service_matches(self):
        from denoiser.analysis.issues import fingerprint

        assert fingerprint("payments", "timeout after <NUM>ms") == fingerprint("Payments", "timeout after <NUM>ms")

    def test_same_pattern_different_service_differs(self):
        from denoiser.analysis.issues import fingerprint

        assert fingerprint("payments", "timeout") != fingerprint("checkout", "timeout")


class TestSummarizeRecords:
    def test_tags_histogram_and_window(self):
        from denoiser.analysis.issues import summarize_records

        base = datetime(2026, 2, 10, 16, 0, 0)
        records = [
            _Record("a", base, {"service": "payments", "level": "ERROR"}),
            _Record("b", base + timedelta(minutes=5), {"service": "payments", "level": "ERROR"}),
            _Record("c", base + timedelta(hours=2), {"service": "payments", "level": "WARN"}),
        ]

        facts = summarize_records(records)

        assert facts["event_count"] == 3
        assert facts["tags"]["service"][0] == {"value": "payments", "count": 3, "pct": 100.0}
        # Two hourly buckets: 16:00 (two lines) and 18:00 (one).
        assert [p["count"] for p in facts["histogram"]] == [2, 1]
        assert facts["first_seen"] == base
        assert facts["last_seen"] == base + timedelta(hours=2)
        assert len(facts["samples"]) == 3

    def test_records_without_timestamps_still_counted(self):
        from denoiser.analysis.issues import summarize_records

        facts = summarize_records([_Record("a", None), _Record("b", None)])

        assert facts["event_count"] == 2
        assert facts["histogram"] == []
        assert facts["first_seen"] is None


class TestUpsert:
    def test_first_run_creates_issue(self, db):
        from denoiser.analysis.issues import upsert_issues
        from denoiser.storage.db import LogIssue

        template = "connection refused to <IP>"
        records = [_Record("connection refused to 10.0.0.4", datetime(2026, 2, 10, 16, 0), {"service": "payments"})]

        result = upsert_issues(
            db, 1, "run_a", [_cluster_data(template=template)],
            clusters=[_Cluster(0, [template])], groups={template: records},
        )
        db.commit()

        assert result == {"created": 1, "updated": 0}
        issue = db.query(LogIssue).one()
        assert issue.state == "FOR_REVIEW"
        assert issue.service == "payments"
        assert issue.total_events == 1
        assert issue.run_count == 1

    def test_second_run_updates_the_same_issue(self, db):
        from denoiser.analysis.issues import upsert_issues
        from denoiser.storage.db import LogIssue

        template = "connection refused to <IP>"
        # Recent, because the merged histogram only retains the last four weeks.
        earlier = utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(days=2)
        later = earlier + timedelta(days=1)
        first = [_Record("x", earlier, {"service": "payments"})]
        second = [_Record("y", later, {"service": "payments"})]

        upsert_issues(db, 1, "run_a", [_cluster_data(template=template)],
                      clusters=[_Cluster(0, [template])], groups={template: first})
        db.commit()
        result = upsert_issues(db, 1, "run_b", [_cluster_data(cluster_id=7, template=template)],
                               clusters=[_Cluster(7, [template])], groups={template: second})
        db.commit()

        assert result == {"created": 0, "updated": 1}
        issue = db.query(LogIssue).one()
        assert issue.total_events == 2
        assert issue.run_count == 2
        assert issue.last_run_id == "run_b"
        assert issue.first_seen == earlier
        assert issue.last_seen == later
        # Both runs' buckets survive the merge.
        assert len(issue.histogram) == 2

    def test_representative_drift_does_not_fork_the_issue(self, db):
        """The representative template can change between runs; the issue must not."""
        from denoiser.analysis.issues import upsert_issues
        from denoiser.storage.db import LogIssue

        a, b = "connection refused to <IP>", "connection reset by <IP>"
        cluster = _Cluster(0, [a, b])

        upsert_issues(db, 1, "run_a", [_cluster_data(template=a)],
                      clusters=[cluster], groups={a: [_Record("x", None)], b: []})
        db.commit()
        upsert_issues(db, 1, "run_b", [_cluster_data(template=b)],
                      clusters=[_Cluster(0, [b, a])], groups={b: [_Record("y", None)], a: []})
        db.commit()

        assert db.query(LogIssue).count() == 1

    def test_recurrence_after_resolution_reopens(self, db):
        from denoiser.analysis.issues import upsert_issues
        from denoiser.storage.db import IssueEvent, LogIssue

        template = "disk full on <HOST>"
        upsert_issues(db, 1, "run_a", [_cluster_data(template=template)],
                      clusters=[_Cluster(0, [template])], groups={template: [_Record("x", None)]})
        db.commit()

        issue = db.query(LogIssue).one()
        issue.state = "RESOLVED"
        db.commit()

        upsert_issues(db, 1, "run_b", [_cluster_data(template=template)],
                      clusters=[_Cluster(0, [template])], groups={template: [_Record("y", None)]})
        db.commit()

        db.refresh(issue)
        assert issue.state == "FOR_REVIEW"
        assert db.query(IssueEvent).filter(IssueEvent.kind == "regression").count() == 1

    def test_other_tenants_issues_are_not_matched(self, db):
        from denoiser.analysis.issues import upsert_issues
        from denoiser.storage.db import LogIssue

        template = "shared pattern <NUM>"
        for tenant in (1, 2):
            upsert_issues(db, tenant, "run_a", [_cluster_data(template=template)],
                          clusters=[_Cluster(0, [template])], groups={template: [_Record("x", None)]})
        db.commit()

        assert db.query(LogIssue).count() == 2


class TestSuspectDeployment:
    def test_last_deploy_before_first_seen_is_returned(self, db):
        from denoiser.analysis.issues import suspect_deployment
        from denoiser.storage.db import DeploymentMarker, LogIssue

        first_seen = datetime(2026, 2, 10, 16, 0)
        db.add_all([
            DeploymentMarker(tenant_id=1, service="payments", version="1.0.0",
                             environment="prod", timestamp=first_seen - timedelta(hours=6)),
            DeploymentMarker(tenant_id=1, service="payments", version="1.1.0",
                             environment="prod", timestamp=first_seen - timedelta(minutes=20)),
            # After the fact — cannot have caused it.
            DeploymentMarker(tenant_id=1, service="payments", version="1.2.0",
                             environment="prod", timestamp=first_seen + timedelta(hours=1)),
        ])
        issue = LogIssue(tenant_id=1, fingerprint="fp", title="t", service="payments",
                         first_seen=first_seen, last_seen=first_seen)
        db.add(issue)
        db.commit()

        suspect = suspect_deployment(db, 1, issue)

        assert suspect["version"] == "1.1.0"
        assert suspect["minutes_before_first_seen"] == 20

    def test_no_marker_in_window_returns_none(self, db):
        from denoiser.analysis.issues import suspect_deployment
        from denoiser.storage.db import DeploymentMarker, LogIssue

        first_seen = datetime(2026, 2, 10, 16, 0)
        db.add(DeploymentMarker(tenant_id=1, service="payments", version="0.9.0",
                                environment="prod", timestamp=first_seen - timedelta(days=9)))
        issue = LogIssue(tenant_id=1, fingerprint="fp2", title="t", service="payments",
                         first_seen=first_seen, last_seen=first_seen)
        db.add(issue)
        db.commit()

        assert suspect_deployment(db, 1, issue) is None


@pytest.fixture()
def client(db):
    from denoiser.api.auth import get_current_user
    from denoiser.api.main import app
    from denoiser.storage.db import User

    admin = db.query(User).filter(User.email == "issues-admin@semanticos.io").first()
    if admin is None:
        admin = User(email="issues-admin@semanticos.io", hashed_password="x", role="ADMIN", tenant_id=1)
        db.add(admin)
        db.commit()
        db.refresh(admin)

    app.dependency_overrides[get_current_user] = lambda: admin
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def seeded_issue(db):
    from denoiser.analysis.issues import upsert_issues
    from denoiser.storage.db import LogIssue

    template = "payment gateway timeout after <NUM>ms"
    records = [
        _Record("payment gateway timeout after 3000ms", datetime(2026, 2, 10, 16, 0),
                {"service": "payments", "level": "ERROR", "env": "prod"}),
        _Record("payment gateway timeout after 5000ms", datetime(2026, 2, 10, 16, 30),
                {"service": "payments", "level": "ERROR", "env": "prod"}),
    ]
    upsert_issues(db, 1, "run_seed", [_cluster_data(template=template)],
                  clusters=[_Cluster(0, [template])], groups={template: records})
    db.commit()
    return db.query(LogIssue).one()


class TestIssuesApi:
    def test_list_returns_issue_and_state_counts(self, client, seeded_issue):
        body = client.get("/issues").json()

        assert body["total"] == 1
        assert body["counts"]["FOR_REVIEW"] == 1
        row = body["issues"][0]
        assert row["service"] == "payments"
        assert row["severity"] == "P1"
        # The sparkline is gap-filled so a quiet hour is drawn, not skipped.
        assert len(row["sparkline"]) == 48

    def test_detail_carries_tags_samples_and_activity(self, client, seeded_issue):
        detail = client.get(f"/issues/{seeded_issue.id}").json()

        assert detail["tags"]["level"][0]["value"] == "ERROR"
        assert detail["tags"]["level"][0]["pct"] == 100.0
        assert len(detail["samples"]) == 2
        assert detail["activity"][0]["kind"] == "seen"

    def test_state_change_is_recorded_on_the_activity_feed(self, client, seeded_issue):
        response = client.patch(f"/issues/{seeded_issue.id}", json={"state": "REVIEWED"})
        assert response.status_code == 200
        assert response.json()["state"] == "REVIEWED"

        activity = client.get(f"/issues/{seeded_issue.id}").json()["activity"]
        state_events = [e for e in activity if e["kind"] == "state"]
        assert state_events[0]["detail"] == {"from": "FOR_REVIEW", "to": "REVIEWED"}

    def test_unknown_state_is_rejected(self, client, seeded_issue):
        assert client.patch(f"/issues/{seeded_issue.id}", json={"state": "WONTFIX"}).status_code == 400

    def test_comment_round_trips(self, client, seeded_issue):
        assert client.post(f"/issues/{seeded_issue.id}/comments", json={"body": "  paging the payments team  "}).status_code == 200

        detail = client.get(f"/issues/{seeded_issue.id}").json()
        assert detail["comments"][0]["body"] == "paging the payments team"
        assert detail["comments"][0]["author_email"] == "issues-admin@semanticos.io"

    def test_empty_comment_is_rejected(self, client, seeded_issue):
        assert client.post(f"/issues/{seeded_issue.id}/comments", json={"body": "   "}).status_code == 400

    def test_filters_narrow_the_list(self, client, seeded_issue):
        assert client.get("/issues?severity=P0").json()["total"] == 0
        assert client.get("/issues?severity=P1").json()["total"] == 1
        assert client.get("/issues?q=gateway").json()["total"] == 1
        assert client.get("/issues?q=nothing-matches-this").json()["total"] == 0

    def test_facets_report_value_counts(self, client, seeded_issue):
        facets = client.get("/issues/facets").json()["facets"]

        assert {"value": "payments", "count": 1} in facets["service"]
        assert {"value": "P1", "count": 1} in facets["severity"]

    def test_missing_issue_is_404(self, client):
        assert client.get("/issues/999999").status_code == 404
