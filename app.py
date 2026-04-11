import asyncio
import cv2
import time
import threading
import os
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

# Cargar variables locales desde .env si existe.
load_dotenv(Path(__file__).with_name(".env"))


class MongoOccupancyStore:
    def __init__(self, uri: str, db_name: str, collection_name: str, flush_interval: float = 86400.0) -> None:
        self.enabled = bool(uri)
        self.flush_interval = max(60.0, flush_interval)
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
            self._collection = None

    @staticmethod
    def _current_day_utc() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    @staticmethod
    def _day_start_timestamp_utc(ts: float) -> float:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return day_start.timestamp()

    def _add_pending_seconds(self, day_utc: str, machine_id: str, seconds: float) -> None:
        if seconds <= 0:
            return

        if day_utc not in self._pending_by_day:
            self._pending_by_day[day_utc] = {"maquina1": 0.0, "maquina2": 0.0}

        self._pending_by_day[day_utc][machine_id] += seconds

    def add_interval(self, machine_id: str, start_ts: float, end_ts: float) -> None:
        if machine_id not in ("maquina1", "maquina2"):
            return

        if end_ts <= start_ts:
            return

        start_cursor = start_ts

        with self._lock:
            # Divide intervalos que cruzan medianoche UTC para mantener agregados diarios correctos.
            while start_cursor < end_ts:
                day_start = self._day_start_timestamp_utc(start_cursor)
                next_day_start = day_start + timedelta(days=1).total_seconds()
                chunk_end = min(end_ts, next_day_start)

                day_utc = datetime.fromtimestamp(start_cursor, tz=timezone.utc).date().isoformat()
                self._add_pending_seconds(day_utc, machine_id, chunk_end - start_cursor)
                start_cursor = chunk_end

    def flush(self, force: bool = False) -> None:
        if not self.enabled or self._collection is None:
            return

        with self._lock:
            pending_by_day = {
                day: dict(values)
                for day, values in self._pending_by_day.items()
            }

            if not force and not pending_by_day:
                return

            self._pending_by_day = {}

        now_utc = datetime.now(timezone.utc)
        ops = []

        for day_utc, machine_values in pending_by_day.items():
            for machine_id, seconds in machine_values.items():
                rounded_seconds = round(seconds, 2)
                if rounded_seconds <= 0:
                    continue

                ops.append(
                    UpdateOne(
                        {"date": day_utc, "machine_id": machine_id},
                        {
                            "$inc": {"occupied_seconds": rounded_seconds},
                            "$set": {"updated_at": now_utc},
                            "$setOnInsert": {"created_at": now_utc},
                        },
                        upsert=True,
                    )
                )

        if not ops:
            return

        try:
            self._collection.bulk_write(ops, ordered=False)
        except Exception as exc:
            print(f"Error al guardar ocupacion en MongoDB: {exc}")
            # Reponer acumulado local para intentar en el siguiente flush.
            with self._lock:
                for day_utc, machine_values in pending_by_day.items():
                    if day_utc not in self._pending_by_day:
                        self._pending_by_day[day_utc] = {"maquina1": 0.0, "maquina2": 0.0}
                    for machine_id, seconds in machine_values.items():
                        self._pending_by_day[day_utc][machine_id] += seconds

    def _flush_loop(self) -> None:
        while not self._stop_event.wait(self.flush_interval):
            self.flush()

    def get_today_totals(self) -> dict:
        totals = {"maquina1": 0.0, "maquina2": 0.0}
        today_utc = self._current_day_utc()

        if self.enabled and self._collection is not None:
            docs = self._collection.find(
                {"date": today_utc},
                {"_id": 0, "machine_id": 1, "occupied_seconds": 1},
            )
            for doc in docs:
                machine_id = doc.get("machine_id")
                if machine_id in totals:
                    totals[machine_id] = float(doc.get("occupied_seconds", 0.0))

        with self._lock:
            today_pending = self._pending_by_day.get(today_utc, {"maquina1": 0.0, "maquina2": 0.0})
            totals["maquina1"] += today_pending["maquina1"]
            totals["maquina2"] += today_pending["maquina2"]

        return totals

    def close(self) -> None:
        if not self.enabled:
            return

        self._stop_event.set()
        if self._flush_thread is not None and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=2.0)

        self.flush(force=True)

        if self._client is not None:
            self._client.close()


