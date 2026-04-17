from __future__ import annotations

import asyncio
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from .config import settings


class CloudStateStore:
    def __init__(self, zone_names: list[str]) -> None:
        self._lock = threading.Lock()
        self.zone_names = list(zone_names)
        self.zone_status: dict[str, int] = {z: 0 for z in zone_names}
        self.zone_totals: dict[str, float] = {z: 0.0 for z in zone_names}
        self.last_state_ts: Optional[float] = None
        self.last_frame_ts: Optional[float] = None
        self.latest_jpeg: Optional[bytes] = None

    def update_state(self, states: dict[str, int], totals_seconds: Optional[dict[str, float]] = None) -> None:
        with self._lock:
            for zone, value in states.items():
                if zone not in self.zone_names:
                    self.zone_names.append(zone)
                self.zone_status[zone] = 1 if int(value) == 1 else 0

            if totals_seconds:
                for zone, seconds in totals_seconds.items():
                    if zone not in self.zone_names:
                        self.zone_names.append(zone)
                    self.zone_totals[zone] = max(0.0, float(seconds))

            self.last_state_ts = time.time()

    def status_payload(self) -> dict[str, int]:
        with self._lock:
            return {z: int(self.zone_status.get(z, 0)) for z in self.zone_names}

    def totals_payload(self) -> dict[str, float]:
        with self._lock:
            return {z: float(self.zone_totals.get(z, 0.0)) for z in self.zone_names}

    def set_frame(self, jpeg_bytes: bytes) -> None:
        with self._lock:
            self.latest_jpeg = jpeg_bytes
            self.last_frame_ts = time.time()

    def get_frame(self) -> Optional[bytes]:
        with self._lock:
            return self.latest_jpeg

    def get_frame_snapshot(self) -> tuple[Optional[bytes], Optional[float]]:
        with self._lock:
            return self.latest_jpeg, self.last_frame_ts


class CloudAPIServer:
    def __init__(self, frontend_path: Path, zone_names: list[str]) -> None:
        self.frontend_path = frontend_path
        self.store = CloudStateStore(zone_names)
        self.app = FastAPI(title="Trackny Cloud API")
        self._clientes_conectados: set[WebSocket] = set()
        self._server_loop: Optional[asyncio.AbstractEventLoop] = None
        self._register_routes()

    def _check_internal_auth(self, request: Request) -> None:
        token = settings.internal_api_token
        if not token and not settings.allow_insecure_internal:
            raise HTTPException(status_code=503, detail="Configura INTERNAL_API_TOKEN o habilita ALLOW_INSECURE_INTERNAL")

        if settings.allow_insecure_internal:
            return

        provided = request.headers.get("x-internal-token", "").strip()
        if not provided:
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                provided = auth_header.split(" ", 1)[1].strip()

        if provided != token:
            raise HTTPException(status_code=401, detail="Token interno invalido")

    async def _broadcast(self, data: dict[str, int]) -> None:
        for cliente in list(self._clientes_conectados):
            try:
                await cliente.send_json(data)
            except Exception:
                self._clientes_conectados.discard(cliente)

    def broadcast_status(self) -> None:
        if self._server_loop is None or not self._server_loop.is_running():
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(self.store.status_payload()), self._server_loop)

    def _register_routes(self) -> None:
        @self.app.get("/", response_class=HTMLResponse)
        async def home() -> HTMLResponse:
            if self.frontend_path.exists():
                return HTMLResponse(self.frontend_path.read_text(encoding="utf-8"))
            return HTMLResponse("<html><body><h1>Frontend no encontrado</h1></body></html>")

        @self.app.on_event("startup")
        async def on_startup() -> None:
            self._server_loop = asyncio.get_running_loop()

        @self.app.get("/healthz")
        async def healthz() -> dict[str, str]:
            return {"ok": "true"}

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket) -> None:
            await websocket.accept()
            self._clientes_conectados.add(websocket)
            await websocket.send_json(self.store.status_payload())
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                self._clientes_conectados.discard(websocket)

        @self.app.get("/api/ocupacion/hoy")
        async def ocupacion_hoy() -> dict:
            totals = self.store.totals_payload()
            return {
                "persistencia_activa": False,
                "fecha_utc": datetime.now(timezone.utc).date().isoformat(),
                **{f"{z}_segundos": round(float(totals.get(z, 0.0)), 2) for z in self.store.zone_names},
            }

        @self.app.get("/api/video/meta")
        async def video_meta() -> dict:
            _, frame_ts = self.store.get_frame_snapshot()
            return {
                "available": frame_ts is not None,
                "last_frame_ts": frame_ts,
            }

        @self.app.get("/api/video/snapshot")
        async def video_snapshot() -> Response:
            frame, _ = self.store.get_frame_snapshot()
            if frame is None:
                raise HTTPException(status_code=404, detail="No hay frame publicado")
            return Response(content=frame, media_type="image/jpeg")

        @self.app.get("/api/video/live")
        async def video_live() -> StreamingResponse:
            async def frame_generator():
                last_sent_ts = None
                while True:
                    frame, frame_ts = self.store.get_frame_snapshot()
                    if frame is not None and frame_ts is not None and frame_ts != last_sent_ts:
                        last_sent_ts = frame_ts
                        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                    await asyncio.sleep(0.05)

            return StreamingResponse(
                frame_generator(),
                media_type="multipart/x-mixed-replace; boundary=frame",
            )

        @self.app.post("/internal/state")
        async def internal_state(request: Request) -> JSONResponse:
            self._check_internal_auth(request)
            payload = await request.json()
            states = payload.get("states", {})
            totals = payload.get("totals_seconds")

            if not isinstance(states, dict) or not states:
                raise HTTPException(status_code=400, detail="states debe ser un objeto con zonas")

            parsed_states = {str(k): 1 if int(v) == 1 else 0 for k, v in states.items()}
            parsed_totals = None
            if isinstance(totals, dict):
                parsed_totals = {str(k): max(0.0, float(v)) for k, v in totals.items()}

            self.store.update_state(parsed_states, parsed_totals)
            self.broadcast_status()
            return JSONResponse({"ok": True, "zones": len(parsed_states)})

        @self.app.post("/internal/frame")
        async def internal_frame(request: Request) -> JSONResponse:
            self._check_internal_auth(request)
            content_type = request.headers.get("content-type", "")
            if "image/jpeg" not in content_type and "application/octet-stream" not in content_type:
                raise HTTPException(status_code=415, detail="Content-Type debe ser image/jpeg")

            body = await request.body()
            if not body:
                raise HTTPException(status_code=400, detail="Body vacio")
            if len(body) > 4_000_000:
                raise HTTPException(status_code=413, detail="Frame demasiado grande")

            self.store.set_frame(body)
            return JSONResponse({"ok": True, "size": len(body)})


server = CloudAPIServer(frontend_path=settings.frontend_path, zone_names=settings.zone_names_env)
app = server.app
