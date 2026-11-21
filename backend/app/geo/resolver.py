"""Transforme une intention en point sur la carte. Responsable : D.

Le repère est toujours cherché autour de la dernière position connue de
l'unité (ou du centre de la zone), et chaque point porte un rayon d'incertitude.
"""

import re

from ..config import settings
from . import osm
from .geometry import BEARINGS, DIRECTION_BEARINGS, Point, closest_pair, destination, haversine, nearest

# Rayon d'incertitude par relation, en mètres (voir le tableau du plan).
UNCERTAINTY_M = {"at": 30, "near": 150, "north_of": 80, "south_of": 80, "east_of": 80,
                 "west_of": 80, "towards": 60, "along": 50, "intersection": 20, "offset": 60}
CARDINAL_OFFSET_M = 120
NAMED_PLACE_RADIUS_M = 10_000   # Paris intra-muros et proche banlieue
REGION_RADIUS_M = 60_000        # Île-de-France (Disneyland, Versailles…)
DEFAULT_ALONG_M = 100
STREET_TYPES = {"rue", "avenue", "av", "boulevard", "bd", "place", "quai", "impasse", "passage", "allée", "allee",
                "cours", "square", "chemin", "route", "villa", "cité", "cite", "street", "road"}
FAMOUS_IMPORTANCE = 0.45        # notoriété Nominatim au-delà de laquelle un lieu est « connu de tous »


def _place(pt: Point, relation: str, label: str | None, osm_id: str | None = None) -> dict:
    return {"lat": pt[0], "lon": pt[1], "uncertainty_m": UNCERTAINTY_M.get(relation, 100),
            "label": label, "osm_id": osm_id}


def _unit_position(name: str) -> tuple[Point, str] | None:
    """« Bravo 2 », « beta2 » -> dernière position connue de cette unité, si elle est sur la carte."""
    from ..intent.rules import normalize_callsign
    from ..state import journal

    raw = " ".join(name.upper().split())
    candidates = {raw, normalize_callsign(name) or raw, re.sub(r"([A-Z])(\d)", r"\1 \2", raw)}
    for unit in candidates:
        pos = journal.last_position(unit)
        if pos:
            return pos, unit
    # Transcription approximative (« tome 2 » pour TOM 2) : unité de la carte au nom proche,
    # avec exactement le même numéro.
    best = _closest_unit(raw, journal.units_on_map())
    if best:
        return journal.last_position(best), best
    return None


def _closest_unit(raw: str, units: list[str]) -> str | None:
    import difflib

    def split(u: str) -> tuple[str, str]:
        return re.sub(r"[^A-Z]", "", u), re.sub(r"\D", "", u)

    letters, digits = split(raw)
    scored = []
    for u in units:
        ul, ud = split(u)
        if ud != digits or not letters or not ul:
            continue
        score = difflib.SequenceMatcher(None, letters, ul).ratio()
        if score >= 0.7:
            scored.append((score, u))
    return max(scored)[1] if scored else None


