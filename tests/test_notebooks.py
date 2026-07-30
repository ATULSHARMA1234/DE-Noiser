"""
Integration tests for the /notebooks CRUD endpoints.

Covers the round trip and the tenant-isolation guards, which are the part
worth protecting: notebooks hold investigation queries, so a leak across
tenants leaks what another customer was investigating.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """Test client authenticated as an ADMIN with no tenant scoping."""
    from denoiser.api.auth import get_current_user
    from denoiser.api.main import app, verify_ingest_auth
    from denoiser.storage.db import User

    mock_user = User(id=1, email="admin@semanticos.io", role="ADMIN")
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[verify_ingest_auth] = lambda: None

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def notebook(client):
    """Create a notebook and clean it up afterwards."""
    response = client.post(
        "/notebooks",
        json={"title": "Checkout latency investigation", "cells": [{"id": "c1", "type": "markdown", "content": "# Notes"}]},
    )
    assert response.status_code == 200
    nb = response.json()
    yield nb
    client.delete(f"/notebooks/{nb['id']}")


class TestNotebookCRUD:
    def test_create_returns_persisted_notebook(self, notebook):
        assert notebook["id"] > 0
        assert notebook["title"] == "Checkout latency investigation"
        assert notebook["cells"][0]["content"] == "# Notes"

    def test_create_applies_default_title(self, client):
        response = client.post("/notebooks", json={})
        assert response.status_code == 200
        nb = response.json()
        assert nb["title"] == "Untitled Notebook"
        assert nb["cells"] == []
        client.delete(f"/notebooks/{nb['id']}")

    def test_get_returns_the_notebook(self, client, notebook):
        response = client.get(f"/notebooks/{notebook['id']}")
        assert response.status_code == 200
        assert response.json()["title"] == notebook["title"]

    def test_list_includes_the_notebook(self, client, notebook):
        response = client.get("/notebooks")
        assert response.status_code == 200
        assert notebook["id"] in [nb["id"] for nb in response.json()]

    def test_update_persists_title_and_cells(self, client, notebook):
        response = client.put(
            f"/notebooks/{notebook['id']}",
            json={"title": "Renamed", "cells": [{"id": "c1", "type": "query", "content": "severity:ERROR"}]},
        )
        assert response.status_code == 200

        reloaded = client.get(f"/notebooks/{notebook['id']}").json()
        assert reloaded["title"] == "Renamed"
        assert reloaded["cells"][0]["content"] == "severity:ERROR"

    def test_update_leaves_omitted_fields_alone(self, client, notebook):
        client.put(f"/notebooks/{notebook['id']}", json={"title": "Only the title"})

        reloaded = client.get(f"/notebooks/{notebook['id']}").json()
        assert reloaded["title"] == "Only the title"
        assert reloaded["cells"] == notebook["cells"]

    def test_delete_removes_the_notebook(self, client):
        nb = client.post("/notebooks", json={"title": "Disposable"}).json()

        assert client.delete(f"/notebooks/{nb['id']}").status_code == 200
        assert client.get(f"/notebooks/{nb['id']}").status_code == 404


class TestNotebookMissing:
    """Unknown ids are 404, never a 500 from dereferencing None."""

    def test_get_unknown_is_404(self, client):
        assert client.get("/notebooks/99999999").status_code == 404

    def test_update_unknown_is_404(self, client):
        assert client.put("/notebooks/99999999", json={"title": "x"}).status_code == 404

    def test_delete_unknown_is_404(self, client):
        assert client.delete("/notebooks/99999999").status_code == 404


class TestNotebookTenantIsolation:
    def test_other_tenants_notebook_does_not_exist(self, client, notebook):
        """A notebook owned by tenant 1 is not readable, writable or deletable by tenant 2.

        404 rather than 403: a 403 confirms the id is real, which is enough to
        count another organisation's notebooks by walking the id space. The two
        answers were both in use before `TenantScope` — 29 routes returned 404
        and 14 returned 403 for the same class of access — and this is the one
        that gives an attacker nothing.
        """
        from denoiser.storage.db import Notebook as DBNotebook
        from denoiser.storage.db import SessionLocal

        db = SessionLocal()
        try:
            db.query(DBNotebook).filter(DBNotebook.id == notebook["id"]).update({"tenant_id": 1})
            db.commit()
        finally:
            db.close()

        from denoiser.api.auth import get_current_user
        from denoiser.api.main import app
        from denoiser.storage.db import User

        app.dependency_overrides[get_current_user] = lambda: User(id=2, email="other@tenant.io", role="ADMIN", tenant_id=2)
        try:
            assert client.get(f"/notebooks/{notebook['id']}").status_code == 404
            assert client.put(f"/notebooks/{notebook['id']}", json={"title": "hijacked"}).status_code == 404
            assert client.delete(f"/notebooks/{notebook['id']}").status_code == 404

            # It must not show up in their list either.
            assert notebook["id"] not in [nb["id"] for nb in client.get("/notebooks").json()]
        finally:
            app.dependency_overrides[get_current_user] = lambda: User(id=1, email="admin@semanticos.io", role="ADMIN")

    def test_unowned_notebooks_are_not_shared_across_organisations(self, client, notebook):
        """A notebook with tenant_id NULL belongs to nobody, not to everybody.

        These rows predate tenant scoping, and three routes used to read NULL as
        "shared" so an upgraded deployment would not appear to lose data. On a
        deployment serving one customer that is harmless; on one serving two it
        is a channel between them, and for notebooks a writable one. Migration
        f7a2c04b91de adopts the rows into the first organisation instead, so
        ownership is a plain equality and nothing is shared by accident.
        """
        from denoiser.api.auth import get_current_user
        from denoiser.api.main import app
        from denoiser.storage.db import User

        app.dependency_overrides[get_current_user] = lambda: User(id=3, email="scoped@tenant.io", role="ADMIN", tenant_id=7)
        try:
            assert client.get(f"/notebooks/{notebook['id']}").status_code == 404
            assert notebook["id"] not in [nb["id"] for nb in client.get("/notebooks").json()]
        finally:
            app.dependency_overrides[get_current_user] = lambda: User(id=1, email="admin@semanticos.io", role="ADMIN")

    def test_an_unassigned_admin_still_sees_unowned_notebooks(self, client, notebook):
        """The unassigned bucket is a tenant like any other, not a black hole.

        `column == None` compiles to `= NULL` and matches nothing, so a naive
        equality would make these rows unreachable by anyone at all — which is
        how the same fix broke user administration when it was first written.
        """
        assert client.get(f"/notebooks/{notebook['id']}").status_code == 200
