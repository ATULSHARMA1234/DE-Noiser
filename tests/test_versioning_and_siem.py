"""The API contract, and the audit trail leaving the building.

Two things a customer's security and integration reviews ask for that the
platform could not answer:

  * **"What is your API versioning and deprecation policy?"** There were 148
    routes at the root and no version segment, so any breaking change broke
    every integration at once.
  * **"Can your audit events reach our SIEM?"** They could not. They lived in a
    Postgres table behind the platform's own UI — which is to say, inside the
    system being audited.
"""

from __future__ import annotations

import socket
import threading

import pytest
from fastapi.testclient import TestClient

from denoiser.api.siem import (
    SIEMConfig,
    format_cef,
    format_syslog,
    get_siem_config,
    render,
    send,
)

# ── /v1 addresses every existing route ───────────────────────────────────────

class TestVersionedRouting:
    def test_an_unversioned_route_is_reachable_under_v1(self, client):
        assert client.get("/health").status_code == 200
        assert client.get("/v1/health").status_code == 200

    def test_a_nested_route_is_reachable_under_v1(self, client):
        assert client.get("/v1/health/live").status_code == 200

    def test_the_unversioned_alias_keeps_working(self, client):
        """Existing integrations must not be broken by adding a version.

        The unversioned paths are permanent aliases, not a deprecation window.
        """
        assert client.get("/health/ready").status_code in (200, 503)

    def test_authentication_still_applies_under_v1(self, client):
        """The rewrite must not become a way around the auth dependencies.

        It changes the path before routing and nothing else, so /v1/users is
        the same handler with the same guards.
        """
        anonymous = TestClient(client.app)
        assert anonymous.get("/v1/users").status_code in (401, 403)

    def test_otlp_paths_that_genuinely_live_under_v1_are_untouched(self, client):
        """/v1/logs is OpenTelemetry's path, not ours.

        Rewriting it to /logs would break OTLP ingestion outright. A path that
        already matches a registered route is never rewritten — a rule that
        needs no exception list and cannot go stale.
        """
        # 405, not 404: the route exists and only accepts POST.
        assert client.get("/v1/logs").status_code == 405
        assert client.get("/v1/traces").status_code == 405

    def test_an_unknown_path_still_404s_under_v1(self, client):
        assert client.get("/v1/no-such-endpoint").status_code == 404

    def test_every_response_names_the_contract_that_answered(self, client):
        assert client.get("/health").headers["X-API-Version"] == "v1"

    def test_the_rewrite_reports_the_resolved_path_to_the_audit_trail(self, client):
        """A request to /v1/users must be audited and metered as /users.

        Otherwise every endpoint gains a second identity, quota accounting
        splits across the two, and the audit log has to be searched twice.
        """
        import inspect

        from denoiser.api.versioning import VersionPrefixMiddleware

        source = inspect.getsource(VersionPrefixMiddleware.dispatch)
        assert 'request.scope["path"] = stripped' in source


# ── SIEM forwarding ──────────────────────────────────────────────────────────

class TestSIEMConfiguration:
    def test_forwarding_is_off_until_a_host_is_configured(self, monkeypatch):
        """A deployment with no SIEM is ordinary, not misconfigured — it must
        not warn once per audit event about it."""
        monkeypatch.delenv("SIEM_HOST", raising=False)
        assert get_siem_config().enabled is False

    def test_a_configured_host_enables_it(self, monkeypatch):
        monkeypatch.setenv("SIEM_HOST", "siem.internal")
        monkeypatch.setenv("SIEM_PORT", "1514")
        config = get_siem_config()
        assert config.enabled is True
        assert config.host == "siem.internal"
        assert config.port == 1514


