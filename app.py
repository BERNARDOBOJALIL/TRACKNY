import asyncio
import cv2
import time
import threading
import os
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Optional
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
from pymongo import MongoClient, UpdateOne
from ultralytics import YOLO

# =========================
# FastAPI / WebSockets
# =========================
app = FastAPI()
clientes_conectados = set()
server_loop: Optional[asyncio.AbstractEventLoop] = None
frontend_path = Path(__file__).with_name("index.html")

load_dotenv(Path(__file__).with_name(".env"))
BASE_DIR = Path(__file__).resolve().parent

# =========================
# ZONAS POLIGONALES
# Pega aquí el código exportado por la herramienta interactiva.
# Cada clave es el nombre de la máquina; el valor es un array de puntos (x, y)
# en coordenadas de la resolución original de tu cámara.
# =========================
ZONAS: dict[str, np.ndarray] = {
    "zona1": np.array([(4, 334), (198, 325), (217, 506), (8, 515), (4, 337)], dtype=np.int32),
    "zona2": np.array([(4, 241), (185, 236), (198, 319), (9, 324), (4, 241)], dtype=np.int32),
    "zona3": np.array([(5, 188), (173, 175), (185, 231), (7, 234), (5, 186)], dtype=np.int32),
    "zona4": np.array([(8, 116), (5, 179), (167, 173), (151, 128), (9, 116)], dtype=np.int32),
    "zona5": np.array([(218, 297), (257, 496), (503, 425), (404, 283), (221, 297)], dtype=np.int32),
    "zona6": np.array([(186, 190), (218, 287), (432, 262), (374, 185), (187, 190)], dtype=np.int32),
}

# Colores BGR para dibujar cada zona en el frame (se asignan automáticamente si no se definen)
COLORES_ZONAS: dict[str, tuple] = {
    "zona1": (255, 80, 80),
    "zona2": (80, 180, 255),
    "zona3": (120, 220, 120),
    "zona4": (255, 200, 80),
    "zona5": (220, 120, 255),
    "zona6": (80, 255, 200),
}

def color_zona(nombre: str) -> tuple:
    """Devuelve un color BGR para la zona, generando uno automático si no está definido."""
    if nombre in COLORES_ZONAS:
        return COLORES_ZONAS[nombre]
    # Genera color determinista a partir del hash del nombre
    h = hash(nombre) & 0xFFFFFF
    return ((h >> 16) & 0xFF, (h >> 8) & 0xFF, h & 0xFF)

def punto_en_zona(cx: int, cy: int, pts: np.ndarray) -> bool:
    return cv2.pointPolygonTest(pts, (float(cx), float(cy)), False) >= 0


class AsyncFrameReader:
    """Lee frames en un hilo dedicado y mantiene solo el más reciente."""
    def __init__(self, cap: cv2.VideoCapture, frame_interval: float = 0.0) -> None:
        self.cap = cap
        self._frame_interval = max(0.0, frame_interval)
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest_frame = None
        self._has_unread = False
        self._ended = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        next_read_at = time.perf_counter()
        while not self._stop_event.is_set():
            if self._frame_interval > 0.0:
                now = time.perf_counter()
                if now < next_read_at:
                    time.sleep(next_read_at - now)
                elif now - next_read_at > self._frame_interval * 3:
                    next_read_at = now

            ret, frame = self.cap.read()
            if not ret:
                with self._lock:
                    self._ended = True
                self._event.set()
                break

            if self._frame_interval > 0.0:
                next_read_at += self._frame_interval

            with self._lock:
                self._latest_frame = frame
                self._has_unread = True
            self._event.set()

    def read_latest(self, timeout: float = 0.25) -> tuple[bool, Optional[np.ndarray]]:
        while True:
            with self._lock:
                if self._has_unread:
                    self._has_unread = False
                    frame = self._latest_frame
                    if not self._has_unread:
                        self._event.clear()
                    return True, frame
                if self._ended:
                    return False, None
            if not self._event.wait(timeout=timeout):
                return False, None

    @property
    def ended(self) -> bool:
        with self._lock:
            return self._ended

    def stop(self) -> None:
        self._stop_event.set()
        self._event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)


