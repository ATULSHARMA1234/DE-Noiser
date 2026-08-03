"""API versioning: every endpoint reachable under ``/v1``.

The platform served 148 routes at the root — ``/users``, ``/incidents``,
``/runs`` — with no version segment anywhere. That is fine until the first
integrated customer, at which point there is no way to change a response shape
without breaking every one of them at once, and no artifact to point at when
procurement asks what the API stability commitment is.

Adding a prefix to 148 decorators would be a large, mechanical diff with a
real chance of a typo in a path nobody notices until a customer hits it. This
does it in one place instead: a request to ``/v1/<path>`` is rewritten to
``/<path>`` before routing, so every existing endpoint gains a versioned
address and every existing client keeps working, forever, at the unversioned
one.

**The OTLP exception.** ``/v1/logs`` and ``/v1/traces`` are not our paths — the
OpenTelemetry specification puts them there, and the router already mounts them
under ``/v1``. Rewriting those to ``/logs`` and ``/traces`` would break OTLP
ingestion outright. So a path that already matches a registered route is never
rewritten; the rewrite only ever rescues a request that would otherwise 404.
That rule needs no exception list and cannot go stale as routes are added.

**What this is not.** It is a compatibility layer, not a second implementation.
``/v1/users`` and ``/users`` are the same handler and will always behave
identically. When a genuinely breaking ``/v2`` is needed, it gets real routes
of its own; this middleware keeps ``/v1`` pinned to what shipped.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

#: The version every current route answers to.
CURRENT_VERSION = "v1"

_PREFIX = f"/{CURRENT_VERSION}"


class VersionPrefixMiddleware(BaseHTTPMiddleware):
    """Routes ``/v1/<path>`` to ``<path>`` when nothing is registered at ``/v1/<path>``."""

    def __init__(self, app, fastapi_app: FastAPI) -> None:
        """`app` is the next ASGI app in the chain, `fastapi_app` the application.

        They are not the same object, and the distinction is the whole
        correctness of this middleware: `add_middleware` hands each layer the
        *wrapped* app beneath it, which has no `.routes`. Reading the route
        table off that gives an empty set, every path looks unregistered, and
        the rewrite silently never fires — a bug that presents as "the feature
        does nothing" rather than as an error.
        """
        super().__init__(app)
        self._fastapi_app = fastapi_app
        self._routes_snapshot: int | None = None
        self._registered: set[str] = set()

    def _registered_paths(self) -> set[str]:
        """Literal path templates the application serves.

        Cached against the route count so a router included after startup — a
        test that builds its own app, for instance — is picked up rather than
        silently missing from the exclusion set.
        """
        routes = getattr(self._fastapi_app, "routes", [])
        if self._routes_snapshot != len(routes):
            self._registered = {
                str(path) for route in routes if (path := getattr(route, "path", None))
            }
            self._routes_snapshot = len(routes)
        return self._registered

    def _matches_a_route(self, path: str) -> bool:
        """True when some registered route could serve this path.

        Compared segment-count-first and then segment by segment, treating any
        ``{placeholder}`` as a wildcard. Starlette's own regexes would be more
        precise, but this only decides *whether to rewrite*: a false positive
        leaves the path alone and the request 404s exactly as it does today,
        and a false negative rewrites a path that had no handler either way.
        """
        for template in self._registered_paths():
            if template == path:
                return True
            template_parts = template.strip("/").split("/")
            path_parts = path.strip("/").split("/")
            if len(template_parts) != len(path_parts):
                continue
            if all(
                t.startswith("{") or t == p
                for t, p in zip(template_parts, path_parts, strict=True)
            ):
                return True
        return False

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.scope.get("path", "")

        versioned = path == _PREFIX or path.startswith(f"{_PREFIX}/")
        # Only rewrite what would otherwise 404: a path already served at this
        # exact address (OTLP's /v1/logs, /v1/traces) is left alone.
        if versioned and not self._matches_a_route(path):
            stripped = path[len(_PREFIX):] or "/"
            if self._matches_a_route(stripped):
                request.scope["path"] = stripped
                # raw_path is what Starlette's router actually matches on when
                # present; leaving it stale would make the rewrite a no-op that
                # only appears to work.
                if "raw_path" in request.scope:
                    request.scope["raw_path"] = stripped.encode("utf-8")

        response = await call_next(request)
        # Lets a client confirm which contract answered it, and gives support a
        # way to tell "called /v1" from "called the unversioned alias".
        response.headers["X-API-Version"] = CURRENT_VERSION
        return response
