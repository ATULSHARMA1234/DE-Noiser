"""Analysis input cap (audit finding M3).

A single analysis run bounds how many raw lines it loads so a huge source cannot
OOM the worker. These lock the precedence: per-request override > env > default.
"""

from denoiser.analysis.pipeline import (
    DEFAULT_MAX_ANALYSIS_LINES as _DEFAULT_MAX_ANALYSIS_LINES,
)
from denoiser.analysis.pipeline import (
    resolve_max_lines as _resolve_max_lines,
)


def test_default_cap(monkeypatch):
    monkeypatch.delenv("SEMANTICOS_MAX_ANALYSIS_LINES", raising=False)
    assert _resolve_max_lines({}) == _DEFAULT_MAX_ANALYSIS_LINES


def test_env_overrides_default(monkeypatch):
    monkeypatch.setenv("SEMANTICOS_MAX_ANALYSIS_LINES", "12345")
    assert _resolve_max_lines({}) == 12345


def test_request_overrides_env(monkeypatch):
    monkeypatch.setenv("SEMANTICOS_MAX_ANALYSIS_LINES", "12345")
    assert _resolve_max_lines({"max_lines": 999}) == 999
