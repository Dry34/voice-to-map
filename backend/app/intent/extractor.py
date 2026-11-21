"""Extraction d'intention : Mistral en JSON, repli sur les règles. Responsable : C."""

import asyncio
import json
import logging
import re

import httpx

from ..config import settings
from . import rules

log = logging.getLogger(__name__)

MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"

RELATIONS = {
    "at", "near", "north_of", "south_of", "east_of", "west_of",
    "towards", "along", "intersection", "offset", "unknown",
}
DIRECTIONS = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}

SYSTEM_PROMPT = """You convert short radio status reports from field units into JSON.
Reports may be in English or French. Answer with ONE JSON object and nothing else:
{
  "callsign": string | null,          // unit callsign if spoken ("Alpha 1", "Bravo deux"), written like "ALPHA 1"
  "subject": "self" | "enemy",        // who is being located: the reporting unit, or an enemy it sees
  "enemy_id": string | null,          // only for enemy: id of an ALREADY KNOWN enemy if named ("E3", "ennemi 3", "E 3" -> "E3")
  "count": integer | null,            // only for enemy: how many ("trois ennemis" -> 3)
  "description": string | null,       // only for enemy: short French description ("2 hommes armés", "véhicule blindé")
  "type": "position" | "movement" | "kill",   // "kill" only for enemy: it was neutralised
  "relation": "at" | "near" | "north_of" | "south_of" | "east_of" | "west_of" | "towards" | "along" | "intersection" | "offset" | "unknown",
  "landmark": {"name": string, "kind": string} | null,
  "landmarks": [string, string],      // only for "intersection": the two street names
  "direction": "N" | "NE" | "E" | "SE" | "S" | "SW" | "W" | "NW" | null,   // for "along" and "offset"
  "distance_m": number | null,
  "confidence": number between 0 and 1
}
Rules:
- subject "enemy" when the report is about hostiles the unit sees: "contact", "ennemi(s)", "hostile(s)", "enemy spotted",
  "je vois deux types armés", "tango". Then relation/landmark describe WHERE THE ENEMY IS, not where the unit is.
  "Contact, trois ennemis au nord de l'église" -> subject enemy, count 3, relation north_of, landmark church.
  "Ennemi à 200 mètres au nord-est" (relative to the reporting unit, no landmark) -> subject enemy, relation offset,
  direction NE, distance_m 200, landmark null. Without a distance, distance_m null.
  "E3 au sud du pont", "l'ennemi 3 bouge vers la gare" -> subject enemy, enemy_id "E3" (a known enemy moved).
  "E3 neutralisé", "ennemi 2 abattu", "cible E4 détruite", "E1 down", "j'ai tué l'ennemi 1" -> subject enemy, type "kill",
  enemy_id, relation "unknown". "J'ai tué l'ennemi" without a number -> type "kill", enemy_id null.
  "Ennemi devant moi à 200 mètres" -> offset with distance 200 and direction null.
  Otherwise subject "self".
- Another unit can be the landmark: "je vais vers Bravo 2", "je veux aller vers Beta 2", "je rejoins Alpha 1",
  "je suis avec Charlie 3" -> landmark {"name": "BRAVO 2", "kind": "unit"} (name written like a callsign), relation
  "at" (with, rejoins, goes to) or "towards"; callsign stays null unless the speaker names THEIR OWN callsign first.
- landmark.kind is a generic English noun: "church", "pharmacy", "school", "station", "hospital", "park", "bridge", "street", or "named_place" for a proper name.
- For a generic landmark ("the church"), name = kind = "church". For a street, kind = "street" and name = the street name as said ("35th Street").
- "I am south of the church" -> type position, relation south_of.
- "I moved towards 35th street" -> type movement, relation towards.
- callsign: NATO alphabet word + number, digits only ("Alpha un" -> "ALPHA 1"). null if no callsign is spoken.
  "Alpha 1 et aux Invalides" is a transcription of "Alpha 1 est aux Invalides": callsign "ALPHA 1".
- Reports come from speech-to-text and may contain transcription errors. Fix obvious ones in
  landmark and street names ("Atour Eiffel" -> "Tour Eiffel", "suit of" -> "south of"), and give the full
  well-known name of the place when a part was lost or misheard: "la Dame de Paris" -> "Notre-Dame de Paris",
  "Bir Hakeem" -> "Bir-Hakeim", "au HAL" -> "Les Halles", "Tine Paris" -> "Tour Eiffel" only if clearly meant.
- Keep proper names in the local language of the place (in Paris: "Tour Eiffel", "Musée du Louvre",
  "rue de Rivoli"), because they are looked up in OpenStreetMap.
- If you cannot tell, relation "unknown" and a low confidence."""


def _enemy_id(value) -> str | None:
    m = re.search(r"(\d+)", str(value or ""))
    return f"E{int(m.group(1))}" if m else None


def _normalize(raw: dict) -> dict:
    out = {
        "callsign": rules.normalize_callsign(raw.get("callsign")),
        "subject": "enemy" if raw.get("subject") == "enemy" else "self",
        "count": int(raw["count"]) if isinstance(raw.get("count"), (int, float)) and raw["count"] > 0 else None,
        "description": str(raw["description"])[:80] if raw.get("description") else None,
        "enemy_id": _enemy_id(raw.get("enemy_id")),
        "type": raw.get("type") if raw.get("type") in ("position", "movement", "kill") else "position",
        "relation": raw.get("relation") if raw.get("relation") in RELATIONS else "unknown",
        "landmark": None,
        "landmarks": [str(x) for x in (raw.get("landmarks") or [])][:2],
        "direction": raw.get("direction") if raw.get("direction") in DIRECTIONS else None,
        "distance_m": raw.get("distance_m") if isinstance(raw.get("distance_m"), (int, float)) else None,
        "confidence": float(raw.get("confidence") or 0.0),
        "source": "mistral",
    }
    lm = raw.get("landmark")
    if isinstance(lm, dict) and lm.get("name"):
        out["landmark"] = {"name": str(lm["name"]), "kind": str(lm.get("kind") or "named_place").lower()}
    return out


async def _ask_mistral(text: str, unit: str, last_position) -> dict:
    context = f"Unit: {unit}. Last known position: {last_position or 'unknown'}."
    body = {
        "model": settings.mistral_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{context}\nReport: {text}"},
        ],
    }
    headers = {"Authorization": f"Bearer {settings.mistral_api_key}"}
    async with httpx.AsyncClient(timeout=8) as client:
        # Une seule nouvelle tentative, et seulement sur erreur serveur passagère (5xx).
        # Sur 429 (quota), on bascule tout de suite sur les règles pour garder la carte réactive.
        r = await client.post(MISTRAL_URL, json=body, headers=headers)
        if r.status_code >= 500:
            await asyncio.sleep(0.4)
            r = await client.post(MISTRAL_URL, json=body, headers=headers)
        r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return _normalize(json.loads(content))


async def extract_intent(text: str, unit: str, last_position=None) -> dict:
    if settings.mistral_api_key:
        try:
            return await _ask_mistral(text, unit, last_position)
        except Exception as exc:  # réseau, quota, JSON invalide : on bascule sur les règles
            log.warning("Mistral indisponible, repli sur les règles : %s", exc)
    return rules.parse(text)
