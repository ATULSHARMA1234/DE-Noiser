"""Every response carries the standard security headers.

There were none anywhere — not in the application, not in `nginx.conf`, not in
the `Caddyfile` — on an API that authenticates with cookies and shares an origin
with the dashboard behind the proxy. A framed dashboard is a clickjack onto real
ADMIN actions; a missing HSTS header means one plaintext request is enough to
strip a session.

These are also the first thing an enterprise security review checks, and it
checks them with a single curl.
"""

import pytest
from fastapi.testclient import TestClient

EXPECTED = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


@pytest.fixture(scope="module")
def client():
    from denoiser.api.main import app
    return TestClient(app)


class TestHeadersArePresent:
    @pytest.mark.parametrize("header,value", EXPECTED.items())
    def test_on_a_successful_response(self, client, header, value):
        res = client.get("/health")
        assert res.status_code == 200
        assert res.headers.get(header) == value

    @pytest.mark.parametrize("header,value", EXPECTED.items())
    def test_on_an_unauthenticated_rejection(self, client, header, value):
        """401s come from a dependency, not a route, and must still carry them."""
        res = client.get("/users")
        assert res.status_code in (401, 403)
        assert res.headers.get(header) == value

    @pytest.mark.parametrize("header,value", EXPECTED.items())
    def test_on_a_not_found(self, client, header, value):
        res = client.get("/no-such-route-exists")
        assert res.status_code == 404
        assert res.headers.get(header) == value

    def test_permissions_policy_disables_what_we_never_use(self, client):
        policy = client.get("/health").headers.get("Permissions-Policy", "")
        for feature in ("camera", "microphone", "geolocation"):
            assert f"{feature}=()" in policy


class TestContentSecurityPolicy:
    def test_report_only_by_default(self, client):
        res = client.get("/health")
        # Enforcing a policy nobody has watched reports for breaks the console
        # on the day it ships, and a broken console gets the header deleted
        # rather than fixed.
        assert "Content-Security-Policy-Report-Only" in res.headers
        assert "Content-Security-Policy" not in res.headers

    def test_it_denies_framing_and_offsite_posting(self, client):
        policy = client.get("/health").headers["Content-Security-Policy-Report-Only"]
        # The two directives that matter for a console rendering customer log
        # content: it cannot be framed, and an injected script cannot post what
        # it reads somewhere else.
        assert "frame-ancestors 'none'" in policy
        assert "connect-src 'self'" in policy
        assert "object-src 'none'" in policy

    def test_enforcing_mode_switches_the_header(self):
        from fastapi import FastAPI

        from denoiser.api.middleware import SecurityHeadersMiddleware

        app = FastAPI()

        @app.get("/x")
        def _x():
            return {"ok": True}

        app.add_middleware(SecurityHeadersMiddleware, enforce_csp=True)
        res = TestClient(app).get("/x")

        assert "Content-Security-Policy" in res.headers
        assert "Content-Security-Policy-Report-Only" not in res.headers


class TestTheProxiesAgree:
    """The headers are duplicated at the edge; drift between them is the risk."""

    def test_nginx_sets_every_header_the_app_does(self):
        from pathlib import Path

        conf = Path("nginx/nginx.conf").read_text()
        for header in EXPECTED:
            assert header in conf, f"nginx.conf is missing {header}"
            # "always", or nginx silently drops it on error responses — which is
            # exactly when a browser is most likely to render something odd.
            line = next(ln for ln in conf.splitlines() if header in ln)
            assert line.strip().endswith("always;"), f"{header} in nginx.conf is not 'always'"

    def test_caddy_sets_every_header_the_app_does(self):
        from pathlib import Path

        conf = Path("Caddyfile").read_text()
        for header in EXPECTED:
            assert header in conf, f"Caddyfile is missing {header}"

    def test_caddy_no_longer_serves_the_app_over_plain_http(self):
        from pathlib import Path

        conf = Path("Caddyfile").read_text()
        port_80 = conf.split(":80 {", 1)[1].split("}", 1)[0]
        assert "redir" in port_80
        assert "reverse_proxy" not in port_80

    def test_caddy_hostname_is_not_hardcoded(self):
        from pathlib import Path

        conf = Path("Caddyfile").read_text()
        directives = "\n".join(
            line for line in conf.splitlines() if not line.strip().startswith("#")
        )
        assert "nip.io" not in directives
        assert "{$SEMANTICOS_DOMAIN}" in directives
