"""Work that was implemented but never reachable, executed, or consumed.

- Usage metering lived on a second Celery app with its own beat that no
  deployment ever started, and no endpoint read the meters it would have
  written. `BillingMeter` was a table definition and nothing else.
- The eBPF collector wrote kernel events to disk and no code anywhere read the
  file, so TCP retransmits and OOM kills were captured and discarded.
- Host telemetry describes the SemanticOS node, not the monitored fleet, and
  said nothing about which.
"""

import json

import pytest
from fastapi.testclient import TestClient

from denoiser.api.auth import create_access_token, get_password_hash
from denoiser.storage.db import BillingMeter, SessionLocal, Tenant, User, init_db


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


@pytest.fixture
def client():
    from denoiser.api.main import app
    return TestClient(app)


@pytest.fixture
def admin_auth():
    db = SessionLocal()
    email = "metering-admin@semanticos.io"
    try:
        db.query(User).filter(User.email == email).delete()
        db.commit()
        tenant = db.query(Tenant).order_by(Tenant.id).first()
        db.add(User(
            email=email, hashed_password=get_password_hash("password123"),
            role="ADMIN", tenant_id=tenant.id if tenant else 1, is_active=True,
        ))
        db.commit()
        yield {"Authorization": f"Bearer {create_access_token(data={'sub': email})}"}
    finally:
        db.query(User).filter(User.email == email).delete()
        db.commit()
        db.close()


class TestBillingIsScheduled:
    def test_metering_runs_on_the_platform_beat(self):
        """It used to require a second beat process nobody ever started."""
        from denoiser.workers import analysis_worker

        scheduled = []

        class Recorder:
            def add_periodic_task(self, schedule, signature, name=None, **kwargs):
                scheduled.append(name)

        analysis_worker.setup_periodic_tasks(Recorder())
        assert "aggregate_billing_daily" in scheduled

    def test_aggregation_is_callable_without_celery(self):
        from denoiser.workers.billing_worker import aggregate_billing

        summary = aggregate_billing(enforce_retention=False)
        assert "tenants" in summary and "metered" in summary

    def test_retention_is_derived_from_the_tier(self):
        from denoiser.workers.billing_worker import (
            DEFAULT_RETENTION_DAYS,
            RETENTION_DAYS_BY_TIER,
        )

        assert RETENTION_DAYS_BY_TIER["enterprise"] > RETENTION_DAYS_BY_TIER["pro"]
        assert RETENTION_DAYS_BY_TIER["pro"] > RETENTION_DAYS_BY_TIER["free"]
        assert RETENTION_DAYS_BY_TIER["free"] == DEFAULT_RETENTION_DAYS


class TestUsageEndpoint:
    def test_usage_reports_stored_meters(self, client, admin_auth):
        db = SessionLocal()
        try:
            tenant = db.query(Tenant).order_by(Tenant.id).first()
            tenant_id = tenant.id if tenant else 1
            from denoiser.utils.time import utcnow

            today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            db.query(BillingMeter).filter(
                BillingMeter.tenant_id == tenant_id, BillingMeter.date == today
            ).delete()
            db.add(BillingMeter(
                tenant_id=tenant_id, date=today,
                total_logs_ingested=1234, total_bytes_ingested=98765,
                total_traces_ingested=7,
            ))
            db.commit()
        finally:
            db.close()

        body = client.get("/admin/usage", headers=admin_auth).json()
        assert body["totals"]["logs"] >= 1234
        assert body["totals"]["traces"] >= 7
        assert body["retention_days"] > 0
        assert any(d["logs"] == 1234 for d in body["daily"])

        db = SessionLocal()
        try:
            db.query(BillingMeter).filter(BillingMeter.total_logs_ingested == 1234).delete()
            db.commit()
        finally:
            db.close()

    def test_usage_requires_admin(self, client):
        assert client.get("/admin/usage").status_code in (401, 403)

    def test_recalculate_does_not_delete_data(self, client, admin_auth, monkeypatch):
        """Asking for a fresh number must not trigger retention deletion."""
        deleted = []
        from denoiser.storage.clickhouse_store import ClickHouseStore

        monkeypatch.setattr(
            ClickHouseStore, "cleanup_old_data",
            lambda self, tenant_id, days: deleted.append((tenant_id, days)),
        )
        res = client.post("/admin/usage/recalculate", headers=admin_auth)
        assert res.status_code == 200
        assert deleted == []


