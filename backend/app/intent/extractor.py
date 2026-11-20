"""Extraction d'intention : Mistral en JSON, repli sur les règles. Responsable : C."""

import json
import logging

import httpx

from ..config import settings
from . import rules

log = logging.getLogger(__name__)

MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"

RELATIONS = {
    "at", "near", "north_of", "south_of", "east_of", "west_of",
    "towards", "along", "intersection", "unknown",
}

SYSTEM_PROMPT = """You convert short radio status reports from field units into JSON.
Reports may be in English or French. Answer with ONE JSON object and nothing else:
{
  "type": "position" | "movement",
  "relation": "at" | "near" | "north_of" | "south_of" | "east_of" | "west_of" | "towards" | "along" | "intersection" | "unknown",
  "landmark": {"name": string, "kind": string} | null,
  "landmarks": [string, string],      // only for "intersection": the two street names
  "direction": "N" | "S" | "E" | "W" | null,   // only for "along"
  "distance_m": number | null,
  "confidence": number between 0 and 1
}
Rules:
- landmark.kind is a generic English noun: "church", "pharmacy", "school", "station", "hospital", "park", "bridge", "street", or "named_place" for a proper name.
- For a generic landmark ("the church"), name = kind = "church". For a street, kind = "street" and name = the street name as said ("35th Street").
- "I am south of the church" -> type position, relation south_of.
- "I moved towards 35th street" -> type movement, relation towards.
- If you cannot tell, relation "unknown" and a low confidence."""


def _normalize(raw: dict) -> dict:
    out = {
        "type": raw.get("type") if raw.get("type") in ("position", "movement") else "position",
        "relation": raw.get("relation") if raw.get("relation") in RELATIONS else "unknown",
        "landmark": None,
        "landmarks": [str(x) for x in (raw.get("landmarks") or [])][:2],
        "direction": raw.get("direction") if raw.get("direction") in ("N", "S", "E", "W") else None,
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
