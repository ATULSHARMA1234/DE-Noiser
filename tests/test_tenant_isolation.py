import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from denoiser.api.auth import get_current_user
from denoiser.api.main import app
from denoiser.storage.db import (
    AnalysisRun,
    Base,
    Incident,
    ServiceLevelObjective,
    User,
    get_db,
)

# SQLite database setup for isolation tests
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_tenant_isolation.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="module", autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    
    # Tenant 1 Data
    db.add(ServiceLevelObjective(id=301, name="SLO T1", service="auth-service", sli_type="latency", target_percentage=99.0, tenant_id=1))
    db.add(AnalysisRun(id="run-t1", tenant_id=1, status="completed", source="auth-service"))
    db.add(Incident(id=401, title="Incident T1", status="OPEN", run_id="run-t1", tenant_id=1))
    
    # Tenant 2 Data
    db.add(ServiceLevelObjective(id=302, name="SLO T2", service="payment-service", sli_type="latency", target_percentage=99.5, tenant_id=2))
    db.add(AnalysisRun(id="run-t2", tenant_id=2, status="completed", source="payment-service"))
    db.add(Incident(id=402, title="Incident T2", status="OPEN", run_id="run-t2", tenant_id=2))
    
    db.commit()
    db.close()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def db_session_override():
    def get_test_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = get_test_db
    yield
    app.dependency_overrides.pop(get_db, None)


class TestTenantIsolation:
    """Verifies that tenant boundaries prevent data leakage across different tenant_ids."""

    def test_tenant_1_isolation(self):
        user_t1 = User(id=1, email="user1@tenant1.com", role="ADMIN", tenant_id=1)
        app.dependency_overrides[get_current_user] = lambda: user_t1

        with TestClient(app) as client:
            # Verify SLOs filtered for Tenant 1
            slo_resp = client.get("/slos")
            assert slo_resp.status_code == 200
            slos = slo_resp.json()
            assert len(slos) == 1
            assert slos[0]["id"] == 301

            # Verify Runs filtered for Tenant 1
            run_resp = client.get("/runs")
            assert run_resp.status_code == 200
            runs = run_resp.json()
            assert len(runs) == 1
            assert runs[0]["id"] == "run-t1"

            # Verify Incidents filtered for Tenant 1
            inc_resp = client.get("/incidents")
            assert inc_resp.status_code == 200
            incs = inc_resp.json()
            assert len(incs) == 1
            assert incs[0]["id"] == 401

        app.dependency_overrides.pop(get_current_user, None)

    def test_tenant_2_isolation(self):
        user_t2 = User(id=2, email="user2@tenant2.com", role="ADMIN", tenant_id=2)
        app.dependency_overrides[get_current_user] = lambda: user_t2

        with TestClient(app) as client:
            # Verify SLOs filtered for Tenant 2
            slo_resp = client.get("/slos")
            assert slo_resp.status_code == 200
            slos = slo_resp.json()
            assert len(slos) == 1
            assert slos[0]["id"] == 302

            # Verify Runs filtered for Tenant 2
            run_resp = client.get("/runs")
            assert run_resp.status_code == 200
            runs = run_resp.json()
            assert len(runs) == 1
            assert runs[0]["id"] == "run-t2"

            # Verify Incidents filtered for Tenant 2
            inc_resp = client.get("/incidents")
            assert inc_resp.status_code == 200
            incs = inc_resp.json()
            assert len(incs) == 1
            assert incs[0]["id"] == 402

        app.dependency_overrides.pop(get_current_user, None)

    def test_cross_tenant_run_details_access_blocked(self):
        user_t1 = User(id=1, email="user1@tenant1.com", role="ADMIN", tenant_id=1)
        app.dependency_overrides[get_current_user] = lambda: user_t1

        with TestClient(app) as client:
            # T1 user can access T1 run
            resp_ok = client.get("/runs/run-t1")
            assert resp_ok.status_code == 200

            # T1 user cannot access T2 run (should return 404 or 403)
            resp_blocked = client.get("/runs/run-t2")
            assert resp_blocked.status_code in (403, 404)

        app.dependency_overrides.pop(get_current_user, None)

    def test_cross_tenant_incident_access_blocked(self):
        user_t1 = User(id=1, email="user1@tenant1.com", role="ADMIN", tenant_id=1)
        app.dependency_overrides[get_current_user] = lambda: user_t1

        with TestClient(app) as client:
            # T1 user can read their own incident
            resp_ok = client.get("/incidents/401")
            assert resp_ok.status_code == 200

            # T1 user cannot read, resolve, or delete a T2 incident by ID
            assert client.get("/incidents/402").status_code in (403, 404)
            assert client.put("/incidents/402/resolve", json={"resolved": True}).status_code in (403, 404)
            assert client.delete("/incidents/402").status_code in (403, 404)

            # Confirm the T2 incident is untouched (still OPEN)
            user_t2 = User(id=2, email="user2@tenant2.com", role="ADMIN", tenant_id=2)
            app.dependency_overrides[get_current_user] = lambda: user_t2
            t2_view = client.get("/incidents/402")
            assert t2_view.status_code == 200
            assert t2_view.json()["status"] == "OPEN"

        app.dependency_overrides.pop(get_current_user, None)
