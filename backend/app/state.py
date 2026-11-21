"""Objets partagés par toute l'application : le journal, le registre des unités et le hub WebSocket."""

from fastapi import WebSocket

from .config import settings
from .store.journal import Journal
from .store.units import UnitRegistry


class Hub:
    """Garde les navigateurs connectés et leur diffuse chaque nouvel événement."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, message: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(message)
            except Exception:
                self.clients.discard(ws)


journal = Journal(settings.db_path)
units = UnitRegistry(settings.db_path)
hub = Hub()