class TestKernelEventsAreConsumed:
    @pytest.fixture
    def event_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SEMANTICOS_DATA_DIR", str(tmp_path))
        path = tmp_path / "ebpf_events.jsonl"
        path.write_text("\n".join(json.dumps(e) for e in [
            {"timestamp": 1_000, "event_type": 1, "pid": 10, "comm": "nginx"},
            {"timestamp": 1_500, "event_type": 1, "pid": 10, "comm": "nginx"},
            {"timestamp": 2_000, "event_type": 2, "pid": 22, "comm": "python"},
            {"timestamp": 9_000, "event_type": 1, "pid": 33, "comm": "postgres"},
        ]) + "\n")
        return path

    def test_events_are_readable_and_named(self, event_file):
        from denoiser.telemetry.ebpf_collector import read_events

        events = read_events()
        assert len(events) == 4
        assert events[0]["event_name"] == "tcp_retransmit"
        assert events[2]["event_name"] == "oom_kill"

    def test_since_filters_by_time(self, event_file):
        from denoiser.telemetry.ebpf_collector import read_events

        assert len(read_events(since_ms=2_000)) == 2

    def test_malformed_lines_are_skipped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SEMANTICOS_DATA_DIR", str(tmp_path))
        (tmp_path / "ebpf_events.jsonl").write_text(
            'not json\n{"timestamp": 5, "event_type": 1}\n{"no_timestamp": true}\n'
        )
        from denoiser.telemetry.ebpf_collector import read_events

        assert len(read_events()) == 1

    def test_missing_file_is_empty_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SEMANTICOS_DATA_DIR", str(tmp_path / "nowhere"))
        from denoiser.telemetry.ebpf_collector import read_events

        assert read_events() == []

    def test_correlator_folds_kernel_events_into_anomaly_context(self, event_file):
        """The whole point: an OOM kill beside an anomaly is now visible evidence."""
        from denoiser.detection.metrics_correlator import MetricsCorrelator

        correlator = MetricsCorrelator(stream_path=str(event_file.parent / "absent.jsonl"))
        context = correlator.get_context_for_anomaly(1_500, window_ms=1_000)

        kernel = context["kernel_events"]
        assert kernel["tcp_retransmits"] == 2
        assert kernel["oom_kills"] == 1
        assert "nginx" in kernel["processes"]
        # Kernel evidence alone is enough to call the window correlated.
        assert context["status"] == "correlated"

    def test_events_outside_the_window_are_excluded(self, event_file):
        from denoiser.detection.metrics_correlator import MetricsCorrelator

        correlator = MetricsCorrelator(stream_path=str(event_file.parent / "absent.jsonl"))
        context = correlator.get_context_for_anomaly(1_000, window_ms=100)
        assert context["kernel_events"]["events_analyzed"] == 1


class TestHostTelemetryIsScoped:
    def test_snapshot_declares_whose_host_it_describes(self):
        from denoiser.telemetry.metrics_collector import MetricsCollector

        snapshot = MetricsCollector().collect_snapshot()
        assert snapshot["scope"] == "semanticos_api_host"
        assert snapshot["host"]

    def test_vitals_endpoint_labels_the_scope(self, client, admin_auth):
        body = client.get("/vitals", headers=admin_auth).json()
        assert body["scope"] == "semanticos_api_host"
        assert "not the monitored fleet" in body["description"]

    def test_collection_can_be_disabled(self, monkeypatch):
        from denoiser.telemetry.metrics_collector import MetricsCollector

        monkeypatch.setenv("HOST_TELEMETRY_ENABLED", "false")
        collector = MetricsCollector()
        assert collector.enabled is False
        collector.start()
        assert collector._running is False
