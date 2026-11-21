"""Orchestration : texte -> intention -> lieu -> événement -> diffusion."""

from .config import settings
from .geo import osm
from .geo.geometry import DIRECTION_BEARINGS, bearing_between, destination, haversine, project_on_line
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
    # Les faits sont traités dans l'ordre où ils sont dits : « trois ennemis devant moi, du coup
    # je recule » place les ennemis là où l'unité était quand elle les a vus, puis la fait reculer.
    events: list[dict] = []
    error: Exception | None = None
    killed_at: list[tuple[float, float]] = []  # positions des ennemis neutralisés dans cette annonce
    # « Je remonte 300 m et je vois une squad ennemie » : l'analyse recopie parfois les 300 m sur
    # l'ennemi. Une distance d'ennemi identique à celle d'un déplacement de l'unité est ignorée.
    own = {i.get("distance_m") for i in intents if i.get("subject") != "enemy" and i.get("distance_m")}
    for i in intents:
        if i.get("subject") == "enemy" and i.get("distance_m") in own:
            i["distance_m"] = None
    for intent in intents:
        try:
            if intent.get("subject") == "enemy" and intent.get("on_route"):
                # « Sur le chemin, j'ai croisé 4 ennemis » : le long du trajet que l'unité vient de faire.
                route = journal.last_route(unit)
                if route:
                    events.append(await _enemies_on_route(unit, text, intent, route, signed))
                    continue
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
    if intent.get("type") == "facing":
        return await set_facing(unit, intent, text, signed=signed)
    if intent.get("type") == "confirm":
        return await confirm_pending(unit, None, text, signed=signed)
    if intent.get("type") == "sos":
        return await raise_alert(unit, text, intent.get("description"), signed=signed)
    if intent.get("type") == "sos_clear":
        return await clear_alert(unit, text, signed=signed)
    solo_left = None
    if intent.get("solo") and journal.group_of(unit):
        # « Je pars tout seul » : on quitte l'escouade, puis seul l'auteur bouge.
        solo_left = await leave_group(unit, text, signed=signed)
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

    # Direction mal rangée par l'analyse : « south » pris pour un lieu, ou direction oubliée
    # (« je continue 40 m vers le sud ») -> on la relit dans la phrase.
    lm = intent.get("landmark") or {}
    if lm.get("kind") == "direction" or (lm.get("name") or "").lower() in WORD_DIRECTIONS:
        intent["direction"] = intent.get("direction") or WORD_DIRECTIONS.get((lm.get("name") or "").lower())
        intent["landmark"] = None
    if intent.get("subject") != "enemy" and not intent.get("direction") and not intent.get("landmark") \
            and intent.get("relation") in ("offset", "towards", "along", "unknown"):
        intent["direction"] = direction_in(text)

    # « On va prendre la rue Étienne-Marcel » (sans direction) : on rejoint la rue. Si l'annonce
    # continue (« …et ensuite le centre de santé »), le trajet suivant part de là.
    if intent.get("relation") == "along" and not intent.get("direction") \
            and (intent.get("landmark") or {}).get("kind") == "street":
        intent["relation"] = "at"

    # « Je suis la route et tourne à droite au prochain bloc » : un pâté de maisons dans le sens
    # de marche, puis on tourne ; la flèche tourne avec. « Je prends la rue de Rivoli et tourne à
    # droite » : d'abord jusqu'à la rue, dans le sens de la rue le plus proche de sa direction.
    if intent.get("relation") == "turn" and intent.get("subject") == "enemy":
        intent["relation"] = "unknown"  # où vont les ennemis ensuite, on ne le sait pas
    if intent.get("relation") == "turn" and not intent.get("turn"):
        # Pas de « droite / gauche / tout droit / demi-tour » dit : « prendre la rue X » = passer par la rue X.
        intent["relation"] = "at" if (intent.get("landmark") or {}).get("name") else "unknown"
    if intent.get("relation") == "turn" and last:
        heading = journal.last_heading(unit) or 0.0
        start = last
        street_name = (intent.get("landmark") or {}).get("name")
        if street_name:
            found = await osm.find_street(street_name, last, settings.zone_radius_m)
            if found:
                best = min(((project_on_line(last, line), line) for line in found[1] if len(line) >= 2),
                           key=lambda x: haversine(last, x[0][0]), default=None)
                if best:
                    (entry, seg), _ = best
                    # sens de la rue le plus proche de la direction actuelle
                    diff = abs((seg - heading + 180) % 360 - 180)
                    heading = seg if diff <= 90 else (seg + 180) % 360
                    start = entry
        turn = {"right": 90, "left": -90, "straight": 0, "back": 180}.get(intent.get("turn") or "straight", 0)
        if intent.get("turn") == "back":
            end = destination(start, heading + 180, intent.get("distance_m") or BLOCK_M)
        elif turn == 0:
            end = destination(start, heading, intent.get("distance_m") or BLOCK_M * (intent.get("blocks") or 1))
        else:
            corner = destination(start, heading, BLOCK_M * (intent.get("blocks") or 1))
            end = destination(corner, heading + turn, intent.get("distance_m") or AFTER_TURN_M)
        words = {"right": "à droite", "left": "à gauche", "straight": "tout droit", "back": "demi-tour"}
        intent.update(relation="offset", bearing_deg=round(bearing_between(last, end), 1),
                      distance_m=round(haversine(last, end)), confidence=max(intent.get("confidence") or 0, 0.8),
                      turn_label="demi-tour" if intent.get("turn") == "back"
                      else f"{words.get(intent.get('turn'), 'tout droit')} après {intent.get('blocks') or 1} bloc")
        intent["heading_after"] = round((heading + turn) % 360, 1)

    # « Je marche 60 m vers le sud » : une direction sans lieu = déplacement depuis sa position.
    if not intent.get("landmark") and intent.get("relation") in ("towards", "along", "at", "near", "unknown") \
            and (intent.get("direction") or intent.get("clock")):
        intent["relation"] = "offset"
    # « Je monte de 200 m », « j'avance de 50 m » : une distance sans direction = droit devant.
    if intent.get("subject") != "enemy" and intent.get("relation") == "offset" and intent.get("distance_m") \
            and not intent.get("direction") and not intent.get("clock") and intent.get("bearing_deg") is None:
        intent["clock"] = 12
    # Ennemi vu sans distance : en ville, on ne voit pas à travers les immeubles -> 50 m devant.
    if intent.get("subject") == "enemy" and intent.get("relation") == "offset" and not intent.get("distance_m"):
        intent["distance_m"] = ENEMY_SIGHT_M
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
        if confirmed:
            # L'unité marche par les rues et s'arrête SUR la rue (jamais dans un bâtiment),
            # à quelques mètres des autres unités si elles sont au même endroit.
            route, end = await walk_to(unit, last, (place["lat"], place["lon"]))
            if route:
                intent["route"] = [list(p) for p in route]
            place = {**place, "lat": end[0], "lon": end[1]}
            # Où regarde l'unité : son sens de marche (sinon elle garde sa direction d'avant).
            if intent.get("heading_after") is not None:  # après un virage : la nouvelle direction de la rue
                intent["heading_deg"] = intent["heading_after"]
            elif last and haversine(last, end) > 5:
                intent["heading_deg"] = round(bearing_between(last, end), 1)
            elif journal.last_heading(unit) is not None:
                intent["heading_deg"] = journal.last_heading(unit)
    elif confirmed and previous:
        # Ennemi déplacé par une annonce : trajet observé, par les rues.
        route = await foot_route(previous, (place["lat"], place["lon"]))
        if route:
            intent["route"] = [list(p) for p in route]
    if group == 1 and confirmed and intent.get("subject") == "enemy" and intent.get("relation") == "offset":
        # Un ennemi vu est dans la rue, pas au milieu d'un pâté de maisons.
        street = await osm.nearest_street((place["lat"], place["lon"]))
        if street:
            snapped, _ = project_on_line((place["lat"], place["lon"]), street[1])
            if haversine((place["lat"], place["lon"]), snapped) <= 40:
                place = {**place, "lat": snapped[0], "lon": snapped[1], "label": f"{place['label']} · {street[0]}"}
    if group > 1 and confirmed:
        return await _enemy_group(unit, text, intent, place, group, signed)
    names: list[str] = []
    if confirmed and intent.get("subject") != "enemy" and not intent.get("solo"):
        # Coéquipiers nommés (« moi et Tom, on va à Châtelet ») et escouade : ils suivent, juste à côté.
        named = [n for n in (_resolve_unit_name(x) for x in intent.get("with_units") or []) if n]
        squad = journal.group_of(unit)
        names = [n for n in dict.fromkeys(named + (squad[1] if squad else [])) if n != unit]
        if names:
            intent["squad"] = squad[0] if squad else None
            intent["squad_members"] = [unit, *names]
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
    if names:
        event["companions"] = await _move_companions(unit, text, intent, place, names)
    if solo_left:
        event["companions"] = [solo_left, *event.get("companions", [])]
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


