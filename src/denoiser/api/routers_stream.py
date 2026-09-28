"""The real-time log websocket.

Split out of `api.main`, which held eleven unrelated concerns in 1,500 lines:
every parallel feature branch touched that one file and every one of them
conflicted. A pure move — no handler below is changed, and the routes are the
same paths at the same methods.
"""

from __future__ import annotations

import json
import time

from fastapi import (
    APIRouter,
    Depends,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy.orm import Session

from denoiser import runtime
from denoiser.api.auth import (
    get_current_user,
)
from denoiser.logging import get_logger
from denoiser.storage.db import get_db

logger = get_logger(__name__)

router = APIRouter(tags=["Stream"])


# ─── WEBSOCKET — Real-time log streaming ─────────────────────────────────────

@router.websocket("/stream")
async def websocket_endpoint(websocket: WebSocket, token: str | None = None, db: Session = Depends(get_db)):
    await websocket.accept()

    # Prefer the token from the Authorization header (keeps it out of URLs/proxy
    # logs); fall back to the legacy query parameter for existing clients.
    if not token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1]

    if not token:
        # The browser cannot set headers on a WebSocket handshake, and the
        # session cookie is httpOnly so page script cannot put it in the query
        # string either. The handshake does carry cookies, so read it there.
        from denoiser.api.cookies import ACCESS_COOKIE

        token = websocket.cookies.get(ACCESS_COOKIE)

    if not token:
        await websocket.close(code=4001, reason="Authentication token required")
        return

    try:
        # Keyword arguments — positionally, `token` binds to the `request`
        # parameter and every websocket handshake is rejected as invalid.
        user = get_current_user(request=None, token=token, db=db)
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    # Task 45: Redis Pub/Sub for WebSockets — scoped per tenant so a subscriber
    # only ever receives its own tenant's log stream.
    channel = f"log_stream:{user.tenant_id}"
    pubsub = runtime.redis_client().pubsub()
    await pubsub.subscribe(channel)

    try:
        # We simulate the UI format expected by the frontend
        # The frontend expects {id, level, service, message, timestamp}
        line_id = 0
        async for message in pubsub.listen():
            if message["type"] == "message":
                line_id += 1
                try:
                    payload = json.loads(message["data"])
                    # Transform raw payload to UI expected format if needed
                    # If it's just raw log, make a best guess
                    level = "INFO"
                    raw_msg = str(payload.get("message", payload.get("log", str(payload))))
                    if "ERROR" in raw_msg.upper(): level = "ERROR"
                    elif "WARN" in raw_msg.upper(): level = "WARN"
                    elif "FATAL" in raw_msg.upper() or "CRITICAL" in raw_msg.upper(): level = "ANOMALY"

                    ws_msg = {
                        "id": str(line_id).zfill(4),
                        "level": payload.get("level", level).upper(),
                        "service": payload.get("service", "api"),
                        "message": raw_msg[:200],
                        "timestamp": payload.get("timestamp", time.time()),
                    }
                    await websocket.send_json(ws_msg)
                except Exception:
                    # Fallback for plain string
                    raw_msg = message["data"]
                    level = "INFO"
                    if "ERROR" in raw_msg.upper(): level = "ERROR"
                    elif "WARN" in raw_msg.upper(): level = "WARN"

                    await websocket.send_json({
                        "id": str(line_id).zfill(4),
                        "level": level,
                        "service": "unknown",
                        "message": raw_msg[:200],
                        "timestamp": time.time(),
                    })
    except WebSocketDisconnect:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await pubsub.unsubscribe(channel)
        await pubsub.close()
