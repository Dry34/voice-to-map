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
Reports may be in English or French. One report can contain SEVERAL facts ("4 ennemis à Notre-Dame, et moi je suis
à Châtelet" = an enemy sighting AND the unit's own position). Answer with ONE JSON object and nothing else:
{"facts": [FACT, FACT, ...]}   // one FACT per distinct fact, in the order spoken (usually 1, sometimes 2-3)
where each FACT is:
{
  "callsign": string | null,          // unit callsign if spoken ("Alpha 1", "Bravo deux"), written like "ALPHA 1"
  "subject": "self" | "enemy",        // who is being located: the reporting unit, or an enemy it sees
  "with_units": [string],             // only for self: OTHER units moving/standing together with the speaker
  "enemy_id": string | null,          // only for enemy: id of an ALREADY KNOWN enemy if named ("E3", "ennemi 3", "E 3" -> "E3")
  "enemy_ids": [string],              // only for kill: every enemy named ("E1, E2 et ennemi 3" -> ["E1","E2","E3"])
  "scope": "listed" | "nearest" | "around_me" | "all" | null,   // only for kill: which enemies
  "count": integer | null,            // only for enemy: how many ("trois ennemis" -> 3)
  "description": string | null,       // only for enemy: short French description ("2 hommes armés", "véhicule blindé")
  "type": "position" | "movement" | "kill" | "group_join" | "group_leave",   // "kill" only for enemy
  "relation": "at" | "near" | "north_of" | "south_of" | "east_of" | "west_of" | "towards" | "along" | "intersection" | "offset" | "unknown",
  "landmark": {"name": string, "kind": string} | null,
  "landmarks": [string, string],      // only for "intersection": the two street names
  "direction": "N" | "NE" | "E" | "SE" | "S" | "SW" | "W" | "NW" | null,   // for "along" and "offset"
  "clock": integer 1-12 | null,       // military clock position ("à 6h", "six heures", "at 3 o'clock") for "offset"
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
  enemy_id, enemy_ids ["E3"], scope "listed", relation "unknown". Any verb of killing counts: tué, abattu, neutralisé,
  éliminé, exterminé, détruit, killed, down.
  "J'ai éliminé ennemi 1, ennemi 2 et ennemi 3" -> kill, enemy_ids ["E1","E2","E3"], scope "listed".
  "J'ai tué l'ennemi" without a number -> kill, scope "nearest", count 1.
  "Je viens d'exterminer les cinq ennemis" -> kill, scope "nearest", count 5.
  "J'élimine les ennemis autour de moi" -> kill, scope "around_me". "Tous les ennemis sont neutralisés" -> kill, scope "all".
- After a kill in the same report, "je me suis déplacé à cette position", "j'y vais", "je suis sur place", "je vais là-bas",
  "je prends leur position" -> a SECOND fact: subject self, type movement, relation "at",
  landmark {"name": "kill site", "kind": "kill_site"}.
- Speech-to-text often garbles "les ennemis" ("les noms", "les nains", "l'énemi"): after a killing verb, read it as enemies.
  "Ennemi devant moi à 200 mètres" -> offset with distance 200 and direction null.
  Otherwise subject "self".
- Another unit can be the landmark: "je vais vers Bravo 2", "je veux aller vers Beta 2", "je rejoins Alpha 1",
  "je suis avec Charlie 3" -> landmark {"name": "BRAVO 2", "kind": "unit"} (name written like a callsign), relation
  "at" (with, rejoins, goes to) or "towards"; callsign stays null unless the speaker names THEIR OWN callsign first.
- "Moi et Tom 2, on va à Châtelet", "avec Epsilon on se déplace vers le Louvre", "Alpha 1 et moi sommes au pont" ->
  subject self, with_units ["TOM 2"] / ["EPSILON"] / ["ALPHA 1"] (names of the OTHER units, written like the units on
  the map), landmark = the place. Not the speaker's own unit.
- Squads: "Moi et Tom 2, on est en groupe", "on forme une escouade avec Epsilon et Alpha 1", "on se regroupe avec Tom"
  -> subject self, type "group_join", with_units [other units], landmark null. If a place is also said
  ("… et on va au Louvre"), add a SECOND fact for the movement.
  "Je quitte le groupe", "je me sépare de l'escouade", "je me barre du groupe" -> subject self, type "group_leave".
- Relative moves without a place: "je marche 60 m vers le sud", "je me déplace de 200 m au nord-est"
  -> subject self, relation "offset", direction, distance_m, landmark null.
- MILITARY CLOCK (this is a military tool): "ennemi à 6h", "contact 3 heures 200 mètres", "enemy at 6h00",
  "tango à midi" -> relation "offset", clock 6 / 3 / 6 / 12, direction null, distance_m if said. 12 = straight ahead,
  6 = behind. Never turn a clock position into a compass direction yourself. Confidence high (0.9).
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
        "with_units": [str(x) for x in (raw.get("with_units") or []) if x][:5],
        "enemy_id": _enemy_id(raw.get("enemy_id")),
        "enemy_ids": [e for e in (_enemy_id(x) for x in (raw.get("enemy_ids") or [])) if e][:20],
        "scope": raw.get("scope") if raw.get("scope") in ("listed", "nearest", "around_me", "all") else None,
        "type": raw.get("type") if raw.get("type") in ("position", "movement", "kill", "group_join", "group_leave") else "position",
        "relation": raw.get("relation") if raw.get("relation") in RELATIONS else "unknown",
        "landmark": None,
        "landmarks": [str(x) for x in (raw.get("landmarks") or [])][:2],
        "direction": raw.get("direction") if raw.get("direction") in DIRECTIONS else None,
        "distance_m": raw.get("distance_m") if isinstance(raw.get("distance_m"), (int, float)) else None,
        "clock": int(raw["clock"]) if isinstance(raw.get("clock"), (int, float)) and 1 <= raw["clock"] <= 12 else None,
        "confidence": float(raw.get("confidence") or 0.0),
        "source": "mistral",
    }
    lm = raw.get("landmark")
    if isinstance(lm, dict) and lm.get("name"):
        out["landmark"] = {"name": str(lm["name"]), "kind": str(lm.get("kind") or "named_place").lower()}
    return out


async def _ask_mistral(text: str, unit: str, last_position) -> list[dict]:
    from ..state import journal

    others = [u for u in journal.units_on_map() if u != unit]
    context = (f"Unit: {unit}. Last known position: {last_position or 'unknown'}. "
               f"Other units on the map (fix misheard names to these): {', '.join(others) or 'none'}.")
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
    content = json.loads(r.json()["choices"][0]["message"]["content"])
    facts = content.get("facts") if isinstance(content, dict) else None
    if not isinstance(facts, list) or not facts:
        facts = [content]  # ancien format : un seul objet
    return [_normalize(f) for f in facts[:5] if isinstance(f, dict)]


async def extract_intents(text: str, unit: str, last_position=None) -> list[dict]:
    """Une ou plusieurs intentions (« 4 ennemis à Notre-Dame et moi je suis à Châtelet » -> 2)."""
    if settings.mistral_api_key:
        try:
            intents = await _ask_mistral(text, unit, last_position)
            if intents:
                return intents
        except Exception as exc:  # réseau, quota, JSON invalide : on bascule sur les règles
            log.warning("Mistral indisponible, repli sur les règles : %s", exc)
    return rules.parse_many(text)


async def extract_intent(text: str, unit: str, last_position=None) -> dict:
    return (await extract_intents(text, unit, last_position))[0]
