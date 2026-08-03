"""
Regression tests for the enterprise-trial findings.

Each class here pins one invariant that was broken in the audited build. They
are written as reproductions of the original defect, so a failure names the
vulnerability rather than the assertion.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from denoiser.api.main import app
from denoiser.storage.db import SessionLocal, Tenant, User

ACME = "acme@hardening.test"
GLOBEX = "globex@hardening.test"
PASSWORD = "HardeningTest!2026"


@pytest.fixture(scope="module")
def tenants():
    """Two tenants with an ADMIN each — the shape a shared deployment has."""
    # The schema is normally created by the app's lifespan; this fixture runs
    # before any TestClient exists, so it has to establish it itself.
    from denoiser.api.auth import get_password_hash
    from denoiser.storage.db import init_db

    init_db()

    db = SessionLocal()
    try:
        created = {}
        for name, email in (("acme-hardening", ACME), ("globex-hardening", GLOBEX)):
            tenant = db.query(Tenant).filter(Tenant.name == name).first()
            if not tenant:
                tenant = Tenant(name=name, tier="enterprise")
                db.add(tenant)
                db.commit()
                db.refresh(tenant)
            user = db.query(User).filter(User.email == email).first()
            if not user:
                user = User(
                    email=email,
                    hashed_password=get_password_hash(PASSWORD),
                    role="ADMIN",
                    tenant_id=tenant.id,
                )
                db.add(user)
                db.commit()
                db.refresh(user)
            created[name] = (tenant.id, user)
        yield created
    finally:
        db.close()


@pytest.fixture(scope="module")
def _app_client():
    """One TestClient running the app's real lifespan."""
    with TestClient(app) as c:
        yield c


def _authenticated(client: TestClient, email: str) -> TestClient:
    """Sign in for real rather than stubbing get_current_user.

    The stub was tempting but wrong for these tests: get_current_user is what
    stamps the tenant onto request.state, so overriding it silently drops the
    tenant from every audit row and makes the scoping tests vacuous.
    """
    resp = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    client.headers.update({"Authorization": f"Bearer {resp.json()['access_token']}"})
    return client


@pytest.fixture
def acme_client(tenants, _app_client):
    client = TestClient(app)
    with client:
        yield _authenticated(client, ACME)


@pytest.fixture
def globex_client(tenants, _app_client):
    client = TestClient(app)
    with client:
        yield _authenticated(client, GLOBEX)


class TestWebhookTenantIsolation:
    """The alert registry was a process-global dict with no owner."""

    SECRET_URL = "https://hooks.slack.com/services/T00ACME/B00ACME/xoxbAcmeSuperSecret9931"

    def _create(self, client):
        return client.post("/webhooks", json={
            "name": "Acme PROD PagerDuty",
            "url": self.SECRET_URL,
            "channel_type": "slack",
        })

    def test_webhook_url_is_never_returned_in_full(self, acme_client):
        resp = self._create(acme_client)
        assert resp.status_code == 201
        assert self.SECRET_URL not in resp.text
        assert "xoxbAcmeSuperSecret9931" not in resp.text

        listed = acme_client.get("/webhooks")
        assert listed.status_code == 200
        assert "xoxbAcmeSuperSecret9931" not in listed.text

    def test_other_tenant_cannot_see_or_touch_the_webhook(self, acme_client, globex_client):
        created = self._create(acme_client)
        webhook_id = created.json()["id"]

        # Not listed...
        listing = globex_client.get("/webhooks")
        assert webhook_id not in listing.text
        assert "xoxbAcmeSuperSecret9931" not in listing.text

        # ...and not reachable by id.
        assert globex_client.put(f"/webhooks/{webhook_id}", json={"name": "pwned"}).status_code == 404
        assert globex_client.post(f"/webhooks/{webhook_id}/test").status_code == 404
        assert globex_client.delete(f"/webhooks/{webhook_id}").status_code == 404

        # The owner's destination survived every one of those attempts.
        still_there = acme_client.get("/webhooks")
        assert webhook_id in still_there.text

    def test_webhook_survives_a_process_restart(self, acme_client, tenants):
        """It lived in memory before, so every destination was lost on restart."""
        self._create(acme_client)
        from denoiser.integrations import webhook_store

        db = SessionLocal()
        try:
            rows = webhook_store.list_webhooks(db, tenants["acme-hardening"][0])
            assert rows, "webhook was not persisted"
            # Stored encrypted, not as plaintext.
            assert "xoxbAcmeSuperSecret9931" not in rows[0].url_encrypted
            # ...but still recoverable for delivery.
            assert webhook_store.to_config(rows[0]).url == self.SECRET_URL
        finally:
            db.close()


