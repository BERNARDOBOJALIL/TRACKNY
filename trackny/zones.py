from __future__ import annotations

import cv2
import numpy as np

ZONAS: dict[str, np.ndarray] = {
    "zona1": np.array([(4, 334), (198, 325), (217, 506), (8, 515), (4, 337)], dtype=np.int32),
    "zona2": np.array([(4, 241), (185, 236), (198, 319), (9, 324), (4, 241)], dtype=np.int32),
    "zona3": np.array([(5, 188), (173, 175), (185, 231), (7, 234), (5, 186)], dtype=np.int32),
    "zona4": np.array([(8, 116), (5, 179), (167, 173), (151, 128), (9, 116)], dtype=np.int32),
    "zona5": np.array([(218, 297), (257, 496), (503, 425), (404, 283), (221, 297)], dtype=np.int32),
    "zona6": np.array([(186, 190), (218, 287), (432, 262), (374, 185), (187, 190)], dtype=np.int32),
}

COLORES_ZONAS: dict[str, tuple[int, int, int]] = {
    "zona1": (255, 80, 80),
    "zona2": (80, 180, 255),
    "zona3": (120, 220, 120),
    "zona4": (255, 200, 80),
    "zona5": (220, 120, 255),
    "zona6": (80, 255, 200),
}


def zone_names() -> list[str]:
    return list(ZONAS.keys())


def color_zona(nombre: str) -> tuple[int, int, int]:
    if nombre in COLORES_ZONAS:
        return COLORES_ZONAS[nombre]
    # Color determinista basado en el nombre para zonas nuevas.
    h = hash(nombre) & 0xFFFFFF
    return ((h >> 16) & 0xFF, (h >> 8) & 0xFF, h & 0xFF)


def punto_en_zona(cx: int, cy: int, pts: np.ndarray) -> bool:
    return cv2.pointPolygonTest(pts, (float(cx), float(cy)), False) >= 0