class TestCEFFormatting:
    def _event(self, **overrides):
        event = {
            "tenant_id": 3,
            "user_id": 42,
            "action": "DELETE",
            "resource_type": "/users/7",
            "ip_address": "10.1.2.3",
            "status_code": 200,
        }
        event.update(overrides)
        return event

    def test_the_header_carries_vendor_product_and_signature(self):
        record = format_cef(self._event())
        assert record.startswith("CEF:1|SemanticOS|SemanticOS|")
        assert "DELETE" in record

    def test_arcsight_dictionary_names_are_used(self):
        """So an off-the-shelf SIEM parser maps the fields with no config."""
        record = format_cef(self._event())
        for field in ("suid=42", "src=10.1.2.3", "requestMethod=DELETE"):
            assert field in record

    def test_the_tenant_travels_as_a_custom_string(self):
        assert "cs1Label=tenantId" in format_cef(self._event())
        assert "cs1=3" in format_cef(self._event())

    def test_a_denied_action_is_more_severe_than_a_successful_one(self):
        """403s are somebody probing a boundary, which is what a SIEM rule
        fires on. Ranking them below a 200 would bury them."""
        denied = format_cef(self._event(status_code=403))
        allowed = format_cef(self._event(status_code=200))
        denied_severity = int(denied.split("|")[7 - 1])
        allowed_severity = int(allowed.split("|")[7 - 1])
        assert denied_severity > allowed_severity

    def test_outcome_reflects_the_status(self):
        assert "outcome=success" in format_cef(self._event(status_code=204))
        assert "outcome=failure" in format_cef(self._event(status_code=500))

    def test_a_newline_cannot_split_one_event_into_two(self):
        """This is how a forged audit entry gets into a SIEM: a value carrying
        a newline terminates the record and starts a second one the attacker
        controls."""
        record = format_cef(self._event(resource_type="/users/7\nCEF:1|Evil|"))
        assert "\n" not in record

    def test_an_equals_sign_in_a_value_is_escaped(self):
        """Unescaped, it would end the extension field early and let the rest
        be read as further key/value pairs."""
        record = format_cef(self._event(resource_type="/search?q=a=b"))
        assert "\\=" in record

    def test_a_pipe_in_a_header_field_is_escaped(self):
        """The header is pipe-delimited; an unescaped pipe shifts every
        subsequent field, including severity."""
        record = format_cef(self._event(action="GET|INJECTED"))
        assert "GET\\|INJECTED" in record


class TestSyslogFraming:
    def test_the_frame_is_rfc_5424(self):
        framed = format_syslog({}, "hello", hostname="host1")
        assert framed.startswith("<109>1 ")
        assert " host1 SemanticOS " in framed
        assert framed.endswith("hello")

    def test_render_wraps_cef_in_a_syslog_frame(self):
        payload = render(
            {"action": "POST", "resource_type": "/x", "status_code": 200},
            SIEMConfig(enabled=True, host="h", port=514, protocol="udp", fmt="cef"),
        )
        assert payload.startswith("<109>1 ")
        assert "CEF:1|" in payload

    def test_the_plain_syslog_format_omits_cef(self):
        payload = render(
            {"action": "POST", "resource_type": "/x", "status_code": 200},
            SIEMConfig(enabled=True, host="h", port=514, protocol="udp", fmt="syslog"),
        )
        assert "CEF:" not in payload
        assert "action=POST" in payload


