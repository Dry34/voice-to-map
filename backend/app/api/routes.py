"""Routes HTTP et WebSocket. Responsable : B."""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..config import settings
from ..pipeline import process_report
from ..speech.stt import TranscriptionError, transcribe
from ..state import hub, journal

MAX_AUDIO_BYTES = 5 * 1024 * 1024  # ~ plusieurs minutes d'audio compressé, largement assez

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


@router.post("/api/reports/audio")
async def post_audio_report(unit: str = Form("ALPHA 2"), audio: UploadFile = File(...)) -> dict:
    data = await audio.read()
    if not data:
        raise HTTPException(400, "Enregistrement vide.")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Enregistrement trop long.")
    try:
        text = await transcribe(data, audio.filename or "annonce.webm", audio.content_type or "audio/webm")
    except TranscriptionError as exc:
        raise HTTPException(502, str(exc)) from exc
    if not text:
        raise HTTPException(422, "Rien n'a été compris dans l'enregistrement.")
    return await process_report(unit.strip().upper()[:40] or "ALPHA 2", text[:500])


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
