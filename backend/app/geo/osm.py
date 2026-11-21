"""Accès OpenStreetMap. Responsable : D.

Ordre de recherche, du plus rapide au plus lent :
1. cache local de la zone (data/landmarks/zone.json, voir scripts/fetch_landmarks.py) ;
2. Overpass, mis de côté 2 minutes après un échec (le serveur public est souvent saturé) ;
3. Photon pour les repères, Nominatim pour les noms propres et la géométrie des rues.
"""

import asyncio
import difflib
import json
import logging
import re
import time
import unicodedata

import httpx

from ..config import settings
from .geometry import Point, haversine

log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
PHOTON_URL = "https://photon.komoot.io/api/"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {"User-Agent": "voice-to-map-hackathon/0.1"}
OVERPASS_TIMEOUT_S = 3
OVERPASS_PAUSE_S = 120

# Mot générique prononcé -> filtre de tags OSM (Overpass).
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

# Même chose pour Photon : tag OSM + mots de recherche (Photon cherche dans les noms,
# il faut donc les mots de la langue locale en premier).
PHOTON_QUERIES = {
    "church": ("amenity:place_of_worship", ["église", "chapelle", "basilique", "church"]),
    "pharmacy": ("amenity:pharmacy", ["pharmacie", "pharmacy"]),
    "school": ("amenity:school", ["école", "collège", "lycée", "school"]),
    "hospital": ("amenity:hospital", ["hôpital", "hospital"]),
    "station": ("railway:station", ["gare", "station"]),
    "park": ("leisure:park", ["parc", "jardin", "square", "park"]),
    "bridge": (None, ["pont", "bridge"]),
    "cafe": ("amenity:cafe", ["café", "cafe"]),
    "restaurant": ("amenity:restaurant", ["restaurant"]),
    "bank": ("amenity:bank", ["banque", "bank"]),
    "police": ("amenity:police", ["police", "commissariat"]),
    "townhall": ("amenity:townhall", ["mairie", "hôtel de ville", "town hall"]),
    "museum": ("tourism:museum", ["musée", "museum"]),
    "supermarket": ("shop:supermarket", ["supermarché", "supermarket"]),
}

ALIASES = {"église": "church", "eglise": "church", "chapel": "church", "chapelle": "church",
           "cathedral": "church", "cathédrale": "church", "pharmacie": "pharmacy", "école": "school",
           "ecole": "school", "hôpital": "hospital", "hopital": "hospital", "gare": "station",
           "parc": "park", "pont": "bridge", "café": "cafe", "banque": "bank",
           "town hall": "townhall", "city hall": "townhall", "mairie": "townhall", "musée": "museum"}

_cache: dict[str, object] = {}
_nominatim_lock = asyncio.Lock()
_nominatim_last = 0.0
_overpass_paused_until = 0.0
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


def _box(ref: Point, radius_m: float) -> tuple[float, float, float, float]:
    """(lon_min, lat_min, lon_max, lat_max) autour de ref."""
    d = radius_m / 111_000
    return (ref[1] - d * 1.5, ref[0] - d, ref[1] + d * 1.5, ref[0] + d)


# --- Overpass ---------------------------------------------------------------

async def overpass(query: str) -> list[dict] | None:
    """Éléments trouvés, ou None si Overpass est indisponible."""
    global _overpass_paused_until
    if query in _cache:
        return _cache[query]
    if time.monotonic() < _overpass_paused_until:
        return None
    try:
        async with httpx.AsyncClient(timeout=OVERPASS_TIMEOUT_S, headers=HEADERS) as client:
            r = await client.post(OVERPASS_URL, data={"data": query})
            r.raise_for_status()
        elements = r.json().get("elements", [])
    except Exception as exc:
        log.warning("Overpass indisponible, mis de côté %ss : %s", OVERPASS_PAUSE_S, exc)
        _overpass_paused_until = time.monotonic() + OVERPASS_PAUSE_S
        return None
    _cache[query] = elements
    return elements


def _element_point(el: dict) -> Point | None:
    if "lat" in el:
        return (el["lat"], el["lon"])
    if "center" in el:
        return (el["center"]["lat"], el["center"]["lon"])
    return None


# --- Photon -----------------------------------------------------------------

async def photon(query: str, ref: Point, radius_m: float, osm_tag: str | None = None) -> list[dict]:
    key = f"photon:{query}:{osm_tag}:{round(ref[0], 3)}:{round(ref[1], 3)}:{int(radius_m)}"
    if key in _cache:
        return _cache[key]
    params = {"q": query, "limit": 20, "bbox": ",".join(f"{v:.5f}" for v in _box(ref, radius_m))}
    if osm_tag:
        params["osm_tag"] = osm_tag
    try:
        async with httpx.AsyncClient(timeout=5, headers=HEADERS) as client:
            r = await client.get(PHOTON_URL, params=params)
            r.raise_for_status()
        features = r.json().get("features", [])
    except Exception as exc:
        log.warning("Photon indisponible : %s", exc)
        return []
    out = []
    for f in features:
        lon, lat = f["geometry"]["coordinates"][:2]
        p = f.get("properties", {})
        out.append({"name": p.get("name") or query, "lat": lat, "lon": lon,
                    "osm_id": f"{p.get('osm_type', '?')}/{p.get('osm_id', '?')}"})
    _cache[key] = out
    return out


