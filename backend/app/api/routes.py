"""Routes HTTP et WebSocket. Responsable : B."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..config import settings
from ..pipeline import process_report
from ..state import hub, journal

router = APIRouter()


class ReportIn(BaseModel):
    unit: str = Field(default="ALPHA 2", min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=500)


@router.get("/api/config")
def get_config() -> dict:
    return {
        "zone_name": settings.zone_name,
        "center": [settings.zone_lat, settings.zone_lon],
        "node_mode": settings.node_mode,
        "llm": "mistral" if settings.mistral_api_key else "rules",
    }


@router.post("/api/reports")
async def post_report(report: ReportIn) -> dict:
    return await process_report(report.unit.strip().upper(), report.text.strip())


@router.get("/api/events")
def list_events(unit: str | None = None) -> list[dict]:
    return journal.all(unit)


@router.get("/api/integrity")
def integrity() -> dict:
    return journal.verify()


@router.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    await hub.connect(ws)
    await ws.send_json({"type": "snapshot", "events": journal.all()})
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(ws)
