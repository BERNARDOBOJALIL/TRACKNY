from __future__ import annotations

import time
from typing import Optional

import cv2
import requests


class RemotePublisher:
    def __init__(
        self,
        ingest_url: str,
        token: str,
        state_interval: float = 1.0,
        video_enabled: bool = False,
        video_fps: float = 5.0,
        jpeg_quality: int = 70,
        timeout_seconds: float = 2.0,
    ) -> None:
        self.ingest_url = ingest_url.rstrip("/")
        self.token = token.strip()
        self.enabled = bool(self.ingest_url)
        self.state_interval = max(0.2, state_interval)
        self.video_enabled = video_enabled and self.enabled
        self.video_interval = 1.0 / max(1.0, video_fps)
        self.jpeg_quality = max(40, min(95, int(jpeg_quality)))
        self.timeout_seconds = timeout_seconds

        self._last_state_push = 0.0
        self._last_frame_push = 0.0
        self._session = requests.Session()

        if self.enabled:
            print(f"Publicador remoto activo: {self.ingest_url}")
            if not self.token:
                print("Advertencia: INTERNAL_API_TOKEN vacio en publicador remoto.")

    def _headers(self, content_type: Optional[str] = None) -> dict[str, str]:
        headers = {}
        if self.token:
            headers["x-internal-token"] = self.token
        if content_type:
            headers["content-type"] = content_type
        return headers

    def push_state(self, states: dict[str, int], totals_seconds: Optional[dict[str, float]] = None, force: bool = False) -> None:
        if not self.enabled:
            return

        now = time.time()
        if not force and (now - self._last_state_push) < self.state_interval:
            return

        payload = {"states": states}
        if totals_seconds is not None:
            payload["totals_seconds"] = totals_seconds

        try:
            response = self._session.post(
                f"{self.ingest_url}/internal/state",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout_seconds,
            )
            if response.ok:
                self._last_state_push = now
            else:
                print(f"Remote state push fallo: HTTP {response.status_code}")
        except Exception as exc:
            print(f"Remote state push error: {exc}")

    def push_frame(self, frame) -> None:
        if not self.video_enabled:
            return

        now = time.time()
        if (now - self._last_frame_push) < self.video_interval:
            return

        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
        )
        if not ok:
            return

        try:
            response = self._session.post(
                f"{self.ingest_url}/internal/frame",
                data=encoded.tobytes(),
                headers=self._headers(content_type="image/jpeg"),
                timeout=self.timeout_seconds,
            )
            if response.ok:
                self._last_frame_push = now
            else:
                print(f"Remote frame push fallo: HTTP {response.status_code}")
        except Exception as exc:
            print(f"Remote frame push error: {exc}")

    def close(self) -> None:
        self._session.close()
