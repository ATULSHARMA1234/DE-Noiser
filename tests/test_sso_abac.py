import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from denoiser.api.auth import get_password_hash
from denoiser.api.main import app
from denoiser.storage.db import Incident, SessionLocal, Tenant, User, init_db


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    init_db()


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_sso_login_redirect():
    with TestClient(app) as client:
        # Check that SSO login returns a redirect to mock callback
        response = client.get("/auth/sso/login", follow_redirects=False)
        assert response.status_code == 307
        assert "auth/sso/callback" in response.headers["location"]


def test_sso_callback_auto_provisioning(db_session: Session):
    with TestClient(app) as client:
        email = "okta-operator@semanticos.io"
        
        # Cleanup user first if they exist
        db_session.query(User).filter(User.email == email).delete()
        db_session.commit()

        # Execute callback
        response = client.get("/auth/sso/callback?code=mock_okta_code_abc123")
        assert response.status_code == 200
        
        data = response.json()
        assert "access_token" in data
        assert data["user"]["email"] == email
        assert data["user"]["department"] == "Operations"
        assert "prod" in data["user"]["environment_access"]

        # Check DB to verify auto-provisioning
        db_user = db_session.query(User).filter(User.email == email).first()
        assert db_user is not None
        assert db_user.department == "Operations"
        assert db_user.role == "ANALYST"


def test_abac_policies(db_session: Session):
    with TestClient(app) as client:
        # Create a test tenant
        default_tenant = db_session.query(Tenant).filter(Tenant.name == "Default Workspace").first()
        tenant_id = default_tenant.id if default_tenant else 1

        # Create two incident resources: one in prod, one in dev (domain contains env info)
        prod_incident = Incident(
            title="Database Outage",
            domain="db-primary-prod.semanticos.io",
            impact_score=95.0, # >80 triggers contains_pii=True
            status="OPEN",
            tenant_id=tenant_id
        )
        dev_incident = Incident(
            title="Local test fail",
            domain="localhost-dev",
            impact_score=30.0,
            status="OPEN",
            tenant_id=tenant_id
        )
        db_session.add_all([prod_incident, dev_incident])
        db_session.commit()

        # Clean up users to avoid duplicate key errors
        db_session.query(User).filter(User.email.in_([
            "eng-dev@semanticos.io",
            "ops-prod@semanticos.io",
            "view-compliance@semanticos.io"
        ])).delete()
        db_session.commit()

        # User 1: Analyst in Engineering with only dev access
        user_eng = User(
            email="eng-dev@semanticos.io",
            hashed_password=get_password_hash("password123"),
            role="ANALYST",
            tenant_id=tenant_id,
            is_active=True,
            department="Engineering",
            environment_access=["dev"]
        )
        # User 2: Analyst in Operations with staging/dev/prod access
        user_ops = User(
            email="ops-prod@semanticos.io",
            hashed_password=get_password_hash("password123"),
            role="ANALYST",
            tenant_id=tenant_id,
            is_active=True,
            department="Operations",
            environment_access=["dev", "prod"]
        )
        # User 3: Viewer in Compliance with prod access
        user_view = User(
            email="view-compliance@semanticos.io",
            hashed_password=get_password_hash("password123"),
            role="VIEWER",
            tenant_id=tenant_id,
            is_active=True,
            department="Compliance",
            environment_access=["dev", "prod"]
        )
        db_session.add_all([user_eng, user_ops, user_view])
        db_session.commit()

        # Login to get tokens
        t_eng = client.post("/auth/login", json={"email": user_eng.email, "password": "password123"}).json()["access_token"]
        t_ops = client.post("/auth/login", json={"email": user_ops.email, "password": "password123"}).json()["access_token"]
        t_view = client.post("/auth/login", json={"email": user_view.email, "password": "password123"}).json()["access_token"]

        # --- Rule 1: Environment-based isolation check ---
        # User 1 (Eng with dev only) accesses Dev incident: OK
        res = client.get(f"/incidents/{dev_incident.id}", headers={"Authorization": f"Bearer {t_eng}"})
        assert res.status_code == 200

        # User 1 (Eng with dev only) accesses Prod incident: 403 Forbidden
        res = client.get(f"/incidents/{prod_incident.id}", headers={"Authorization": f"Bearer {t_eng}"})
        assert res.status_code == 403

        # User 2 (Ops with prod access) accesses Prod incident: OK
        res = client.get(f"/incidents/{prod_incident.id}", headers={"Authorization": f"Bearer {t_ops}"})
        assert res.status_code == 200

        # --- Rule 2: Department-based write restriction check ---
        # User 1 (Eng department) tries to resolve Dev incident: 403 Forbidden (Only Operations / Security can write)
        res = client.put(f"/incidents/{dev_incident.id}/resolve", json={"resolved": True}, headers={"Authorization": f"Bearer {t_eng}"})
        assert res.status_code == 403

        # User 2 (Ops department) tries to resolve Dev incident: OK
        res = client.put(f"/incidents/{dev_incident.id}/resolve", json={"resolved": True}, headers={"Authorization": f"Bearer {t_ops}"})
        assert res.status_code == 200

        # --- Rule 3: PII Sensitivity check for Viewers ---
        # User 3 (Viewer) tries to access high impact prod incident (impact > 80 triggers contains_pii): 403 Forbidden
        res = client.get(f"/incidents/{prod_incident.id}", headers={"Authorization": f"Bearer {t_view}"})
        assert res.status_code == 403

        # User 3 (Viewer) tries to access low impact dev incident (contains_pii is False): OK
        res = client.get(f"/incidents/{dev_incident.id}", headers={"Authorization": f"Bearer {t_view}"})
        assert res.status_code == 200
