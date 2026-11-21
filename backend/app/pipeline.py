"""Orchestration : texte -> intention -> lieu -> événement -> diffusion."""

from .config import settings
from .geo import osm
from .geo.geometry import destination, haversine, project_on_line
from .geo.resolver import UNCERTAINTY_M, resolve
from .geo.routing import foot_route
from .intent.extractor import extract_intents
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
    intents = await extract_intents(text, unit=unit, last_position=journal.last_position(unit))
    # Ordre : neutralisations (là où l'unité est encore), puis sa propre position, puis les
    # ennemis vus (« ennemi à 200 m au nord » se calcule depuis la nouvelle position).
    def order(i: dict) -> int:
        if i.get("subject") == "enemy" and i.get("type") == "kill":
            return 0
        if i.get("type") in ("group_join", "group_leave"):  # se regrouper avant de bouger ensemble
            return 1
        return 2 if i.get("subject") != "enemy" else 3

    intents.sort(key=order)
    events: list[dict] = []
    error: Exception | None = None
    killed_at: list[tuple[float, float]] = []  # positions des ennemis neutralisés dans cette annonce
    for intent in intents:
        try:
            ev = await _process_fact(unit, text, intent, signed, killed_at)
            killed_at += [tuple(p) for p in ev.pop("killed_at", [])]
            companions = ev.pop("companions", [])
            events.append(ev)
            events.extend(companions)
        except UnknownEnemy as exc:  # un fait raté (« tue E9 » inconnu) n'annule pas les autres
            error = exc
    if not events:
        raise error or UnknownEnemy("Annonce non comprise.")
    first = events[0]
    if len(events) > 1:
        first = {**first, "also": events[1:]}
    return first


async def _process_fact(unit: str, text: str, intent: dict, signed: dict | None,
                        killed_at: list[tuple[float, float]] | None = None) -> dict:
    if intent.get("type") == "group_join":
        return await join_group(unit, intent.get("with_units") or [], text, signed=signed)
    if intent.get("type") == "group_leave":
        return await leave_group(unit, text, signed=signed)
    spoken = intent.get("callsign")
    if spoken and spoken != unit:
        if signed:
            intent["callsign_ignored"] = True
        else:
            unit = spoken
    if intent.get("subject") == "enemy" and intent.get("type") == "kill":
        return await kill_enemies(unit, select_targets(unit, intent), text, intent=intent, signed=signed)

    group = 1
    previous = None  # position d'avant, pour tracer le trajet par les rues
    if intent.get("subject") == "enemy":
        # Ennemi déjà connu (« E3 est maintenant au sud du pont ») ou nouvel ennemi : E1, E2…
        known = journal.enemies()
        eid = intent.get("enemy_id")
        if eid in known and known[eid]["alive"]:
            intent["enemy_id"] = eid
            if known[eid]["lat"] is not None:
                previous = (known[eid]["lat"], known[eid]["lon"])
        else:
            intent["enemy_id"] = journal.next_enemy_id()
            group = max(1, min(int(intent.get("count") or 1), MAX_GROUP))

    last = journal.last_position(unit)
    ref = last or (settings.zone_lat, settings.zone_lon)

    # « Je marche 60 m vers le sud » : une direction sans lieu = déplacement depuis sa position.
    if not intent.get("landmark") and intent.get("relation") in ("towards", "along", "at", "near", "unknown") \
            and (intent.get("direction") or intent.get("clock")):
        intent["relation"] = "offset"
    # Horaire militaire : 12h = droit devant (sens de marche de l'unité, nord si inconnu), 6h = derrière.
    if intent.get("clock"):
        heading = journal.last_heading(unit) or 0.0
        intent["bearing_deg"] = round((heading + 30 * (intent["clock"] % 12)) % 360, 1)
        intent["confidence"] = max(intent.get("confidence") or 0, 0.8)

    landmark = intent.get("landmark") or {}
    if killed_at and intent.get("subject") != "enemy" and landmark.get("kind") == "kill_site":
        place = None  # « je me suis déplacé à cette position » : là où étaient les ennemis neutralisés
    else:
        place = await resolve(intent, ref=ref, last=last)
    if place is None and killed_at and intent.get("subject") != "enemy":
        lat = sum(p[0] for p in killed_at) / len(killed_at)
        lon = sum(p[1] for p in killed_at) / len(killed_at)
        place = {"lat": lat, "lon": lon, "uncertainty_m": UNCERTAINTY_M["near"],
                 "label": "position des ennemis neutralisés"}
        intent["confidence"] = max(intent.get("confidence") or 0, MIN_CONFIDENCE)
    if place is None and intent.get("subject") == "enemy" and last and not intent.get("landmark"):
        # « 5 ennemis autour de moi », « ennemi devant moi » : sur la position de l'unité qui l'annonce.
        place = {"lat": last[0], "lon": last[1], "uncertainty_m": UNCERTAINTY_M["near"], "label": f"près de {unit}"}

    confirmed = place is not None and intent.get("confidence", 0) >= MIN_CONFIDENCE
    if intent.get("subject") != "enemy":
        previous = last
        if confirmed:  # pas pile sur une autre unité : quelques mètres d'écart
            lat, lon = spread((place["lat"], place["lon"]), unit)
            place = {**place, "lat": lat, "lon": lon}
    if confirmed and previous:
        # Trajet à pied par les rues entre l'ancienne et la nouvelle position (sinon trait droit).
        route = await foot_route(previous, (place["lat"], place["lon"]))
        if route:
            intent["route"] = [list(p) for p in route]
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
    if confirmed and intent.get("subject") != "enemy":
        # Coéquipiers nommés (« moi et Tom, on va à Châtelet ») et escouade : ils suivent, juste à côté.
        named = [n for n in (_resolve_unit_name(x) for x in intent.get("with_units") or []) if n]
        squad = journal.group_of(unit)
        names = list(dict.fromkeys(named + (squad[1] if squad else [])))
        if [n for n in names if n != unit]:
            event["companions"] = await _move_companions(unit, text, intent, place, names)
    return event


