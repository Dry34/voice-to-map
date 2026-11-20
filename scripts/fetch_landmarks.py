"""Extrait les repères et les rues de la zone de démo dans data/landmarks/zone.json.

Le cache évite d'interroger Overpass à chaque annonce et sert au relais hors ligne.
Lancer depuis la racine du repo : python scripts/fetch_landmarks.py
Responsable : D.
"""

import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "backend"))

from app.geo.osm import GENERIC_TAGS, HEADERS, OVERPASS_URL  # noqa: E402

LAT = float(os.getenv("ZONE_LAT", "48.8584"))
LON = float(os.getenv("ZONE_LON", "2.3470"))
RADIUS = int(float(os.getenv("ZONE_RADIUS_M", "2000")))
OUT = ROOT / "data" / "landmarks" / "zone.json"


def query(q: str) -> list[dict]:
    r = httpx.post(OVERPASS_URL, data={"data": q}, headers=HEADERS, timeout=120)
    r.raise_for_status()
    return r.json()["elements"]


def main() -> None:
    pois = []
    for kind, tags in GENERIC_TAGS.items():
        q = f"[out:json][timeout:60];nwr{tags}(around:{RADIUS},{LAT},{LON});out center;"
        for el in query(q):
            pt = (el["lat"], el["lon"]) if "lat" in el else (el["center"]["lat"], el["center"]["lon"]) if "center" in el else None
            if pt:
                pois.append({"name": el.get("tags", {}).get("name", kind), "kind": kind,
                             "lat": pt[0], "lon": pt[1], "osm_id": f"{el['type']}/{el['id']}"})
        print(f"{kind:12} {sum(1 for p in pois if p['kind'] == kind)}")

    streets: dict[str, list] = {}
    q = f'[out:json][timeout:90];way["highway"]["name"](around:{RADIUS},{LAT},{LON});out geom;'
    for el in query(q):
        name = el["tags"]["name"]
        streets.setdefault(name, []).extend([g["lat"], g["lon"]] for g in el.get("geometry", []))
    print(f"{'streets':12} {len(streets)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "zone": {"lat": LAT, "lon": LON, "radius_m": RADIUS},
        "pois": pois,
        "streets": [{"name": n, "points": pts} for n, pts in streets.items()],
    }, ensure_ascii=False), encoding="utf-8")
    print(f"Écrit : {OUT}")


if __name__ == "__main__":
    main()
