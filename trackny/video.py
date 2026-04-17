from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2

VIDEO_EXTS = ("*.mp4", "*.avi", "*.mov", "*.mkv")


def open_video_source(base_dir: Path, video_path_env: str) -> tuple[cv2.VideoCapture, Optional[Path], float]:
    video_path: Optional[Path] = None

    if video_path_env:
        candidate = Path(video_path_env)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        if candidate.exists():
            video_path = candidate

    if video_path is None:
        for pattern in VIDEO_EXTS:
            matches = sorted(base_dir.glob(pattern))
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

    return cap, video_path, source_fps
