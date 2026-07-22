import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from denoiser.api.auth import User, require_role
from denoiser.storage.db import Notebook as DBNotebook
from denoiser.storage.db import get_db

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

    class Config:
        from_attributes = True


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("", response_model=list[NotebookSchema])
def list_notebooks(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))
):
    query = db.query(DBNotebook)
    if current_user.tenant_id:
        query = query.filter(
            (DBNotebook.tenant_id == current_user.tenant_id) |
            (DBNotebook.tenant_id.is_(None))
        )
    return query.order_by(DBNotebook.updated_at.desc()).all()


@router.post("", response_model=NotebookSchema)
def create_notebook(
    payload: NotebookCreateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))
):
    notebook = DBNotebook(
        tenant_id=current_user.tenant_id,
        title=payload.title,
        cells=payload.cells,
    )
    db.add(notebook)
    db.commit()
    db.refresh(notebook)
    return notebook


@router.get("/{notebook_id}", response_model=NotebookSchema)
def get_notebook(
    notebook_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))
):
    nb = db.query(DBNotebook).filter(DBNotebook.id == notebook_id).first()
    if not nb:
        raise HTTPException(status_code=404, detail="Notebook not found")
    if current_user.tenant_id and nb.tenant_id and nb.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return nb


@router.put("/{notebook_id}", response_model=NotebookSchema)
def update_notebook(
    notebook_id: int,
    payload: NotebookUpdateSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"]))
):
    nb = db.query(DBNotebook).filter(DBNotebook.id == notebook_id).first()
    if not nb:
        raise HTTPException(status_code=404, detail="Notebook not found")
    if current_user.tenant_id and nb.tenant_id and nb.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    if payload.title is not None:
        nb.title = payload.title
    if payload.cells is not None:
        nb.cells = payload.cells
    nb.updated_at = datetime.datetime.utcnow()

    db.commit()
    db.refresh(nb)
    return nb


@router.delete("/{notebook_id}")
def delete_notebook(
    notebook_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["ADMIN"]))
):
    nb = db.query(DBNotebook).filter(DBNotebook.id == notebook_id).first()
    if not nb:
        raise HTTPException(status_code=404, detail="Notebook not found")
    if current_user.tenant_id and nb.tenant_id and nb.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    db.delete(nb)
    db.commit()
    return {"status": "deleted"}