class TestWebhookSSRF:
    """Destinations were fetched with no restriction on where they pointed."""

    BLOCKED = [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "https://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/admin/credentials",
        "https://127.0.0.1:8123/?query=SHOW+DATABASES",
        "https://10.0.0.1:8080/",
        "https://192.168.1.1/",
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_INFO",
    ]

    @pytest.mark.parametrize("url", BLOCKED)
    def test_internal_destinations_are_rejected_at_registration(self, acme_client, url):
        resp = acme_client.post("/webhooks", json={
            "name": "probe", "url": url, "channel_type": "generic",
        })
        assert resp.status_code == 400, f"{url} was accepted"

    def test_public_https_destination_is_accepted(self, acme_client):
        resp = acme_client.post("/webhooks", json={
            "name": "legit", "url": "https://hooks.example.com/services/abc", "channel_type": "generic",
        })
        assert resp.status_code == 201


class TestDeliveryErrorsDoNotEchoUpstream:
    """The upstream response body came back in the `error` field."""

    def test_http_failure_describes_status_without_the_body(self):
        from denoiser.integrations.alert_router import _describe_http_failure

        message = _describe_http_failure(500)
        assert "500" in message
        assert "<html>" not in message

    @pytest.mark.asyncio
    async def test_failed_delivery_does_not_leak_response_content(self, respx_mock):
        import httpx

        from denoiser.integrations.alert_router import (
            AlertRouter,
            ChannelType,
            WebhookConfig,
        )

        url = "https://hooks.example.com/hook"
        respx_mock.post(url).mock(
            return_value=httpx.Response(403, text="SECRET-INTERNAL-RESPONSE-BODY")
        )
        router = AlertRouter()
        cfg = WebhookConfig(
            id="x", name="probe", channel_type=ChannelType.GENERIC, url=url, min_priority="P3"
        )
        router.MAX_RETRIES = 1
        record = await router._deliver_with_retry(cfg, _alert())
        assert "SECRET-INTERNAL-RESPONSE-BODY" not in (record.error or "")


def _alert():
    from denoiser.integrations.alert_router import AlertPayload

    return AlertPayload(
        source="test", run_id="r", priority="P0", cluster_id=1,
        cluster_summary="s", representative_log="l", anomaly_score=0.5,
        causal_links=[], intelligence=None, keyword_flag=False,
    )


class TestAuditTenantScoping:
    """/audit/ served one global stream to every tenant's admin."""

    def test_audit_rows_are_scoped_to_the_callers_tenant(self, acme_client, globex_client, tenants):
        acme_tenant_id = tenants["acme-hardening"][0]
        globex_tenant_id = tenants["globex-hardening"][0]

        # Generate an auditable action for each tenant.
        acme_client.put("/settings", json={"retention_days": 45})
        globex_client.put("/settings", json={"retention_days": 46})

        resp = globex_client.get("/audit/?limit=200")
        assert resp.status_code == 200
        rows = resp.json()["data"]
        assert rows, "no audit rows recorded"

        db = SessionLocal()
        try:
            from denoiser.storage.db import AuditLog

            visible_ids = {r["id"] for r in rows}
            foreign = (
                db.query(AuditLog)
                .filter(AuditLog.tenant_id == acme_tenant_id, AuditLog.id.in_(visible_ids))
                .count()
            )
            assert foreign == 0, "another tenant's audit rows were visible"
            assert all(
                db.query(AuditLog).filter(AuditLog.id == r["id"]).first().tenant_id == globex_tenant_id
                for r in rows
            )
        finally:
            db.close()

    def test_settings_change_records_the_previous_value(self, acme_client):
        acme_client.put("/settings", json={"retention_days": 33})
        acme_client.put("/settings", json={"retention_days": 77})

        rows = acme_client.get("/audit/?limit=50").json()["data"]
        changes = [
            r["details"]["changes"]
            for r in rows
            if r["resource_type"] == "/settings" and (r.get("details") or {}).get("changes")
        ]
        assert changes, "no before/after captured for a settings change"
        assert any(
            c.get("retention_days", {}).get("from") == 33
            and c.get("retention_days", {}).get("to") == 77
            for c in changes
        ), f"previous value not recorded: {changes}"

    def test_reading_log_data_is_audited(self, acme_client):
        acme_client.get("/runs/some-run-id")
        rows = acme_client.get("/audit/?limit=50").json()["data"]
        reads = [r for r in rows if (r.get("details") or {}).get("access") == "read"]
        assert reads, "read access to log data was not audited"


