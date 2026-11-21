"""Journal d'événements : SQLite, ajout seul, chaîné par hash SHA-256.

Chaque événement contient le hash du précédent. Toute modification ou
suppression dans l'historique casse la chaîne, et verify() la détecte.

Une annonce venant d'un terminal garde aussi le message signé par ce terminal,
sa signature Ed25519 et sa clé publique : verify() revérifie chaque signature.
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
# Ajoutés au hash seulement pour les événements signés (les anciens restent valides).
SIGNED = ["signed_message", "signature", "public_key"]

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
    hash              TEXT NOT NULL,
    signed_message    TEXT,
    signature         TEXT,
    public_key        TEXT
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'journal en ajout seul'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'journal en ajout seul'); END;
"""


def compute_hash(row: dict, prev_hash: str) -> str:
    fields = HASHED + (SIGNED if row.get("signature") else [])
    payload = json.dumps({k: row[k] for k in fields}, sort_keys=True, ensure_ascii=False)
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
        # Base créée avant les signatures : on ajoute les colonnes manquantes.
        existing = {r["name"] for r in self._conn.execute("PRAGMA table_info(events)")}
        for col in SIGNED:
            if col not in existing:
                self._conn.execute(f"ALTER TABLE events ADD COLUMN {col} TEXT")
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
        signed: dict | None = None,
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
        if signed:
            row.update({k: signed[k] for k in SIGNED})
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
        """Dernière position confirmée, en ignorant tout ce qui précède un départ (status 'left')."""
        left = self._conn.execute(
            "SELECT coalesce(max(seq), 0) AS s FROM events WHERE (unit = ? AND status = 'left') OR status = 'cleared'",
            (unit,),
        ).fetchone()["s"]
        row = self._conn.execute(
            "SELECT lat, lon FROM events WHERE unit = ? AND seq > ? AND lat IS NOT NULL "
            "AND status = 'confirmed' "
            "AND coalesce(json_extract(intent_json, '$.subject'), 'self') != 'enemy' "  # ennemi vu ≠ position de l'unité
            "ORDER BY seq DESC LIMIT 1",
            (unit, left),
        ).fetchone()
        return (row["lat"], row["lon"]) if row else None

    def enemies(self) -> dict[str, dict]:
        """État de chaque ennemi : {"E3": {"alive": bool, "lat", "lon"}}, rejoué depuis le journal."""
        state: dict[str, dict] = {}
        rows = self._conn.execute(
            "SELECT status, lat, lon, json_extract(intent_json, '$.enemy_id') AS eid, "
            "json_extract(intent_json, '$.sim') AS sim FROM events "
            "WHERE json_extract(intent_json, '$.subject') = 'enemy' AND eid IS NOT NULL "
            "AND seq > ? ORDER BY seq",
            (self.last_clear(),),
        )
        for r in rows:
            e = state.setdefault(r["eid"], {"alive": True, "lat": None, "lon": None, "home": None})
            if r["status"] == "enemy_down":
                e["alive"] = False
            elif r["status"] == "confirmed" and r["lat"] is not None:
                e.update(lat=r["lat"], lon=r["lon"])
                if not r["sim"]:  # là où une unité l'a vu : la simulation le fait rôder autour
                    e["home"] = (r["lat"], r["lon"])
        return state

    def groups(self) -> dict[str, list[str]]:
        """Escouades actuelles {"G1": ["ALPHA 1", "TOM 2"]}, rejouées depuis la dernière remise à zéro.

        Événements « group » : intent.type = group_join (members, group_id) ou group_leave.
        Une unité qui quitte la carte (« left ») quitte aussi son escouade ; une escouade
        réduite à une unité est dissoute.
        """
        of: dict[str, str] = {}
        rows = self._conn.execute(
            "SELECT unit, status, intent_json FROM events WHERE seq > ? AND status IN ('group', 'left') ORDER BY seq",
            (self.last_clear(),),
        )

        def leave(u: str) -> None:
            gid = of.pop(u, None)
            rest = [m for m, g in of.items() if g == gid]
            if gid and len(rest) < 2:
                for m in rest:
                    of.pop(m, None)

        for r in rows:
            intent = json.loads(r["intent_json"] or "{}")
            if r["status"] == "left" or intent.get("type") == "group_leave":
                leave(r["unit"])
            elif intent.get("type") == "group_join":
                gid = intent["group_id"]
                for m in intent.get("members", []):
                    if of.get(m) not in (None, gid):
                        leave(m)
                    of[m] = gid
        out: dict[str, list[str]] = {}
        for m, g in of.items():
            out.setdefault(g, []).append(m)
        return {g: sorted(ms) for g, ms in sorted(out.items()) if len(ms) >= 2}

    def group_of(self, unit: str) -> tuple[str, list[str]] | None:
        for gid, members in self.groups().items():
            if unit in members:
                return gid, members
        return None

    def units_on_map(self) -> list[str]:
        """Unités qui ont une position depuis la dernière remise à zéro (hors SIMULATION / SYSTÈME)."""
        rows = self._conn.execute(
            "SELECT DISTINCT unit FROM events WHERE seq > ? AND status = 'confirmed' AND lat IS NOT NULL "
            "AND coalesce(json_extract(intent_json, '$.subject'), 'self') != 'enemy'",
            (self.last_clear(),),
        )
        return [r["unit"] for r in rows if self.last_position(r["unit"])]

    def last_heading(self, unit: str) -> float | None:
        """Où regarde l'unité : dernier cap enregistré (« je regarde à 3h » ou sens de marche), ou None."""
        from ..geo.geometry import bearing_between, haversine

        row = self._conn.execute(
            "SELECT json_extract(intent_json, '$.heading_deg') AS h FROM events WHERE unit = ? AND seq > ? "
            "AND status IN ('confirmed', 'facing') AND json_extract(intent_json, '$.heading_deg') IS NOT NULL "
            "AND coalesce(json_extract(intent_json, '$.subject'), 'self') != 'enemy' ORDER BY seq DESC LIMIT 1",
            (unit, self.last_clear()),
        ).fetchone()
        if row:
            return float(row["h"])
        rows = self._conn.execute(
            "SELECT lat, lon, json_extract(intent_json, '$.route') AS route FROM events "
            "WHERE unit = ? AND seq > ? AND status = 'confirmed' AND lat IS NOT NULL "
            "AND coalesce(json_extract(intent_json, '$.subject'), 'self') != 'enemy' ORDER BY seq DESC LIMIT 2",
            (unit, self.last_clear()),
        ).fetchall()
        if not rows:
            return None
        # Direction générale entre les deux dernières positions (le dernier bout d'itinéraire
        # n'est pas fiable : l'arrivée dans une cour peut partir dans n'importe quel sens).
        if len(rows) == 2 and haversine((rows[1]["lat"], rows[1]["lon"]), (rows[0]["lat"], rows[0]["lon"])) > 5:
            return bearing_between((rows[1]["lat"], rows[1]["lon"]), (rows[0]["lat"], rows[0]["lon"]))
        return None

    def last_route(self, unit: str) -> list | None:
        """Itinéraire du dernier déplacement de l'unité (depuis la remise à zéro), ou None."""
        row = self._conn.execute(
            "SELECT json_extract(intent_json, '$.route') AS r FROM events WHERE unit = ? AND seq > ? "
            "AND status = 'confirmed' AND coalesce(json_extract(intent_json, '$.subject'), 'self') != 'enemy' "
            "ORDER BY seq DESC LIMIT 1",
            (unit, self.last_clear()),
        ).fetchone()
        return json.loads(row["r"]) if row and row["r"] else None

    def since_clear(self) -> list[dict]:
        """Événements depuis la dernière remise à zéro (elle comprise) : ce que montrent les cartes."""
        rows = self._conn.execute("SELECT * FROM events WHERE seq >= ? ORDER BY seq", (self.last_clear(),))
        return [_to_event(r) for r in rows]

    def get(self, seq: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM events WHERE seq = ?", (seq,)).fetchone()
        return _to_event(row) if row else None

    def confirmable(self, unit: str) -> list[dict]:
        """Annonces « à confirmer » de l'unité, avec un lieu trouvé, pas encore confirmées (récentes d'abord)."""
        rows = self._conn.execute(
            "SELECT * FROM events WHERE unit = ? AND seq > ? AND status = 'pending_review' AND lat IS NOT NULL "
            "AND seq NOT IN (SELECT json_extract(intent_json, '$.confirmed_from') FROM events "
            "                WHERE json_extract(intent_json, '$.confirmed_from') IS NOT NULL) "
            "ORDER BY seq DESC",
            (unit, self.last_clear()),
        )
        return [_to_event(r) for r in rows]

    def last_clear(self) -> int:
        """seq de la dernière remise à zéro de la carte (0 si jamais)."""
        return self._conn.execute(
            "SELECT coalesce(max(seq), 0) AS s FROM events WHERE status = 'cleared'"
        ).fetchone()["s"]

    def next_enemy_id(self) -> str:
        nums = [int(k[1:]) for k in self.enemies() if k[1:].isdigit()]
        return f"E{max(nums, default=0) + 1}"

    def verify(self) -> dict:
        """Rejoue la chaîne et les signatures, renvoie le premier événement qui ne correspond pas."""
        from ..security.signing import verify_signature

        prev = GENESIS
        count = signed = 0
        for row in self._conn.execute("SELECT * FROM events ORDER BY seq"):
            r = dict(row)
            if r["prev_hash"] != prev or compute_hash(r, prev) != r["hash"]:
                return {"ok": False, "count": count, "signed": signed, "broken_at": r["seq"], "reason": "hash"}
            if r["signature"]:
                parts = (r["signed_message"] or "").split("|")
                if (len(parts) != 7 or parts[3] != r["unit"]
                        or not verify_signature(r["public_key"], r["signature"], r["signed_message"])):
                    return {"ok": False, "count": count, "signed": signed, "broken_at": r["seq"], "reason": "signature"}
                signed += 1
            prev = r["hash"]
            count += 1
        return {"ok": True, "count": count, "signed": signed, "broken_at": None, "reason": None}
