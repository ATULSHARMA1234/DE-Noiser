"""Ingestion no longer stops at the first 500k lines.

The pipeline read every line into a list before templating anything, so it
needed a cap to avoid exhausting the worker — and that cap meant a large
customer's run analysed a fraction of their data and wrote a log line saying
so. The log line was honest. The behaviour was not what "denoise everything"
promises.

Reading is now streaming: a chunk is normalised, folded into the deduplicator
and dropped. Peak memory tracks *distinct templates*, which is what embedding
and clustering actually cost, rather than lines read.

The load-bearing test here is the equivalence one. Chunking is only safe if the
result does not depend on where the boundaries fall — a template split across
two chunks must produce one group with the full count, not two groups.
"""

from __future__ import annotations

from denoiser.analysis import pipeline
from denoiser.analysis.pipeline import RunRequest, RunState, fold_chunk
from denoiser.preprocessing.deduplication import Deduplicator

#: Message shapes that differ in their *words*. Varying only a number would not
#: produce distinct templates — collapsing numeric variance is precisely what
#: normalisation does, so `user 1 failed` and `user 2 failed` are one template,
#: which is the point of the whole pipeline.
_SHAPES = (
    "connection refused by upstream",
    "database deadlock detected on write",
    "certificate verification failed for peer",
    "rate limit exceeded for client",
    "disk quota exhausted on volume",
    "authentication token expired",
    "upstream returned malformed payload",
    "cache eviction storm detected",
    "replication lag exceeded threshold",
    "worker heartbeat missed",
    "index rebuild aborted midway",
    "message queue backpressure engaged",
    "tls handshake timed out",
    "schema migration lock contended",
    "memory allocator returned null",
    "dns resolution failed for host",
    "checksum mismatch on restore",
    "leader election lost quorum",
    "snapshot upload interrupted",
    "background compaction stalled",
    "session store unreachable",
)


def _records(count: int, distinct: int = 5) -> list[dict]:
    """`count` lines drawn from `distinct` shapes, so grouping is predictable."""
    assert distinct <= len(_SHAPES), "add more shapes to _SHAPES"
    return [
        {
            "raw_text": f"{_SHAPES[i % distinct]} (attempt {i})",
            "source_path": "/logs/app.log",
            "source_label": "app",
            "line_number": i,
            "timestamp": None,
            "timestamp_ms": 0,
            "metadata": "{}",
        }
        for i in range(count)
    ]


def _state(**kwargs) -> RunState:
    return RunState(request=RunRequest(sources=["app.log"], **kwargs))


class TestChunkingDoesNotChangeTheResult:
    def test_one_pass_and_many_chunks_agree(self):
        """The whole safety argument for streaming.

        If the grouping depended on chunk boundaries, a template straddling two
        chunks would become two clusters with half the count each — and nothing
        downstream could tell that had happened.
        """
        records = _records(1000, distinct=7)

        whole = Deduplicator()
        fold_chunk(_state(), records, whole)

        chunked = Deduplicator()
        chunked_state = _state()
        for start in range(0, len(records), 97):  # a size that divides nothing evenly
            fold_chunk(chunked_state, records[start:start + 97], chunked)

        assert whole.unique_count == chunked.unique_count
        assert whole.total_count == chunked.total_count
        assert whole.get_all_counts() == chunked.get_all_counts()
        assert set(whole.get_unique_templates()) == set(chunked.get_unique_templates())

    def test_occurrence_counts_survive_chunking(self):
        """Counts drive cluster sizes and issue trends, so they must be exact."""
        records = _records(500, distinct=5)

        deduper = Deduplicator()
        state = _state()
        for start in range(0, len(records), 33):
            fold_chunk(state, records[start:start + 33], deduper)

        assert deduper.total_count == 500
        assert deduper.unique_count == 5
        assert sum(deduper.get_all_counts().values()) == 500

    def test_an_empty_chunk_is_harmless(self):
        deduper = Deduplicator()
        fold_chunk(_state(), [], deduper)
        assert deduper.total_count == 0


class TestTheLineCeilingIsNoLongerTheWorkingLimit:
    def test_the_default_cap_is_no_longer_half_a_million_lines(self):
        """500k is minutes of output for a large customer. It was a memory
        ceiling wearing the clothes of a policy."""
        assert pipeline.DEFAULT_MAX_ANALYSIS_LINES > 5_000_000

    def test_ingestion_is_chunked_rather_than_accumulated(self):
        assert pipeline.INGEST_CHUNK_LINES <= 100_000

    def test_a_streaming_run_does_not_retain_the_input(self, tmp_path, monkeypatch):
        """Peak memory must track templates, not lines read."""
        source = tmp_path / "big.log"
        source.write_text(
            "".join(f"{_SHAPES[i % 4]} (attempt {i})\n" for i in range(2_000))
        )

        monkeypatch.setattr(pipeline, "INGEST_CHUNK_LINES", 100)
        monkeypatch.setattr(
            "denoiser.api.sources.resolve_source", lambda raw, tenant_id: source
        )

        state = _state()
        state.deduper = Deduplicator()
        peak = 0

        def fold(records):
            nonlocal peak
            peak = max(peak, len(records))
            fold_chunk(state, records, state.deduper)

        pipeline.ingest(state, on_chunk=fold)

        assert state.lines_read == 2_000
        assert peak <= 100, "a chunk grew past the configured size"
        assert state.records == [], "records were retained after the final chunk"
        assert state.deduper.total_count == 2_000
        assert state.deduper.unique_count == 4


class TestTheTemplateCeiling:
    def test_new_templates_stop_being_admitted_past_the_ceiling(self):
        """Embedding and clustering are O(templates). A million distinct
        templates is a normalisation failure, not an analysis."""
        state = _state()
        state.max_templates = 3
        deduper = Deduplicator()

        fold_chunk(state, _records(200, distinct=20), deduper)

        assert deduper.unique_count == 3
        assert state.templates_truncated is True

    def test_occurrences_of_known_templates_still_count_past_the_ceiling(self):
        """Dropping the rest of the chunk outright would understate the sizes
        of the clusters that *are* being reported — a quieter kind of wrong."""
        state = _state()
        state.max_templates = 2
        deduper = Deduplicator()

        fold_chunk(state, _records(100, distinct=2), deduper)
        before = deduper.total_count

        fold_chunk(state, _records(100, distinct=2), deduper)

        assert deduper.unique_count == 2
        assert deduper.total_count == before + 100

    def test_an_ordinary_run_never_reaches_the_ceiling(self):
        state = _state()
        deduper = Deduplicator()
        fold_chunk(state, _records(1_000, distinct=20), deduper)
        assert state.templates_truncated is False
        assert deduper.unique_count == 20


class TestTruncationIsStillReported:
    def test_hitting_the_line_cap_is_recorded_on_the_state(self, tmp_path, monkeypatch):
        """A truncated run must not look like a complete one."""
        source = tmp_path / "capped.log"
        source.write_text("".join(f"line {i}\n" for i in range(500)))

        monkeypatch.setattr(
            "denoiser.api.sources.resolve_source", lambda raw, tenant_id: source
        )

        state = _state(max_lines=100)
        state.deduper = Deduplicator()
        pipeline.ingest(state, on_chunk=lambda r: fold_chunk(state, r, state.deduper))

        assert state.truncated is True
        assert state.lines_read == 100