# =========================
# MongoDB
# =========================
class MongoOccupancyStore:
    def __init__(self, uri: str, db_name: str, collection_name: str,
                 zone_names: list[str], flush_interval: float = 86400.0) -> None:
        self.enabled = bool(uri)
        self.flush_interval = max(60.0, flush_interval)
        self._zone_names = zone_names
        self._lock = threading.Lock()
        self._pending_by_day: dict[str, dict[str, float]] = {}
        self._stop_event = threading.Event()
        self._flush_thread: Optional[threading.Thread] = None
        self._client: Optional[MongoClient] = None
        self._collection = None

        if not self.enabled:
            print("MongoDB desactivado: define MONGO_URI para persistir tiempos de ocupacion.")
            return

        try:
            self._client = MongoClient(uri, serverSelectionTimeoutMS=4000, retryWrites=True)
            self._client.admin.command("ping")
            self._collection = self._client[db_name][collection_name]
            self._collection.create_index([("date", 1), ("machine_id", 1)], unique=True)
            self._flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
            self._flush_thread.start()
            print(f"MongoDB activo: {db_name}.{collection_name} | flush cada {self.flush_interval:.0f}s")
        except Exception as exc:
            print(f"No se pudo conectar a MongoDB Atlas: {exc}")
            self.enabled = False
            if self._client is not None:
                self._client.close()
                self._client = None

    def _empty_day(self) -> dict[str, float]:
        return {z: 0.0 for z in self._zone_names}

    @staticmethod
    def _current_day_utc() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    @staticmethod
    def _day_start_timestamp_utc(ts: float) -> float:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def add_interval(self, machine_id: str, start_ts: float, end_ts: float) -> None:
        if machine_id not in self._zone_names or end_ts <= start_ts:
            return
        start_cursor = start_ts
        with self._lock:
            while start_cursor < end_ts:
                day_start = self._day_start_timestamp_utc(start_cursor)
                next_day = day_start + timedelta(days=1).total_seconds()
                chunk_end = min(end_ts, next_day)
                day_utc = datetime.fromtimestamp(start_cursor, tz=timezone.utc).date().isoformat()
                if day_utc not in self._pending_by_day:
                    self._pending_by_day[day_utc] = self._empty_day()
                self._pending_by_day[day_utc][machine_id] += chunk_end - start_cursor
                start_cursor = chunk_end

    def flush(self, force: bool = False) -> None:
        if not self.enabled or self._collection is None:
            return
        with self._lock:
            pending = {d: dict(v) for d, v in self._pending_by_day.items()}
            if not force and not pending:
                return
            self._pending_by_day = {}

        now_utc = datetime.now(timezone.utc)
        ops = []
        for day_utc, zone_values in pending.items():
            for machine_id, seconds in zone_values.items():
                s = round(seconds, 2)
                if s <= 0:
                    continue
                ops.append(UpdateOne(
                    {"date": day_utc, "machine_id": machine_id},
                    {
                        "$inc": {"occupied_seconds": s},
                        "$set": {"updated_at": now_utc},
                        "$setOnInsert": {"created_at": now_utc},
                    },
                    upsert=True,
                ))
        if not ops:
            return
        try:
            self._collection.bulk_write(ops, ordered=False)
        except Exception as exc:
            print(f"Error al guardar en MongoDB: {exc}")
            with self._lock:
                for day_utc, zone_values in pending.items():
                    if day_utc not in self._pending_by_day:
                        self._pending_by_day[day_utc] = self._empty_day()
                    for machine_id, seconds in zone_values.items():
                        self._pending_by_day[day_utc][machine_id] += seconds

    def _flush_loop(self) -> None:
        while not self._stop_event.wait(self.flush_interval):
            self.flush()

    def get_today_totals(self) -> dict[str, float]:
        totals = self._empty_day()
        today_utc = self._current_day_utc()
        if self.enabled and self._collection is not None:
            for doc in self._collection.find({"date": today_utc}, {"_id": 0, "machine_id": 1, "occupied_seconds": 1}):
                mid = doc.get("machine_id")
                if mid in totals:
                    totals[mid] = float(doc.get("occupied_seconds", 0.0))
        with self._lock:
            for mid, secs in self._pending_by_day.get(today_utc, {}).items():
                if mid in totals:
                    totals[mid] += secs
        return totals

    def close(self) -> None:
        if not self.enabled:
            return
        self._stop_event.set()
        if self._flush_thread and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=2.0)
        self.flush(force=True)
        if self._client:
            self._client.close()


NOMBRES_ZONAS = list(ZONAS.keys())

mongo_store = MongoOccupancyStore(
    uri=os.getenv("MONGO_URI", ""),
    db_name=os.getenv("MONGO_DB_NAME", "trackny"),
    collection_name=os.getenv("MONGO_COLLECTION", "occupancy_daily"),
    zone_names=NOMBRES_ZONAS,
    flush_interval=float(os.getenv("MONGO_FLUSH_INTERVAL", "86400")),
)

