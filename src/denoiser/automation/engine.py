import contextlib

import requests
from requests.auth import HTTPBasicAuth
from sqlalchemy.orm import Session

from denoiser.utils.time import utcnow

# Attempt to load Kubernetes config for cluster management actions
try:
    from kubernetes import client, config
    try:
        config.load_incluster_config()
    except config.ConfigException:
        with contextlib.suppress(config.ConfigException):
            config.load_kube_config()  # Fallback handled in the action logic
    K8S_AVAILABLE = True
except ImportError:
    K8S_AVAILABLE = False


from denoiser.logging import get_logger
from denoiser.storage.db import Incident, Runbook, RunbookExecution

logger = get_logger(__name__)

def _checked_destination(url: str | None, what: str) -> str:
    """The URL a step wants to reach, or a refusal.

    Runbook steps are written by tenant operators — ANALYST is enough to author
    one and to run it — and the URLs in them are ordinary user input that this
    process then fetches. Unguarded, that is a request forgery primitive with
    the platform's network position: `http://169.254.169.254/...` reaches the
    cloud metadata service, and any internal admin port is one hostname away.

    The check runs at execution rather than only at save time because DNS is
    mutable: a name that resolved publicly when the runbook was written can
    point somewhere else by the time it fires. `validate_destination` re-resolves
    on every call for that reason.
    """
    from denoiser.integrations.net_guard import DestinationNotAllowed, validate_destination

    if not url:
        raise ValueError(f"{what} is missing.")
    try:
        return validate_destination(url)
    except DestinationNotAllowed as exc:
        # The reason, never the response. See the `raise_for_status` handling
        # below and the equivalent note in integrations.alert_router.
        raise ValueError(f"{what} is not an allowed destination: {exc}")


def execute_runbook_step(step: dict, incident: Incident, execution_logs: list):
    """
    Execute a runbook step with real API integrations.
    """
    action_type = step.get("action", "unknown")
    execution_logs.append(f"[{utcnow().isoformat()}] Starting step: {step.get('name', 'Unnamed step')}")
    
    incident_payload = {
        "incident_id": incident.id,
        "title": incident.title,
        "severity": incident.severity,
        "status": incident.status,
    }

    try:
        if action_type == "webhook":
            url = _checked_destination(step.get("url"), "Webhook URL")
            execution_logs.append(f"[{utcnow().isoformat()}] Sending POST request to {url}")
            response = requests.post(url, json=incident_payload, timeout=10)
            response.raise_for_status()
            execution_logs.append(f"[{utcnow().isoformat()}] Webhook returned {response.status_code} OK")

        elif action_type == "slack_notification":
            slack_url = _checked_destination(step.get("slack_webhook_url"), "Slack Webhook URL")

            payload = {
                "text": f"🚨 *New Incident:* {incident.title}\n*Severity:* {incident.severity}\n*Status:* {incident.status}"
            }
            execution_logs.append(f"[{utcnow().isoformat()}] Sending Slack notification...")
            response = requests.post(slack_url, json=payload, timeout=10)
            response.raise_for_status()
            execution_logs.append(f"[{utcnow().isoformat()}] Slack message delivered")

        elif action_type == "jira_issue":
            jira_url = step.get("jira_url")
            jira_user = step.get("jira_user")
            jira_token = step.get("jira_api_token")
            if not all([jira_url, jira_user, jira_token]):
                raise ValueError("Jira integration is not fully configured in this runbook step.")

            # Before the credential exists, not after. This step carries a Jira
            # API token in an Authorization header, so an unchecked host here is
            # not only a request forgery — it hands the token to whoever the
            # runbook author pointed it at.
            jira_url = _checked_destination(jira_url, "Jira URL")
            url = f"{jira_url.rstrip('/')}/rest/api/2/issue"
            auth = HTTPBasicAuth(jira_user, jira_token)
            payload = {
                "fields": {
                    "project": {"key": "SRE"},
                    "summary": f"Incident: {incident.title}",
                    "description": f"Severity: {incident.severity}\nAuto-created from Semantic Log Denoiser.",
                    "issuetype": {"name": "Task"}
                }
            }
            execution_logs.append(f"[{utcnow().isoformat()}] Creating Jira Issue...")
            response = requests.post(url, json=payload, auth=auth, timeout=15)
            response.raise_for_status()
            issue_key = response.json().get("key", "UNKNOWN")
            execution_logs.append(f"[{utcnow().isoformat()}] Created Jira ticket {issue_key}")

        elif action_type == "escalate":
            pagerduty_key = step.get("pagerduty_integration_key")
            if not pagerduty_key:
                raise ValueError("PagerDuty Integration Key is not configured in this runbook step.")
            
            url = "https://events.pagerduty.com/v2/enqueue"
            payload = {
                "routing_key": pagerduty_key,
                "event_action": "trigger",
                "payload": {
                    "summary": incident.title,
                    "severity": "critical" if incident.severity == "critical" else "error",
                    "source": "semantic-log-denoiser"
                }
            }
            execution_logs.append(f"[{utcnow().isoformat()}] Escalating to PagerDuty...")
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            execution_logs.append(f"[{utcnow().isoformat()}] PagerDuty escalation successful")

        elif action_type == "restart_service" or action_type == "scale_pods":
            if not K8S_AVAILABLE:
                raise ValueError("Kubernetes python client is not installed or configured.")
            
            service = step.get("service")
            if not service:
                raise ValueError("Service name is missing.")
            
            apps_v1 = client.AppsV1Api()
            # For simplicity, assuming default namespace or parsing from string
            namespace = "default"
            if "/" in service:
                namespace, service = service.split("/", 1)

            if action_type == "restart_service":
                execution_logs.append(f"[{utcnow().isoformat()}] Issuing restart command for {namespace}/{service}")
                # K8s standard way to restart a deployment is to patch its annotations
                now = utcnow().isoformat("T") + "Z"
                body = {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}}
                apps_v1.patch_namespaced_deployment(name=service, namespace=namespace, body=body)
                execution_logs.append(f"[{utcnow().isoformat()}] Service {service} restarted successfully")
            else:
                # scale_pods
                execution_logs.append(f"[{utcnow().isoformat()}] Scaling up {namespace}/{service} (+2 replicas)")
                deployment = apps_v1.read_namespaced_deployment(name=service, namespace=namespace)
                current_replicas = deployment.spec.replicas or 1
                body = {"spec": {"replicas": current_replicas + 2}}
                apps_v1.patch_namespaced_deployment_scale(name=service, namespace=namespace, body=body)
                execution_logs.append(f"[{utcnow().isoformat()}] Kubernetes scale up command issued successfully")
                
        else:
            execution_logs.append(f"[{utcnow().isoformat()}] Unknown action type: {action_type}")
            raise ValueError(f"Unsupported action: {action_type}")

    except requests.exceptions.RequestException as e:
        execution_logs.append(f"[{utcnow().isoformat()}] HTTP Request failed: {e}")
        raise
    except Exception as e:
        execution_logs.append(f"[{utcnow().isoformat()}] Action failed: {e}")
        raise

    execution_logs.append(f"[{utcnow().isoformat()}] Step completed successfully.")

