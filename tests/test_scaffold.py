"""Smoke tests for project scaffolding and CLI import."""

from __future__ import annotations


def test_version_exists() -> None:
    from denoiser import __version__
    assert __version__ == "0.1.0"


def test_cli_app_importable() -> None:
    from denoiser.cli.main import app
    assert app is not None


def test_config_defaults() -> None:
    from denoiser.config import settings
    assert settings.embedding_model == "all-MiniLM-L6-v2"
    assert settings.min_cluster_size == 5
    assert settings.redact_by_default is True
    assert settings.default_format == "table"
