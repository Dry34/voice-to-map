"""Calculs géométriques sur la sphère. Responsable : D."""

import math

EARTH_RADIUS_M = 6_371_000

BEARINGS = {"north_of": 0, "east_of": 90, "south_of": 180, "west_of": 270}
DIRECTION_BEARINGS = {"N": 0, "E": 90, "S": 180, "W": 270}

Point = tuple[float, float]


def haversine(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def destination(p: Point, bearing_deg: float, distance_m: float) -> Point:
    lat1, lon1 = math.radians(p[0]), math.radians(p[1])
    brg = math.radians(bearing_deg)
    d = distance_m / EARTH_RADIUS_M
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(brg))
    lon2 = lon1 + math.atan2(math.sin(brg) * math.sin(d) * math.cos(lat1), math.cos(d) - math.sin(lat1) * math.sin(lat2))
    return (math.degrees(lat2), math.degrees(lon2))


def nearest(ref: Point, points: list[Point]) -> Point | None:
    return min(points, key=lambda p: haversine(ref, p), default=None)


def closest_pair(a: list[Point], b: list[Point]) -> tuple[Point, float] | None:
    """Point milieu des deux sommets les plus proches entre deux géométries."""
    best = None
    for p in a:
        for q in b:
            d = haversine(p, q)
            if best is None or d < best[1]:
                best = (((p[0] + q[0]) / 2, (p[1] + q[1]) / 2), d)
    return best
