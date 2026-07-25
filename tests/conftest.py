"""Test-wide isolation.

The suite used to run against whatever ``DATABASE_URL`` pointed at, which in a
developer checkout is the same SQLite file the running app uses. Tests that
create rows — monitors, users, integrations — left them in the real database, so
a fake monitor from a test run showed up on the Monitors page, and any later
verification of the app was reading data the suite had written.

This module redirects the database (and the data directory that log files, run
artifacts and the dead-letter queue are written to) at a temporary location
before anything under ``denoiser`` is imported. Nothing here changes what the
tests assert; it only changes where their side effects land.
"""

import os
import tempfile
from pathlib import Path

# Must happen before the first `denoiser` import: denoiser.storage.db reads
# DATABASE_URL at module scope and builds the engine from it once.
_TEST_ROOT = Path(tempfile.mkdtemp(prefix="semanticos-tests-"))
_TEST_DATA = _TEST_ROOT / "data"
_TEST_DATA.mkdir(parents=True, exist_ok=True)

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_ROOT / 'test.db'}"
os.environ.setdefault("SEMANTICOS_DATA_DIR", str(_TEST_DATA))
# Keep the suite off a developer's real infrastructure even if their shell has
# these exported. ClickHouse-backed tests already skip when the client is absent.
os.environ.setdefault("SEMANTICOS_ENV", "test")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def test_data_dir() -> Path:
    """The throwaway data directory this run writes to."""
    return _TEST_DATA


@pytest.fixture(scope="session", autouse=True)
def _report_isolation(request):
    """Fail loudly if the redirect did not take.

    A silent fallback to the developer's database is exactly the failure this
    module exists to prevent, so assert it rather than trusting the env var.
    """
    from denoiser.storage.db import DATABASE_URL

    assert str(_TEST_ROOT) in DATABASE_URL, (
        f"tests are pointed at {DATABASE_URL!r}, not the temporary database. "
        "Something imported denoiser.storage.db before conftest ran."
    )
    yield