WORD_DIRECTIONS = {"north": "N", "nord": "N", "south": "S", "sud": "S", "east": "E", "est": "E", "west": "W",
                   "ouest": "W", "nord-est": "NE", "northeast": "NE", "nord-ouest": "NW", "northwest": "NW",
                   "sud-est": "SE", "southeast": "SE", "sud-ouest": "SW", "southwest": "SW"}


def direction_in(text: str) -> str | None:
    """Direction dite dans la phrase (« vers le sud », « au nord-est », « to the west »).

    Exige une préposition : « est » seul est le plus souvent le verbe (« Alpha 1 est aux Invalides »).
    """
    import re
    words = sorted(WORD_DIRECTIONS, key=len, reverse=True)  # « nord-est » avant « nord »
    m = re.search(r"\b(?:vers l[ea']\s?|vers|au|à l'|a l'|direction|plein|to the|towards|toward|heading)\s*("
                  + "|".join(re.escape(w) for w in words) + r")\b", text.lower())
    return WORD_DIRECTIONS[m.group(1)] if m else None


ENEMY_SIGHT_M = 50  # portée de vue dans une rue, pour un ennemi vu sans distance
BLOCK_M = 100       # un pâté de maisons à Paris
AFTER_TURN_M = 50   # distance parcourue après le virage
SPREAD_M = 6        # écart entre deux unités au même endroit (max 10 m)
MIN_GAP_M = 4       # en dessous, on considère qu'elles sont l'une sur l'autre


