"""Routes HTTP et WebSocket. Responsable : B."""

import json
import re

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .. import sim
from ..config import settings
from ..intent.rules import normalize_callsign
from ..pipeline import UnknownEnemy, kill_enemy, leave_group, process_report
from ..security.signing import SignatureError, verify_request
from ..speech.stt import TranscriptionError, transcribe
from ..state import hub, journal, units

MAX_AUDIO_BYTES = 5 * 1024 * 1024  # ~ plusieurs minutes d'audio compressé, largement assez

router = APIRouter()


UNIT_RE = re.compile(r"^[A-Z0-9][A-Z0-9 -]{0,39}$")


def clean_unit(raw: str) -> str:
    """« alpha1 », « Alpha-1 » -> « ALPHA 1 ». 400 si le nom contient autre chose."""
    unit = normalize_callsign(raw) or " ".join(raw.upper().split())
    if not UNIT_RE.match(unit):
        raise HTTPException(400, "Nom d'unité invalide (lettres, chiffres, espaces, tirets).")
    return unit


class Signed(BaseModel):
    unit: str = Field(min_length=1, max_length=40)
    ts: int
    nonce: str = Field(min_length=8, max_length=64)
    signature: str = Field(min_length=1, max_length=200)


class ClaimIn(Signed):
    public_key: str = Field(min_length=1, max_length=100)


class ReportIn(Signed):
    text: str = Field(min_length=1, max_length=500)


def check_signed(action: str, unit: str, ts: int, nonce: str, signature: str, content: bytes,
                 public_key: str | None = None) -> dict:
    """Vérifie que la requête vient du terminal qui tient l'unité. Renvoie les champs à journaliser."""
    key = public_key or units.key_of(unit)
    if key is None:
        raise HTTPException(403, f"{unit} n'est réservée par aucun terminal.")
    try:
        message = verify_request(action=action, unit=unit, ts_ms=ts, nonce=nonce, content=content,
                                 public_key_b64=key, signature_b64=signature)
    except SignatureError as exc:
        raise HTTPException(401, str(exc)) from exc
    if public_key is None:  # annonce, neutralisation… du terminal qui tient l'unité : il est actif
        hub.touch(unit)
    return {"signed_message": message, "signature": signature, "public_key": key}


@router.get("/api/config")
def get_config() -> dict:
    return {
        "zone_name": settings.zone_name,
        "center": [settings.zone_lat, settings.zone_lon],
        "node_mode": settings.node_mode,
        "llm": "mistral" if settings.mistral_api_key else "rules",
        "sim": sim.enabled,
    }


class SimIn(BaseModel):
    on: bool


@router.post("/api/sim")
async def set_sim(body: SimIn) -> dict:
    """Active ou coupe la marche autonome des ennemis (démo)."""
    await sim.set_enabled(body.on)
    return {"on": sim.enabled}


@router.get("/api/units")
def list_units() -> list[dict]:
    return units.all()


@router.post("/api/units/claim")
async def claim_unit(body: ClaimIn) -> dict:
    """Le terminal prouve qu'il détient la clé privée en signant sa demande."""
    # La signature porte sur le nom tel que tapé ; on renvoie le nom normalisé
    # (« alpha1 » -> « ALPHA 1 »), que le terminal utilisera ensuite pour signer.
    check_signed("claim", body.unit, body.ts, body.nonce, body.signature,
                 body.public_key.encode("utf-8"), public_key=body.public_key)
    unit = clean_unit(body.unit)
    if units.claim(unit, body.public_key):
        hub.touch(unit)
        await hub.broadcast({"type": "units"})  # nouvelle unité : les cartes rechargent les couleurs
        return {"unit": unit, "taken_over": False}
    # Tenue par une autre clé. Si aucun onglet de cette unité n'est connecté (crash, onglet
    # fermé, autre navigateur), on la reprend ; sinon elle reste protégée.
    if hub.online(unit):
        raise HTTPException(409, f"{unit} est utilisée par un terminal connecté en ce moment.")
    units.transfer(unit, body.public_key)
    hub.touch(unit)
    # L'onglet qui tenait l'unité (s'il revient) sait qu'il l'a perdue.
    await hub.broadcast({"type": "unit_owner", "unit": unit, "public_key": body.public_key})
    return {"unit": unit, "taken_over": True}


