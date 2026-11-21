"""Analyse par règles, sans LLM.

Sert de repli quand il n'y a pas de clé Mistral, quand l'API est lente,
et sur le relais hors ligne. Responsable : C.
"""

import re

CARDINAL = {
    "north": "north_of", "south": "south_of", "east": "east_of", "west": "west_of",
    "nord": "north_of", "sud": "south_of", "est": "east_of", "ouest": "west_of",
}
DIRECTIONS = {"north": "N", "south": "S", "east": "E", "west": "W",
              "nord": "N", "sud": "S", "est": "E", "ouest": "W"}

STREET_WORDS = re.compile(
    r"\b(street|st|avenue|ave|road|rd|boulevard|blvd|lane|rue|bd|quai|allée|allee|chemin|place)\b",
    re.I,
)
MOVEMENT_WORDS = re.compile(
    r"\b(moved|moving|move|heading|going|walking|advancing|je vais|on avance|en route|vers)\b", re.I
)

ENEMY_WORDS = re.compile(r"\b(enemy|enemies|hostiles?|contact|ennemis?|tangos?|cibles?|targets?)\b|\be\s?\d+\b", re.I)
ENEMY_ID = re.compile(r"\b(?:e|ennemi|enemy|cible|target)\s*(?:n°|numéro|number|no\.?)?\s*-?\s*(\d+)\b", re.I)
KILL_WORDS = re.compile(r"\b(neutralis\w*|abattu\w*|tu[ée]e?s?|d[ée]truit\w*|[ée]limin\w*|extermin\w*|killed|down|destroyed|neutrali[sz]ed)\b", re.I)
ALL_WORDS = re.compile(r"\b(tous|toutes|all|every)\b", re.I)
AROUND_ME = re.compile(r"\b(autour de moi|près de moi|pres de moi|around me|near me)\b", re.I)
COUNT = re.compile(r"\b(\d+|" + "|".join(["one", "two", "three", "four", "five", "un", "une", "deux", "trois", "quatre", "cinq"]) + r")\s+(?:enem|hostil|ennemi|tango)", re.I)
OFFSET = re.compile(
    r"(\d+)\s*(?:m|meters|metres|mètres)\s+(?:au |to the )?"
    r"(nord-est|nord-ouest|sud-est|sud-ouest|north-?east|north-?west|south-?east|south-?west|nord|sud|est|ouest|north|south|east|west)\b",
    re.I,
)
OFFSET_DIR = {"nord": "N", "north": "N", "sud": "S", "south": "S", "est": "E", "east": "E", "ouest": "W", "west": "W",
              "nord-est": "NE", "northeast": "NE", "north-east": "NE", "nord-ouest": "NW", "northwest": "NW",
              "north-west": "NW", "sud-est": "SE", "southeast": "SE", "south-east": "SE", "sud-ouest": "SW",
              "southwest": "SW", "south-west": "SW"}

CLOCK = re.compile(r"\b(?:à|a|at)\s+(\d{1,2})\s*(?:h(?:eures?)?|o'?clock)(?:\s*00)?\b|\bà\s+midi\b", re.I)

_LEADING = re.compile(r"^(the|a|an|la|le|les|l')\s*", re.I)

NATO = ("alpha|bravo|charlie|delta|echo|foxtrot|golf|hotel|india|juliett?|kilo|lima|mike|november|"
        "oscar|papa|quebec|romeo|sierra|tango|uniform|victor|whiskey|x-?ray|yankee|zulu")
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
                "sept": 7, "huit": 8, "neuf": 9, "dix": 10}
_NUM = r"\d+|" + "|".join(NUMBER_WORDS)
CALLSIGN = re.compile(rf"^\W*({NATO})[\s-]*({_NUM})\b[\s,.:;!-]*", re.I)


def normalize_callsign(value) -> str | None:
    """« alpha un », « Alpha-1 », « ALPHA 1 » -> « ALPHA 1 ». None si ce n'est pas un indicatif."""
    if not isinstance(value, str) or not value.strip():
        return None
    m = re.fullmatch(rf"\s*({NATO})[\s-]*({_NUM})\s*", value, re.I)
    if not m:
        return None
    num = m.group(2).lower()
    return f"{m.group(1).upper()} {NUMBER_WORDS.get(num, num)}"


def split_callsign(text: str) -> tuple[str | None, str]:
    """Sépare l'indicatif en tête de phrase du reste : « Alpha 1, I am… » -> (« ALPHA 1 », « I am… »)."""
    m = CALLSIGN.match(text)
    if not m:
        return None, text
    return normalize_callsign(f"{m.group(1)} {m.group(2)}"), text[m.end():]


def clean(name: str) -> str:
    name = name.strip().rstrip(".!?,;")
    name = _LEADING.sub("", name).strip()
    return name


def kind_of(name: str) -> str:
    if STREET_WORDS.search(name) or re.match(r"^\d+(st|nd|rd|th)$", name.strip(), re.I):
        return "street"
    return name.lower() if len(name.split()) == 1 else "named_place"