async def join_group(unit: str, with_units: list[str], text: str, *, signed: dict | None = None) -> dict:
    """« Moi et Tom 2, on est en groupe » : une escouade (nouvelle, ou celle d'un des membres)."""
    named = [n for n in (_resolve_unit_name(x) for x in with_units) if n and n != unit]
    if not named:
        raise GroupError("Avec qui ? Aucune unité de la carte n'a été reconnue.")
    groups = journal.groups()
    of = {m: g for g, ms in groups.items() for m in ms}
    gid = of.get(unit) or next((of[n] for n in named if n in of), None)
    if gid is None:
        nums = [int(g[1:]) for g in groups if g[1:].isdigit()]
        gid = f"G{max(nums, default=0) + 1}"
    members = list(dict.fromkeys([unit, *named]))
    event = journal.append(
        unit=unit, transcript=text,
        intent={"type": "group_join", "group_id": gid, "members": members, "confidence": 1.0},
        lat=None, lon=None, uncertainty_m=None, resolved_landmark=gid, status="group",
        origin_node=settings.node_mode, signed=signed,
    )
    await hub.broadcast({"type": "event", "event": event})
    return event


async def leave_group(unit: str, text: str, *, signed: dict | None = None) -> dict:
    squad = journal.group_of(unit)
    if not squad:
        raise GroupError(f"{unit} n'est dans aucune escouade.")
    event = journal.append(
        unit=unit, transcript=text,
        intent={"type": "group_leave", "group_id": squad[0], "confidence": 1.0},
        lat=None, lon=None, uncertainty_m=None, resolved_landmark=squad[0], status="group",
        origin_node=settings.node_mode, signed=signed,
    )
    await hub.broadcast({"type": "event", "event": event})
    return event


SPREAD_M = 6        # écart entre deux unités au même endroit (max 10 m)
MIN_GAP_M = 4       # en dessous, on considère qu'elles sont l'une sur l'autre


def spread(pt: tuple[float, float], unit: str, taken: list[tuple[float, float]] | None = None) -> tuple[float, float]:
    """Décale pt de quelques mètres s'il tombe sur une autre unité (ou un point de `taken`)."""
    others = [journal.last_position(u) for u in journal.units_on_map() if u != unit]
    others = [p for p in others if p] + (taken or [])
    if all(haversine(pt, o) >= MIN_GAP_M for o in others):
        return pt
    for k in range(8):
        cand = destination(pt, 45 * k, SPREAD_M)
        if all(haversine(cand, o) >= MIN_GAP_M for o in others):
            return cand
    return destination(pt, 0, 9)


def _resolve_unit_name(name: str) -> str | None:
    from .geo.resolver import _closest_unit
    from .intent.rules import normalize_callsign
    from .state import units as registry

    known = set(journal.units_on_map()) | {u["unit"] for u in registry.all()}
    raw = " ".join(str(name).upper().split())
    for cand in (raw, normalize_callsign(name)):
        if cand in known:
            return cand
    return _closest_unit(raw, sorted(known))


