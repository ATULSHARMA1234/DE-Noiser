"""
Tenant-scoped resolution of log source files.

Two separate problems are solved here, and they are easy to conflate.

**Confinement.** ``/analyze`` takes a source path as an ordinary string and
hands it to the log reader, which opens whatever it is given. Unconstrained,
that is an arbitrary-file-read primitive: any analyst account could ask the
platform to "analyse" ``/etc/passwd`` or the deployment's own ``.env`` and read
the results back out of the run. Every path now has to resolve inside the data
root, checked after symlink resolution so a symlink planted in the upload
directory cannot point out of it.

**Ownership.** Even confined to the data root, a single flat directory is a
shared namespace: one tenant could list, analyse and delete another tenant's
uploaded logs. Files now live under ``data/tenants/{tenant_id}/`` and every
lookup is scoped to the caller's tenant.

Shared sample data that ships with the platform stays readable by everyone —
it belongs to no tenant and exists so a new workspace has something to look at.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Root for all log source files. Honours ``SEMANTICOS_DATA_DIR`` so a
#: deployment can mount it elsewhere, and so the test suite can redirect it
#: away from a developer's real data directory.
DATA_DIR = Path(os.getenv("SEMANTICOS_DATA_DIR", "data"))

#: Files any tenant may read: the demo/sample logs shipped with the platform.
#: They contain no customer data, and hiding them would leave a new workspace
#: with nothing to analyse.
SHARED_SOURCE_DIR = DATA_DIR

#: Names never exposed as sources regardless of location.
EXCLUDED_NAMES = {"settings.json"}
EXCLUDED_SUFFIXES = {".db", ".sqlite", ".sqlite3"}

SOURCE_GLOBS = ("*.log", "*.txt", "*.json", "*.jsonl", "*.ndjson")


class SourceNotAllowed(ValueError):
    """Raised when a requested path is outside what the caller may read."""


def tenant_dir(tenant_id: int | str | None) -> Path:
    """The upload directory for a tenant, created on demand."""
    safe = str(tenant_id if tenant_id is not None else "unassigned")
    # The tenant id comes from a verified JWT, but it reaches a filesystem path
    # here, so it is constrained to characters that cannot traverse.
    safe = "".join(ch for ch in safe if ch.isalnum() or ch in "-_")
    if not safe:
        safe = "unassigned"
    path = DATA_DIR / "tenants" / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_excluded(path: Path) -> bool:
    return path.name in EXCLUDED_NAMES or path.suffix.lower() in EXCLUDED_SUFFIXES


def _within(child: Path, parent: Path) -> bool:
    """True when ``child`` resolves inside ``parent``.

    Both sides are fully resolved first, so a symlink inside the data root
    pointing at /etc cannot pass this check.
    """
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def resolve_source(raw: str, tenant_id: int | str | None) -> Path:
    """Resolve a caller-supplied source string to a readable file.

    Accepts a bare filename, a path relative to the data root, or an absolute
    path — but only ever returns one that lives inside this tenant's directory
    or the shared sample set.

    Raises
    ------
    SourceNotAllowed
        If the path escapes the data root, belongs to another tenant, or does
        not exist. The message never distinguishes "outside the root" from
        "belongs to someone else", so it cannot be used to probe for the
        existence of another tenant's files.
    """
    if not raw or not str(raw).strip():
        raise SourceNotAllowed("A source path is required")

    candidate = str(raw).strip()
    generic_error = SourceNotAllowed(
        f"Source '{os.path.basename(candidate)}' was not found in this workspace"
    )

    tenant_root = tenant_dir(tenant_id)
    data_root = DATA_DIR.resolve()

    # Try, in order: this tenant's directory, then the shared data root.
    attempts: list[Path] = []
    name = os.path.basename(candidate)
    if name:
        attempts.append(tenant_root / name)

    as_path = Path(candidate)
    if as_path.is_absolute():
        attempts.append(as_path)
    else:
        attempts.append(DATA_DIR / candidate)
        attempts.append(as_path)

    for attempt in attempts:
        try:
            resolved = attempt.resolve()
        except OSError:
            continue

        if not resolved.is_file() or _is_excluded(resolved):
            continue

        # Must be inside the data root at all...
        if not _within(resolved, data_root):
            continue

        # ...and then either this tenant's own directory, or a shared file that
        # is not inside *another* tenant's directory.
        if _within(resolved, tenant_root):
            return resolved

        tenants_root = (DATA_DIR / "tenants").resolve()
        if _within(resolved, tenants_root):
            continue  # someone else's upload

        return resolved

    raise generic_error


def list_sources(tenant_id: int | str | None) -> list[Path]:
    """Every source file this tenant may analyse: their own, plus shared samples."""
    seen: dict[str, Path] = {}

    for pattern in SOURCE_GLOBS:
        for f in tenant_dir(tenant_id).glob(pattern):
            if f.is_file() and not _is_excluded(f):
                seen[f.name] = f

    tenants_root = (DATA_DIR / "tenants").resolve()
    for pattern in SOURCE_GLOBS:
        for f in SHARED_SOURCE_DIR.glob(pattern):
            if not f.is_file() or _is_excluded(f):
                continue
            if _within(f, tenants_root):
                continue
            seen.setdefault(f.name, f)

    return list(seen.values())
