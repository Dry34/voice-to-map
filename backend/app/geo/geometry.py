"""Calculs géométriques sur la sphère. Responsable : D."""

import math

EARTH_RADIUS_M = 6_371_000

BEARINGS = {"north_of": 0, "east_of": 90, "south_of": 180, "west_of": 270}
DIRECTION_BEARINGS = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}

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


def _segment_intersection(p1: Point, p2: Point, q1: Point, q2: Point) -> Point | None:
    """Croisement de deux segments (approximation plane, suffisante à l'échelle d'une ville)."""
    d1 = (p2[0] - p1[0], p2[1] - p1[1])
    d2 = (q2[0] - q1[0], q2[1] - q1[1])
    denom = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(denom) < 1e-15:
        return None
    t = ((q1[0] - p1[0]) * d2[1] - (q1[1] - p1[1]) * d2[0]) / denom
    u = ((q1[0] - p1[0]) * d1[1] - (q1[1] - p1[1]) * d1[0]) / denom
    if 0 <= t <= 1 and 0 <= u <= 1:
        return (p1[0] + t * d1[0], p1[1] + t * d1[1])
    return None


Line = list[Point]


def crossing(a: list[Line], b: list[Line]) -> Point | None:
    """Premier point où un tronçon de a croise un tronçon de b, sinon None."""
    for la in a:
        for lb in b:
            for i in range(len(la) - 1):
                for j in range(len(lb) - 1):
                    pt = _segment_intersection(la[i], la[i + 1], lb[j], lb[j + 1])
                    if pt:
                        return pt
    return None


def closest_pair(a: list[Line], b: list[Line]) -> tuple[Point, float] | None:
    """Croisement exact si les rues se croisent, sinon milieu des deux sommets les plus proches.

    Renvoie (point, écart en mètres) : 0 pour un vrai croisement.
    """
    pt = crossing(a, b)
    if pt:
        return pt, 0.0
    best = None
    for p in (p for line in a for p in line):
        for q in (q for line in b for q in line):
            d = haversine(p, q)
            if best is None or d < best[1]:
                best = (((p[0] + q[0]) / 2, (p[1] + q[1]) / 2), d)
    return best


def project_on_line(p: Point, line: list[Point]) -> tuple[Point, float]:
    """Point de la polyligne le plus proche de p (projection sur les segments) et cap du segment (degrés)."""
    k = math.cos(math.radians(p[0]))  # repère local plat : x = lon * cos(lat), y = lat
    best, best_d, best_bearing = line[0], float("inf"), 0.0
    for a, b in zip(line, line[1:]):
        ax, ay, bx, by = a[1] * k, a[0], b[1] * k, b[0]
        px, py = p[1] * k, p[0]
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        q = (ay + t * dy, (ax + t * dx) / k)
        d = haversine(p, q)
        if d < best_d:
            best, best_d = q, d
            best_bearing = (math.degrees(math.atan2(dx, dy)) + 360) % 360
    return best, best_bearing


def bearing_between(a: Point, b: Point) -> float:
    """Cap (degrés, 0 = nord) pour aller de a à b."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlon = math.radians(b[1] - a[1])
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360
