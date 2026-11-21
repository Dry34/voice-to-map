"""Simulation : les ennemis actifs marchent tout seuls dans les rues. Responsable : B.

Une « étape » par ennemi : il choisit un point proche (sans s'éloigner à plus de 100 m de
l'endroit où une unité l'a vu), le serveur calcule l'itinéraire
à pied, et journalise UN événement (destination + itinéraire + vitesse). Les cartes
animent le losange le long de l'itinéraire selon l'heure ; à l'arrivée, nouvelle étape.
Le journal ne reçoit donc qu'un événement par étape, pas un par seconde.
"""

import asyncio
import logging
import random
import time

from .config import settings
from .geo.geometry import destination, haversine
from .geo.routing import foot_route, route_length
from .state import hub, journal

log = logging.getLogger(__name__)

SPEED_MPS = 1.2        # au pas
TICK_S = 3
LEG_MIN_M, LEG_MAX_M = 25, 90   # petits déplacements…
HOME_RADIUS_M = 100            # …autour de l'endroit où une unité l'a vu
PAUSE_S = (5, 25)              # s'arrête un moment entre deux déplacements

enabled = False  # coupée : on ne sait pas où va un ennemi, il reste là où il a été vu
_leg_end: dict[str, float] = {}   # ennemi -> fin de l'étape en cours (time.monotonic)
_task: asyncio.Task | None = None


async def set_enabled(on: bool) -> None:
    global enabled
    enabled = on
    if not on:
        _leg_end.clear()
    await hub.broadcast({"type": "sim", "on": on})


async def _new_leg(eid: str, pos: tuple[float, float], home: tuple[float, float] | None) -> None:
    home = home or pos
    for _ in range(8):  # un point proche qui reste dans la zone de l'ennemi
        target = destination(pos, random.uniform(0, 360), random.uniform(LEG_MIN_M, LEG_MAX_M))
        if haversine(home, target) <= HOME_RADIUS_M:
            break
    else:
        target = home
    route = await foot_route(pos, target)
    if not route:
        _leg_end[eid] = time.monotonic() + 30  # pas d'itinéraire : on réessaiera plus tard
        return
    length = route_length(route)
    end = route[-1]
    event = journal.append(
        unit="SIMULATION", transcript=None,
        intent={"subject": "enemy", "enemy_id": eid, "type": "movement", "sim": True,
                "route": [list(p) for p in route], "speed_mps": SPEED_MPS, "confidence": 1.0},
        lat=end[0], lon=end[1], uncertainty_m=30, resolved_landmark="déplacement simulé",
        status="confirmed", origin_node=settings.node_mode,
    )
    await hub.broadcast({"type": "event", "event": event})
    _leg_end[eid] = time.monotonic() + length / SPEED_MPS + random.uniform(*PAUSE_S)


async def _loop() -> None:
    while True:
        await asyncio.sleep(TICK_S)
        if not enabled:
            continue
        try:
            now = time.monotonic()
            for eid, e in journal.enemies().items():
                if e["alive"] and e["lat"] is not None and _leg_end.get(eid, 0) <= now:
                    await _new_leg(eid, (e["lat"], e["lon"]), e.get("home"))
        except Exception:  # la simulation ne doit jamais faire tomber le serveur
            log.exception("Simulation : erreur, on continue")


def start() -> None:
    global _task
    if _task is None:
        _task = asyncio.get_event_loop().create_task(_loop())
