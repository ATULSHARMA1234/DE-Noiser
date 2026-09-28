"""Inbound alert triggers for automated runbooks.

Split out of `api.main`, which held eleven unrelated concerns in 1,500 lines:
every parallel feature branch touched that one file and every one of them
conflicted. A pure move — no handler below is changed, and the routes are the
same paths at the same methods.
"""

from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
)
from sqlalchemy.orm import Session

from denoiser.api.auth import (
    require_role,
)
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.integrations.alert_router import AlertPayload
from denoiser.logging import get_logger
from denoiser.storage.db import Incident, User, get_db

logger = get_logger(__name__)

router = APIRouter(tags=["Alerts"])


# ─── ALERT TRIGGERS — Automated Runbooks ────────────────────────────────────

@router.post("/alerts/trigger")
def trigger_alert(alert: AlertPayload, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ANALYST", "ADMIN"])), scope: TenantScope = Depends(tenant_scope)):
    """
    Receives an alert and triggers RunbookExecution if it's P0.
    In a real system, this could be triggered by internal analysis or external webhooks.
    """
    if alert.priority == "P0":
        from denoiser.automation.engine import process_incident

        # Check if an incident already exists for this run or create one.
        # Column is `run_id`, not `analysis_run_id` — the old name did not exist
        # on the model and raised AttributeError before any P0 alert could land.
        incident = scope.query(Incident).filter(
            Incident.run_id == alert.run_id,
        ).first()
        if not incident:
            incident = Incident(
                tenant_id=current_user.tenant_id,
                title=f"[P0] {alert.cluster_summary}",
                severity="P0",
                impact_score=1.0,
                status="OPEN",
                run_id=alert.run_id,
                summary=alert.intelligence.get("incident_summary", alert.cluster_summary) if alert.intelligence else alert.cluster_summary,
            )
            db.add(incident)
            db.commit()
            db.refresh(incident)

        process_incident(db, incident)

    return {"status": "success", "alert_fingerprint": alert.fingerprint}

