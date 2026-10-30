import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.api.pagination import ResourceId
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.storage.db import Notebook as DBNotebook
from denoiser.storage.db import get_db
from denoiser.utils.time import utcnow

router = APIRouter(prefix="/notebooks", tags=["notebooks"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class NotebookCreateSchema(BaseModel):
    title: str = "Untitled Notebook"
    cells: list = []

class NotebookUpdateSchema(BaseModel):
    title: str | None = None
    cells: list | None = None

class NotebookSchema(BaseModel):
    id: int
    tenant_id: int | None = None
    title: str
    cells: list
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = ConfigDict(from_attributes=True)


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("", response_model=list[NotebookSchema])
def list_notebooks(
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))
):
    return scope.query(DBNotebook).order_by(DBNotebook.updated_at.desc()).all()


@router.post("", response_model=NotebookSchema)
def create_notebook(
    payload: NotebookCreateSchema,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))
):
    notebook = DBNotebook(
        title=payload.title,
        cells=payload.cells,
    )
    scope.add(notebook)
    db.commit()
    db.refresh(notebook)
    return notebook


@router.get("/{notebook_id}", response_model=NotebookSchema)
def get_notebook(
    notebook_id: ResourceId,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))
):
    nb = scope.get_or_404(DBNotebook, notebook_id, "Notebook not found")
    return nb


@router.put("/{notebook_id}", response_model=NotebookSchema)
def update_notebook(
    notebook_id: ResourceId,
    payload: NotebookUpdateSchema,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))
):
    nb = scope.get_or_404(DBNotebook, notebook_id, "Notebook not found")

    if payload.title is not None:
        nb.title = payload.title
    if payload.cells is not None:
        nb.cells = payload.cells
    nb.updated_at = utcnow()

    db.commit()
    db.refresh(nb)
    return nb


@router.delete("/{notebook_id}")
def delete_notebook(
    notebook_id: ResourceId,
    db: Session = Depends(get_db),
    scope: TenantScope = Depends(tenant_scope),
    current_user: User = Depends(require_role(["ADMIN"]))
):
    nb = scope.get_or_404(DBNotebook, notebook_id, "Notebook not found")

    db.delete(nb)
    db.commit()
    return {"status": "deleted"}
