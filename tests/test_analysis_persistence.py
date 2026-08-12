"""The analysis task's persistence and announcement phases.

Everything after `if llm_payload:` in `run_analysis_task` — the Incident, the
alert dispatch, the runbook trigger — had **no test coverage at all**. The five
existing tests that reach the task all pass ``intelligence: False``, so
`llm_payload` is None on every one and that whole block never executes.

That is worse than an untested corner. It is the part of the task most worth
restructuring, so its absence of coverage is exactly what made restructuring
unsafe: the suite would have stayed green whatever the change did to it.

These tests stub the LLM rather than the phases around it, so the task runs its
real control flow end to end and the assertions are about observable effects —
what was committed, what was dispatched, in which order.
"""

from __future__ import annotations

import pytest

from denoiser.api.sources import tenant_dir
from denoiser.storage.db import (
    AnalysisRun,
    Incident,
    Runbook,
    SessionLocal,
    Tenant,
    Webhook,
)

TENANT_NAME = "analysis-persistence"

#: An LLM verdict severe enough to raise an incident and clear the P2 alert gate.
STUB_SUMMARY = {
    "incident_summary": "The billing database stopped accepting connections.",
    "failure_domain": "Database Connection Pool",
    "root_cause_hints": ["Check the replica", "Check pool limits"],
    "cluster_summaries": ["Connection pool exhausted"],
}


@pytest.fixture(scope="module")
def tenant():
    from denoiser.storage.db import init_db

    init_db()
    db = SessionLocal()
    try:
        row = db.query(Tenant).filter(Tenant.name == TENANT_NAME).first()
        if row is None:
            row = Tenant(name=TENANT_NAME, tier="enterprise")
            db.add(row)
            db.commit()
            db.refresh(row)
        yield row.id
    finally:
        db.close()


@pytest.fixture
def source(tenant):
    """A log file with a genuine error pattern, in the tenant's own directory."""
    directory = tenant_dir(tenant)
    path = directory / "persistence-probe.log"
    path.write_text(
        "\n".join(
            f"2026-07-27T09:0{i}:00.000Z [ERROR] billing-service: "
            f"Failed to acquire connection from pool: timeout after 30s (attempt {i})"
            for i in range(9)
        )
        + "\n"
    )
    yield path.name
    path.unlink(missing_ok=True)


@pytest.fixture
def intelligent(monkeypatch):
    """Stub the LLM so the intelligence path runs without a model."""
    from denoiser.intelligence.llm import IncidentIntelligence

    monkeypatch.setattr(
        IncidentIntelligence, "generate_summary",
        lambda self, clusters, anomalies, top_n=10: dict(STUB_SUMMARY),
    )
    monkeypatch.setattr(
        IncidentIntelligence, "narrate_causal_links",
        lambda self, links, *a, **k: links,
    )


def _run(tenant_id: int, source_name: str) -> dict:
    from denoiser.workers.analysis_worker import run_analysis_task

    return run_analysis_task.apply(
        args=[{
            "source": source_name,
            "sources": [source_name],
            "intelligence": True,
            "top_n": 5,
            "tenant_id": tenant_id,
        }]
    ).get()


def _cleanup(tenant_id: int) -> None:
    db = SessionLocal()
    try:
        db.query(Incident).filter(Incident.tenant_id == tenant_id).delete()
        db.query(Runbook).filter(Runbook.tenant_id == tenant_id).delete()
        db.query(Webhook).filter(Webhook.tenant_id == tenant_id).delete()
        db.commit()
    finally:
        db.close()


class TestTheIntelligencePathIsReached:
    def test_an_incident_is_committed_for_the_right_organisation(self, tenant, source, intelligent):
        try:
            result = _run(tenant, source)
            assert result["status"] == "success"
            assert result["intelligence"]["failure_domain"] == "Database Connection Pool"

            db = SessionLocal()
            try:
                incidents = db.query(Incident).filter(Incident.tenant_id == tenant).all()
                assert len(incidents) == 1, "the intelligence path committed no Incident"
                assert incidents[0].domain == "Database Connection Pool"
                assert incidents[0].run_id == result["run_id"]
            finally:
                db.close()
        finally:
            _cleanup(tenant)

    def test_a_matching_runbook_is_triggered(self, tenant, source, intelligent):
        """`process_incident` runs against the committed incident."""
        db = SessionLocal()
        try:
            db.add(Runbook(
                tenant_id=tenant, name="Page the DBA", enabled=True,
                trigger_condition={"keyword": "Database"},
                steps=[{"type": "log", "message": "runbook fired"}],
            ))
            db.commit()
        finally:
            db.close()

        try:
            _run(tenant, source)
            from denoiser.storage.db import RunbookExecution

            db = SessionLocal()
            try:
                rb = db.query(Runbook).filter(Runbook.tenant_id == tenant).first()
                assert db.query(RunbookExecution).filter(
                    RunbookExecution.runbook_id == rb.id
                ).count() >= 1, "the runbook trigger never ran"
            finally:
                db.close()
        finally:
            _cleanup(tenant)


class TestNothingIsAnnouncedFromInsideTheTransaction:
    """Verified by regression: reverting the commit to after the announcement
    block makes the test below fail, and takes the runbook test with it (the
    execution row's FK points at an incident that does not exist yet). A test
    for an ordering guarantee is only worth having if the wrong order breaks
    it, so that was checked rather than assumed."""

    def test_the_run_is_committed_before_any_alert_is_dispatched(
        self, tenant, source, intelligent, monkeypatch
    ):
        """The ordering guarantee, asserted rather than assumed.

        Dispatch used to run before `db.commit()`, so a webhook that took its
        full retry budget held a Postgres transaction — and its pooled
        connection — open for tens of seconds. It also meant an alert could
        reach an operator describing a run that was not yet readable.
        """
        from denoiser.integrations import alert_router as router_module

        observed: dict = {}

        async def _watch(alert, destinations=None):
            # Read through a *separate* session: the task's own session would
            # see its uncommitted rows either way, so it cannot answer this.
            probe = SessionLocal()
            try:
                observed["incident_visible_to_others"] = probe.query(Incident).filter(
                    Incident.tenant_id == tenant
                ).count()
                observed["run_visible_to_others"] = probe.query(AnalysisRun).filter(
                    AnalysisRun.id == alert.run_id
                ).count()
            finally:
                probe.close()
            observed["dispatched"] = alert.priority
            return []

        monkeypatch.setattr(router_module.alert_router, "dispatch", _watch)

        db = SessionLocal()
        try:
            from denoiser.integrations import webhook_store

            webhook_store.create_webhook(
                db, tenant_id=tenant, name="ops", channel_type="slack",
                url="https://hooks.slack.com/services/T000/B000/persistencetestxxxx",
            )
            db.commit()
        finally:
            db.close()

        try:
            _run(tenant, source)

            assert observed.get("dispatched"), "no alert was dispatched at all"
            assert observed["incident_visible_to_others"] == 1, (
                "the alert was dispatched before the run was committed — a "
                "recipient could act on an incident nobody else can read yet"
            )
            assert observed["run_visible_to_others"] == 1, (
                "the alert names a run that is not yet durable; following the "
                "link in it would 404"
            )
        finally:
            _cleanup(tenant)