class TestIngestValidation:
    """Anything JSON-shaped was accepted as a log line."""

    @pytest.mark.parametrize("payload", [
        [1, 2, 3],
        [None],
        [True],
        [[1, 2]],
        [{"message": "ok"}, 5],
    ])
    def test_non_log_entries_are_rejected(self, acme_client, payload):
        assert acme_client.post("/ingest", json=payload).status_code == 422

    def test_oversized_batch_is_rejected(self, acme_client):
        from denoiser.api.schemas import MAX_INGEST_BATCH

        payload = [{"message": "x"}] * (MAX_INGEST_BATCH + 1)
        assert acme_client.post("/ingest", json=payload).status_code == 422

    def test_ordinary_batch_is_accepted(self, acme_client):
        resp = acme_client.post("/ingest", json=[{"message": "hello"}, "plain line"])
        assert resp.status_code == 200


class TestPaginationBounds:
    """`limit=-1` is 'no limit' in both SQLite and PostgreSQL."""

    NEGATIVE = [
        "/issues?limit=-1",
        "/issues?offset=-5",
        "/alerts/?limit=-1",
        "/alerts/?skip=-10",
        "/audit/?limit=-1",
        "/audit/?skip=-10",
        "/webhooks/log?limit=-1",
    ]
    OVERSIZED = [
        "/issues?limit=100000000",
        "/alerts/?limit=99999999",
        "/audit/?limit=100000000",
        "/webhooks/log?limit=99999999",
    ]

    @pytest.mark.parametrize("path", NEGATIVE)
    def test_negative_pagination_is_rejected(self, acme_client, path):
        assert acme_client.get(path).status_code == 422

    @pytest.mark.parametrize("path", OVERSIZED)
    def test_oversized_pagination_is_rejected(self, acme_client, path):
        assert acme_client.get(path).status_code == 422


class TestInputBounds:
    """Out-of-range input produced a 500 rather than a 4xx."""

    def test_oversized_integer_path_param_is_a_client_error(self, acme_client):
        resp = acme_client.get("/incidents/99999999999999999999")
        assert resp.status_code == 422

    def test_deeply_nested_query_is_a_client_error(self):
        from denoiser.query.parser import QueryTooComplex, parse_query

        with pytest.raises(QueryTooComplex):
            parse_query(" AND ".join(["level:ERROR"] * 2000))

    def test_overlong_query_is_a_client_error(self):
        from denoiser.query.parser import QueryTooComplex, parse_query

        with pytest.raises(QueryTooComplex):
            parse_query("a" * 50000)

    def test_ordinary_query_still_parses(self):
        from denoiser.query.parser import parse_query

        assert parse_query("level:ERROR timeout") is not None


class TestComplianceSettingsAreWired:
    """Both toggles were stored, shown in the UI, and read by nothing."""

    def test_redaction_setting_is_consulted(self):
        from denoiser.api.platform_settings import build_redactor, load_settings, save_settings

        original = load_settings()
        try:
            save_settings({**original, "redact_pii": True})
            assert build_redactor().enabled is True
            save_settings({**original, "redact_pii": False})
            assert build_redactor().enabled is False
        finally:
            save_settings(original)

    def test_raw_log_storage_setting_is_consulted(self):
        from denoiser.api.platform_settings import (
            load_settings,
            raw_log_storage_enabled,
            save_settings,
        )

        original = load_settings()
        try:
            save_settings({**original, "store_raw_logs": False})
            assert raw_log_storage_enabled() is False
            save_settings({**original, "store_raw_logs": True})
            assert raw_log_storage_enabled() is True
        finally:
            save_settings(original)

    def test_ingested_pii_is_not_written_to_disk_verbatim(self, acme_client, test_data_dir):
        """The whole compliance claim, end to end."""
        marker = "F500HardeningMarker"
        acme_client.post("/ingest", json=[{
            "message": f"{marker} SSN=123-45-6789 card=4111111111111111 "
                       f"email=jane@acme.com password=superSecret123",
            "level": "ERROR",
        }])

        stream = test_data_dir / "live_stream.log"
        if not stream.exists():
            pytest.skip("raw log storage is disabled in this configuration")

        content = stream.read_text(errors="replace")
        assert marker in content, "the log was not written at all"
        for secret in ("123-45-6789", "4111111111111111", "jane@acme.com", "superSecret123"):
            assert secret not in content, f"{secret} was stored verbatim"