mongo_store = MongoOccupancyStore(
    uri=os.getenv("MONGO_URI", ""),
    db_name=os.getenv("MONGO_DB_NAME", "trackny"),
    collection_name=os.getenv("MONGO_COLLECTION", "occupancy_daily"),
    flush_interval=float(os.getenv("MONGO_FLUSH_INTERVAL", "86400")),
)


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    if frontend_path.exists():
        return HTMLResponse(frontend_path.read_text(encoding="utf-8"))

    return HTMLResponse(
        "<html><body><h1>Frontend no encontrado</h1><p>Falta index.html.</p></body></html>"
    )


@app.on_event("startup")
async def configurar_event_loop() -> None:
    global server_loop
    server_loop = asyncio.get_running_loop()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    clientes_conectados.add(websocket)
    print("Cliente conectado")

    # Enviar estado actual al conectar para evitar frontend sin datos iniciales.
    await websocket.send_json({"maquina1": int(ocupada_m1), "maquina2": int(ocupada_m2)})

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

    if inicio_ocupacion_m1 is not None:
        totals["maquina1"] += max(0.0, ahora_ts - inicio_ocupacion_m1)
    if inicio_ocupacion_m2 is not None:
        totals["maquina2"] += max(0.0, ahora_ts - inicio_ocupacion_m2)

    return {
        "persistencia_activa": mongo_store.enabled,
        "fecha_utc": datetime.now(timezone.utc).date().isoformat(),
        "maquina1_segundos": round(totals["maquina1"], 2),
        "maquina2_segundos": round(totals["maquina2"], 2),
    }


async def broadcast(data: dict) -> None:
    if not clientes_conectados:
        return

    for cliente in list(clientes_conectados):
        try:
            await cliente.send_json(data)
        except Exception:
            clientes_conectados.discard(cliente)


def enviar_estado_maquinas() -> None:
    if server_loop is None or not server_loop.is_running():
        return

    asyncio.run_coroutine_threadsafe(
        broadcast({"maquina1": int(ocupada_m1), "maquina2": int(ocupada_m2)}),
        server_loop,
    )


# =========================
# Cargar modelo YOLO
# =========================
model = YOLO("yolo26n.pt")  # tu modelo YOLO 26

# =========================
# Cámara
# =========================
cap = cv2.VideoCapture(0)

# =========================
# Ventana en pantalla completa
# =========================
WINDOW_NAME = "Deteccion de Maquina"
cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
cv2.setWindowProperty(
    WINDOW_NAME,
    cv2.WND_PROP_FULLSCREEN,
    cv2.WINDOW_FULLSCREEN
)

# =========================
# Parámetros de tiempo (segundos)
# =========================
TIEMPO_PARA_OCUPAR = 10
TIEMPO_PARA_DESOCUPAR = 5

# Estados para Máquina 1
tiempo_con_persona_m1 = 0.0
tiempo_sin_persona_m1 = 0.0
ocupada_m1 = False

# Estados para Máquina 2
tiempo_con_persona_m2 = 0.0
tiempo_sin_persona_m2 = 0.0
ocupada_m2 = False

ultimo_tiempo = time.time()
estado_anterior_m1 = ocupada_m1
estado_anterior_m2 = ocupada_m2
inicio_ocupacion_m1: Optional[float] = None
inicio_ocupacion_m2: Optional[float] = None


def iniciar_servidor() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info", access_log=False)


servidor_thread = threading.Thread(target=iniciar_servidor, daemon=True)
servidor_thread.start()