MAX_SNAP_M = 150  # au-delà, l'arrivée sur la rue est trop loin du lieu annoncé : on garde le lieu


async def walk_to(unit: str, start: tuple[float, float] | None, target: tuple[float, float],
                  taken: list[tuple[float, float]] | None = None) -> tuple[list | None, tuple[float, float]]:
    """Itinéraire à pied jusqu'à target, arrivée sur la rue, écartée des autres unités.

    Renvoie (itinéraire ou None, point d'arrivée).
    """
    from .geo.routing import route_length

    if not start:
        return None, spread(target, unit, taken)
    route = await foot_route(start, target, snap_end=True)
    if not route:
        return None, spread(target, unit, taken)
    if haversine(route[-1], target) > MAX_SNAP_M:  # lieu loin de toute rue praticable (grand parc…)
        full = await foot_route(start, target)
        return (full, target) if full else (None, spread(target, unit, taken))
    # Unité déjà à l'arrivée : on s'arrête quelques mètres avant, le long de la rue.
    others = [p for p in (journal.last_position(u) for u in journal.units_on_map() if u != unit) if p] + (taken or [])
    pts = [tuple(p) for p in route]
    for _ in range(4):
        end = pts[-1]
        if all(haversine(end, o) >= MIN_GAP_M for o in others) or route_length(pts) <= SPREAD_M:
            break
        left = SPREAD_M
        while len(pts) > 2 and haversine(pts[-2], pts[-1]) <= left:
            left -= haversine(pts[-2], pts[-1])
            pts.pop()
        a, b = pts[-2], pts[-1]
        seg = haversine(a, b)
        t = 1 - left / seg if seg else 1
        pts[-1] = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return pts, pts[-1]


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
        comp_intent = {**intent, "with_units": None, "moved_by": unit, "route": None}
        prev = journal.last_position(comp)
        route, pt = await walk_to(comp, prev, (place["lat"], place["lon"]), taken)
        taken.append(pt)
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


