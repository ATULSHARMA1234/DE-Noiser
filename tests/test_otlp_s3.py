import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import json
import os
from datetime import datetime, timedelta, UTC

from denoiser.api.main import app, _load_settings, _save_settings
from denoiser.storage.db import Span, SessionLocal, User, init_db
from denoiser.api.auth import get_password_hash


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


def test_otlp_logs_ingestion():
    with TestClient(app) as client:
        # Construct standard OTLP JSON logs payload
        otlp_payload = {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "payment-api-prod"}}
                        ]
                    },
                    "scopeLogs": [
                        {
                            "logRecords": [
                                {
                                    "timeUnixNano": "1717500000000000000",
                                    "severityText": "ERROR",
                                    "body": {"stringValue": "Database connection timeout during payment checkout"},
                                    "attributes": [
                                        {"key": "http.status_code", "value": {"stringValue": "504"}}
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        # Send with mock header authentication
        headers = {"X-API-Key": "semanticos-ingest-key-123"}
        response = client.post("/v1/logs", json=otlp_payload, headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["ingested"] == 1


def test_otlp_traces_ingestion(db_session: Session):
    with TestClient(app) as client:
        # Construct standard OTLP JSON traces payload
        otlp_payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "auth-service-prod"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "trace1122334455667788",
                                    "spanId": "span998877",
                                    "parentSpanId": None,
                                    "name": "/auth/login",
                                    "startTimeUnixNano": "1717500000000000000",
                                    "endTimeUnixNano": "1717500005000000000",
                                    "attributes": [
                                        {"key": "http.method", "value": {"stringValue": "POST"}}
                                    ],
                                    "status": {"code": "STATUS_CODE_OK"}
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        headers = {"X-API-Key": "semanticos-ingest-key-123"}
        response = client.post("/v1/traces", json=otlp_payload, headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["spans_ingested"] == 1

        # Check DB to verify span was saved
        span = db_session.query(Span).filter(Span.span_id == "span998877").first()
        assert span is not None
        assert span.service_name == "auth-service-prod"
        assert span.operation_name == "/auth/login"
        assert span.status_code == "STATUS_CODE_OK"


def test_s3_archival_and_hydration(db_session: Session):
    with TestClient(app) as client:
        # Create an ADMIN operator to authenticate admin requests
        admin_email = "admin_archive@semanticos.io"
        pwd = "password123"
        
        db_session.query(User).filter(User.email == admin_email).delete()
        db_session.commit()
        
        from denoiser.storage.db import Tenant
        default_tenant = db_session.query(Tenant).filter(Tenant.name == "Default Workspace").first()
        
        admin_user = User(
            email=admin_email,
            hashed_password=get_password_hash(pwd),
            role="ADMIN",
            tenant_id=default_tenant.id if default_tenant else 1,
            is_active=True
        )
        db_session.add(admin_user)
        db_session.commit()

        # Login to get JWT
        login_res = client.post("/auth/login", json={"email": admin_email, "password": pwd})
        assert login_res.status_code == 200
        token = login_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Seed an old trace span (older than 7 days)
        old_time = datetime.now(UTC) - timedelta(days=10)
        old_span = Span(
            trace_id="oldtrace123",
            span_id="oldspan456",
            service_name="background-worker",
            operation_name="process_reports",
            start_time=old_time,
            end_time=old_time + timedelta(seconds=5),
            duration_ms=5000.0,
            status_code="STATUS_CODE_OK",
            attributes={},
            events=[]
        )
        db_session.add(old_span)
        db_session.commit()

        # Adjust settings to set s3_archive_days = 7
        settings = _load_settings()
        old_archive_days = settings.get("s3_archive_days")
        settings["s3_archive_days"] = 7
        settings["s3_enabled"] = False # Disable active S3 to avoid network errors, test local backup file
        _save_settings(settings)

        try:
            # Trigger Archival
            archive_res = client.post("/storage/archive/trigger", headers=headers)
            assert archive_res.status_code == 200
            assert archive_res.json()["status"] == "success"

            # Check that the span is pruned from active DB
            span_in_db = db_session.query(Span).filter(Span.span_id == "oldspan456").first()
            assert span_in_db is None

            # Verify that the archive directory has the gzipped file
            from denoiser.storage.archiver import ARCHIVE_DIR
            files = list(ARCHIVE_DIR.glob("traces_*.jsonl.gz"))
            assert len(files) > 0
            
            # Use the newest archive file name
            files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            archive_filename = files[0].name

            # Hydrate archive
            hydrate_res = client.post("/storage/archive/hydrate", json={"archive_filename": archive_filename}, headers=headers)
            assert hydrate_res.status_code == 200
            assert hydrate_res.json()["status"] == "success"
            assert hydrate_res.json()["restored"] >= 1

            # Check that the span is back in DB
            restored_span = db_session.query(Span).filter(Span.span_id == "oldspan456").first()
            assert restored_span is not None
            assert restored_span.service_name == "background-worker"

        finally:
            # Revert settings
            settings["s3_archive_days"] = old_archive_days
            _save_settings(settings)