class TestSourceConfinement:
    """`source` went straight to the log reader as a filesystem path."""

    @pytest.mark.parametrize("path", [
        "/etc/passwd",
        "/etc/hosts",
        ".env",
        "../.env",
        "data/../.env",
    ])
    def test_paths_outside_the_data_root_are_rejected(self, path):
        from denoiser.api.sources import SourceNotAllowed, resolve_source

        with pytest.raises(SourceNotAllowed):
            resolve_source(path, tenant_id=1)

    def test_a_tenant_cannot_resolve_another_tenants_upload(self):
        from denoiser.api.sources import SourceNotAllowed, resolve_source, tenant_dir

        theirs = tenant_dir(4242) / "private.log"
        theirs.write_text("secret\n")
        try:
            with pytest.raises(SourceNotAllowed):
                resolve_source("private.log", tenant_id=1)
            # ...but the owner can.
            assert resolve_source("private.log", tenant_id=4242) == theirs.resolve()
        finally:
            theirs.unlink(missing_ok=True)


class TestLoginBackoff:
    """A flat lockout let anyone lock out any account they knew the email of."""

    def test_backoff_escalates_rather_than_locking_flat(self):
        from denoiser.api.main import LOGIN_FREE_ATTEMPTS, _backoff_seconds

        assert _backoff_seconds(LOGIN_FREE_ATTEMPTS) == 0
        delays = [_backoff_seconds(n) for n in range(LOGIN_FREE_ATTEMPTS + 1, LOGIN_FREE_ATTEMPTS + 6)]
        assert delays == sorted(delays), "backoff should not decrease"
        assert delays[0] < delays[-1], "backoff should escalate"

    def test_admin_unlock_endpoint_is_tenant_scoped(self, acme_client):
        resp = acme_client.post(
            "/admin/login-lockout/clear", json={"email": GLOBEX},
        )
        assert resp.status_code == 404, "an admin unlocked another tenant's account"


class TestCorrelationId:
    """Every 500 came back with request_id 'no-request'."""

    def test_error_response_carries_the_real_request_id(self, acme_client):
        resp = acme_client.get(
            "/incidents/99999999999999999999",
            headers={"X-Request-ID": "trace-me-123"},
        )
        # Now a 422 rather than a 500, but the header must still round-trip.
        assert resp.headers.get("x-request-id") == "trace-me-123"

    def test_resolver_prefers_request_state(self):
        from types import SimpleNamespace

        from denoiser.api.middleware import _resolve_request_id

        request = SimpleNamespace(state=SimpleNamespace(request_id="from-state"))
        assert _resolve_request_id(request) == "from-state"


