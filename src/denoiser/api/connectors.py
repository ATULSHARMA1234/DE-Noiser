"""Kubernetes, AWS and Docker connector routes.

Thin by construction: real-versus-simulated is a choice of adapter in
`denoiser.integrations.connectors`, not a conditional in a handler, so what is
left here is parameter binding and the 502 an unreachable backend earns.
"""

from fastapi import APIRouter, Depends, Form, HTTPException

from denoiser.api import sources as source_registry
from denoiser.api.auth import require_role
from denoiser.integrations import connectors
from denoiser.storage.db import User

router = APIRouter(tags=["connectors"])

# ─── CONNECTORS — Kubernetes, AWS, and Docker ───────────────────────────────
#
# The bodies of these six routes were about two hundred lines of try-real /
# except / fabricate-sandbox-data, with the fake pod names and fake log lines as
# literals inside the handler. Real and simulated are adapters now
# (`denoiser.integrations.connectors`), which is what makes the *real* fetch
# reachable from a test — previously the only branch a test could take was the
# simulated one.


def _connector_response(provider: str, key: str):
    try:
        return connectors.discover(provider).as_response(key)
    except connectors.ConnectorUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


def _connector_fetch(provider: str, current_user: User, **params):
    try:
        fetched, filename = connectors.fetch(provider, **params)
    except connectors.ConnectorUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    count = connectors.write_source(
        fetched.lines, filename, source_registry.tenant_dir(current_user.tenant_id)
    )
    payload = {
        "status": "simulated" if fetched.simulated else "success",
        "source": filename,
        "lines": count,
    }
    if fetched.message:
        payload["message"] = fetched.message
    return payload


@router.get("/connectors/k8s/pods")
def list_k8s_pods(current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """Discover K8s namespaces and pods via the real Kubernetes API."""
    return _connector_response("k8s", "pods")


@router.post("/connectors/k8s/fetch")
async def fetch_k8s_logs(
    namespace: str = Form(...),
    pod_name: str = Form(...),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"])),
):
    """Fetch logs from a K8s pod and save them as one of the caller's sources."""
    return _connector_fetch("k8s", current_user, namespace=namespace, pod_name=pod_name)


@router.get("/connectors/aws/groups")
def list_aws_groups(current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """Discover AWS CloudWatch log groups."""
    return _connector_response("aws", "groups")


@router.post("/connectors/aws/fetch")
async def fetch_aws_logs(
    log_group: str = Form(...),
    log_stream: str | None = Form(None),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"])),
):
    """Fetch logs from CloudWatch and save them as one of the caller's sources."""
    return _connector_fetch(
        "aws", current_user, log_group=log_group, log_stream=log_stream
    )


@router.get("/connectors/docker/containers")
def list_docker_containers(current_user: User = Depends(require_role(["VIEWER", "ANALYST", "ADMIN"]))):
    """Discover running Docker containers on the host."""
    return _connector_response("docker", "containers")


@router.post("/connectors/docker/fetch")
async def fetch_docker_logs(
    container_name: str = Form(...),
    current_user: User = Depends(require_role(["ANALYST", "ADMIN"])),
):
    """Fetch a container's logs and save them as one of the caller's sources."""
    return _connector_fetch("docker", current_user, container_name=container_name)