class TestDelivery:
    def test_a_udp_record_reaches_the_collector(self):
        """End to end over a real socket: the framing and the send path are
        exactly what a collector will receive."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(3)
        _host, port = listener.getsockname()

        received: list[bytes] = []

        def receive():
            with __import__("contextlib").suppress(Exception):
                received.append(listener.recvfrom(4096)[0])

        reader = threading.Thread(target=receive)
        reader.start()
        try:
            config = SIEMConfig(enabled=True, host="127.0.0.1", port=port, protocol="udp", fmt="cef")
            assert send("<109>1 test message", config) is True
            reader.join(timeout=3)
        finally:
            listener.close()

        assert received and b"test message" in received[0]

    def test_an_unreachable_collector_does_not_raise(self):
        """The database row is the system of record. Failing the request here
        would turn a logging outage into a customer outage."""
        config = SIEMConfig(enabled=True, host="127.0.0.1", port=1, protocol="tcp", fmt="cef")
        assert send("anything", config) is False

    def test_nothing_is_sent_when_forwarding_is_disabled(self):
        config = SIEMConfig(enabled=False, host="", port=514, protocol="udp", fmt="cef")
        assert send("anything", config) is False


# ── Alert rules and backup automation are real artifacts ─────────────────────

class TestOperationalArtifacts:
    def _repo_file(self, relative: str) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parents[1] / relative).read_text()

    def test_dead_lettering_pages_somebody(self):
        """A quarantined record is a log line the customer will never be able
        to query. Any increase at all has to reach a human."""
        rules = self._repo_file("deploy/prometheus/alerts.yaml")
        assert "SemanticOSIngestionDeadLettering" in rules
        assert "semanticos_ingestion_dead_lettered_total" in rules

    def test_a_stopped_consumer_pages_somebody(self):
        rules = self._repo_file("deploy/prometheus/alerts.yaml")
        assert "SemanticOSIngestionConsumerDown" in rules
        assert "semanticos_ingestion_consumer_up" in rules

    def test_consumer_lag_is_alerted_on_growth_not_just_size(self):
        """Alerting on an absolute backlog pages during every deliberate
        replay; alerting on it growing does not."""
        rules = self._repo_file("deploy/prometheus/alerts.yaml")
        assert "deriv(semanticos_ingestion_consumer_lag" in rules

    def test_backups_are_scheduled_rather_than_documented(self):
        chart = self._repo_file("deploy/helm/semanticos/templates/backup-cronjob.yaml")
        assert "kind: CronJob" in chart
        assert "pg_dump" in chart

    def test_the_backup_job_verifies_the_objects_landed(self):
        """A job that exits 0 without checking is a green dashboard and no
        backup."""
        chart = self._repo_file("deploy/helm/semanticos/templates/backup-cronjob.yaml")
        assert "nothing landed in the bucket" in chart

    def test_dependency_scanning_is_blocking(self):
        """Advisory scanning means a known CVE cannot fail a release, which is
        the control the questionnaire asks about by name."""
        ci = self._repo_file(".github/workflows/ci.yml")
        scan_block = ci.split("- name: Dependency vulnerability scan")[1].split("- name:")[0]
        assert "continue-on-error" not in scan_block

    def test_images_are_signed_and_carry_an_sbom(self):
        ci = self._repo_file(".github/workflows/ci.yml")
        assert "cosign sign" in ci
        assert "sbom: true" in ci

    def test_no_moving_latest_tag_is_published(self):
        """Two clusters 'running the same version' must not be able to be
        running different code.

        Checks the `tags:` lines rather than the file, so the comment
        explaining why `:latest` is gone does not fail the test.
        """
        ci = self._repo_file(".github/workflows/ci.yml")
        tag_lines = [line for line in ci.splitlines() if line.strip().startswith("tags:")]
        assert tag_lines, "expected the build job to declare image tags"
        assert not any(":latest" in line for line in tag_lines)

    def test_the_chart_does_not_default_to_a_moving_tag(self):
        values = self._repo_file("deploy/helm/semanticos/values.yaml")
        tag_lines = [
            line for line in values.splitlines()
            if line.strip().startswith("tag:") and not line.strip().startswith("#")
        ]
        assert tag_lines
        assert not any("latest" in line for line in tag_lines)

    def test_the_e2e_suite_actually_runs_in_ci(self):
        ci = self._repo_file(".github/workflows/ci.yml")
        assert "playwright test" in ci
        assert "frontend-e2e" in ci


@pytest.fixture
def client():
    """The real application, so the middleware chain under test is the one that
    ships."""
    from denoiser.api.main import app

    return TestClient(app)
