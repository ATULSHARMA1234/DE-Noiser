"""The analytical half of a run, exercised as a path.

`test_analysis_persistence.py` opens with the reason this file exists:

    Everything after `if llm_payload:` in run_analysis_task — the Incident, the
    alert dispatch, the runbook trigger — had no test coverage at all. […] It is
    the part of the task most worth restructuring, so its absence of coverage is
    exactly what made restructuring unsafe.

That file covers the persistence half. The ingest→cluster half had no test that
ran it as a path either, because the only entry point was a Celery task. It has
one now: the stages take a `RunState` and return it, so they can be run without
a broker, a database or an outbound socket.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from denoiser.analysis import pipeline
from denoiser.analysis.pipeline import RunAborted, RunRequest, RunState


@pytest.fixture()
def workspace():
    """The caller's own upload directory.

    Sources are confined to it on purpose: `/analyze` takes a path as an
    ordinary string, so an unconfined reader is an arbitrary-file-read
    primitive. Tests write where a real upload would land.
    """
    from denoiser.api.sources import tenant_dir

    return tenant_dir(1)


@pytest.fixture()
def log_file(workspace):
    path = workspace / "app.log"
    path.write_text(
        "\n".join([
            "2026-07-01T10:00:00Z ERROR db connection failed for user 41",
            "2026-07-01T10:00:01Z ERROR db connection failed for user 42",
            "2026-07-01T10:00:02Z ERROR db connection failed for user 43",
            "2026-07-01T10:00:03Z INFO request served in 12ms",
            "2026-07-01T10:00:04Z INFO request served in 30ms",
            "2026-07-01T10:00:05Z WARN cache miss key=abc",
        ]) + "\n",
        encoding="utf-8",
    )
    return path


def _request(log_file, **overrides):
    defaults = dict(sources=[str(log_file)], tenant_id=1, run_id="run_test")
    defaults.update(overrides)
    return RunRequest(**defaults)


class TestRunRequest:
    def test_a_singular_source_is_accepted_as_well_as_a_list(self):
        assert RunRequest.from_dict({"source": "a.log"}).sources == ["a.log"]
        assert RunRequest.from_dict({"sources": ["a.log", "b.log"]}).sources == [
            "a.log", "b.log"
        ]

    def test_blank_sources_are_dropped_not_carried_as_empty_strings(self):
        assert RunRequest.from_dict({"sources": ["a.log", "", None]}).sources == ["a.log"]

    def test_a_run_id_is_always_present(self):
        assert RunRequest.from_dict({"sources": ["a.log"]}).run_id.startswith("run_")

    def test_the_line_cap_precedence_is_request_then_env_then_default(self, monkeypatch):
        monkeypatch.setenv("SEMANTICOS_MAX_ANALYSIS_LINES", "500")
        assert RunRequest.from_dict({"sources": ["a"]}).max_lines == 500
        assert RunRequest.from_dict({"sources": ["a"], "max_lines": 9}).max_lines == 9


class TestIngest:
    def test_a_source_outside_the_tenants_uploads_is_refused(self):
        """`source` is a free-form string from the API; without resolution the
        reader would happily open /etc/passwd and return it in the results."""
        state = RunState(request=RunRequest(sources=["/etc/passwd"], tenant_id=1))
        with pytest.raises(RunAborted):
            pipeline.ingest(state)

    def test_no_sources_at_all_aborts_rather_than_returning_an_empty_run(self):
        state = RunState(request=RunRequest(sources=[]))
        with pytest.raises(RunAborted, match="No readable source"):
            pipeline.ingest(state)

    def test_lines_are_read_with_their_source_label_and_timestamp(self, log_file):
        state = RunState(request=_request(log_file))
        pipeline.ingest(state)

        assert len(state.records) == 6
        assert {r["source_label"] for r in state.records} == {"app"}
        assert all(r["timestamp_ms"] > 0 for r in state.records)
        assert not state.truncated

    def test_the_line_cap_truncates_and_says_so(self, log_file):
        state = RunState(request=_request(log_file, max_lines=2))
        pipeline.ingest(state)

        assert len(state.records) == 2
        assert state.truncated is True

    def test_secrets_are_redacted_before_anything_retains_the_line(self, workspace):
        """Redaction at read time is what makes the persisted snapshot, the
        ClickHouse copy and the LLM sample all inherit the masked text."""
        path = workspace / "creds.log"
        path.write_text("ERROR login failed password=hunter2 for bob\n", encoding="utf-8")
        try:
            state = RunState(request=_request(path))
            pipeline.ingest(state)
        finally:
            path.unlink(missing_ok=True)

        assert "hunter2" not in state.records[0]["raw_text"]


class TestNormalizeAndDedupe:
    def test_repeated_lines_collapse_to_one_template(self, log_file):
        state = RunState(request=_request(log_file))
        pipeline.ingest(state)
        pipeline.normalize_and_dedupe(state)

        assert state.deduper.total_count == 6
        # The three "db connection failed for user N" lines share one template.
        assert len(state.unique_templates) < 6

    def test_nothing_to_template_aborts(self, workspace):
        path = workspace / "blank.log"
        path.write_text("\n", encoding="utf-8")
        try:
            state = RunState(request=_request(path))
            try:
                pipeline.ingest(state)
            except RunAborted:
                return  # an empty file yields no records, which is also correct
            with pytest.raises(RunAborted):
                pipeline.normalize_and_dedupe(state)
        finally:
            path.unlink(missing_ok=True)


class TestTheWholeAnalyticalHalf:
    def test_a_run_reaches_formatted_clusters_without_a_broker_or_a_database(
        self, log_file
    ):
        state = pipeline.analyse(_request(log_file))

        assert state.clusters
        assert state.formatted_clusters
        assert state.total_logs == 6
        assert all("priority" in c for c in state.formatted_clusters)
        assert all("projection_2d" in c for c in state.formatted_clusters)

    def test_progress_is_reported_in_order(self, log_file):
        seen: list[tuple[int, str]] = []
        pipeline.analyse(_request(log_file), progress=lambda p, s: seen.append((p, s)))

        percents = [p for p, _ in seen]
        assert percents == sorted(percents)
        assert percents[0] == 10


class TestPartialFailureIsVisible:
    """A run that lost half its enrichment used to report plain success."""

    def test_a_failing_stage_is_named_in_the_result(self, log_file, monkeypatch):
        state = pipeline.analyse(_request(log_file))

        def boom(*a, **k):
            raise RuntimeError("scorer exploded")

        monkeypatch.setattr(
            "denoiser.detection.severity.SeverityScorer.score_all", boom
        )
        state.failures.clear()
        pipeline.correlate(state)

        assert [f.stage for f in state.failures] == ["severity"]
        assert pipeline.result(state)["complete"] is False
        assert pipeline.result(state)["skipped_stages"] == [
            {"stage": "severity", "error": "scorer exploded"}
        ]

    def test_a_clean_run_is_marked_complete(self, log_file):
        state = pipeline.analyse(_request(log_file))
        payload = pipeline.result(state)

        assert payload["complete"] is True
        assert payload["skipped_stages"] == []
        assert payload["status"] == "success"

    def test_a_broken_vector_store_does_not_lose_the_run(self, log_file):
        """It costs the index, not the analysis — and it says which."""
        store = MagicMock()
        store.add_embeddings.side_effect = RuntimeError("lancedb down")

        state = pipeline.analyse(_request(log_file), vector_store=store)

        assert "vector_index" in [f.stage for f in state.failures]
        assert state.formatted_clusters, "the run still produced its clusters"

    def test_a_missing_baseline_costs_the_scoring_not_the_run(self, log_file):
        """This used to be uncaught, so a bad baseline path failed the whole run
        after the expensive stages had already been paid for."""
        state = pipeline.analyse(_request(log_file, baseline="/nonexistent/baseline"))

        assert "anomaly_scoring" in [f.stage for f in state.failures]
        assert state.formatted_clusters


class TestPendingAlert:
    _UNSET = object()

    def _state(self, priority, llm_payload=_UNSET):
        state = RunState(request=RunRequest(sources=["a.log"], run_id="r1"))
        state.llm_payload = (
            {"incident_summary": "pool exhausted"}
            if llm_payload is self._UNSET else llm_payload
        )
        state.formatted_clusters = [
            {"cluster_id": 3, "priority": priority, "representative_log": "boom",
             "anomaly_score": 0.9, "keyword_flag": True}
        ]
        return state

    def test_no_intelligence_means_no_alert(self):
        assert pipeline.pending_alert(self._state("P0", llm_payload=None)) is None

    def test_p3_does_not_alert(self):
        assert pipeline.pending_alert(self._state("P3")) is None

    @pytest.mark.parametrize("priority", ["P0", "P1", "P2"])
    def test_p2_and_above_alert(self, priority):
        alert = pipeline.pending_alert(self._state(priority))
        assert alert is not None
        assert alert.priority == priority
        assert alert.run_id == "r1"

    def test_the_alert_reads_the_state_not_a_branch_local(self):
        """Two values here were bound inside one `if llm_payload:` and read
        seventy lines later under a second one — correct only for as long as
        those two conditions stayed identical."""
        alert = pipeline.pending_alert(self._state("P1"))
        assert alert.representative_log == "boom"
        assert alert.keyword_flag is True

    def test_an_alert_with_no_clusters_still_names_something(self):
        state = self._state("P0")
        state.formatted_clusters = []
        assert pipeline.pending_alert(state) is None, "no clusters means no priority"


class TestIntelligenceDoesNotLeakAcrossRuns:
    def test_asking_for_intelligence_does_not_enable_it_globally(self, log_file):
        """`settings.llm_enabled = True` was assigned inside the task, on a
        process-wide singleton, and never restored — so the first run that asked
        for intelligence turned it on for every run after it in that worker."""
        from denoiser.config import settings

        before = settings.llm_enabled
        pipeline.analyse(_request(log_file, intelligence=True))
        assert settings.llm_enabled == before

    def test_the_flag_is_per_instance(self):
        from denoiser.intelligence.llm import IncidentIntelligence

        assert IncidentIntelligence(enabled=False).enabled is False
