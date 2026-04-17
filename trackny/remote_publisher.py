from __future__ import annotations

import threading
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
        frame_max_width: int = 960,
        timeout_seconds: float = 2.0,
    ) -> None:
        self.ingest_url = ingest_url.rstrip("/")
        self.token = token.strip()
        self.enabled = bool(self.ingest_url)
        self.state_interval = max(0.2, state_interval)
        self.video_enabled = video_enabled and self.enabled
        self.video_interval = 1.0 / max(1.0, video_fps)
        self.jpeg_quality = max(40, min(95, int(jpeg_quality)))
        self.frame_max_width = max(320, int(frame_max_width))
        self.timeout_seconds = timeout_seconds

        self._last_state_push = 0.0
        self._last_frame_push = 0.0
        self._stop_event = threading.Event()
        self._frame_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._worker_thread: Optional[threading.Thread] = None
        self._session = requests.Session()

        if self.enabled:
            print(f"Publicador remoto activo: {self.ingest_url}")
            if not self.token:
                print("Advertencia: INTERNAL_API_TOKEN vacio en publicador remoto.")
            if self.video_enabled:
                self._worker_thread = threading.Thread(target=self._frame_worker_loop, daemon=True)
                self._worker_thread.start()

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

        with self._frame_lock:
            self._latest_frame = frame
        self._frame_event.set()

    def _prepare_frame(self, frame):
        height, width = frame.shape[:2]
        if width > self.frame_max_width:
            new_height = max(1, int(height * (self.frame_max_width / float(width))))
            frame = cv2.resize(frame, (self.frame_max_width, new_height), interpolation=cv2.INTER_AREA)

        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
        )
        if not ok:
            return None
        return encoded.tobytes()

    def _frame_worker_loop(self) -> None:
        next_send_at = 0.0
        while not self._stop_event.is_set():
            now = time.monotonic()
            if now < next_send_at:
                time.sleep(min(0.05, next_send_at - now))
                continue

            if not self._frame_event.wait(timeout=0.05):
                continue

            with self._frame_lock:
                frame = self._latest_frame
                self._latest_frame = None
                self._frame_event.clear()

            if frame is None:
                continue

            payload = self._prepare_frame(frame)
            if payload is None:
                continue

            try:
                response = self._session.post(
                    f"{self.ingest_url}/internal/frame",
                    data=payload,
                    headers=self._headers(content_type="image/jpeg"),
                    timeout=self.timeout_seconds,
                )
                if response.ok:
                    self._last_frame_push = time.time()
                else:
                    print(f"Remote frame push fallo: HTTP {response.status_code}")
            except Exception as exc:
                print(f"Remote frame push error: {exc}")

            next_send_at = time.monotonic() + self.video_interval

    def close(self) -> None:
        self._stop_event.set()
        self._frame_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.5)
        self._session.close()