class TestCookieSessionAndCsrf:
    """Tokens were returned to the browser and kept in localStorage."""

    @pytest.fixture
    def raw_client(self, tenants, _app_client):
        client = TestClient(app)
        with client:
            yield client

    def _login(self, client):
        resp = client.post("/auth/login", json={"email": ACME, "password": PASSWORD})
        assert resp.status_code == 200, resp.text
        return resp

    def test_login_sets_httponly_session_cookies(self, raw_client):
        resp = self._login(raw_client)
        raw_headers = resp.headers.get_list("set-cookie")
        by_name = {h.split("=", 1)[0]: h for h in raw_headers}

        assert "sos_access" in by_name
        assert "sos_refresh" in by_name
        assert "sos_csrf" in by_name

        # The credentials must be unreadable from page script...
        assert "httponly" in by_name["sos_access"].lower()
        assert "httponly" in by_name["sos_refresh"].lower()
        # ...while the CSRF token must be readable, since the client echoes it.
        assert "httponly" not in by_name["sos_csrf"].lower()

    def test_cookie_alone_authenticates_a_read(self, raw_client):
        self._login(raw_client)
        raw_client.headers.pop("Authorization", None)
        resp = raw_client.get("/auth/me")
        assert resp.status_code == 200
        assert resp.json()["email"] == ACME

    def test_state_change_without_csrf_token_is_rejected(self, raw_client):
        self._login(raw_client)
        # httpx's TestClient sends the cookies automatically; omitting the
        # header is exactly the shape of a cross-site forgery.
        resp = raw_client.put("/settings", json={"retention_days": 31})
        assert resp.status_code == 403
        assert "CSRF" in resp.text

    def test_state_change_with_csrf_token_succeeds(self, raw_client):
        self._login(raw_client)
        csrf = raw_client.cookies.get("sos_csrf")
        resp = raw_client.put(
            "/settings", json={"retention_days": 31},
            headers={"X-CSRF-Token": csrf},
        )
        assert resp.status_code == 200

    def test_bearer_clients_are_not_subject_to_csrf(self, raw_client):
        """A browser never sets Authorization on its own, so header auth is safe."""
        token = self._login(raw_client).json()["access_token"]
        raw_client.cookies.clear()
        resp = raw_client.put(
            "/settings", json={"retention_days": 32},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

    def test_refresh_works_from_the_cookie_with_no_body_token(self, raw_client):
        """The browser cannot read the httpOnly refresh cookie to send it."""
        self._login(raw_client)
        before = raw_client.cookies.get("sos_access")

        resp = raw_client.post("/auth/refresh", json={})
        assert resp.status_code == 200
        assert resp.json().get("access_token")
        assert raw_client.cookies.get("sos_access") != before, "session cookie was not rotated"

    def test_logout_revokes_the_session(self, raw_client):
        self._login(raw_client)
        csrf = raw_client.cookies.get("sos_csrf")
        assert raw_client.post(
            "/auth/logout", json={}, headers={"X-CSRF-Token": csrf}
        ).status_code == 200
        raw_client.headers.pop("Authorization", None)
        assert raw_client.get("/auth/me").status_code == 401


class TestOrganisationBoundaryAndInternalCollaboration:
    """Two customers on one deployment: isolated from each other, collaborative within.

    This is the shape the product is sold in — Tenant is the company, Users are
    its staff. Colleagues must be able to find, assign to and talk to each
    other; nobody may see across the company boundary.
    """

    def test_admin_only_sees_their_own_organisations_staff(self, acme_client, globex_client):
        acme_users = acme_client.get("/users")
        assert acme_users.status_code == 200
        assert GLOBEX not in acme_users.text, "another company's staff were listed"
        assert ACME in acme_users.text, "own colleagues were missing"

        globex_users = globex_client.get("/users")
        assert ACME not in globex_users.text

    def test_created_users_join_the_creating_admins_organisation(self, acme_client, tenants):
        """A new hire with a null tenant is invisible to their own colleagues."""
        email = "newhire@hardening.test"
        resp = acme_client.post("/users", json={
            "email": email, "password": "NewHire!2026", "role": "ANALYST",
        })
        assert resp.status_code == 201
        assert resp.json()["tenant_id"] == tenants["acme-hardening"][0]

        # ...and they show up for their colleagues.
        assert email in acme_client.get("/users").text

    def test_admin_cannot_delete_another_companys_employee(self, acme_client, globex_client):
        victim = next(u for u in globex_client.get("/users").json() if u["email"] == GLOBEX)
        assert acme_client.delete(f"/users/{victim['id']}").status_code == 404
        # The account is untouched.
        assert globex_client.get("/auth/me").status_code == 200

    def test_admin_cannot_deactivate_another_companys_employee(self, acme_client, globex_client):
        victim = next(u for u in globex_client.get("/users").json() if u["email"] == GLOBEX)
        assert acme_client.put(f"/users/{victim['id']}/deactivate").status_code == 404
        assert globex_client.get("/auth/me").json()["is_active"] is True

    def test_colleagues_can_collaborate_inside_the_company(self, acme_client, tenants):
        """The other half of the requirement: isolation must not block teamwork."""
        from denoiser.storage.db import LogIssue

        tenant_id = tenants["acme-hardening"][0]
        db = SessionLocal()
        try:
            issue = LogIssue(
                tenant_id=tenant_id,
                fingerprint="collab-fingerprint",
                title="checkout latency",
                service="checkout",
                severity="P1",
                state="FOR_REVIEW",
            )
            db.add(issue)
            db.commit()
            db.refresh(issue)
            issue_id = issue.id
        finally:
            db.close()

        # A colleague is offered as an assignee...
        assignees = acme_client.get(f"/issues/{issue_id}/assignees")
        assert assignees.status_code == 200
        emails = {u["email"] for u in assignees.json()["users"]}
        assert ACME in emails
        assert GLOBEX not in emails, "another company's staff were assignable"

        # ...the issue can be handed to them...
        me = acme_client.get("/auth/me").json()
        patched = acme_client.patch(f"/issues/{issue_id}", json={"assignee_id": me["id"]})
        assert patched.status_code == 200

        # ...and discussed.
        commented = acme_client.post(
            f"/issues/{issue_id}/comments", json={"body": "taking a look now"}
        )
        assert commented.status_code in (200, 201)
        assert "taking a look now" in acme_client.get(f"/issues/{issue_id}").text

    def test_the_other_company_cannot_see_that_work(self, acme_client, globex_client, tenants):
        assert "checkout latency" not in globex_client.get("/issues").text