@router.post("/api/units/release")
async def release_unit(body: Signed) -> dict:
    """Le terminal quitte son unité : elle redevient libre et son point disparaît de la carte.

    Le journal n'efface rien : on ajoute un événement « left », que les cartes appliquent
    en retirant le marqueur et le tracé de l'unité.
    """
    unit = clean_unit(body.unit)
    signed = check_signed("release", unit, body.ts, body.nonce, body.signature, b"")
    units.release(unit)
    event = journal.append(unit=unit, transcript=None, intent={"type": "left", "subject": "self"},
                           lat=None, lon=None, uncertainty_m=None, resolved_landmark=None,
                           status="left", origin_node=settings.node_mode, signed=signed)
    await hub.broadcast({"type": "event", "event": event})
    return {"unit": unit, "released": True, "event": event}


class KillIn(Signed):
    enemy_id: str = Field(pattern=r"^E\d{1,4}$")


@router.get("/api/groups")
def list_groups() -> dict:
    return journal.groups()


@router.post("/api/groups/leave")
async def post_leave_group(body: Signed) -> dict:
    unit = clean_unit(body.unit)
    signed = check_signed("group_leave", unit, body.ts, body.nonce, body.signature, b"")
    try:
        return await leave_group(unit, f"{unit} quitte l'escouade", signed=signed)
    except UnknownEnemy as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/enemies")
def list_enemies() -> dict:
    return journal.enemies()


@router.post("/api/enemies/kill")
async def post_kill(body: KillIn) -> dict:
    """Bouton « Neutraliser » sur un ennemi : signé par le terminal qui le déclare."""
    unit = clean_unit(body.unit)
    signed = check_signed("kill", unit, body.ts, body.nonce, body.signature, body.enemy_id.encode("utf-8"))
    try:
        ev = await kill_enemy(unit, body.enemy_id, f"{body.enemy_id} neutralisé", signed=signed)
        ev.pop("killed_at", None)
        return ev
    except UnknownEnemy as exc:
        raise HTTPException(409, str(exc)) from exc


async def _process(unit: str, text: str, signed: dict) -> dict:
    try:
        return await process_report(unit, text, signed=signed)
    except UnknownEnemy as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/reports")
async def post_report(report: ReportIn) -> dict:
    unit = clean_unit(report.unit)
    signed = check_signed("report", unit, report.ts, report.nonce, report.signature, report.text.encode("utf-8"))
    return await _process(unit, report.text.strip(), signed)


@router.post("/api/reports/audio")
async def post_audio_report(
    unit: str = Form(...), ts: int = Form(...), nonce: str = Form(...), signature: str = Form(...),
    audio: UploadFile = File(...),
) -> dict:
    data = await audio.read()
    if not data:
        raise HTTPException(400, "Enregistrement vide.")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Enregistrement trop long.")
    unit = clean_unit(unit)
    signed = check_signed("audio", unit, ts, nonce, signature, data)
    try:
        text = await transcribe(data, audio.filename or "annonce.webm", audio.content_type or "audio/webm")
    except TranscriptionError as exc:
        raise HTTPException(502, str(exc)) from exc
    if not text:
        raise HTTPException(422, "Rien n'a été compris dans l'enregistrement.")
    return await _process(unit, text[:500], signed)


@router.get("/api/events")
def list_events(unit: str | None = None) -> list[dict]:
    return journal.all(unit)


@router.get("/api/integrity")
def integrity() -> dict:
    return journal.verify()


@router.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    await hub.connect(ws)
    # Depuis la dernière remise à zéro seulement : la carte et l'historique repartent de là
    # (le journal complet reste vérifiable avec /api/integrity et lisible avec /api/events).
    await ws.send_json({"type": "snapshot", "events": journal.since_clear()})
    try:
        while True:
            # Seul message attendu : {"type": "presence", "unit": "ALPHA 1" | null}
            try:
                msg = json.loads(await ws.receive_text())
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("type") == "presence":
                u = msg.get("unit")
                hub.set_presence(ws, u if isinstance(u, str) and units.key_of(u) else None)
    except WebSocketDisconnect:
        hub.disconnect(ws)
