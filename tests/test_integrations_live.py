import json
import pytest
import respx
from httpx import Response
from unittest.mock import MagicMock, patch

from denoiser.integrations.alert_router import (
    AlertPayload,
    AlertRouter,
    ChannelType,
    DeliveryStatus,
    WebhookConfig,
)
from denoiser.integrations.email import EmailNotifier


@pytest.fixture
def sample_alert() -> AlertPayload:
    return AlertPayload(
        source="billing-service",
        run_id="run-12345",
        priority="P0",
        cluster_id=42,
        cluster_summary="Database connection timeouts in billing-service",
        representative_log="ERROR [db] Failed to connect to postgres: pool timeout after 30s",
        anomaly_score=0.95,
        causal_links=[
            {"source_service": "gateway", "target_service": "billing-service", "confidence": 0.8}
        ],
        intelligence={
            "failure_domain": "Database",
            "incident_summary": "Database connectivity loss affecting billing APIs.",
            "root_cause_hints": ["Check DB replica status", "Verify networking between gateway and DB"]
        },
        keyword_flag=True
    )


class TestSlackAndPagerDutyAlertRouting:
    """Tests verify Slack and PagerDuty alert serialization and delivery over HTTP using respx."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_slack_webhook_delivery_success(self, sample_alert):
        webhook_url = "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
        
        # Register a mock Slack responder route
        slack_route = respx.post(webhook_url).mock(return_value=Response(200, json={"ok": True}))

        router = AlertRouter()
        cfg = WebhookConfig(
            id="slack-test",
            name="#sre-alerts",
            channel_type=ChannelType.SLACK,
            url=webhook_url,
            min_priority="P2"
        )
        router.register(cfg)

        records = await router.dispatch(sample_alert)
        assert len(records) == 1
        assert records[0].status == DeliveryStatus.DELIVERED
        assert records[0].http_status == 200

        # Verify payload sent to Slack
        assert slack_route.called
        request_data = json.loads(slack_route.calls.last.request.content)
        assert "blocks" in request_data
        # Verify header structure
        header_text = request_data["blocks"][0]["text"]["text"]
        assert "🔴 [P0] SemanticOS Alert" in header_text
        assert "Database" in header_text

    @respx.mock
    @pytest.mark.asyncio
    async def test_pagerduty_v2_events_delivery_success(self, sample_alert):
        pd_url = "https://events.pagerduty.com/v2/enqueue"
        
        # Register a mock PagerDuty responder route
        pd_route = respx.post(pd_url).mock(return_value=Response(202, json={"status": "success", "message": "Event processed"}))

        router = AlertRouter()
        cfg = WebhookConfig(
            id="pd-test",
            name="PagerDuty Service Alerting",
            channel_type=ChannelType.PAGERDUTY,
            url=pd_url,
            min_priority="P1",
            extra={"routing_key": "pd_routing_key_123"}
        )
        router.register(cfg)

        records = await router.dispatch(sample_alert)
        assert len(records) == 1
        assert records[0].status == DeliveryStatus.DELIVERED
        assert records[0].http_status == 202

        # Verify payload sent to PagerDuty
        assert pd_route.called
        request_data = json.loads(pd_route.calls.last.request.content)
        assert request_data["routing_key"] == "pd_routing_key_123"
        assert request_data["event_action"] == "trigger"
        assert request_data["payload"]["severity"] == "critical"
        assert "Database connection timeouts" in request_data["payload"]["summary"]
        assert request_data["payload"]["custom_details"]["cluster_id"] == 42


class TestEmailIntegration:
    """Tests verify SMTP configuration and generated email body structures."""

    @patch("smtplib.SMTP")
    def test_email_alert_generation_and_sending(self, mock_smtp_class, sample_alert):
        mock_smtp = MagicMock()
        mock_smtp_class.return_value.__enter__.return_value = mock_smtp

        notifier = EmailNotifier()
        notifier.host = "smtp.mailhog.local"
        notifier.port = 1025
        notifier.from_addr = "alerts@semanticos.io"
        notifier.to_addr = "sre-ops@semanticos.io"
        notifier.user = "test_user"
        notifier.password = "test_password"

        notifier.send_alert(sample_alert)

        # Verify SMTP server lifecycle
        mock_smtp_class.assert_called_once_with("smtp.mailhog.local", 1025)
        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once_with("test_user", "test_password")
        
        # Verify email message generation
        mock_smtp.send_message.assert_called_once()
        sent_msg = mock_smtp.send_message.call_args[0][0]
        
        assert sent_msg["Subject"] == "[P0] SemanticOS Incident - Database connection timeouts in billing-service"
        assert sent_msg["From"] == "alerts@semanticos.io"
        assert sent_msg["To"] == "sre-ops@semanticos.io"
        
        # Verify content types
        assert sent_msg.is_multipart()
        html_payload = None
        for part in sent_msg.walk():
            if part.get_content_subtype() == "html":
                html_payload = part.get_content()
                break

        assert html_payload is not None
        assert "[P0] Incident in Database" in html_payload
        assert "billing-service" in html_payload
        assert "0.950" in html_payload  # Anomaly score formatted
        assert "Failed to connect to postgres" in html_payload
