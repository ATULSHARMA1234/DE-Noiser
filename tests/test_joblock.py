"""
Tests for the scheduled-job distributed lock.

The property that matters: when several replicas fire the same nightly job at
the same minute, exactly one runs it. The jobs delete files and database rows,
so a second runner is not a wasted cycle — it is a race over destructive work.

A fake Redis stands in for the real one. It implements only SET NX EX, which is
the entire contract the lock depends on, and it makes the concurrent case
testable without a server.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from denoiser.api import joblock
from denoiser.api.joblock import acquire_job_slot, single_instance


class FakeRedis:
    """Minimal SET NX EX. Shared across 'replicas' like a real server would be."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.set_calls = 0

    async def set(self, key, value, nx=False, ex=None):
        self.set_calls += 1
        # Yield, so concurrent callers genuinely interleave here.
        await asyncio.sleep(0)
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def aclose(self):
        pass


class UnreachableRedis:
    async def set(self, *a, **kw):
        raise ConnectionError("connection refused")

    async def aclose(self):
        pass


@pytest.fixture
def fake_redis(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(joblock, "_redis_url", lambda: "redis://fake:6379/0")
    monkeypatch.setattr(joblock, "_make_client", lambda url: redis)
    return redis


class TestExclusivity:
    @pytest.mark.asyncio
    async def test_only_the_first_caller_wins(self, fake_redis):
        assert await acquire_job_slot("nightly") is True
        assert await acquire_job_slot("nightly") is False
        assert await acquire_job_slot("nightly") is False

    @pytest.mark.asyncio
    async def test_exactly_one_winner_under_concurrency(self, fake_redis):
        """Five replicas firing at once: one runs, four skip."""
        results = await asyncio.gather(*(acquire_job_slot("nightly") for _ in range(5)))

        assert sum(results) == 1, f"expected exactly one winner, got {sum(results)}"

    @pytest.mark.asyncio
    async def test_different_jobs_do_not_block_each_other(self, fake_redis):
        assert await acquire_job_slot("archive") is True
        assert await acquire_job_slot("cleanup") is True

    @pytest.mark.asyncio
    async def test_next_days_occurrence_is_a_new_slot(self, fake_redis):
        """A job that ran today must still be allowed to run tomorrow."""
        day1 = datetime(2026, 7, 23, 2, 0, tzinfo=UTC)
        day2 = datetime(2026, 7, 24, 2, 0, tzinfo=UTC)

        assert await acquire_job_slot("archive", now=day1) is True
        assert await acquire_job_slot("archive", now=day1) is False
        assert await acquire_job_slot("archive", now=day2) is True


class TestAvailabilityPolicy:
    @pytest.mark.asyncio
    async def test_runs_unlocked_when_no_backend_is_configured(self, monkeypatch):
        """Single-instance self-hosting must not silently lose retention."""
        monkeypatch.setattr(joblock, "_redis_url", lambda: None)

        assert await acquire_job_slot("archive") is True

    @pytest.mark.asyncio
    async def test_skips_when_the_backend_is_configured_but_down(self, monkeypatch):
        """Exclusivity cannot be proven, so destructive work must not proceed."""
        monkeypatch.setattr(joblock, "_redis_url", lambda: "redis://down:6379/0")
        monkeypatch.setattr(joblock, "_make_client", lambda url: UnreachableRedis())

        assert await acquire_job_slot("archive") is False


class TestDecorator:
    @pytest.mark.asyncio
    async def test_wrapped_job_body_runs_once_across_replicas(self, fake_redis):
        runs = []

        @single_instance("destructive")
        async def delete_everything():
            runs.append(1)
            return "done"

        results = await asyncio.gather(*(delete_everything() for _ in range(4)))

        assert len(runs) == 1, "job body executed on more than one replica"
        assert results.count("done") == 1
        assert results.count(None) == 3

    @pytest.mark.asyncio
    async def test_unconfigured_backend_still_runs_the_body(self, monkeypatch):
        monkeypatch.setattr(joblock, "_redis_url", lambda: None)
        runs = []

        @single_instance("job")
        async def work():
            runs.append(1)

        await work()
        assert len(runs) == 1
