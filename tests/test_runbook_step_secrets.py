"""Runbook credentials: encrypted at rest, masked over the API, opened only to run.

A Jira token or PagerDuty key typed into a runbook step was stored as typed and
returned by `GET /runbooks` to every VIEWER in the tenant.
"""

from denoiser.automation.step_secrets import SECRET_MASK, masked_steps, opened_step, seal_steps


def _step(**secrets):
    return {"name": "page on-call", "action": "escalate", **secrets}


class TestSealing:
    def test_secrets_are_not_stored_as_typed(self):
        [stored] = seal_steps([_step(pagerduty_integration_key="R0UT1NG", jira_api_token="tok")])
        assert stored["pagerduty_integration_key"] != "R0UT1NG"
        assert stored["jira_api_token"] != "tok"
        assert stored["name"] == "page on-call"

    def test_engine_gets_the_plaintext_back(self):
        [stored] = seal_steps([_step(pagerduty_integration_key="R0UT1NG")])
        assert opened_step(stored)["pagerduty_integration_key"] == "R0UT1NG"

    def test_rows_saved_before_encryption_still_run(self):
        assert opened_step(_step(pagerduty_integration_key="legacy"))["pagerduty_integration_key"] == "legacy"

    def test_api_never_shows_a_secret(self):
        [stored] = seal_steps([_step(pagerduty_integration_key="R0UT1NG")])
        [shown] = masked_steps([stored])
        assert shown["pagerduty_integration_key"] == SECRET_MASK
        assert shown["action"] == "escalate"

    def test_round_tripped_mask_keeps_the_stored_value(self):
        before = seal_steps([_step(pagerduty_integration_key="R0UT1NG")])
        after = seal_steps(masked_steps(before), previous=before)
        assert opened_step(after[0])["pagerduty_integration_key"] == "R0UT1NG"

    def test_retyped_secret_replaces_the_stored_one(self):
        before = seal_steps([_step(pagerduty_integration_key="OLD")])
        after = seal_steps([_step(pagerduty_integration_key="NEW")], previous=before)
        assert opened_step(after[0])["pagerduty_integration_key"] == "NEW"

    def test_mask_with_nothing_stored_is_dropped_not_saved(self):
        [stored] = seal_steps([_step(pagerduty_integration_key=SECRET_MASK)])
        assert "pagerduty_integration_key" not in stored


class TestEngineDecrypts:
    def test_pagerduty_step_sends_the_real_key(self, monkeypatch):
        from denoiser.automation.engine import execute_runbook_step

        sent = []

        class _Resp:
            status_code = 202

            def raise_for_status(self):
                pass

        monkeypatch.setattr(
            "denoiser.automation.engine.requests.post",
            lambda url, json=None, **kw: sent.append(json) or _Resp(),
        )

        class _Incident:
            id, title, severity, status = 1, "Checkout latency", "critical", "OPEN"

        [stored] = seal_steps([_step(pagerduty_integration_key="R0UT1NG")])
        execute_runbook_step(stored, _Incident(), [])
        assert sent[0]["routing_key"] == "R0UT1NG"
