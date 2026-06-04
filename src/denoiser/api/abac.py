from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
import re

from denoiser.api.auth import get_current_user
from denoiser.storage.db import User, get_db, Incident, AnalysisRun

class ABACPolicyEngine:
    @staticmethod
    def evaluate(user: User, action: str, resource_type: str, resource_attrs: dict) -> bool:
        """
        Evaluate an Attribute-Based Access Control (ABAC) request.
        - Subject Attributes: user.role, user.department, user.environment_access
        - Resource Attributes: resource_attrs.get("environment"), resource_attrs.get("contains_pii")
        - Action: read, write, delete
        """
        # ADMIN role bypasses ABAC checks
        if user.role == "ADMIN":
            return True

        # Rule 1: Environment-based isolation
        resource_env = resource_attrs.get("environment")
        user_envs = getattr(user, "environment_access", []) or []
        if resource_env and "*" not in user_envs and resource_env not in user_envs:
            return False

        # Rule 2: Department-based write/delete restrictions
        user_dept = getattr(user, "department", "Engineering")
        if action in ["write", "delete"] and resource_type == "incident":
            # Only Operations and Security departments can mutate incidents
            if user_dept not in ["Operations", "Security"]:
                return False

        # Rule 3: PII / Sensitivity policy
        if action == "read" and resource_attrs.get("contains_pii") and user.role == "VIEWER":
            # VIEWERS cannot read sensitive resources containing PII
            return False

        return True


class require_abac:
    def __init__(self, action: str, resource_type: str):
        self.action = action
        self.resource_type = resource_type

    def __call__(
        self,
        request: Request,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
    ) -> User:
        # Extract environment or attributes dynamically from route path
        resource_attrs = {"environment": "dev", "contains_pii": False}

        # Resolve path variables (e.g. incident_id or run_id)
        path_params = request.path_params
        
        if "incident_id" in path_params:
            try:
                incident_id = int(path_params["incident_id"])
                incident = db.query(Incident).filter(Incident.id == incident_id).first()
                if incident:
                    # Map domains ending with .prod or containing prod to environment 'prod'
                    domain = incident.domain or ""
                    resource_attrs["environment"] = "prod" if "prod" in domain.lower() else "dev"
                    # If severity/impact score is very high, assume it contains PII context
                    if (incident.impact_score or 0) > 80:
                        resource_attrs["contains_pii"] = True
            except Exception:
                pass
        
        elif "run_id" in path_params:
            try:
                run_id = path_params["run_id"]
                run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
                if run:
                    # Analysis run source contains environment info
                    source = run.source or ""
                    resource_attrs["environment"] = "prod" if "prod" in source.lower() else "dev"
            except Exception:
                pass

        # Evaluate policy
        allowed = ABACPolicyEngine.evaluate(current_user, self.action, self.resource_type, resource_attrs)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"ABAC Access Denied: You do not have permissions to {self.action} {self.resource_type} in environment '{resource_attrs.get('environment')}' as a {current_user.role} in department '{getattr(current_user, 'department', 'Engineering')}'."
            )
        return current_user
