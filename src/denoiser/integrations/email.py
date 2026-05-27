import os
import smtplib
from email.message import EmailMessage

from denoiser.integrations.alert_router import AlertPayload
from denoiser.logging import get_logger

logger = get_logger(__name__)

class EmailNotifier:
    """Sends HTML email alerts via SMTP."""

    def __init__(self):
        self.host = os.getenv("SMTP_HOST", "localhost")
        self.port = int(os.getenv("SMTP_PORT", "1025")) # Default to MailHog
        self.user = os.getenv("SMTP_USER", "")
        self.password = os.getenv("SMTP_PASSWORD", "")
        self.from_addr = os.getenv("SMTP_FROM", "alerts@semanticos.local")
        self.to_addr = os.getenv("SMTP_TO", "admin@semanticos.local")

    def send_alert(self, alert: AlertPayload):
        """Sends an email alert. Runs synchronously."""
        if not self.host or not self.to_addr:
            logger.warning("SMTP not configured, skipping email alert.")
            return

        try:
            msg = EmailMessage()
            msg['Subject'] = f"[{alert.priority}] SemanticOS Incident - {alert.cluster_summary[:50]}"
            msg['From'] = self.from_addr
            msg['To'] = self.to_addr

            domain = alert.intelligence.get("failure_domain", "Unknown") if alert.intelligence else "Unknown"

            # Build HTML body
            body = f"<h2>[{alert.priority}] Incident in {domain}</h2>"
            body += f"<p><strong>Source:</strong> {alert.source}<br>"
            body += f"<strong>Run ID:</strong> {alert.run_id}<br>"
            body += f"<strong>Anomaly Score:</strong> {alert.anomaly_score:.3f}</p>"

            body += f"<h3>Incident Summary</h3><p>{alert.cluster_summary}</p>"

            if alert.representative_log:
                body += "<h3>Representative Log</h3>"
                body += f"<pre style='background:#f4f4f4;padding:10px;'>{alert.representative_log[:500]}</pre>"

            if alert.causal_links:
                body += "<h3>Causal Propagation</h3><ul>"
                for link in alert.causal_links[:3]:
                    src = link.get('source_service', '?')
                    tgt = link.get('target_service', '?')
                    conf = link.get('confidence', 0) * 100
                    delay = link.get('avg_delay_ms', 0)
                    body += f"<li>{src} &rarr; {tgt} ({conf:.0f}% confidence, {delay:.0f}ms delay)</li>"
                body += "</ul>"

            msg.set_content("Please enable HTML to view this alert.")
            msg.add_alternative(body, subtype='html')

            with smtplib.SMTP(self.host, self.port) as server:
                if self.user and self.password:
                    server.starttls()
                    server.login(self.user, self.password)
                server.send_message(msg)

            logger.info(f"Email alert sent successfully to {self.to_addr}.")
        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")

email_notifier = EmailNotifier()