# =========================
# Loop principal
# =========================
while True:
    ret, frame = cap.read()
    if not ret:
        break

    alto, ancho, _ = frame.shape

    # =========================
    # Zonas de las máquinas (con espacio en medio)
    # =========================
    # Máquina 1 (lado izquierdo, 40% del ancho)
    zona_m1_x1 = 0
    zona_m1_y1 = 0
    zona_m1_x2 = int(ancho * 0.40)
    zona_m1_y2 = alto

    # Espacio vacío en el medio (20% del ancho)
    
    # Máquina 2 (lado derecho, 40% del ancho)
    zona_m2_x1 = int(ancho * 0.60)
    zona_m2_y1 = 0
    zona_m2_x2 = ancho
    zona_m2_y2 = alto

    # Dibujar zonas
    cv2.rectangle(frame, (zona_m1_x1, zona_m1_y1), (zona_m1_x2, zona_m1_y2), (255, 0, 0), 3)
    cv2.putText(frame, "MAQUINA 1", (20, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 0), 2)

    cv2.rectangle(frame, (zona_m2_x1, zona_m2_y1), (zona_m2_x2, zona_m2_y2), (255, 165, 0), 3)
    cv2.putText(frame, "MAQUINA 2", (zona_m2_x1 + 20, 50),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 165, 0), 2)

    # =========================
    # Detección
    # =========================
    resultados = model(frame, verbose=False)[0]
    persona_en_zona_m1 = False
    persona_en_zona_m2 = False

    for box in resultados.boxes:
        cls = int(box.cls[0])

        if cls == 0:  # persona
            x1, y1, x2, y2 = map(int, box.xyxy[0])

            # Centro del bounding box
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2

            # Verificar en qué zona está
            if zona_m1_x1 <= cx <= zona_m1_x2 and zona_m1_y1 <= cy <= zona_m1_y2:
                persona_en_zona_m1 = True
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(frame, "Persona M1", (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            elif zona_m2_x1 <= cx <= zona_m2_x2 and zona_m2_y1 <= cy <= zona_m2_y2:
                persona_en_zona_m2 = True
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(frame, "Persona M2", (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

    # =========================
    # Cálculo de tiempo real
    # =========================
    ahora = time.time()
    delta = ahora - ultimo_tiempo
    ultimo_tiempo = ahora

    # =========================
    # Lógica de contadores MÁQUINA 1
    # =========================
    if persona_en_zona_m1:
        tiempo_con_persona_m1 += delta
        tiempo_sin_persona_m1 = 0.0
    else:
        tiempo_con_persona_m1 = 0.0
        if ocupada_m1:
            tiempo_sin_persona_m1 += delta
        else:
            tiempo_sin_persona_m1 = 0.0

    # =========================
    # Lógica de contadores MÁQUINA 2
    # =========================
    if persona_en_zona_m2:
        tiempo_con_persona_m2 += delta
        tiempo_sin_persona_m2 = 0.0
    else:
        tiempo_con_persona_m2 = 0.0
        if ocupada_m2:
            tiempo_sin_persona_m2 += delta
        else:
            tiempo_sin_persona_m2 = 0.0

    # =========================
    # Lógica de estado MÁQUINA 1
    # =========================
    if not ocupada_m1 and tiempo_con_persona_m1 >= TIEMPO_PARA_OCUPAR:
        ocupada_m1 = True

    if ocupada_m1 and tiempo_sin_persona_m1 >= TIEMPO_PARA_DESOCUPAR:
        ocupada_m1 = False
        tiempo_sin_persona_m1 = 0.0

    # =========================
    # Lógica de estado MÁQUINA 2
    # =========================
    if not ocupada_m2 and tiempo_con_persona_m2 >= TIEMPO_PARA_OCUPAR:
        ocupada_m2 = True

    if ocupada_m2 and tiempo_sin_persona_m2 >= TIEMPO_PARA_DESOCUPAR:
        ocupada_m2 = False
        tiempo_sin_persona_m2 = 0.0

    # Registra intervalos de ocupacion en memoria local.
    if ocupada_m1 and inicio_ocupacion_m1 is None:
        inicio_ocupacion_m1 = ahora
    elif not ocupada_m1 and inicio_ocupacion_m1 is not None:
        mongo_store.add_interval("maquina1", inicio_ocupacion_m1, ahora)
        inicio_ocupacion_m1 = None

    if ocupada_m2 and inicio_ocupacion_m2 is None:
        inicio_ocupacion_m2 = ahora
    elif not ocupada_m2 and inicio_ocupacion_m2 is not None:
        mongo_store.add_interval("maquina2", inicio_ocupacion_m2, ahora)
        inicio_ocupacion_m2 = None

    # =========================
    # Notificar cambios por WebSocket
    # =========================
    if ocupada_m1 != estado_anterior_m1 or ocupada_m2 != estado_anterior_m2:
        estado_anterior_m1 = ocupada_m1
        estado_anterior_m2 = ocupada_m2
        enviar_estado_maquinas()

    # =========================
    # Visualización de estado
    # =========================
    # Tamaño adaptativo según resolución
    escala_base = min(alto / 1080, ancho / 1920)
    escala_fuente = max(0.6, escala_base * 1.5)
    grosor = max(2, int(escala_fuente * 2))
    
    # =========================
    # MÁQUINA 1 - Lado Izquierdo
    # =========================
    estado_m1 = "OCUPADA" if ocupada_m1 else "DESOCUPADA"
    color_estado_m1 = (0, 0, 255) if ocupada_m1 else (0, 255, 0)
    
    margen = 20
    x_info_m1 = margen
    y_base = int(60 * escala_base) + 80
    espaciado = int(40 * escala_base) + 35
    
    # Fondo para Máquina 1
    overlay = frame.copy()
    ancho_panel = (ancho // 2) - 40
    cv2.rectangle(overlay, (x_info_m1 - 10, y_base - int(40 * escala_base)), 
                  (ancho_panel, y_base + espaciado * 2 + 20), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

    cv2.putText(frame, f"M1: {estado_m1}",
                (x_info_m1, y_base),
                cv2.FONT_HERSHEY_DUPLEX, escala_fuente, color_estado_m1, grosor)

    cv2.putText(frame, f"Con persona: {int(tiempo_con_persona_m1)} s",
                (x_info_m1, y_base + espaciado),
                cv2.FONT_HERSHEY_DUPLEX, escala_fuente * 0.6, (255, 255, 255), max(2, grosor - 1))

    if ocupada_m1:
        cv2.putText(frame, f"Sin persona: {int(tiempo_sin_persona_m1)} s",
                    (x_info_m1, y_base + espaciado * 2),
                    cv2.FONT_HERSHEY_DUPLEX, escala_fuente * 0.6, (0, 255, 255), max(2, grosor - 1))

    # =========================
    # MÁQUINA 2 - Lado Derecho
    # =========================
    estado_m2 = "OCUPADA" if ocupada_m2 else "DESOCUPADA"
    color_estado_m2 = (0, 0, 255) if ocupada_m2 else (0, 255, 0)
    
    x_info_m2 = ancho // 2 + margen
    
    # Fondo para Máquina 2
    overlay = frame.copy()
    cv2.rectangle(overlay, (x_info_m2 - 10, y_base - int(40 * escala_base)), 
                  (ancho - 20, y_base + espaciado * 2 + 20), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

    cv2.putText(frame, f"M2: {estado_m2}",
                (x_info_m2, y_base),
                cv2.FONT_HERSHEY_DUPLEX, escala_fuente, color_estado_m2, grosor)

    cv2.putText(frame, f"Con persona: {int(tiempo_con_persona_m2)} s",
                (x_info_m2, y_base + espaciado),
                cv2.FONT_HERSHEY_DUPLEX, escala_fuente * 0.6, (255, 255, 255), max(2, grosor - 1))

    if ocupada_m2:
        cv2.putText(frame, f"Sin persona: {int(tiempo_sin_persona_m2)} s",
                    (x_info_m2, y_base + espaciado * 2),
                    cv2.FONT_HERSHEY_DUPLEX, escala_fuente * 0.6, (0, 255, 255), max(2, grosor - 1))

    # =========================
    # Mostrar frame
    # =========================
    cv2.imshow(WINDOW_NAME, frame)

    # ESC para salir
    if cv2.waitKey(1) & 0xFF == 27:
        break

# =========================
# Liberar recursos
# =========================
cap.release()
cv2.destroyAllWindows()

fin_ts = time.time()
if inicio_ocupacion_m1 is not None:
    mongo_store.add_interval("maquina1", inicio_ocupacion_m1, fin_ts)
if inicio_ocupacion_m2 is not None:
    mongo_store.add_interval("maquina2", inicio_ocupacion_m2, fin_ts)

mongo_store.close()
