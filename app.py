import asyncio
import cv2
import time
import threading
from typing import Optional
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from ultralytics import YOLO

# =========================
# FastAPI / WebSockets
# =========================
app = FastAPI()
clientes_conectados = set()
server_loop: Optional[asyncio.AbstractEventLoop] = None
frontend_path = Path(__file__).with_name("index.html")


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
