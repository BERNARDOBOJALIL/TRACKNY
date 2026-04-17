from __future__ import annotations

import threading
import time
from typing import Optional

import cv2
import numpy as np


class AsyncFrameReader:
    """Lee frames en un hilo dedicado y mantiene solo el frame mas reciente."""

    def __init__(self, cap: cv2.VideoCapture, frame_interval: float = 0.0) -> None:
        self.cap = cap
        self._frame_interval = max(0.0, frame_interval)
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest_frame: Optional[np.ndarray] = None
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
