"""Accès OpenStreetMap : cache local de la zone, Overpass, Nominatim. Responsable : D."""

import asyncio
import json
import logging
import re
import time

import httpx

from ..config import settings
from .geometry import Point, haversine

log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "voice-to-map-hackathon/0.1"}

# Mot générique prononcé -> filtre de tags OSM.
GENERIC_TAGS = {
    "church": '["amenity"="place_of_worship"]["religion"="christian"]',
    "pharmacy": '["amenity"="pharmacy"]',
    "school": '["amenity"="school"]',
    "hospital": '["amenity"="hospital"]',
    "station": '["railway"="station"]',
    "park": '["leisure"="park"]',
    "bridge": '["bridge"="yes"]["name"]',
    "cafe": '["amenity"="cafe"]',
    "restaurant": '["amenity"="restaurant"]',
    "bank": '["amenity"="bank"]',
    "police": '["amenity"="police"]',
    "townhall": '["amenity"="townhall"]',
    "museum": '["tourism"="museum"]',
    "supermarket": '["shop"="supermarket"]',
}
ALIASES = {"église": "church", "eglise": "church", "pharmacie": "pharmacy", "école": "school",
           "ecole": "school", "hôpital": "hospital", "hopital": "hospital", "gare": "station",
           "parc": "park", "pont": "bridge", "café": "cafe", "banque": "bank",
           "town hall": "townhall", "city hall": "townhall", "mairie": "townhall", "musée": "museum"}

_cache: dict[str, object] = {}
_nominatim_lock = asyncio.Lock()
_nominatim_last = 0.0
_zone: dict | None = None


def generic_kind(kind: str) -> str | None:
    k = ALIASES.get(kind.lower(), kind.lower())
    return k if k in GENERIC_TAGS else None


def zone_cache() -> dict:
    """Repères pré-extraits par scripts/fetch_landmarks.py (vide si absent)."""
    global _zone
    if _zone is None:
        try:
            _zone = json.loads(settings.landmarks_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _zone = {"pois": [], "streets": []}
    return _zone


async def overpass(query: str) -> list[dict]:
    if query in _cache:
        return _cache[query]
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
            r = await client.post(OVERPASS_URL, data={"data": query})
            r.raise_for_status()
        elements = r.json().get("elements", [])
    except Exception as exc:
        log.warning("Overpass indisponible : %s", exc)
        return []
    _cache[query] = elements
    return elements


def _element_point(el: dict) -> Point | None:
    if "lat" in el:
        return (el["lat"], el["lon"])
    if "center" in el:
        return (el["center"]["lat"], el["center"]["lon"])
    return None


async def find_poi(kind: str, ref: Point, radius_m: float) -> dict | None:
    """Repère générique le plus proche de ref (cache de zone, puis Overpass)."""
    local = [p for p in zone_cache()["pois"] if p.get("kind") == kind]
    if local:
        best = min(local, key=lambda p: haversine(ref, (p["lat"], p["lon"])))
        if haversine(ref, (best["lat"], best["lon"])) <= radius_m:
            return best
    q = f'[out:json][timeout:10];nwr{GENERIC_TAGS[kind]}(around:{int(radius_m)},{ref[0]},{ref[1]});out center 30;'
    candidates = []
    for el in await overpass(q):
        pt = _element_point(el)
        if pt:
            candidates.append({"name": el.get("tags", {}).get("name", kind), "kind": kind,
                               "lat": pt[0], "lon": pt[1], "osm_id": f"{el['type']}/{el['id']}"})
    return min(candidates, key=lambda p: haversine(ref, (p["lat"], p["lon"])), default=None)


async def find_street(name: str, ref: Point, radius_m: float) -> tuple[str, list[Point]] | None:
    """Tous les sommets des voies portant ce nom autour de ref."""
    for s in zone_cache()["streets"]:
        if s["name"].lower() == name.lower():
            return s["name"], [tuple(p) for p in s["points"]]
    safe = re.escape(name.replace('"', ""))
    q = (f'[out:json][timeout:10];way["highway"]["name"~"^{safe}$",i]'
         f'(around:{int(radius_m)},{ref[0]},{ref[1]});out geom;')
    points: list[Point] = []
    label = name
    for el in await overpass(q):
        label = el.get("tags", {}).get("name", label)
        points += [(g["lat"], g["lon"]) for g in el.get("geometry", [])]
    return (label, points) if points else None


async def nominatim(query: str, ref: Point, radius_m: float) -> dict | None:
    """Recherche d'un nom propre, limitée à une boîte autour de ref. 1 requête/s max."""
    global _nominatim_last
    key = f"nominatim:{query}:{round(ref[0], 3)}:{round(ref[1], 3)}"
    if key in _cache:
        return _cache[key]
    d = radius_m / 111_000
    params = {"q": query, "format": "json", "limit": 1, "bounded": 1,
              "viewbox": f"{ref[1] - d * 1.5},{ref[0] + d},{ref[1] + d * 1.5},{ref[0] - d}"}
    async with _nominatim_lock:
        wait = 1.0 - (time.monotonic() - _nominatim_last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
                r = await client.get(NOMINATIM_URL, params=params)
                r.raise_for_status()
            results = r.json()
        except Exception as exc:
            log.warning("Nominatim indisponible : %s", exc)
            return None
        finally:
            _nominatim_last = time.monotonic()
    hit = None
    if results:
        res = results[0]
        hit = {"name": res.get("display_name", query).split(",")[0], "lat": float(res["lat"]),
               "lon": float(res["lon"]), "osm_id": f"{res.get('osm_type')}/{res.get('osm_id')}"}
    _cache[key] = hit
    return hit
