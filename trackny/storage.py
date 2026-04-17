from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from pymongo import MongoClient, UpdateOne


class MongoOccupancyStore:
    def __init__(
        self,
        uri: str,
        db_name: str,
        collection_name: str,
        zone_names: list[str],
        flush_interval: float = 86400.0,
    ) -> None:
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
                ops.append(
                    UpdateOne(
                        {"date": day_utc, "machine_id": machine_id},
                        {
                            "$inc": {"occupied_seconds": s},
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
            for doc in self._collection.find(
                {"date": today_utc},
                {"_id": 0, "machine_id": 1, "occupied_seconds": 1},
            ):
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
