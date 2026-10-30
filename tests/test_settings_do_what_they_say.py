"""Every control on the Settings page does what it says, or is not there.

Four settings were stored, shown and read by nothing: `auto_analyze`,
`confidence_threshold`, `sampling_threshold` and `llm_model` (a dropdown of
models the deployment was not running). `auto_analyze` is wired to upload now;
`llm_model` is replaced by the narrator the deployment really uses; the other
two had no defined behaviour to wire and are gone.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from denoiser.api.platform_settings import (
    DEFAULT_SETTINGS,
    PLATFORM_SETTING_KEYS,
    TENANT_SETTING_KEYS,
    save_tenant_settings,
)
from denoiser.storage.db import SessionLocal, TenantSetting, User, init_db

TENANT = 1


@pytest.fixture
def client():
    from denoiser.api.auth import get_current_user
    from denoiser.api.main import app

    init_db()
    app.dependency_overrides[get_current_user] = lambda: User(id=1, email="a@x.io", role="ADMIN", tenant_id=TENANT)
    db = SessionLocal()
    before = db.query(TenantSetting).filter(TenantSetting.tenant_id == TENANT).first()
    saved = dict(before.data) if before else None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        row = db.query(TenantSetting).filter(TenantSetting.tenant_id == TENANT).first()
        if saved is None and row is not None:
            db.delete(row)
        elif row is not None:
            row.data = saved
        db.commit()
        db.close()


@pytest.fixture
def upload(client):
    from denoiser.api.sources import tenant_dir

    names = []

    def send():
        name = f"auto-{uuid.uuid4().hex[:8]}.log"
        names.append(name)
        return client.post("/sources/upload", files={"file": (name, b"ERROR boom\n", "text/plain")}).json()

    yield send
    for name in names:
        (tenant_dir(TENANT) / name).unlink(missing_ok=True)


class _Queued:
    id = "task-123"


class TestAutoAnalyze:
    def test_off_means_no_analysis(self, upload, monkeypatch):
        from denoiser.workers.analysis_worker import run_analysis_task

        monkeypatch.setattr(run_analysis_task, "delay", lambda *_: pytest.fail("must not queue"))
        save_tenant_settings(TENANT, {"auto_analyze": False})
        assert upload()["analysis"] is None

    def test_on_queues_an_analysis_of_the_upload_for_its_tenant(self, upload, monkeypatch):
        from denoiser.workers.analysis_worker import run_analysis_task

        sent = []
        monkeypatch.setattr(run_analysis_task, "delay", lambda payload: sent.append(payload) or _Queued())
        save_tenant_settings(TENANT, {"auto_analyze": True})

        res = upload()
        assert res["analysis"] == {"status": "queued", "task_id": "task-123"}
        assert sent[0]["source"] == res["name"]
        assert sent[0]["tenant_id"] == TENANT

    def test_an_unavailable_queue_does_not_fail_the_upload(self, upload, monkeypatch):
        from denoiser.workers.analysis_worker import run_analysis_task

        def down(_payload):
            raise ConnectionError("broker down")

        monkeypatch.setattr(run_analysis_task, "delay", down)
        save_tenant_settings(TENANT, {"auto_analyze": True})

        res = upload()
        assert res["status"] == "uploaded"
        assert res["analysis"]["status"] == "not_queued"


class TestLlmStatus:
    def test_unconfigured_says_so(self, client, monkeypatch):
        from denoiser.config import settings as intelligence

        monkeypatch.setattr(intelligence, "llm_api_key", None)
        monkeypatch.setattr(intelligence, "llm_base_url", None)
        assert client.get("/settings").json()["llm"] == {"configured": False, "model": None, "location": None}

    @pytest.mark.parametrize("url,location", [
        ("http://localhost:11434/v1", "local"),
        ("http://host.docker.internal:11434/v1", "local"),
        ("https://api.groq.com/openai/v1", "external"),
    ])
    def test_reports_the_model_actually_configured(self, client, monkeypatch, url, location):
        from denoiser.config import settings as intelligence

        monkeypatch.setattr(intelligence, "llm_base_url", url)
        monkeypatch.setattr(intelligence, "llm_model", "llama3.1")
        assert client.get("/settings").json()["llm"] == {"configured": True, "model": "llama3.1", "location": location}


def test_controls_with_no_behaviour_are_gone():
    every_key = set(DEFAULT_SETTINGS) | set(TENANT_SETTING_KEYS) | set(PLATFORM_SETTING_KEYS)
    assert not every_key & {"confidence_threshold", "sampling_threshold", "llm_model"}