def run_runbook(db: Session, rb: Runbook, incident: Incident, reason: str = "Manual execution"):
    """Execute every step of a runbook and record the attempt.

    Shared by the incident trigger path and the API's manual "run now" — an
    operator could previously only wait for an incident to match, with no way to
    try a runbook they had just written.
    """
    execution = RunbookExecution(
        runbook_id=rb.id,
        incident_id=incident.id if incident else None,
        status="RUNNING",
        logs=[f"[{utcnow().isoformat()}] {reason}"],
    )
    db.add(execution)
    db.commit()
    db.refresh(execution)

    exec_logs = list(execution.logs)
    try:
        for step in rb.steps:
            execute_runbook_step(step, incident, exec_logs)
        execution.status = "SUCCESS"
    except Exception as e:
        execution.status = "FAILED"
        exec_logs.append(f"[{utcnow().isoformat()}] Execution failed: {e}")

    execution.logs = exec_logs
    db.commit()
    db.refresh(execution)
    return execution


def process_incident(db: Session, incident: Incident):
    """
    Called when a new incident is created. Evaluates active runbooks and executes them.
    """
    runbooks = db.query(Runbook).filter(Runbook.enabled, Runbook.tenant_id == incident.tenant_id).all()

    for rb in runbooks:
        # Evaluate trigger conditions
        trigger = rb.trigger_condition
        match = True

        # Simple evaluation: if trigger has "severity", check incident.severity
        if "severity" in trigger and incident.severity != trigger["severity"]:
            match = False

        # Check incident title keyword
        if "keyword" in trigger and trigger["keyword"].lower() not in incident.title.lower():
            match = False

        if match:
            logger.info(f"Incident {incident.id} matches Runbook {rb.id} ({rb.name}). Executing...")
            run_runbook(db, rb, incident, reason=f"Trigger matched: Incident {incident.id}")
