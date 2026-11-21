"""Transforme une intention en point sur la carte. Responsable : D.

Le repère est toujours cherché autour de la dernière position connue de
l'unité (ou du centre de la zone), et chaque point porte un rayon d'incertitude.
"""

from ..config import settings
from . import osm
from .geometry import BEARINGS, DIRECTION_BEARINGS, Point, closest_pair, destination, nearest

# Rayon d'incertitude par relation, en mètres (voir le tableau du plan).
UNCERTAINTY_M = {"at": 30, "near": 150, "north_of": 80, "south_of": 80, "east_of": 80,
                 "west_of": 80, "towards": 60, "along": 50, "intersection": 20}
CARDINAL_OFFSET_M = 120
NAMED_PLACE_RADIUS_M = 10_000
DEFAULT_ALONG_M = 100


def _place(pt: Point, relation: str, label: str | None, osm_id: str | None = None) -> dict:
    return {"lat": pt[0], "lon": pt[1], "uncertainty_m": UNCERTAINTY_M.get(relation, 100),
            "label": label, "osm_id": osm_id}


async def locate_landmark(landmark: dict, ref: Point) -> tuple[Point, str, str | None] | None:
    name, kind = landmark["name"], landmark.get("kind", "named_place")
    radius = settings.zone_radius_m

    if kind == "street":
        street = await osm.find_street(name, ref, radius)
        if street:
            label, lines = street
            return nearest(ref, [p for line in lines for p in line]), label, None

    generic = osm.generic_kind(kind) or osm.generic_kind(name)
    if generic:
        poi = await osm.find_poi(generic, ref, radius)
        if poi:
            return (poi["lat"], poi["lon"]), poi["name"], poi.get("osm_id")

    for p in osm.zone_cache()["pois"]:
        if name.lower() in p["name"].lower():
            return (p["lat"], p["lon"]), p["name"], p.get("osm_id")

    hit = await osm.nominatim(name, ref, radius)
    if hit is None:
        # Un nom propre (« tour Eiffel ») peut être loin de l'unité : on élargit à toute la ville.
        hit = await osm.nominatim(name, (settings.zone_lat, settings.zone_lon), NAMED_PLACE_RADIUS_M)
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

    if relation == "along":
        bearing = DIRECTION_BEARINGS.get(intent.get("direction") or "")
        if last is None or bearing is None:
            return None
        dist = intent.get("distance_m") or DEFAULT_ALONG_M
        label = (intent.get("landmark") or {}).get("name")
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
        pt = destination(pt, BEARINGS[relation], CARDINAL_OFFSET_M)
    return _place(pt, relation or "at", label, osm_id)
