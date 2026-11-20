"""Journal d'événements : SQLite, ajout seul, chaîné par hash SHA-256.

Chaque événement contient le hash du précédent. Toute modification ou
suppression dans l'historique casse la chaîne, et verify() la détecte.
Responsable : B.
"""

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64

# Champs couverts par le hash, dans cet ordre.
HASHED = [
    "id", "unit", "ts", "transcript", "intent_json", "lat", "lon",
    "uncertainty_m", "resolved_landmark", "status", "origin_node",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq               INTEGER PRIMARY KEY AUTOINCREMENT,
    id                TEXT UNIQUE NOT NULL,
    unit              TEXT NOT NULL,
    ts                TEXT NOT NULL,
    transcript        TEXT,
    intent_json       TEXT,
    lat               REAL,
    lon               REAL,
    uncertainty_m     REAL,
    resolved_landmark TEXT,
    status            TEXT NOT NULL,
    origin_node       TEXT NOT NULL,
    prev_hash         TEXT NOT NULL,
    hash              TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'journal en ajout seul'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'journal en ajout seul'); END;
"""


def compute_hash(row: dict, prev_hash: str) -> str:
    payload = json.dumps({k: row[k] for k in HASHED}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256((payload + prev_hash).encode("utf-8")).hexdigest()


def _to_event(row: sqlite3.Row) -> dict:
    ev = dict(row)
    ev["intent"] = json.loads(ev.pop("intent_json") or "null")
    return ev


class Journal:
    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def _last_hash(self) -> str:
        row = self._conn.execute("SELECT hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        return row["hash"] if row else GENESIS

    def append(
        self,
        *,
        unit: str,
        transcript: str | None,
        intent: dict | None,
        lat: float | None,
        lon: float | None,
        uncertainty_m: float | None,
        resolved_landmark: str | None,
        status: str,
        origin_node: str,
        ts: str | None = None,
    ) -> dict:
        row = {
            "id": str(uuid.uuid4()),
            "unit": unit,
            "ts": ts or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "transcript": transcript,
            "intent_json": json.dumps(intent, ensure_ascii=False, sort_keys=True) if intent is not None else None,
            # Colonnes REAL : on force des flottants pour que le hash soit identique une fois relu.
            "lat": float(lat) if lat is not None else None,
            "lon": float(lon) if lon is not None else None,
            "uncertainty_m": float(uncertainty_m) if uncertainty_m is not None else None,
            "resolved_landmark": resolved_landmark,
            "status": status,
            "origin_node": origin_node,
        }
        with self._lock:
            prev = self._last_hash()
            row["prev_hash"] = prev
            row["hash"] = compute_hash(row, prev)
            cols = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            cur = self._conn.execute(f"INSERT INTO events ({cols}) VALUES ({marks})", list(row.values()))
            self._conn.commit()
            stored = self._conn.execute("SELECT * FROM events WHERE seq = ?", (cur.lastrowid,)).fetchone()
        return _to_event(stored)

    def all(self, unit: str | None = None) -> list[dict]:
        if unit:
            rows = self._conn.execute("SELECT * FROM events WHERE unit = ? ORDER BY seq", (unit,))
        else:
            rows = self._conn.execute("SELECT * FROM events ORDER BY seq")
        return [_to_event(r) for r in rows]

    def last_position(self, unit: str) -> tuple[float, float] | None:
        row = self._conn.execute(
            "SELECT lat, lon FROM events WHERE unit = ? AND lat IS NOT NULL "
            "AND status = 'confirmed' ORDER BY seq DESC LIMIT 1",
            (unit,),
        ).fetchone()
        return (row["lat"], row["lon"]) if row else None

    def verify(self) -> dict:
        """Rejoue la chaîne et renvoie le premier événement qui ne correspond pas."""
        prev = GENESIS
        count = 0
        for row in self._conn.execute("SELECT * FROM events ORDER BY seq"):
            r = dict(row)
            if r["prev_hash"] != prev or compute_hash(r, prev) != r["hash"]:
                return {"ok": False, "count": count, "broken_at": r["seq"]}
            prev = r["hash"]
            count += 1
        return {"ok": True, "count": count, "broken_at": None}
