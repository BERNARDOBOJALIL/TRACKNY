from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse


class APIServer:
    def __init__(
        self,
        frontend_path: Path,
        zone_names: list[str],
        get_status_payload: Callable[[], dict[str, int]],
        get_today_totals: Callable[[], dict[str, float]],
        is_persistence_enabled: Callable[[], bool],
    ) -> None:
        self.frontend_path = frontend_path
        self.zone_names = zone_names
        self.get_status_payload = get_status_payload
        self.get_today_totals = get_today_totals
        self.is_persistence_enabled = is_persistence_enabled

        self.app = FastAPI()
        self._clientes_conectados: set[WebSocket] = set()
        self._server_loop: Optional[asyncio.AbstractEventLoop] = None

        self._register_routes()

    def _register_routes(self) -> None:
        @self.app.get("/", response_class=HTMLResponse)
        async def home() -> HTMLResponse:
            if self.frontend_path.exists():
                return HTMLResponse(self.frontend_path.read_text(encoding="utf-8"))
            return HTMLResponse("<html><body><h1>Frontend no encontrado</h1></body></html>")

        @self.app.on_event("startup")
        async def configurar_event_loop() -> None:
            self._server_loop = asyncio.get_running_loop()

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket) -> None:
            await websocket.accept()
            self._clientes_conectados.add(websocket)
            print("Cliente conectado")
            await websocket.send_json(self.get_status_payload())
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                self._clientes_conectados.discard(websocket)
                print("Cliente desconectado")

        @self.app.get("/api/ocupacion/hoy")
        async def ocupacion_hoy() -> dict:
            totals = self.get_today_totals()
            return {
                "persistencia_activa": self.is_persistence_enabled(),
                "fecha_utc": datetime.now(timezone.utc).date().isoformat(),
                **{f"{z}_segundos": round(float(totals.get(z, 0.0)), 2) for z in self.zone_names},
            }

    async def _broadcast(self, data: dict[str, int]) -> None:
        for cliente in list(self._clientes_conectados):
            try:
                await cliente.send_json(data)
            except Exception:
                self._clientes_conectados.discard(cliente)

    def broadcast_status(self, payload: Optional[dict[str, int]] = None) -> None:
        if self._server_loop is None or not self._server_loop.is_running():
            return
        data = payload or self.get_status_payload()
        asyncio.run_coroutine_threadsafe(self._broadcast(data), self._server_loop)
