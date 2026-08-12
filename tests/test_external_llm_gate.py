"""A remote model is a choice, not a default.

The product's first claim is that data never leaves the operator's
infrastructure. The incident narrator is the one component that can break that
silently: point `SLD_LLM_BASE_URL` at a hosted API and every analysed run sends
representative log lines to a third party. Nothing said so — not a startup
check, not a log line — while the README promised the opposite.

The gate is not a ban. It requires the operator to say they meant it, which is
also the moment they should be adding that provider to their DPA.
"""

import pytest

from denoiser.settings import _external_llm_problems


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("LLM_ALLOW_EXTERNAL", "SLD_LLM_ENABLED", "SLD_LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


def _configure(monkeypatch, url: str, *, enabled: bool = True, allow: bool | None = None):
    monkeypatch.setenv("SLD_LLM_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("SLD_LLM_BASE_URL", url)
    if allow is not None:
        monkeypatch.setenv("LLM_ALLOW_EXTERNAL", "true" if allow else "false")


class TestLocalModelsPassSilently:
    @pytest.mark.parametrize("url", [
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434/v1",
        "http://host.docker.internal:11434/v1",
        "http://ollama:11434/v1",          # a compose service name
        "http://10.4.2.9:8000/v1",         # the operator's own network
        "http://192.168.1.50:8000/v1",
    ])
    def test_no_complaint(self, monkeypatch, url):
        _configure(monkeypatch, url)
        assert _external_llm_problems() == []


class TestRemoteModelsAreRefused:
    @pytest.mark.parametrize("url,host", [
        ("https://generativelanguage.googleapis.com/v1beta/openai/", "generativelanguage.googleapis.com"),
        ("https://api.openai.com/v1", "api.openai.com"),
        ("https://api.anthropic.com/v1", "api.anthropic.com"),
    ])
    def test_the_host_is_named_in_the_refusal(self, monkeypatch, url, host):
        _configure(monkeypatch, url)
        problems = _external_llm_problems()

        assert len(problems) == 1
        # An operator reading a startup failure needs to know which host, and
        # what to do about it, without opening the source.
        assert host in problems[0]
        assert "LLM_ALLOW_EXTERNAL" in problems[0]

    def test_an_explicit_opt_in_is_honoured(self, monkeypatch):
        _configure(monkeypatch, "https://api.openai.com/v1", allow=True)
        assert _external_llm_problems() == []

    def test_a_disabled_llm_is_not_a_problem(self, monkeypatch):
        """No calls are made, so there is nothing to leave the building."""
        _configure(monkeypatch, "https://api.openai.com/v1", enabled=False)
        assert _external_llm_problems() == []

    def test_no_base_url_is_not_a_problem(self, monkeypatch):
        monkeypatch.setenv("SLD_LLM_ENABLED", "true")
        assert _external_llm_problems() == []


class TestItIsWiredIntoTheProductionGate:
    def test_a_remote_model_blocks_a_production_boot(self, monkeypatch):
        from denoiser.settings import InfraSettings, validate_for_production

        _configure(monkeypatch, "https://api.openai.com/v1")
        settings = InfraSettings(
            environment="production",
            jwt_secret_key="k" * 48,
            database_url="postgresql://u:p@db:5432/semanticos",
            admin_password="a-real-password",
            cors_allowed_origins="https://console.example.com",
        )

        problems = validate_for_production(settings)

        assert any("api.openai.com" in problem for problem in problems), problems
