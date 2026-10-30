"""MFA stance (audit finding M2).

MFA is enforced by the enterprise IdP via SSO. Local email+password login
bypasses it, so it is OFF by default in production and ON in development, with an
explicit override for a break-glass admin.
"""

from denoiser.settings import InfraSettings


def test_local_login_on_in_development_by_default():
    s = InfraSettings(environment="development")
    assert s.local_login_enabled is True


def test_local_login_off_in_production_by_default():
    s = InfraSettings(environment="production")
    assert s.local_login_enabled is False


def test_explicit_enable_overrides_production_default():
    s = InfraSettings(environment="production", allow_local_login=True)
    assert s.local_login_enabled is True


def test_explicit_disable_overrides_development_default():
    s = InfraSettings(environment="development", allow_local_login=False)
    assert s.local_login_enabled is False


def test_login_endpoint_rejects_when_local_login_disabled(monkeypatch):
    """A disabled local login returns 403 before any credential check."""
    from fastapi.testclient import TestClient

    import denoiser.api.main as main
    from denoiser.settings import InfraSettings

    # Explicit disable (dev env keeps the production startup guard from firing;
    # the gate only reads local_login_enabled).
    disabled = InfraSettings(environment="development", allow_local_login=False)
    from denoiser.api import routers_auth

    monkeypatch.setattr(routers_auth, "get_infra_settings", lambda: disabled)

    with TestClient(main.app) as client:
        res = client.post("/auth/login", json={"email": "a@b.com", "password": "x"})
        assert res.status_code == 403
        assert "SSO" in res.json()["detail"]
