"""Analysis run history, comparison and deletion."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from denoiser.analysis.drift import ClusterSnapshot, DriftDetector
from denoiser.api.abac import require_abac
from denoiser.api.auth import require_role
from denoiser.api.scope import TenantScope, tenant_scope
from denoiser.storage.db import AnalysisRun, Incident, User, get_db
from denoiser.utils.time import iso_utc

router = APIRouter(tags=["runs"])

# ─── RUNS — History ──────────────────────────────────────────────────────────

@router.get("/analysis/runs")
@router.get("/runs")
def list_analysis_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    scope: TenantScope = Depends(tenant_scope),
):
    """List recent analysis runs (paginated)."""
    runs = (
        scope.query(AnalysisRun)
        .order_by(AnalysisRun.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [_run_to_dict(r, db) for r in runs]


@router.get("/analysis/compare")
def compare_runs(run_a: str, run_b: str, db: Session = Depends(get_db), current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"])), scope: TenantScope = Depends(tenant_scope)):
    """Compare two analysis runs."""
    db_run_a = scope.query(AnalysisRun).filter(AnalysisRun.id == run_a).first()
    db_run_b = scope.query(AnalysisRun).filter(AnalysisRun.id == run_b).first()

    if not db_run_a or not db_run_b:
        raise HTTPException(status_code=404, detail="One or both runs not found")

    snap_a_data = db_run_a.clusters_snapshot or []
    snap_b_data = db_run_b.clusters_snapshot or []

    clusters_a = [
        ClusterSnapshot(
            cluster_id=d.get("cluster_id") or d.get("id") or 0,
            template=d.get("template") or d.get("representative_template") or "",
            size=d.get("size") or 0,
            anomaly_score=d.get("anomaly_score") or 0.0,
            priority=d.get("priority") or "P3",
            composite_severity_score=d.get("composite_severity_score") or 0.0,
            keyword_flag=d.get("keyword_flag") or False,
            summary=d.get("summary") or ""
        ) for d in snap_a_data
    ]
    clusters_b = [
        ClusterSnapshot(
            cluster_id=d.get("cluster_id") or d.get("id") or 0,
            template=d.get("template") or d.get("representative_template") or "",
            size=d.get("size") or 0,
            anomaly_score=d.get("anomaly_score") or 0.0,
            priority=d.get("priority") or "P3",
            composite_severity_score=d.get("composite_severity_score") or 0.0,
            keyword_flag=d.get("keyword_flag") or False,
            summary=d.get("summary") or ""
        ) for d in snap_b_data
    ]

    detector = DriftDetector()
    report = detector.compare(run_a, clusters_a, run_b, clusters_b)

    return report.to_dict()


@router.get("/analysis/runs/{run_id}")
@router.get("/runs/{run_id}")
def get_run_details(run_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_abac("read", "run")), scope: TenantScope = Depends(tenant_scope)):
    """Retrieve full analysis run details by ID."""
    run = scope.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_to_dict(run, db)


@router.delete("/runs/{run_id}")
def delete_run(run_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_role(["ADMIN"])), scope: TenantScope = Depends(tenant_scope)):
    """Delete an analysis run by ID."""
    run = scope.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    db.delete(run)
    db.commit()
    return {"status": "deleted", "id": run_id}


def _run_to_dict(run: AnalysisRun, db: Session | None = None) -> dict:
    data = {
        "id": run.id,
        "source": run.source,
        "status": run.status,
        "raw_lines": run.raw_lines,
        "cluster_count": run.cluster_count,
        "reduction_ratio": run.reduction_ratio,
        "duration_sec": run.duration_sec,
        # Stamped with an offset: run.created_at is naive UTC, and an ISO string
        # without one is parsed as *local* time by the browser, which made a run
        # from a minute ago render hours out.
        "created_at": iso_utc(run.created_at),
        "clusters_snapshot": run.clusters_snapshot,
    }
    if db:
        incident = db.query(Incident).filter(Incident.run_id == run.id).first()
        if incident:
            data["intelligence"] = {
                "failure_domain": incident.title,
                "incident_summary": incident.summary,
                "root_cause_hints": incident.remediation_hints,
            }
    return data