# =========================
# Estado por zona (dinámico)
# =========================
TIEMPO_PARA_OCUPAR   = 10   # segundos con persona para marcar como OCUPADA
TIEMPO_PARA_DESOCUPAR = 5   # segundos sin persona para marcar como DESOCUPADA

tiempo_con_persona:  dict[str, float]          = {z: 0.0  for z in NOMBRES_ZONAS}
tiempo_sin_persona:  dict[str, float]          = {z: 0.0  for z in NOMBRES_ZONAS}
ocupada:             dict[str, bool]           = {z: False for z in NOMBRES_ZONAS}
estado_anterior:     dict[str, bool]           = {z: False for z in NOMBRES_ZONAS}
inicio_ocupacion:    dict[str, Optional[float]] = {z: None  for z in NOMBRES_ZONAS}


# =========================
# FastAPI endpoints
# =========================
@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    if frontend_path.exists():
        return HTMLResponse(frontend_path.read_text(encoding="utf-8"))
    return HTMLResponse("<html><body><h1>Frontend no encontrado</h1></body></html>")


@app.on_event("startup")
async def configurar_event_loop() -> None:
    global server_loop
    server_loop = asyncio.get_running_loop()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    clientes_conectados.add(websocket)
    print("Cliente conectado")
    await websocket.send_json({z: int(ocupada[z]) for z in NOMBRES_ZONAS})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clientes_conectados.discard(websocket)
        print("Cliente desconectado")


@app.get("/api/ocupacion/hoy")
async def ocupacion_hoy() -> dict:
    totals = mongo_store.get_today_totals()
    ahora_ts = time.time()
    for z in NOMBRES_ZONAS:
        if inicio_ocupacion[z] is not None:
            totals[z] += max(0.0, ahora_ts - inicio_ocupacion[z])
    return {
        "persistencia_activa": mongo_store.enabled,
        "fecha_utc": datetime.now(timezone.utc).date().isoformat(),
        **{f"{z}_segundos": round(totals[z], 2) for z in NOMBRES_ZONAS},
    }


async def broadcast(data: dict) -> None:
    for cliente in list(clientes_conectados):
        try:
            await cliente.send_json(data)
        except Exception:
            clientes_conectados.discard(cliente)


def enviar_estado_zonas() -> None:
    if server_loop is None or not server_loop.is_running():
        return
    asyncio.run_coroutine_threadsafe(
        broadcast({z: int(ocupada[z]) for z in NOMBRES_ZONAS}),
        server_loop,
    )


# =========================
# Cargar modelo YOLO
# =========================
model = YOLO("yolo26n.pt")

# =========================
# Cámara
# =========================
VIDEO_EXTS = ("*.mp4", "*.avi", "*.mov", "*.mkv")
video_path_env = os.getenv("VIDEO_PATH", "").strip()
video_path: Optional[Path] = None

if video_path_env:
    candidate = Path(video_path_env)
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    if candidate.exists():
        video_path = candidate

if video_path is None:
    for pattern in VIDEO_EXTS:
        matches = sorted(BASE_DIR.glob(pattern))
        if matches:
            video_path = matches[0]
            break

if video_path is not None:
    print(f"Fuente de video: {video_path.name}")
    cap = cv2.VideoCapture(str(video_path))
else:
    print("Fuente de video: camara (indice 0)")
    cap = cv2.VideoCapture(0)

if not cap.isOpened():
    raise RuntimeError("No se pudo abrir la fuente de video configurada.")

source_fps = 0.0
if video_path is not None:
    source_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if source_fps <= 1.0:
        source_fps = 30.0
    print(f"FPS fuente detectado: {source_fps:.2f}")

frame_reader = AsyncFrameReader(cap, frame_interval=(1.0 / source_fps) if source_fps > 0 else 0.0)
frame_reader.start()

WINDOW_NAME = "Deteccion de Maquinas"
cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

servidor_thread = threading.Thread(
    target=lambda: uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info", access_log=False),
    daemon=True,
)
servidor_thread.start()

ultimo_tiempo = time.time()