async def _enemies_on_route(unit: str, text: str, intent: dict, route: list, signed: dict | None) -> dict:
    """Ennemis répartis le long du trajet (entre 25 % et 75 %), un par équipe annoncée."""
    from .geo.routing import route_length

    n = max(1, min(int(intent.get("count") or 1), MAX_GROUP))
    pts = [tuple(p) for p in route]
    total = route_length(pts)

    def at(frac: float) -> tuple[float, float]:
        left = total * frac
        for a, b in zip(pts, pts[1:]):
            seg = haversine(a, b)
            if left <= seg:
                t = left / seg if seg else 0
                return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
            left -= seg
        return pts[-1]

    first = int(journal.next_enemy_id()[1:])
    ids = [f"E{first + k}" for k in range(n)]
    events = []
    for k, eid in enumerate(ids):
        frac = 0.5 if n == 1 else 0.25 + 0.5 * k / (n - 1)
        lat, lon = at(frac)
        ev = journal.append(
            unit=unit, transcript=text,
            intent={**intent, "enemy_id": eid, "count": None, "group_ids": ids if n > 1 else None},
            lat=lat, lon=lon, uncertainty_m=80, resolved_landmark="sur le trajet",
            status="confirmed", origin_node=settings.node_mode, signed=signed,
        )
        events.append(ev)
        await hub.broadcast({"type": "event", "event": ev})
    return events[0]


ALERT_RADIUS_M = 3000  # unités prévenues quand une unité demande du renfort (1,5 km était trop juste)


async def raise_alert(unit: str, text: str, reason: str | None = None, *, signed: dict | None = None) -> dict:
    """« À l'aide », « besoin de renfort » : alerte envoyée aux unités à moins de 3 km."""
    pos = journal.last_position(unit)
    if pos is None:
        raise UnknownEnemy("Position inconnue : annoncez d'abord où vous êtes, puis redemandez du renfort.")
    recipients = []
    for other in journal.units_on_map():
        p = journal.last_position(other)
        if other != unit and p and haversine(pos, p) <= ALERT_RADIUS_M:
            recipients.append({"unit": other, "distance_m": round(haversine(pos, p))})
    recipients.sort(key=lambda r: r["distance_m"])
    event = journal.append(
        unit=unit, transcript=text,
        intent={"type": "sos", "subject": "self", "reason": reason, "recipients": recipients,
                "radius_m": ALERT_RADIUS_M, "confidence": 1.0},
        lat=pos[0], lon=pos[1], uncertainty_m=None, resolved_landmark=None, status="alert",
        origin_node=settings.node_mode, signed=signed,
    )
    await hub.broadcast({"type": "event", "event": event})
    return event


async def clear_alert(unit: str, text: str, *, signed: dict | None = None) -> dict:
    """« Situation sécurisée » : fin de l'alerte de l'unité."""
    event = journal.append(
        unit=unit, transcript=text, intent={"type": "sos_clear", "subject": "self", "confidence": 1.0},
        lat=None, lon=None, uncertainty_m=None, resolved_landmark=None, status="alert_clear",
        origin_node=settings.node_mode, signed=signed,
    )
    await hub.broadcast({"type": "event", "event": event})
    return event


