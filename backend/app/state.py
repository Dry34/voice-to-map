"""Objets partagés par toute l'application : le journal, le registre des unités et le hub WebSocket."""

import time

from fastapi import WebSocket

from .config import settings
from .store.journal import Journal
from .store.units import UnitRegistry


class Hub:
    """Garde les navigateurs connectés et leur diffuse chaque nouvel événement."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.presence: dict[WebSocket, str] = {}  # onglet connecté -> unité qu'il tient
        self.last_active: dict[str, float] = {}   # unité -> dernière réservation / annonce / présence

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)
        self.presence.pop(ws, None)

    def set_presence(self, ws: WebSocket, unit: str | None) -> None:
        if unit:
            self.presence[ws] = unit
            self.touch(unit)
        else:
            self.presence.pop(ws, None)

    def touch(self, unit: str) -> None:
        self.last_active[unit] = time.monotonic()

    def online(self, unit: str, grace_s: float = 20) -> bool:
        """Un onglet tient-il cette unité en ce moment ?

        Connecté, ou actif il y a moins de grace_s secondes : juste après une réservation,
        l'onglet n'a pas encore signalé sa présence (deux clics simultanés donnaient deux ALPHA 1).
        """
        recent = time.monotonic() - self.last_active.get(unit, float("-inf")) < grace_s
        return recent or unit in self.presence.values()

    async def broadcast(self, message: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(ws)


journal = Journal(settings.db_path)
units = UnitRegistry(settings.db_path)
hub = Hub()
