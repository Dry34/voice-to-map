"""Itinéraires à pied par les rues (OSRM, profil piéton d'openstreetmap.de). Responsable : D.

Sert à tracer les déplacements le long des routes au lieu d'un trait droit,
et à faire marcher les ennemis simulés.
"""

import logging

import httpx

from .geometry import Point, haversine

log = logging.getLogger(__name__)

ROUTE_URL = "https://routing.openstreetmap.de/routed-foot/route/v1/driving/{a};{b}"
HEADERS = {"User-Agent": "voice-to-map-hackathon/0.1"}
MIN_DISTANCE_M = 15     # en dessous, pas besoin d'itinéraire
MAX_DISTANCE_M = 8000   # au-delà, ce n'est pas une marche : trait droit
MAX_POINTS = 150

_cache: dict[str, list[Point] | None] = {}


def _simplify(points: list[Point]) -> list[Point]:
    if len(points) <= MAX_POINTS:
        return points
    step = len(points) / (MAX_POINTS - 1)
    return [points[int(i * step)] for i in range(MAX_POINTS - 1)] + [points[-1]]


async def foot_route(a: Point, b: Point, snap_end: bool = False) -> list[Point] | None:
    """Points [(lat, lon)…] du trajet à pied de a à b, ou None (trop court, trop long, service muet).

    snap_end : l'arrivée est le point de rue le plus proche de b (pour un déplacement relatif,
    « 200 m à 12h » : b tombe souvent au milieu d'un pâté de maisons).
    """
    d = haversine(a, b)
    if d < MIN_DISTANCE_M or d > MAX_DISTANCE_M:
        return None
    key = f"{a[0]:.5f},{a[1]:.5f}>{b[0]:.5f},{b[1]:.5f}:{int(snap_end)}"
    if key in _cache:
        return _cache[key]
    url = ROUTE_URL.format(a=f"{a[1]:.6f},{a[0]:.6f}", b=f"{b[1]:.6f},{b[0]:.6f}")
    try:
        async with httpx.AsyncClient(timeout=4, headers=HEADERS) as client:
            r = await client.get(url, params={"overview": "full", "geometries": "geojson"})
            r.raise_for_status()
        coords = r.json()["routes"][0]["geometry"]["coordinates"]
    except Exception as exc:
        log.warning("Itinéraire indisponible : %s", exc)
        return None
    pts = [(round(c[1], 6), round(c[0], 6)) for c in coords]
    # OSRM part de la rue la plus proche : on raccroche le vrai départ et la vraie arrivée.
    route = _simplify([a, *pts] if snap_end else [a, *pts, b]) if len(pts) >= 2 else None
    _cache[key] = route
    return route


def route_length(points: list[Point]) -> float:
    return sum(haversine(p, q) for p, q in zip(points, points[1:]))
