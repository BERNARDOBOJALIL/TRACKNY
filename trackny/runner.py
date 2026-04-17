from __future__ import annotations

import threading
import time

import cv2
import uvicorn
import torch
from ultralytics import YOLO

from .api import APIServer
from .config import BASE_DIR, settings
from .frame_reader import AsyncFrameReader
from .remote_publisher import RemotePublisher
from .state import OccupancyState
from .storage import MongoOccupancyStore
from .video import open_video_source
from .zones import ZONAS, color_zona, punto_en_zona, zone_names

WINDOW_NAME = "Deteccion de Maquinas"


def _draw_zones(frame, occupancy_state: OccupancyState) -> None:
    for nombre, pts in ZONAS.items():
        color = color_zona(nombre)
        cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=2)
        moments = cv2.moments(pts)
        if moments["m00"] != 0:
            cx_zone = int(moments["m10"] / moments["m00"])
            cy_zone = int(moments["m01"] / moments["m00"])
            estado_txt = "OCUPADA" if occupancy_state.ocupada[nombre] else "LIBRE"
            cv2.putText(
                frame,
                f"{nombre}: {estado_txt}",
                (cx_zone - 60, cy_zone),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
            )


def _draw_status_panel(frame, occupancy_state: OccupancyState) -> None:
    alto, ancho, _ = frame.shape
    escala_base = min(alto / 1080, ancho / 1920)
    escala_fuente = max(0.45, escala_base * 1.2)
    grosor = max(1, int(escala_fuente * 2))
    line_h = max(22, int(28 * escala_base) + 18)

    panel_h = line_h * (len(occupancy_state.zone_names) + 1) + 10
    panel_w = 280
    panel_x = max(8, ancho - panel_w - 8)
    text_x = panel_x + 6

    overlay = frame.copy()
    cv2.rectangle(overlay, (panel_x, 8), (panel_x + panel_w, 8 + panel_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    cv2.putText(
        frame,
        "ESTADO DE ZONAS",
        (text_x, 8 + line_h),
        cv2.FONT_HERSHEY_DUPLEX,
        escala_fuente * 0.7,
        (200, 200, 200),
        1,
    )

    for i, zone in enumerate(occupancy_state.zone_names):
        color_txt = (0, 80, 255) if occupancy_state.ocupada[zone] else (0, 220, 80)
        estado_txt = "OCUPADA" if occupancy_state.ocupada[zone] else "LIBRE"
        cv2.putText(
            frame,
            f"{zone}: {estado_txt}",
            (text_x, 8 + line_h * (i + 2)),
            cv2.FONT_HERSHEY_DUPLEX,
            escala_fuente * 0.65,
            color_txt,
            grosor,
        )


def _process_detections(frame, results, occupancy_state: OccupancyState, coord_scale: float = 1.0) -> dict[str, bool]:
    persona_en = {zone: False for zone in occupancy_state.zone_names}

    for box in results.boxes:
        if int(box.cls[0]) != 0:
            continue

        x1, y1, x2, y2 = map(int, (box.xyxy[0] / coord_scale).tolist())
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2

        zona_detectada = None
        for nombre, pts in ZONAS.items():
            if punto_en_zona(cx, cy, pts):
                zona_detectada = nombre
                break

        color_box = (0, 255, 0) if zona_detectada else (180, 180, 180)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color_box, 2)
        label = f"Persona ({zona_detectada})" if zona_detectada else "Persona (fuera)"
        cv2.putText(frame, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_box, 2)

        if zona_detectada:
            persona_en[zona_detectada] = True

    return persona_en


def _prepare_inference_frame(frame):
    height, width = frame.shape[:2]
    max_width = settings.inference_max_width
    if width <= max_width:
        return frame, 1.0

    scale = max_width / float(width)
    resized = cv2.resize(frame, (max_width, max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    return resized, scale


def run() -> None:
    zones = zone_names()
    occupancy_state = OccupancyState(
        zone_names=zones,
        occupy_seconds=settings.zone_occupy_seconds,
        release_seconds=settings.zone_release_seconds,
    )

    mongo_store = MongoOccupancyStore(
        uri=settings.mongo_uri,
        db_name=settings.mongo_db_name,
        collection_name=settings.mongo_collection,
        zone_names=zones,
        flush_interval=settings.mongo_flush_interval,
    )

    def today_totals() -> dict[str, float]:
        persisted = mongo_store.get_today_totals()
        return occupancy_state.merge_live_totals(persisted, time.time())

    api_server = APIServer(
        frontend_path=settings.frontend_path,
        zone_names=zones,
        get_status_payload=occupancy_state.status_payload,
        get_today_totals=today_totals,
        is_persistence_enabled=lambda: mongo_store.enabled,
    )

    remote_publisher = RemotePublisher(
        ingest_url=settings.remote_ingest_url,
        token=settings.internal_api_token,
        state_interval=settings.remote_state_interval,
        video_enabled=settings.remote_video_enabled,
        video_fps=settings.remote_video_fps,
        jpeg_quality=settings.remote_jpeg_quality,
        frame_max_width=settings.remote_frame_max_width,
    )

    if settings.run_local_api:
        server_thread = threading.Thread(
            target=lambda: uvicorn.run(
                api_server.app,
                host=settings.host,
                port=settings.port,
                log_level="info",
                access_log=False,
            ),
            daemon=True,
        )
        server_thread.start()
    else:
        print("API local desactivada (RUN_LOCAL_API=false).")

    model = YOLO(str(settings.model_path))
    try:
        model.fuse()
    except Exception:
        pass

    use_half = bool(torch.cuda.is_available())
    cap, _, source_fps = open_video_source(BASE_DIR, settings.video_path_env)
    frame_reader = AsyncFrameReader(cap, frame_interval=(1.0 / source_fps) if source_fps > 0 else 0.0)
    frame_reader.start()

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    ultimo_tiempo = time.time()
    inference_index = 0
    last_persona_en = {zone: False for zone in occupancy_state.zone_names}
    last_totals_sync = 0.0
    cached_totals = today_totals()

    if remote_publisher.enabled:
        remote_publisher.push_state(occupancy_state.status_payload(), cached_totals, force=True)

    try:
        while True:
            ret, frame = frame_reader.read_latest()
            if not ret:
                if frame_reader.ended:
                    break
                continue

            ahora = time.time()
            delta = ahora - ultimo_tiempo
            ultimo_tiempo = ahora

            inference_index += 1
            should_infer = (inference_index % settings.inference_every_n_frames) == 0

            if should_infer:
                inference_frame, infer_scale = _prepare_inference_frame(frame)
                resultados = model.predict(
                    inference_frame,
                    verbose=False,
                    imgsz=settings.yolo_imgsz,
                    conf=settings.yolo_conf,
                    max_det=settings.yolo_max_det,
                    classes=[0],
                    half=use_half,
                )[0]
                persona_en = _process_detections(frame, resultados, occupancy_state, coord_scale=infer_scale)
                last_persona_en = persona_en
            else:
                persona_en = last_persona_en

            _draw_zones(frame, occupancy_state)

            changed = occupancy_state.update(persona_en, delta, ahora, mongo_store)

            if changed:
                if settings.run_local_api:
                    api_server.broadcast_status(occupancy_state.status_payload())
                cached_totals = today_totals()
                last_totals_sync = ahora
                remote_publisher.push_state(occupancy_state.status_payload(), cached_totals, force=True)
            else:
                if remote_publisher.enabled and (ahora - last_totals_sync) >= settings.remote_state_interval:
                    cached_totals = today_totals()
                    last_totals_sync = ahora
                    remote_publisher.push_state(occupancy_state.status_payload(), cached_totals)
                else:
                    remote_publisher.push_state(occupancy_state.status_payload())

            remote_publisher.push_frame(frame)

            _draw_status_panel(frame, occupancy_state)
            cv2.imshow(WINDOW_NAME, frame)

            if cv2.waitKey(1) & 0xFF == 27:
                break
    finally:
        remote_publisher.close()
        frame_reader.stop()
        cap.release()
        cv2.destroyAllWindows()

        occupancy_state.close_open_intervals(time.time(), mongo_store)
        mongo_store.close()