# =========================
# Loop principal
# =========================
while True:
    ret, frame = frame_reader.read_latest()
    if not ret:
        if frame_reader.ended:
            break
        continue

    alto, ancho, _ = frame.shape
    ahora = time.time()
    delta = ahora - ultimo_tiempo
    ultimo_tiempo = ahora

    # Detección YOLO
    resultados = model(frame, verbose=False)[0]

    # Qué zonas tienen persona este frame
    persona_en: dict[str, bool] = {z: False for z in NOMBRES_ZONAS}

    for box in resultados.boxes:
        if int(box.cls[0]) != 0:   # solo clase "persona"
            continue
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2

        zona_detectada = None
        for nombre, pts in ZONAS.items():
            if punto_en_zona(cx, cy, pts):
                zona_detectada = nombre
                break   # una persona se asigna a la primera zona que la contenga

        color_box = (0, 255, 0) if zona_detectada else (180, 180, 180)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color_box, 2)
        label = f"Persona ({zona_detectada})" if zona_detectada else "Persona (fuera)"
        cv2.putText(frame, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_box, 2)

        if zona_detectada:
            persona_en[zona_detectada] = True

    # Dibujar zonas poligonales
    for nombre, pts in ZONAS.items():
        color = color_zona(nombre)
        cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=2)
        M = cv2.moments(pts)
        if M["m00"] != 0:
            cx_z = int(M["m10"] / M["m00"])
            cy_z = int(M["m01"] / M["m00"])
            estado_txt = "OCUPADA" if ocupada[nombre] else "LIBRE"
            cv2.putText(frame, f"{nombre}: {estado_txt}", (cx_z - 60, cy_z),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

    # Actualizar contadores y estado por zona
    hubo_cambio = False
    for z in NOMBRES_ZONAS:
        if persona_en[z]:
            tiempo_con_persona[z] += delta
            tiempo_sin_persona[z] = 0.0
        else:
            tiempo_con_persona[z] = 0.0
            if ocupada[z]:
                tiempo_sin_persona[z] += delta
            else:
                tiempo_sin_persona[z] = 0.0

        antes = ocupada[z]

        if not ocupada[z] and tiempo_con_persona[z] >= TIEMPO_PARA_OCUPAR:
            ocupada[z] = True
        if ocupada[z] and tiempo_sin_persona[z] >= TIEMPO_PARA_DESOCUPAR:
            ocupada[z] = False
            tiempo_sin_persona[z] = 0.0

        # Registrar intervalo de ocupación
        if ocupada[z] and inicio_ocupacion[z] is None:
            inicio_ocupacion[z] = ahora
        elif not ocupada[z] and inicio_ocupacion[z] is not None:
            mongo_store.add_interval(z, inicio_ocupacion[z], ahora)
            inicio_ocupacion[z] = None

        if ocupada[z] != antes:
            hubo_cambio = True

    if hubo_cambio:
        enviar_estado_zonas()

    # Panel de estado en pantalla (esquina superior derecha)
    escala_base  = min(alto / 1080, ancho / 1920)
    escala_fuente = max(0.45, escala_base * 1.2)
    grosor = max(1, int(escala_fuente * 2))
    line_h = max(22, int(28 * escala_base) + 18)
    panel_h = line_h * (len(NOMBRES_ZONAS) + 1) + 10
    panel_w = 280
    panel_x = max(8, ancho - panel_w - 8)
    text_x = panel_x + 6

    overlay = frame.copy()
    cv2.rectangle(overlay, (panel_x, 8), (panel_x + panel_w, 8 + panel_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    cv2.putText(frame, "ESTADO DE ZONAS", (text_x, 8 + line_h),
                cv2.FONT_HERSHEY_DUPLEX, escala_fuente * 0.7, (200, 200, 200), 1)

    for i, z in enumerate(NOMBRES_ZONAS):
        color_txt = (0, 80, 255) if ocupada[z] else (0, 220, 80)
        estado_txt = "OCUPADA" if ocupada[z] else "LIBRE"
        cv2.putText(frame, f"{z}: {estado_txt}",
                    (text_x, 8 + line_h * (i + 2)),
                    cv2.FONT_HERSHEY_DUPLEX, escala_fuente * 0.65, color_txt, grosor)

    cv2.imshow(WINDOW_NAME, frame)
    if cv2.waitKey(1) & 0xFF == 27:
        break

# =========================
# Liberar recursos
# =========================
frame_reader.stop()
cap.release()
cv2.destroyAllWindows()

fin_ts = time.time()
for z in NOMBRES_ZONAS:
    if inicio_ocupacion[z] is not None:
        mongo_store.add_interval(z, inicio_ocupacion[z], fin_ts)

mongo_store.close()