# --- Nominatim --------------------------------------------------------------

async def _nominatim_search(params: dict) -> list[dict] | None:
    """Appel Nominatim limité à 1 requête par seconde (règle d'usage du service)."""
    global _nominatim_last
    async with _nominatim_lock:
        wait = 1.0 - (time.monotonic() - _nominatim_last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=6, headers=HEADERS) as client:
                r = await client.get(NOMINATIM_URL, params={"format": "json", **params})
                r.raise_for_status()
            return r.json()
        except Exception as exc:
            log.warning("Nominatim indisponible : %s", exc)
            return None
        finally:
            _nominatim_last = time.monotonic()


async def nominatim(query: str, ref: Point, radius_m: float) -> dict | None:
    """Nom propre, limité à une boîte autour de ref."""
    key = f"nominatim:{query}:{round(ref[0], 3)}:{round(ref[1], 3)}:{int(radius_m)}"
    if key in _cache:
        return _cache[key]
    lon_min, lat_min, lon_max, lat_max = _box(ref, radius_m)
    results = await _nominatim_search({"q": query, "limit": 1, "bounded": 1,
                                       "viewbox": f"{lon_min},{lat_max},{lon_max},{lat_min}"})
    if results is None:
        return None
    hit = None
    if results:
        res = results[0]
        hit = {"name": res.get("display_name", query).split(",")[0], "lat": float(res["lat"]),
               "lon": float(res["lon"]), "osm_id": f"{res.get('osm_type')}/{res.get('osm_id')}"}
    _cache[key] = hit
    return hit


STOPWORDS = {"le", "la", "les", "l", "de", "du", "des", "d", "the", "of", "a", "au", "aux", "et", "en", "sur"}


def _tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return [t for t in re.split(r"[^a-z0-9]+", text) if t and t not in STOPWORDS]


def name_matches(query: str, candidate: str) -> bool:
    """Chaque mot du nom demandé doit se retrouver (à une faute près) dans le résultat.

    Évite qu'une recherche « bois de Boulogne » renvoie « Jardins du Trocadéro »
    juste parce que c'est le seul résultat dans la zone.
    """
    wanted, have = _tokens(query), _tokens(candidate)
    if not wanted:
        return False
    for w in wanted:
        if not any(h == w or (len(w) >= 4 and (h.startswith(w) or difflib.SequenceMatcher(None, w, h).ratio() >= 0.8))
                   for h in have):
            return False
    return True


async def nominatim_named(query: str, center: Point, radius_m: float) -> dict | None:
    """Nom propre dans une boîte, classé par notoriété (ordre de Nominatim), nom vérifié.

    Contrairement à nominatim(), on ne prend pas « le plus proche » : pour « Notre-Dame »,
    on veut la cathédrale, pas la petite église à côté de l'unité.
    """
    key = f"nominatim-named:{query}:{round(center[0], 3)}:{round(center[1], 3)}:{int(radius_m)}"
    if key in _cache:
        return _cache[key]
    lon_min, lat_min, lon_max, lat_max = _box(center, radius_m)
    results = await _nominatim_search({"q": query, "limit": 8, "bounded": 1, "namedetails": 1,
                                       "viewbox": f"{lon_min},{lat_max},{lon_max},{lat_min}"})
    if results is None:
        return None
    hit = None
    for res in results:
        name = res.get("name") or res.get("display_name", "").split(",")[0]
        # Tous les noms du lieu (nom d'usage, ancien nom…) et le début de son adresse :
        # « Beaubourg » -> Centre Georges Pompidou, 19 rue Beaubourg.
        names = [name, ", ".join(res.get("display_name", "").split(",")[:3]),
                 *(v for v in (res.get("namedetails") or {}).values() if isinstance(v, str))]
        if any(name_matches(query, n) for n in names):
            hit = {"name": name or query, "lat": float(res["lat"]), "lon": float(res["lon"]),
                   "osm_id": f"{res.get('osm_type')}/{res.get('osm_id')}",
                   "importance": float(res.get("importance") or 0)}
            break
    _cache[key] = hit
    return hit


Line = list[Point]