def _landmark(raw: str) -> dict:
    name = clean(raw)
    return {"name": name, "kind": kind_of(name)}


def parse(text: str) -> dict:
    out = _parse(text)
    low = text.lower()
    m = CLOCK.search(low)
    out["clock"] = None
    if m:
        h = int(m.group(1)) if m.group(1) else 12
        if 1 <= h <= 12:
            dist = re.search(r"(\d+)\s*(?:m|meters|metres|mètres)\b", low)
            out.update(clock=h, relation="offset", landmark=None, direction=None,
                       distance_m=float(dist.group(1)) if dist else None, confidence=0.8)
    if ENEMY_WORDS.search(low):
        m = COUNT.search(low)
        n = m.group(1) if m else None
        out["subject"] = "enemy"
        out["count"] = int(n) if n and n.isdigit() else NUMBER_WORDS.get(n) if n else None
        out["description"] = None
        mid = ENEMY_ID.search(low)
        out["enemy_id"] = f"E{int(mid.group(1))}" if mid else None
        if KILL_WORDS.search(low):
            ids = [f"E{int(n)}" for n in ENEMY_ID.findall(low)]
            scope = ("listed" if ids else "all" if ALL_WORDS.search(low) else
                     "around_me" if AROUND_ME.search(low) else "nearest")
            if not out["count"]:
                m = re.search(r"\b(?:les|the)\s+(\d+|" + "|".join(NUMBER_WORDS) + r")\b", low)
                if m:
                    n = m.group(1)
                    out["count"] = int(n) if n.isdigit() else NUMBER_WORDS[n]
            out.update(type="kill", relation="unknown", landmark=None, confidence=0.8,
                       enemy_ids=ids, enemy_id=ids[0] if ids else None, scope=scope)
            return out
        if out["relation"] in ("unknown", "along", "at", "near"):
            m = OFFSET.search(low)
            if m:
                out.update(relation="offset", landmark=None, distance_m=float(m.group(1)),
                           direction=OFFSET_DIR[m.group(2).lower()], confidence=0.65)
    else:
        out.update(subject="self", count=None, description=None, enemy_id=None, enemy_ids=[], scope=None)
    return out


def parse_many(text: str) -> list[dict]:
    """Découpe l'annonce en phrases (. ; « et moi ») et garde celles qui disent quelque chose."""
    parts = [p for p in re.split(r"[.;!?]\s+|\s+(?:et|and)\s+(?=moi\b|me\b|I\b)", text.strip()) if p.strip()]
    useful = [i for i in (parse(p) for p in parts) if i["relation"] != "unknown" or i.get("type") == "kill"]
    return useful or [parse(text)]


def _parse(text: str) -> dict:
    callsign, t = split_callsign(text.strip())
    t = t.strip()
    low = t.lower()
    is_move = bool(MOVEMENT_WORDS.search(low))
    base = {
        "callsign": callsign,
        "type": "movement" if is_move else "position",
        "relation": "unknown",
        "landmark": None,
        "landmarks": [],
        "direction": None,
        "distance_m": None,
        "confidence": 0.0,
        "source": "rules",
    }

    m = re.search(r"corner of (.+?) and (.+)$", t, re.I) or re.search(r"(?:coin|angle) (?:de |du )?(.+?) et (.+)$", t, re.I)
    if m:
        return {**base, "type": "position", "relation": "intersection",
                "landmarks": [clean(m.group(1)), clean(m.group(2))], "confidence": 0.7}

    m = re.search(r"\b(north|south|east|west|nord|sud|est|ouest)\s+(?:of|de|du|des)\s+(.+)$", t, re.I)
    if m:
        return {**base, "relation": CARDINAL[m.group(1).lower()],
                "landmark": _landmark(m.group(2)), "confidence": 0.7}

    m = re.search(r"\b(north|south|east|west|nord|sud|est|ouest)\b.*?\b(?:along|le long de)\s+(.+?)(?:,|$)", t, re.I)
    if m:
        dist = re.search(r"(\d+)\s*(m|meters|metres|mètres)\b", low)
        return {**base, "type": "movement", "relation": "along",
                "landmark": _landmark(m.group(2)), "direction": DIRECTIONS[m.group(1).lower()],
                "distance_m": float(dist.group(1)) if dist else None, "confidence": 0.65}

    m = re.search(r"\b(?:towards|toward|vers)\s+(.+)$", t, re.I)
    if m:
        return {**base, "type": "movement", "relation": "towards",
                "landmark": _landmark(m.group(1)), "confidence": 0.7}

    m = re.search(r"\b(near|close to|by|next to|près de|pres de)\s+(.+)$", t, re.I)
    if m:
        return {**base, "relation": "near", "landmark": _landmark(m.group(2)), "confidence": 0.6}

    m = re.search(r"\b(?:at|à|au|devant)\s+(.+)$", t, re.I)
    if m:
        return {**base, "relation": "at", "landmark": _landmark(m.group(1)), "confidence": 0.6}

    return base
