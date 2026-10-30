"""Runbook steps name a URL, and this process fetches it. That is user input.

An ANALYST — a customer's own employee, not a platform operator — can author a
runbook and fire it immediately (`POST /runbooks/{id}/run`). Every outbound step
therefore takes a destination from an untrusted source and connects to it with
the platform's network position: inside the VPC, next to the metadata service.

The guard for this already existed (`denoiser.integrations.net_guard`) and was
wired into the alert router and the webhook registry. It was not wired into the
runbook engine, which is the one path that also carries a credential.
"""

import pytest

from denoiser.automation.engine import execute_runbook_step

# Addresses, not names, so none of this depends on a resolver.
BLOCKED = [
    ("http://169.254.169.254/latest/meta-data/", "cloud metadata"),
    ("http://127.0.0.1:8000/platform/tenants", "loopback"),
    ("http://10.0.0.5/admin", "RFC1918"),
    ("http://[::1]:9000/", "IPv6 loopback"),
]


class _Incident:
    id = 1
    title = "Checkout latency"
    severity = "P1"
    status = "OPEN"


class _Recorder:
    """Fails the test if the code under test ever actually sends anything."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError(f"a request was sent to a blocked destination: {args} {kwargs}")


class TestBlockedDestinationsAreRefused:
    @pytest.mark.parametrize("url,label", BLOCKED)
    def test_a_webhook_step_does_not_connect(self, url, label, monkeypatch):
        sent = _Recorder()
        monkeypatch.setattr("denoiser.automation.engine.requests.post", sent)
        logs = []

        with pytest.raises(ValueError, match="not an allowed destination"):
            execute_runbook_step(
                {"name": f"exfil via {label}", "action": "webhook", "url": url},
                _Incident(), logs,
            )

        assert sent.calls == []

    @pytest.mark.parametrize("url,label", BLOCKED)
    def test_a_slack_step_does_not_connect(self, url, label, monkeypatch):
        sent = _Recorder()
        monkeypatch.setattr("denoiser.automation.engine.requests.post", sent)
        logs = []

        with pytest.raises(ValueError, match="not an allowed destination"):
            execute_runbook_step(
                {"name": "notify", "action": "slack_notification", "slack_webhook_url": url},
                _Incident(), logs,
            )

        assert sent.calls == []


class TestACredentialNeverReachesABlockedHost:
    """The Jira step is the worst case: it attaches an API token to the request.

    Validating after the `HTTPBasicAuth` is constructed would still be a leak if
    the ordering were ever rearranged, so the assertion is about the request, not
    about the internal sequence.
    """

    @pytest.mark.parametrize("url,label", BLOCKED)
    def test_no_request_and_no_token_leave_the_process(self, url, label, monkeypatch):
        sent = _Recorder()
        monkeypatch.setattr("denoiser.automation.engine.requests.post", sent)
        logs = []

        with pytest.raises(ValueError, match="not an allowed destination"):
            execute_runbook_step(
                {
                    "name": "file a ticket",
                    "action": "jira_issue",
                    "jira_url": url,
                    "jira_user": "sre@customer.example",
                    "jira_api_token": "ATATT-super-secret-token",
                },
                _Incident(), logs,
            )

        assert sent.calls == []
        # And the token must not have leaked into the execution log either,
        # which the runbook's author can read back through the API.
        assert "ATATT-super-secret-token" not in "\n".join(logs)


class TestSaveTimeRejection:
    """The author should find out while looking at the form, not days later."""

    def test_a_runbook_with_a_blocked_step_is_refused(self):
        from fastapi import HTTPException

        from denoiser.api.runbooks import _reject_disallowed_destinations

        with pytest.raises(HTTPException) as caught:
            _reject_disallowed_destinations(
                [{"name": "ok", "action": "webhook", "url": "https://hooks.slack.com/services/x"},
                 {"name": "bad", "action": "webhook", "url": "http://169.254.169.254/"}]
            )

        assert caught.value.status_code == 400
        # Names the offending step, so a long runbook is actually fixable.
        assert "Step 2" in caught.value.detail

    def test_a_runbook_with_no_destinations_is_fine(self):
        from denoiser.api.runbooks import _reject_disallowed_destinations

        _reject_disallowed_destinations(
            [{"name": "restart", "action": "restart_service", "service": "checkout"}]
        )

    def test_save_time_checking_survives_a_malformed_step(self):
        from denoiser.api.runbooks import _reject_disallowed_destinations

        # Steps are a free-form JSON list; a non-dict entry must not crash the
        # save path before the schema has a chance to reject it.
        _reject_disallowed_destinations(["not a step", None, 42])