async def nearest_street(pt: Point) -> tuple[str, Line] | None:
    """Rue la plus proche d'un point (géocodage inverse Nominatim) : nom et tracé."""
    key = f"reverse-street:{round(pt[0], 4)}:{round(pt[1], 4)}"
    if key in _cache:
        return _cache[key]
    global _nominatim_last
    async with _nominatim_lock:
        wait = 1.0 - (time.monotonic() - _nominatim_last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with httpx.AsyncClient(timeout=4, headers=HEADERS) as client:
                r = await client.get(NOMINATIM_REVERSE_URL, params={
                    "format": "json", "lat": pt[0], "lon": pt[1], "zoom": 17, "layer": "address", "polygon_geojson": 1})
                r.raise_for_status()
            res = r.json()
        except Exception as exc:
            log.warning("Nominatim reverse indisponible : %s", exc)
            return None
        finally:
            _nominatim_last = time.monotonic()
    geo = res.get("geojson") or {}
    coords = geo.get("coordinates") or []
    line = coords if geo.get("type") == "LineString" else coords[0] if geo.get("type") == "MultiLineString" and coords else []
    found = (res.get("name") or "rue", [(c[1], c[0]) for c in line]) if len(line) >= 2 else None
    _cache[key] = found
    return found


async def nominatim_street(name: str, ref: Point, radius_m: float) -> tuple[str, list[Line]] | None:
    """Géométrie d'une rue via Nominatim (polygon_geojson), un tracé par tronçon."""
    key = f"nominatim-street:{name}:{round(ref[0], 3)}:{round(ref[1], 3)}:{int(radius_m)}"
    if key in _cache:
        return _cache[key]
    lon_min, lat_min, lon_max, lat_max = _box(ref, radius_m)
    results = await _nominatim_search({"q": name, "limit": 40, "dedupe": 0, "bounded": 1, "polygon_geojson": 1,
                                       "viewbox": f"{lon_min},{lat_max},{lon_max},{lat_min}"})
    if not results:
        return None
    lines: list[Line] = []
    label = name
    for res in results:
        if res.get("class") != "highway":
            continue
        candidate = res.get("name") or res.get("display_name", name).split(",")[0]
        # La rue renvoyée doit porter le nom demandé : « rue faubourg Saint-Antoine » ne doit pas
        # donner « rue du Grand Prieuré » parce que c'est la seule voie trouvée dans la zone.
        if not name_matches(name, candidate):
            continue
        label = candidate
        geo = res.get("geojson") or {}
        coords = geo.get("coordinates") or []
        raw = coords if geo.get("type") == "MultiLineString" else [coords] if geo.get("type") == "LineString" else []
        lines += [[(c[1], c[0]) for c in line] for line in raw if line]
    found = (label, lines) if lines else None
    _cache[key] = found
    return found


# --- Recherches utilisées par le resolver ----------------------------------

def _nearest_of(ref: Point, candidates: list[dict]) -> dict | None:
    return min(candidates, key=lambda p: haversine(ref, (p["lat"], p["lon"])), default=None)


async def find_poi(kind: str, ref: Point, radius_m: float) -> dict | None:
    """Repère générique le plus proche de ref."""
    local = [p for p in zone_cache()["pois"] if p.get("kind") == kind]
    best = _nearest_of(ref, local)
    if best and haversine(ref, (best["lat"], best["lon"])) <= radius_m:
        return best

    q = f'[out:json][timeout:5];nwr{GENERIC_TAGS[kind]}(around:{int(radius_m)},{ref[0]},{ref[1]});out center 30;'
    elements = await overpass(q)
    if elements:
        candidates = []
        for el in elements:
            pt = _element_point(el)
            if pt:
                candidates.append({"name": el.get("tags", {}).get("name", kind), "kind": kind,
                                   "lat": pt[0], "lon": pt[1], "osm_id": f"{el['type']}/{el['id']}"})
        if candidates:
            return _nearest_of(ref, candidates)

    tag, words = PHOTON_QUERIES.get(kind, (None, [kind]))
    # Photon classe par pertinence, pas par distance : on cherche d'abord tout près,
    # puis on élargit, pour obtenir vraiment le repère le plus proche.
    for r in sorted({min(400, radius_m), min(1000, radius_m), radius_m}):
        for word in words:
            candidates = await photon(word, ref, r, tag)
            if candidates:
                hit = _nearest_of(ref, candidates)
                return {**hit, "kind": kind}
    return None


async def find_street(name: str, ref: Point, radius_m: float) -> tuple[str, list[Line]] | None:
    """Tous les tronçons des voies portant ce nom autour de ref (un tracé par tronçon)."""
    for s in zone_cache()["streets"]:
        if s["name"].lower() == name.lower():
            return s["name"], [[tuple(p) for p in line] for line in s["lines"]]

    safe = re.escape(name.replace('"', ""))
    q = (f'[out:json][timeout:5];way["highway"]["name"~"^{safe}$",i]'
         f'(around:{int(radius_m)},{ref[0]},{ref[1]});out geom;')
    elements = await overpass(q)
    if elements:
        lines: list[Line] = []
        label = name
        for el in elements:
            label = el.get("tags", {}).get("name", label)
            line = [(g["lat"], g["lon"]) for g in el.get("geometry", [])]
            if line:
                lines.append(line)
        if lines:
            return label, lines

    return await nominatim_street(name, ref, radius_m)