async def confirm_pending(unit: str, seq: int | None, text: str, *, signed: dict | None = None) -> dict:
    """Valide une annonce « à confirmer » (bouton, ou « confirmé » à la voix : la plus récente).

    Le journal ne se modifie pas : on ajoute un événement confirmé qui reprend le lieu trouvé
    (confirmed_from = numéro de l'annonce d'origine), avec le trajet depuis la position actuelle.
    """
    candidates = journal.confirmable(unit)
    target = next((e for e in candidates if e["seq"] == seq), None) if seq is not None else (candidates[0] if candidates else None)
    if target is None:
        raise UnknownEnemy("Aucune annonce à confirmer pour cette unité (ou lieu introuvable : il faut la redire).")
    pt = (target["lat"], target["lon"])
    intent = {**(target["intent"] or {}), "confirmed_from": target["seq"], "confidence": 1.0,
              "confirm_text": text, "route": None}
    if (target["intent"] or {}).get("subject") != "enemy":
        last = journal.last_position(unit)
        route, pt = await walk_to(unit, last, pt)
        if route:
            intent["route"] = [list(p) for p in route]
        if last and haversine(last, pt) > 5:
            intent["heading_deg"] = round(bearing_between(last, pt), 1)
    event = journal.append(
        unit=unit, transcript=target["transcript"], intent=intent, lat=pt[0], lon=pt[1],
        uncertainty_m=target["uncertainty_m"], resolved_landmark=target["resolved_landmark"],
        status="confirmed", origin_node=settings.node_mode, signed=signed,
    )
    await hub.broadcast({"type": "event", "event": event})
    return event


async def set_facing(unit: str, intent: dict, text: str, *, signed: dict | None = None) -> dict:
    """« Je regarde à 3h » : 3h devient le nouveau midi (la flèche tourne de 90°).
    « On fait face au nord » : direction absolue. Toute l'escouade tourne avec l'unité."""
    current = journal.last_heading(unit) or 0.0
    if intent.get("clock"):
        heading = (current + 30 * (intent["clock"] % 12)) % 360
    elif intent.get("direction") in DIRECTION_BEARINGS:
        heading = float(DIRECTION_BEARINGS[intent["direction"]])
    else:
        raise UnknownEnemy("Vers où ? Dites une heure (« je regarde à 3h ») ou une direction (« face au nord »).")
    squad = journal.group_of(unit)
    members = [unit] + [m for m in (squad[1] if squad else []) if m != unit]
    events = []
    for m in members:
        ev = journal.append(
            unit=m, transcript=text,
            intent={"type": "facing", "subject": "self", "heading_deg": round(heading, 1), "clock": intent.get("clock"),
                    "direction": intent.get("direction"), "moved_by": unit if m != unit else None, "confidence": 1.0},
            lat=None, lon=None, uncertainty_m=None, resolved_landmark=None, status="facing",
            origin_node=settings.node_mode, signed=signed if m == unit else None,
        )
        events.append(ev)
        await hub.broadcast({"type": "event", "event": ev})
    first = events[0]
    if len(events) > 1:
        first["companions"] = events[1:]
    return first


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
    if intent.get("clock") and pos:
        # « Ennemi éliminé à 4h » : ceux dans la direction de 4h (±45°), le plus proche d'abord.
        target = ((journal.last_heading(unit) or 0.0) + 30 * (intent["clock"] % 12)) % 360

        def off(k: str) -> float:
            v = alive[k]
            if v["lat"] is None:
                return 999.0
            d = abs(bearing_between(pos, (v["lat"], v["lon"])) - target) % 360
            return min(d, 360 - d)

        sector = sorted((k for k in alive if off(k) <= 45), key=lambda k: (dist(k), off(k)))
        if sector:
            if scope == "all":
                return sector
            return sector[: max(1, min(int(intent.get("count") or 1), len(sector)))]
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
