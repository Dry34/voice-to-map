"""Orchestration : texte -> intention -> lieu -> événement -> diffusion."""

from .config import settings
from .geo.resolver import resolve
from .intent.extractor import extract_intent
from .state import hub, journal

MIN_CONFIDENCE = 0.6


async def process_report(unit: str, text: str) -> dict:
    last = journal.last_position(unit)
    ref = last or (settings.zone_lat, settings.zone_lon)

    intent = await extract_intent(text, unit=unit, last_position=last)
    place = await resolve(intent, ref=ref, last=last)

    confirmed = place is not None and intent.get("confidence", 0) >= MIN_CONFIDENCE
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
    )
    await hub.broadcast({"type": "event", "event": event})
    return event