async def _move_companions(unit: str, text: str, intent: dict, place: dict, names: list[str]) -> list[dict]:
    names = [n for n in names if n and n != unit]
    taken = [(place["lat"], place["lon"])]
    events = []
    for comp in dict.fromkeys(names):
        pt = spread((place["lat"], place["lon"]), comp, taken)
        taken.append(pt)
        comp_intent = {**intent, "with_units": None, "moved_by": unit, "route": None}
        prev = journal.last_position(comp)
        if prev:
            route = await foot_route(prev, pt)
            if route:
                comp_intent["route"] = [list(p) for p in route]
        ev = journal.append(
            unit=comp, transcript=text, intent=comp_intent, lat=pt[0], lon=pt[1],
            uncertainty_m=place["uncertainty_m"], resolved_landmark=place["label"],
            status="confirmed", origin_node=settings.node_mode,
        )
        events.append(ev)
        await hub.broadcast({"type": "event", "event": ev})
    return events


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


class GroupError(UnknownEnemy):
    """Hérite d'UnknownEnemy pour être renvoyée en 409 comme les autres faits ratés."""


AROUND_ME_M = 300  # « les ennemis autour de moi »
SAME_GROUP_M = 50  # ennemis à moins de 50 m l'un de l'autre : même groupe


def select_targets(unit: str, intent: dict) -> list[str]:
    """Quels ennemis une annonce de neutralisation vise.

    - « E1, E2 et E3 neutralisés »     -> la liste dite ;
    - « tous les ennemis »             -> tous les vivants ;
    - « les ennemis autour de moi »    -> les vivants à moins de 300 m, sinon le groupe le plus proche ;
    - « les cinq ennemis »             -> les 5 vivants les plus proches ;
    - « j'ai tué l'ennemi »            -> le plus proche.
    """
    alive = {k: v for k, v in journal.enemies().items() if v["alive"]}
    ids = [i for i in (intent.get("enemy_ids") or []) if i] or ([intent["enemy_id"]] if intent.get("enemy_id") else [])
    if ids:
        return ids
    if not alive:
        return []
    pos = journal.last_position(unit)

    def dist(k: str) -> float:
        v = alive[k]
        return haversine(pos, (v["lat"], v["lon"])) if pos and v["lat"] is not None else float("inf")

    ordered = sorted(alive, key=dist) if pos else list(reversed(alive))  # sans position : les derniers signalés
    scope = intent.get("scope")
    if scope == "all":
        return ordered
    if scope == "around_me":
        near = [k for k in ordered if dist(k) <= AROUND_ME_M]
        if near:
            return near
        first = alive[ordered[0]]  # rien dans le rayon : le groupe le plus proche, en entier
        if first["lat"] is None:
            return ordered[:1]
        return [k for k in ordered if alive[k]["lat"] is not None
                and haversine((first["lat"], first["lon"]), (alive[k]["lat"], alive[k]["lon"])) <= SAME_GROUP_M]
    return ordered[: max(1, min(int(intent.get("count") or 1), len(ordered)))]


async def kill_enemy(unit: str, enemy_id: str | None, text: str, *, intent: dict | None = None,
                     signed: dict | None = None) -> dict:
    """Un seul ennemi (bouton « Neutraliser » sur le losange)."""
    return await kill_enemies(unit, [enemy_id] if enemy_id else [], text, intent=intent, signed=signed)


async def kill_enemies(unit: str, enemy_ids: list[str], text: str, *, intent: dict | None = None,
                       signed: dict | None = None) -> dict:
    """Marque des ennemis comme neutralisés : un événement « enemy_down » par ennemi, diffusé à tous.

    Les ennemis inconnus ou déjà neutralisés de la liste sont ignorés ; erreur si aucun ne reste.
    """
    known = journal.enemies()
    ids = list(dict.fromkeys(i for i in enemy_ids if i in known and known[i]["alive"]))
    if not ids:
        asked = ", ".join(enemy_ids) or "?"
        raise UnknownEnemy(f"Aucun ennemi actif à neutraliser ({asked} inconnu ou déjà neutralisé).")
    base = {**(intent or {"confidence": 1.0, "source": "bouton"}), "subject": "enemy", "type": "kill"}
    events = []
    for eid in ids:
        ev = journal.append(
            unit=unit, transcript=text,
            intent={**base, "enemy_id": eid, "count": None, "kill_ids": ids if len(ids) > 1 else None},
            lat=None, lon=None, uncertainty_m=None,
            resolved_landmark=eid, status="enemy_down", origin_node=settings.node_mode, signed=signed,
        )
        events.append(ev)
        await hub.broadcast({"type": "event", "event": ev})
    first = events[0]
    first["killed_at"] = [[known[i]["lat"], known[i]["lon"]] for i in ids if known[i]["lat"] is not None]
    return first
