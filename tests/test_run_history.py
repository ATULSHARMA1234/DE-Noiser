"""One run history, shared by the worker and the CLI.

The CLI used to write `AnalysisRecord`/`ClusterRecord` into a second SQLite file
(`data/cli_history.db`) that nothing ever read back, while the worker wrote
`AnalysisRun` rows the API serves. Same event, two schemas, and the terminal
user's half was invisible to the product.
"""

import numpy as np
import pytest

from denoiser.clustering.models import Cluster
from denoiser.storage.db import AnalysisRun, SessionLocal, init_db
from denoiser.storage.runs import (
    format_clusters,
    record_intelligence,
    record_run,
    worst_priority,
)


@pytest.fixture()
def db():
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _cluster(cluster_id: int, size: int = 10) -> Cluster:
    return Cluster(
        cluster_id=cluster_id,
        centroid=np.zeros(3),
        size=size,
        representative_template="db connection failed",
        representative_raw="ERROR db connection failed",
        representative_source="app.log",
        representative_line=1,
        representative_timestamp_ms=0,
        templates=["db connection failed"],
        projection_2d=[],
    )


class TestSnapshotShape:
    def test_a_cluster_with_no_summary_is_marked_pending_not_blank(self):
        snapshot = format_clusters([_cluster(0)], summaries=[])
        assert snapshot[0]["summary"] == "Analyzing..."

    def test_summaries_are_matched_by_position(self):
        snapshot = format_clusters([_cluster(0), _cluster(1)], summaries=["first", "second"])
        assert [c["summary"] for c in snapshot] == ["first", "second"]

    def test_a_cluster_absent_from_the_scoring_is_known_not_anomalous(self):
        snapshot = format_clusters([_cluster(0)], anomalies={})
        assert snapshot[0]["anomaly_label"] == "known"
        assert snapshot[0]["anomaly_score"] == 0.0


class TestWorstPriority:
    def test_p2_is_surfaced_when_nothing_worse_is_present(self):
        """The loop this replaced only ever promoted to P0/P1, so a run whose
        worst cluster was P2 stayed P3 and never reached the P2 alert gate."""
        assert worst_priority([{"priority": "P3"}, {"priority": "P2"}]) == "P2"

    def test_the_worst_wins_regardless_of_order(self):
        assert worst_priority([{"priority": "P3"}, {"priority": "P0"}]) == "P0"

    def test_no_priorities_at_all_is_p3(self):
        assert worst_priority([{}, {}]) == "P3"
        assert worst_priority([]) == "P3"


class TestRecordRun:
    def test_the_reduction_ratio_is_derived_not_supplied(self, db):
        """Callers cannot disagree about what it means if they cannot pass it."""
        run = record_run(
            db, run_id="cli_test1", tenant_id=None, source="app.log",
            raw_lines=100, clusters_snapshot=[{}] * 10, duration_sec=1.0,
        )
        assert run.cluster_count == 10
        assert run.reduction_ratio == pytest.approx(0.9)

    def test_an_empty_source_does_not_divide_by_zero(self, db):
        run = record_run(
            db, run_id="cli_test2", tenant_id=None, source="empty.log",
            raw_lines=0, clusters_snapshot=[], duration_sec=0.1,
        )
        assert run.reduction_ratio == 0

    def test_a_recorded_run_is_readable_back(self, db):
        record_run(
            db, run_id="cli_test3", tenant_id=None, source="app.log",
            raw_lines=50, clusters_snapshot=format_clusters([_cluster(0)]),
            duration_sec=2.5,
        )
        db.commit()

        stored = db.query(AnalysisRun).filter(AnalysisRun.id == "cli_test3").one()
        assert stored.source == "app.log"
        assert stored.tenant_id is None
        assert stored.clusters_snapshot[0]["representative_template"] == "db connection failed"

        db.delete(stored)
        db.commit()


class TestRecordIntelligence:
    def test_the_incident_carries_the_runs_worst_priority(self, db):
        incident = record_intelligence(
            db, run_id="cli_test4", tenant_id=None, source="app.log",
            raw_lines=100, cluster_count=2,
            clusters_snapshot=[{"priority": "P3"}, {"priority": "P1"}],
            intelligence={"failure_domain": "Database", "incident_summary": "pool exhausted"},
        )
        assert incident.severity == "P1"
        assert incident.title == "Database"
        assert incident.run_id == "cli_test4"

    def test_a_summary_with_no_domain_still_records(self, db):
        incident = record_intelligence(
            db, run_id="cli_test5", tenant_id=None, source="app.log",
            raw_lines=1, cluster_count=1, clusters_snapshot=[],
            intelligence={},
        )
        assert incident.title == "Unknown Failure"
        assert incident.summary == ""


class TestTheSecondDatabaseIsGone:
    def test_the_cli_history_module_no_longer_exists(self):
        with pytest.raises(ImportError):
            import denoiser.storage.database  # noqa: F401

    def test_the_worker_does_not_import_its_pipeline_through_the_cli(self):
        """Importing the worker used to build a Typer app, load the AWS, k8s,
        Slack and S3 connectors, and create a third SQLite database it never read."""
        from pathlib import Path

        import denoiser.workers.analysis_worker as worker

        source = Path(worker.__file__).read_text(encoding="utf-8")
        assert "from denoiser.cli" not in source
        assert "import denoiser.cli" not in source