async def locate_landmark(landmark: dict, ref: Point) -> tuple[Point, str, str | None] | None:
    name, kind = landmark["name"], landmark.get("kind", "named_place")
    radius = settings.zone_radius_m

    # Une autre unité (« je vais vers Bravo 2 », « je suis avec Alpha 1 ») : sa dernière position.
    if kind == "unit" or re.search(r"\d", name):
        found = _unit_position(name)
        if found:
            return found[0], found[1], None
        if kind == "unit":
            return None

    if kind == "street":
        # Une voie célèbre (« les Champs-Élysées ») l'emporte sur une petite rue homonyme près de l'unité
        # (il y a une rue des Champs-Élysées à Gentilly).
        famous = await osm.nominatim_named(name, (settings.zone_lat, settings.zone_lon), NAMED_PLACE_RADIUS_M)
        if famous and famous.get("importance", 0) >= FAMOUS_IMPORTANCE:
            return (famous["lat"], famous["lon"]), famous["name"], famous["osm_id"]
        street = await osm.find_street(name, ref, radius)
        if not street:
            # Nom mal transcrit (« rue de Chabon » pour rue Chapon) : voies au nom proche, la plus près de l'unité.
            core = " ".join(t for t in re.split(r"[\s'-]+", name)
                            if t and t.lower() not in STREET_TYPES and t.lower() not in ("de", "du", "des", "d", "la", "le", "les", "l"))
            cands = [c for c in await osm.photon(core or name, ref, NAMED_PLACE_RADIUS_M, "highway")
                     if osm.name_matches(core or name, c["name"])] if core else []
            if cands:
                best = min(cands, key=lambda c: haversine(ref, (c["lat"], c["lon"])))
                street = await osm.find_street(best["name"], ref, radius) or (best["name"], [[(best["lat"], best["lon"])]])
        if street:
            label, lines = street
            return nearest(ref, [p for line in lines for p in line]), label, None

    # Repère générique seulement si le nom EST le mot générique (« the church », « l'église »).
    # « bois de Boulogne » (kind park) est un nom propre : pas « le parc le plus proche ».
    generic = osm.generic_kind(name)
    if generic:
        poi = await osm.find_poi(generic, ref, radius)
        if poi:
            return (poi["lat"], poi["lon"]), poi["name"], poi.get("osm_id")

    for p in osm.zone_cache()["pois"]:
        if osm.name_matches(name, p["name"]):
            return (p["lat"], p["lon"]), p["name"], p.get("osm_id")

    # Nom propre : le lieu le plus connu qui porte ce nom dans la ville, puis dans la région.
    # (Pas « le plus proche de l'unité » : ça donnait une petite église de Passy pour Notre-Dame.)
    center = (settings.zone_lat, settings.zone_lon)
    hit = await osm.nominatim_named(name, center, NAMED_PLACE_RADIUS_M)
    if hit is None:
        # Nominatim est strict sur les petits mots : « centre de santé de Yvonne Pouzin » ne donne rien,
        # « centre de santé Yvonne Pouzin » oui.
        short = " ".join(t for t in name.split() if t.lower() not in ("de", "du", "des", "d'", "la", "le", "les", "l'"))
        if short and short != name:
            hit = await osm.nominatim_named(short, center, NAMED_PLACE_RADIUS_M)
    if hit is None:
        # Photon (plus tolérant), avec la même vérification du nom.
        for c in await osm.photon(name, center, NAMED_PLACE_RADIUS_M):
            if osm.name_matches(name, c["name"]):
                hit = c
                break
    if hit is None:
        hit = await osm.nominatim_named(name, center, REGION_RADIUS_M)
    if hit:
        return (hit["lat"], hit["lon"]), hit["name"], hit["osm_id"]
    return None


async def resolve(intent: dict, ref: Point, last: Point | None) -> dict | None:
    """Cherche autour de la dernière position de l'unité, puis autour du centre de la zone."""
    place = await _resolve_around(intent, ref, last)
    zone_center = (settings.zone_lat, settings.zone_lon)
    if place is None and ref != zone_center:
        place = await _resolve_around(intent, zone_center, last)
    return place


async def _resolve_around(intent: dict, ref: Point, last: Point | None) -> dict | None:
    relation = intent.get("relation")

    if relation in ("along", "offset"):
        # along : « je remonte la rue vers le nord sur 200 m » ; offset : « ennemi à 300 m au nord-est de moi ».
        bearing = intent.get("bearing_deg")
        if bearing is None:
            bearing = DIRECTION_BEARINGS.get(intent.get("direction") or "")
        if last is None or bearing is None:
            return None
        dist = intent.get("distance_m") or DEFAULT_ALONG_M
        if intent.get("turn_label"):
            return _place(destination(last, bearing, dist), relation, intent["turn_label"])
        where = f"à {intent['clock']}h" if intent.get("clock") else intent.get("direction") or ""
        label = (intent.get("landmark") or {}).get("name") or (f"{int(dist)} m {where}".strip() if relation == "offset" else None)
        return _place(destination(last, bearing, dist), relation, label)

    if relation == "intersection":
        names = intent.get("landmarks") or []
        if len(names) < 2:
            return None
        a = await osm.find_street(names[0], ref, settings.zone_radius_m)
        b = await osm.find_street(names[1], ref, settings.zone_radius_m)
        if not a or not b:
            return None
        pair = closest_pair(a[1], b[1])
        if pair is None:
            return None
        place = _place(pair[0], relation, f"{a[0]} × {b[0]}")
        place["uncertainty_m"] = max(place["uncertainty_m"], round(pair[1]))
        return place

    landmark = intent.get("landmark")
    if not landmark or not landmark.get("name"):
        return None
    found = await locate_landmark(landmark, ref)
    if not found:
        return None
    pt, label, osm_id = found
    if relation in BEARINGS:
        pt = destination(pt, BEARINGS[relation], intent.get("distance_m") or CARDINAL_OFFSET_M)
    return _place(pt, relation or "at", label, osm_id)
