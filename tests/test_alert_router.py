"""
Tests for the AlertRouter (Task 15).

Uses httpx's MockTransport to avoid network calls.
"""
from __future__ import annotations

import pytest

from denoiser.integrations.alert_router import (
    AlertPayload,
    AlertRouter,
    ChannelType,
    DeliveryStatus,
    WebhookConfig,
    _format_generic,
    _format_pagerduty,
    _format_slack,
    _format_teams,
    _should_route,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_alert(priority: str = "P1") -> AlertPayload:
    return AlertPayload(
        source="test/app.log",
        run_id="run_abc123",
        priority=priority,
        cluster_id=5,
        cluster_summary="Database connection pool exhausted",
        representative_log="ERROR db: connection pool exhausted after 30s timeout",
        anomaly_score=0.82,
        causal_links=[
            {"source_service": "api_gw", "target_service": "db", "confidence": 0.91, "avg_delay_ms": 120}
        ],
        intelligence={
            "failure_domain": "Database",
            "incident_summary": "The database connection pool is exhausted causing cascading failures.",
            "root_cause_hints": ["Scale db connection pool", "Add circuit breaker"],
        },
        keyword_flag=False,
    )


def _make_cfg(channel: ChannelType = ChannelType.GENERIC, min_priority: str = "P1") -> WebhookConfig:
    return WebhookConfig(
        id=WebhookConfig.make_id("test-chan", "http://example.com/hook"),
        name="test-chan",
        channel_type=channel,
        url="http://example.com/hook",
        min_priority=min_priority,
    )


# ── Priority routing logic ────────────────────────────────────────────────────

class TestPriorityRouting:

    @pytest.mark.parametrize("alert_p,min_p,expected", [
        ("P0", "P0", True),
        ("P0", "P1", True),   # P0 is more severe than P1 threshold
        ("P0", "P3", True),   # Always route critical
        ("P1", "P0", False),  # P1 does NOT meet P0-only channel
        ("P2", "P1", False),
        ("P3", "P3", True),
        ("P2", "P2", True),
        ("P1", "P1", True),
    ])
    def test_routing_matrix(self, alert_p, min_p, expected):
        assert _should_route(alert_p, min_p) == expected


# ── Payload formatters ────────────────────────────────────────────────────────

class TestPayloadFormatters:

    def test_slack_has_blocks(self):
        alert = _make_alert("P0")
        payload = _format_slack(alert)
        assert "blocks" in payload
        # Header block contains priority
        header_texts = [b.get("text", {}).get("text", "") for b in payload["blocks"]]
        assert any("P0" in t for t in header_texts)

    def test_pagerduty_has_required_fields(self):
        alert = _make_alert("P0")
        payload = _format_pagerduty(alert, routing_key="test-key-123")
        assert payload["routing_key"] == "test-key-123"
        assert payload["event_action"] == "trigger"
        assert "payload" in payload
        assert payload["payload"]["severity"] == "critical"

    def test_teams_has_theme_color(self):
        alert = _make_alert("P1")
        payload = _format_teams(alert)
        assert "themeColor" in payload
        assert len(payload["sections"]) > 0

    def test_generic_is_json_serialisable(self):
        import json
        alert = _make_alert("P2")
        payload = _format_generic(alert)
        # Must serialise cleanly
        dumped = json.dumps(payload)
        parsed = json.loads(dumped)
        assert parsed["priority"] == "P2"
        assert parsed["version"] == "1.0"
        assert "fingerprint" in parsed

    def test_fingerprint_is_deterministic(self):
        a1 = _make_alert("P0")
        a2 = _make_alert("P0")
        assert a1.fingerprint == a2.fingerprint

    def test_different_priorities_give_different_fingerprints(self):
        a1 = _make_alert("P0")
        a2 = _make_alert("P1")
        assert a1.fingerprint != a2.fingerprint


# ── Router registry ───────────────────────────────────────────────────────────

class TestRouterRegistry:

    def test_register_and_list(self):
        router = AlertRouter()
        cfg = _make_cfg()
        router.register(cfg)
        listed = router.list_destinations()
        assert len(listed) == 1
        assert listed[0]["name"] == "test-chan"

    def test_unregister_existing(self):
        router = AlertRouter()
        cfg = _make_cfg()
        router.register(cfg)
        removed = router.unregister(cfg.id)
        assert removed is True
        assert router.list_destinations() == []

    def test_unregister_nonexistent(self):
        router = AlertRouter()
        assert router.unregister("doesnotexist") is False

    def test_get_destination(self):
        router = AlertRouter()
        cfg = _make_cfg()
        router.register(cfg)
        found = router.get_destination(cfg.id)
        assert found is not None
        assert found.name == "test-chan"


# ── Async delivery ────────────────────────────────────────────────────────────

class TestDelivery:

    @pytest.mark.asyncio
    async def test_delivery_skipped_on_priority_mismatch(self):
        """P3 alert should be SKIPPED on a P1-only channel."""
        router = AlertRouter()
        cfg = _make_cfg(min_priority="P1")
        router.register(cfg)

        alert = _make_alert("P3")
        await router.dispatch(alert)

        # No HTTP records because dispatch was skipped
        skipped = [r for r in router.get_delivery_log() if r["status"] == "skipped"]
        assert len(skipped) >= 1

    @pytest.mark.asyncio
    async def test_delivery_attempted_on_matching_priority(self, respx_mock):
        """P0 alert on a P1 channel should be attempted (HTTP mock returns 200)."""
        import httpx

        router = AlertRouter()
        cfg = _make_cfg(min_priority="P1")
        router.register(cfg)

        respx_mock.post(cfg.url).mock(return_value=httpx.Response(200))

        alert = _make_alert("P0")
        records = await router.dispatch(alert)
        assert len(records) == 1
        assert records[0].status == DeliveryStatus.DELIVERED
        assert records[0].http_status == 200
        assert records[0].latency_ms >= 0

    @pytest.mark.asyncio
    async def test_delivery_recorded_in_audit_log(self, respx_mock):
        """Successful delivery appears in get_delivery_log()."""
        import httpx

        router = AlertRouter()
        cfg = _make_cfg(min_priority="P0")
        router.register(cfg)

        respx_mock.post(cfg.url).mock(return_value=httpx.Response(200))

        alert = _make_alert("P0")
        await router.dispatch(alert)

        log = router.get_delivery_log()
        delivered = [r for r in log if r["status"] == "delivered"]
        assert len(delivered) >= 1
        assert delivered[0]["webhook_id"] == cfg.id
        assert delivered[0]["priority"] == "P0"
