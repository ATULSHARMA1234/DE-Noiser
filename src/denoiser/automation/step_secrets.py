"""Credentials inside runbook steps.

A step is free-form JSON, and three of its keys are credentials: a Jira API
token, a PagerDuty routing key, and a Slack webhook URL (the path is the
secret). They were stored as typed and returned by `GET /runbooks` to every
VIEWER. They are now encrypted with the same key as webhook destinations,
masked on the way out, and decrypted only by the engine at execution time.
"""

from __future__ import annotations

from typing import Any

from denoiser.storage.secrets import decrypt, encrypt

SECRET_STEP_KEYS = ("jira_api_token", "pagerduty_integration_key", "slack_webhook_url")

#: What the API returns in place of a set secret. The editor sends it back
#: unchanged when the user did not retype the value.
SECRET_MASK = "********"


def seal_steps(steps: list[dict[str, Any]] | None, previous: list[dict[str, Any]] | None = None) -> list:
    """Encrypt each step's secrets for storage.

    A value equal to the mask means "unchanged": it is replaced with the stored
    value of the same key in the same position, or dropped when there is none.
    """
    previous = previous or []
    sealed = []
    for index, step in enumerate(steps or []):
        if not isinstance(step, dict):
            sealed.append(step)
            continue
        step = dict(step)
        before = previous[index] if index < len(previous) and isinstance(previous[index], dict) else {}
        for key in SECRET_STEP_KEYS:
            value = step.get(key)
            if value == SECRET_MASK:
                if before.get(key):
                    step[key] = before[key]
                else:
                    step.pop(key)
            elif value:
                step[key] = encrypt(value)
        sealed.append(step)
    return sealed


def masked_steps(steps: list[dict[str, Any]] | None) -> list:
    """Steps as the API may show them: every set secret replaced by the mask."""
    return [
        {k: (SECRET_MASK if k in SECRET_STEP_KEYS and v else v) for k, v in step.items()}
        if isinstance(step, dict) else step
        for step in steps or []
    ]


def opened_step(step: dict[str, Any]) -> dict[str, Any]:
    """A step with its secrets decrypted, for the engine. Rows saved before
    encryption carry plaintext, which `decrypt` returns as-is."""
    return {k: (decrypt(v) if k in SECRET_STEP_KEYS and isinstance(v, str) else v) for k, v in step.items()}
