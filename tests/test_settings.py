"""
Tests for infrastructure configuration and the production startup gate.

The gate exists to convert silently-unsafe deployments into loud refusals. The
cases below are the ones that are dangerous precisely because everything keeps
working: a forgeable JWT secret, a wildcard CORS origin on a credentialed API,
a mock IdP left enabled. Nothing 500s — you just have no security.
"""

import pytest
from fastapi.testclient import TestClient

from denoiser.settings import (
    KNOWN_INSECURE_JWT_SECRET,
    InfraSettings,
    validate_for_production,
)

SAFE = {
    "environment": "production",
    "jwt_secret_key": "x" * 48,
    "admin_password": "a-real-password",
    "cors_allowed_origins": "https://semanticos.example.com",
    "database_url": "postgresql://user:pass@db:5432/semanticos",
    "sso_allow_mock": False,
}


def settings(**overrides) -> InfraSettings:
    return InfraSettings(**{**SAFE, **overrides})


class TestProductionGate:
    def test_a_safe_configuration_has_no_problems(self):
        assert validate_for_production(settings()) == []

    def test_missing_jwt_secret_is_rejected(self):
        problems = validate_for_production(settings(jwt_secret_key=None))
        assert any("JWT_SECRET_KEY is not set" in p for p in problems)

    def test_the_known_dev_secret_is_rejected(self):
        """This value shipped in an old commit; every token signed with it is forgeable."""
        problems = validate_for_production(settings(jwt_secret_key=KNOWN_INSECURE_JWT_SECRET))
        assert any("forgeable" in p for p in problems)

    def test_a_short_secret_is_rejected(self):
        problems = validate_for_production(settings(jwt_secret_key="tooshort"))
        assert any("at least 32" in p for p in problems)

    def test_wildcard_cors_is_rejected(self):
        problems = validate_for_production(settings(cors_allowed_origins="*"))
        assert any("'*'" in p for p in problems)

    def test_plaintext_origin_is_rejected(self):
        problems = validate_for_production(settings(cors_allowed_origins="http://app.example.com"))
        assert any("plaintext http://" in p for p in problems)

    def test_localhost_over_http_is_allowed(self):
        """Loopback is not a transport risk and appears in legitimate setups."""
        problems = validate_for_production(settings(cors_allowed_origins="http://localhost:3000"))
        assert not any("plaintext" in p for p in problems)

    def test_mock_sso_is_rejected(self):
        problems = validate_for_production(settings(sso_allow_mock=True))
        assert any("mock IdP" in p for p in problems)

    def test_sqlite_is_rejected(self):
        problems = validate_for_production(settings(database_url="sqlite:///./data/semantic_os.db"))
        assert any("SQLite" in p for p in problems)

    def test_every_problem_is_reported_not_just_the_first(self):
        """An operator should get the whole list in one pass, not one per redeploy."""
        problems = validate_for_production(
            settings(jwt_secret_key=None, cors_allowed_origins="*", sso_allow_mock=True)
        )
        assert len(problems) >= 3


class TestEnvironmentDetection:
    @pytest.mark.parametrize("value,expected", [("production", True), ("prod", True), ("PRODUCTION", True), ("development", False), ("staging", False)])
    def test_is_production(self, value, expected):
        assert InfraSettings(environment=value).is_production is expected

    def test_development_defaults_are_permissive(self):
        """Local development must work with no configuration at all."""
        s = InfraSettings(environment="development")
        assert s.is_production is False
        assert s.cors_origin_list == ["http://localhost:3000", "http://127.0.0.1:3000"]


class TestCorsParsing:
    def test_splits_and_strips(self):
        s = InfraSettings(cors_allowed_origins="https://a.com, https://b.com ,https://c.com")
        assert s.cors_origin_list == ["https://a.com", "https://b.com", "https://c.com"]

    def test_ignores_empty_entries(self):
        assert InfraSettings(cors_allowed_origins="https://a.com,,").cors_origin_list == ["https://a.com"]


class TestStartupRefusal:
    def test_unsafe_production_config_refuses_to_boot(self, monkeypatch):
        """The whole point: a bad deploy fails at startup, not at request time."""
        from denoiser.api import main
        unsafe = InfraSettings(**{**SAFE, "jwt_secret_key": KNOWN_INSECURE_JWT_SECRET})
        monkeypatch.setattr(main, "get_infra_settings", lambda: unsafe)

        with pytest.raises(RuntimeError, match="Refusing to start in production"), TestClient(main.app):
            pass

    def test_safe_production_config_boots(self, monkeypatch):
        from denoiser.api import main
        monkeypatch.setattr(main, "get_infra_settings", lambda: InfraSettings(**SAFE))

        with TestClient(main.app) as client:
            assert client.get("/health").status_code == 200


class TestSettingsCaching:
    def test_get_settings_is_cached(self):
        """The environment is read once per process, not per call site."""
        from denoiser.settings import get_settings

        assert get_settings() is get_settings()

    def test_reload_settings_picks_up_a_changed_environment(self, monkeypatch):
        from denoiser.settings import get_settings, reload_settings

        before = get_settings()
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://reloaded.example.com")

        after = reload_settings()

        assert after is not before
        assert after.cors_origin_list == ["https://reloaded.example.com"]
        reload_settings()  # restore for other tests

    def test_is_testing_detects_pytest(self):
        from denoiser.settings import is_testing

        assert is_testing() is True
