from __future__ import annotations

from typing import Optional

from .storage import MongoOccupancyStore


class OccupancyState:
    def __init__(self, zone_names: list[str], occupy_seconds: float, release_seconds: float) -> None:
        self.zone_names = zone_names
        self.occupy_seconds = occupy_seconds
        self.release_seconds = release_seconds

        self.tiempo_con_persona: dict[str, float] = {z: 0.0 for z in zone_names}
        self.tiempo_sin_persona: dict[str, float] = {z: 0.0 for z in zone_names}
        self.ocupada: dict[str, bool] = {z: False for z in zone_names}
        self.inicio_ocupacion: dict[str, Optional[float]] = {z: None for z in zone_names}

    def status_payload(self) -> dict[str, int]:
        return {z: int(self.ocupada[z]) for z in self.zone_names}

    def update(
        self,
        persona_en: dict[str, bool],
        delta: float,
        now_ts: float,
        mongo_store: MongoOccupancyStore,
    ) -> bool:
        hubo_cambio = False

        for zone in self.zone_names:
            if persona_en.get(zone, False):
                self.tiempo_con_persona[zone] += delta
                self.tiempo_sin_persona[zone] = 0.0
            else:
                self.tiempo_con_persona[zone] = 0.0
                if self.ocupada[zone]:
                    self.tiempo_sin_persona[zone] += delta
                else:
                    self.tiempo_sin_persona[zone] = 0.0

            antes = self.ocupada[zone]

            if (not self.ocupada[zone]) and self.tiempo_con_persona[zone] >= self.occupy_seconds:
                self.ocupada[zone] = True
            if self.ocupada[zone] and self.tiempo_sin_persona[zone] >= self.release_seconds:
                self.ocupada[zone] = False
                self.tiempo_sin_persona[zone] = 0.0

            if self.ocupada[zone] and self.inicio_ocupacion[zone] is None:
                self.inicio_ocupacion[zone] = now_ts
            elif (not self.ocupada[zone]) and self.inicio_ocupacion[zone] is not None:
                mongo_store.add_interval(zone, self.inicio_ocupacion[zone], now_ts)
                self.inicio_ocupacion[zone] = None

            if self.ocupada[zone] != antes:
                hubo_cambio = True

        return hubo_cambio

    def merge_live_totals(self, persisted_totals: dict[str, float], now_ts: float) -> dict[str, float]:
        totals = dict(persisted_totals)
        for zone in self.zone_names:
            totals.setdefault(zone, 0.0)
            if self.inicio_ocupacion[zone] is not None:
                totals[zone] += max(0.0, now_ts - self.inicio_ocupacion[zone])
        return totals

    def close_open_intervals(self, end_ts: float, mongo_store: MongoOccupancyStore) -> None:
        for zone in self.zone_names:
            if self.inicio_ocupacion[zone] is not None:
                mongo_store.add_interval(zone, self.inicio_ocupacion[zone], end_ts)
                self.inicio_ocupacion[zone] = None
