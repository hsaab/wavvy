"""WebSocket connection manager for broadcasting real-time events to the frontend."""

import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts messages."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info("WebSocket client connected (%d total)", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.remove(ws)
        logger.info("WebSocket client disconnected (%d remaining)", len(self._connections))

    async def broadcast(self, event_type: str, payload: Any = None) -> None:
        """Send a JSON message to every connected client."""
        # #region agent log
        if event_type.startswith("scan"):
            from pathlib import Path
            import time as _time
            _log_path = Path(__file__).parent.parent / ".cursor" / "debug-5d0c12.log"
            with open(_log_path, "a") as _f:
                _f.write(json.dumps({"sessionId":"5d0c12","location":"ws_manager.py:broadcast","message":"Broadcasting scan event","data":{"event_type":event_type,"connected_clients":len(self._connections)},"timestamp":int(_time.time()*1000),"hypothesisId":"H4"}) + "\n")
        # #endregion
        message = json.dumps({"type": event_type, "payload": payload})
        stale: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self._connections.remove(ws)


manager = ConnectionManager()
