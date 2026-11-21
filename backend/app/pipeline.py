"""Orchestration : texte -> intention -> lieu -> événement -> diffusion."""

from .config import settings
from .geo import osm
from .geo.geometry import destination, haversine, project_on_line
from .geo.resolver import UNCERTAINTY_M, resolve
from .intent.extractor import extract_intent
from .state import hub, journal

MIN_CONFIDENCE = 0.6
ENEMY_SPACING_M = 2.5  # écart entre deux ennemis d'un groupe
MAX_GROUP = 12  # « 50 ennemis » : on n'affiche pas 50 losanges


async def process_report(unit: str, text: str, *, signed: dict | None = None) -> dict:
    """signed : annonce signée par le terminal de l'unité (voir security/signing.py).

    Un terminal ne parle que pour son unité : l'indicatif prononcé est alors ignoré
    (gardé dans l'intention, avec callsign_ignored si il diffère). Sans signature
    (tests, outils), l'indicatif prononcé l'emporte comme avant.
    """
    intent = await extract_intent(text, unit=unit, last_position=journal.last_position(unit))

    spoken = intent.get("callsign")
    if spoken and spoken != unit:
        if signed:
            intent["callsign_ignored"] = True
        else:
            unit = spoken
    if intent.get("subject") == "enemy" and intent.get("type") == "kill":
        eid = intent.get("enemy_id") or nearest_alive_enemy(unit)  # « j'ai tué l'ennemi » sans numéro
        return await kill_enemy(unit, eid, text, intent=intent, signed=signed)

    group = 1
    if intent.get("subject") == "enemy":
        # Ennemi déjà connu (« E3 est maintenant au sud du pont ») ou nouvel ennemi : E1, E2…
        known = journal.enemies()
        eid = intent.get("enemy_id")
        if eid in known and known[eid]["alive"]:
            intent["enemy_id"] = eid
        else:
            intent["enemy_id"] = journal.next_enemy_id()
            group = max(1, min(int(intent.get("count") or 1), MAX_GROUP))

    last = journal.last_position(unit)
    ref = last or (settings.zone_lat, settings.zone_lon)

    place = await resolve(intent, ref=ref, last=last)
    if place is None and intent.get("subject") == "enemy" and last and not intent.get("landmark"):
        # « 5 ennemis autour de moi », « ennemi devant moi » : sur la position de l'unité qui l'annonce.
        place = {"lat": last[0], "lon": last[1], "uncertainty_m": UNCERTAINTY_M["near"], "label": f"près de {unit}"}

    confirmed = place is not None and intent.get("confidence", 0) >= MIN_CONFIDENCE
    if group > 1 and confirmed:
        return await _enemy_group(unit, text, intent, place, group, signed)
    event = journal.append(
        unit=unit,
        transcript=text,
        intent=intent,
        lat=place["lat"] if place else None,
        lon=place["lon"] if place else None,
        uncertainty_m=place["uncertainty_m"] if place else None,
        resolved_landmark=place["label"] if place else None,
        status="confirmed" if confirmed else "pending_review",
        origin_node=settings.node_mode,
        signed=signed,
    )
    await hub.broadcast({"type": "event", "event": event})
    return event


async def _enemy_group(unit: str, text: str, intent: dict, place: dict, n: int, signed: dict | None) -> dict:
    """« 5 ennemis à l'Hôtel-Dieu » : 5 ennemis distincts (E2…E6), en cercle autour du lieu.

    Un événement par ennemi, pour pouvoir suivre et neutraliser chacun.
    """
    first = int(intent["enemy_id"][1:])
    ids = [f"E{first + k}" for k in range(n)]
    center, bearing = (place["lat"], place["lon"]), None
    # En vrai, un groupe est dans la rue, pas au centre d'un bâtiment : on le pose sur la rue
    # la plus proche (si elle est à moins de 150 m : les grands bâtiments ont leur centre loin de la rue), aligné le long de la rue.
    street = await osm.nearest_street(center)
    if street:
        snapped, seg_bearing = project_on_line(center, street[1])
        if haversine(center, snapped) <= 150:
            center, bearing = snapped, seg_bearing
            place = {**place, "label": f"{place['label']} · {street[0]}"}
    events = []
    for k, eid in enumerate(ids):
        offset = (k - (n - 1) / 2) * ENEMY_SPACING_M
        if bearing is not None:  # alignés dans la rue
            lat, lon = destination(center, bearing if offset >= 0 else bearing + 180, abs(offset))
        else:  # pas de rue trouvée : petit cercle serré
            lat, lon = destination(center, 360 * k / n, ENEMY_SPACING_M)
        ev = journal.append(
            unit=unit, transcript=text, intent={**intent, "enemy_id": eid, "count": None, "group_ids": ids},
            lat=lat, lon=lon, uncertainty_m=place["uncertainty_m"], resolved_landmark=place["label"],
            status="confirmed", origin_node=settings.node_mode, signed=signed,
        )
        events.append(ev)
        await hub.broadcast({"type": "event", "event": ev})
    return events[0]


class UnknownEnemy(Exception):
    pass


def nearest_alive_enemy(unit: str) -> str | None:
    """L'ennemi vivant le plus proche de l'unité (ou le seul, ou le dernier signalé)."""
    alive = {k: v for k, v in journal.enemies().items() if v["alive"] and v["lat"] is not None}
    if not alive:
        return None
    pos = journal.last_position(unit)
    if pos is None:
        return list(alive)[-1]
    return min(alive, key=lambda k: haversine(pos, (alive[k]["lat"], alive[k]["lon"])))


async def kill_enemy(unit: str, enemy_id: str | None, text: str, *, intent: dict | None = None,
                     signed: dict | None = None) -> dict:
    """Marque un ennemi comme neutralisé (événement « enemy_down », diffusé à tous)."""
    known = journal.enemies()
    if not enemy_id or enemy_id not in known or not known[enemy_id]["alive"]:
        raise UnknownEnemy(f"Ennemi {enemy_id or '?'} inconnu ou déjà neutralisé.")
    intent = {**(intent or {"confidence": 1.0, "source": "bouton"}), "subject": "enemy", "type": "kill",
              "enemy_id": enemy_id}
    e = known[enemy_id]
    event = journal.append(
        unit=unit, transcript=text, intent=intent, lat=None, lon=None, uncertainty_m=None,
        resolved_landmark=enemy_id, status="enemy_down", origin_node=settings.node_mode, signed=signed,
    )
    event["enemy_position"] = [e["lat"], e["lon"]]
    await hub.broadcast({"type": "event", "event": event})
    return event
