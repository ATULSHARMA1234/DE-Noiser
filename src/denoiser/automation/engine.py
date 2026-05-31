import time
from datetime import datetime

from sqlalchemy.orm import Session

from denoiser.logging import get_logger
from denoiser.storage.db import Incident, Runbook, RunbookExecution

logger = get_logger(__name__)

def execute_runbook_step(step: dict, execution_logs: list):
    """
    Simulate the execution of a runbook step.
    In a real environment, this might make HTTP requests, run scripts, etc.
    """
    action_type = step.get("action", "unknown")
    execution_logs.append(f"[{datetime.utcnow().isoformat()}] Starting step: {step.get('name', 'Unnamed step')}")

    if action_type == "webhook":
        url = step.get("url", "")
        execution_logs.append(f"[{datetime.utcnow().isoformat()}] Sending POST request to {url}")
        time.sleep(0.5) # Simulate network delay
        execution_logs.append(f"[{datetime.utcnow().isoformat()}] Webhook returned 200 OK")
    elif action_type == "restart_service":
        service = step.get("service", "")
        execution_logs.append(f"[{datetime.utcnow().isoformat()}] Issuing restart command for service {service}")
        time.sleep(1.0)
        execution_logs.append(f"[{datetime.utcnow().isoformat()}] Service {service} restarted successfully")
    elif action_type == "escalate":
        level = step.get("level", "P1")
        execution_logs.append(f"[{datetime.utcnow().isoformat()}] Escalating incident to {level} via PagerDuty integration")
        time.sleep(0.2)
        execution_logs.append(f"[{datetime.utcnow().isoformat()}] Escalation successful")
    else:
        execution_logs.append(f"[{datetime.utcnow().isoformat()}] Unknown action type: {action_type}")
        raise ValueError(f"Unsupported action: {action_type}")

    execution_logs.append(f"[{datetime.utcnow().isoformat()}] Step completed successfully.")

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

            # Create execution record
            execution = RunbookExecution(
                runbook_id=rb.id,
                incident_id=incident.id,
                status="RUNNING",
                logs=[f"[{datetime.utcnow().isoformat()}] Trigger matched: Incident {incident.id}"]
            )
            db.add(execution)
            db.commit()
            db.refresh(execution)

            # Execute steps
            exec_logs = list(execution.logs)
            try:
                for step in rb.steps:
                    execute_runbook_step(step, exec_logs)

                execution.status = "SUCCESS"
            except Exception as e:
                execution.status = "FAILED"
                exec_logs.append(f"[{datetime.utcnow().isoformat()}] Execution failed: {e}")

            execution.logs = exec_logs
            db.commit()
