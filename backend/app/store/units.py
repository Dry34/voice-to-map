"""Registre des unités : quelle clé publique a le droit de parler pour quelle unité.

Premier arrivé, premier servi : le premier navigateur qui réserve ALPHA 1 en devient
le seul terminal, jusqu'à ce qu'il la libère (ou qu'on la libère avec
scripts/release_unit.py si le navigateur a perdu sa clé). Responsable : B.
"""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
    unit        TEXT PRIMARY KEY,
    public_key  TEXT NOT NULL,
    claimed_at  TEXT NOT NULL,
    color       TEXT
);
"""

# Une couleur par unité, la même sur tous les écrans. Ni rouge (ennemis) ni orange (« vous »).
PALETTE = ["#4c9aff", "#3fb950", "#a371f7", "#e3b341", "#39c5cf", "#f778ba",
           "#9be564", "#79c0ff", "#c297ff", "#2ea0a0", "#db61a2", "#d4d4d4"]


class UnitRegistry:
    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        if "color" not in {r["name"] for r in self._conn.execute("PRAGMA table_info(units)")}:
            self._conn.execute("ALTER TABLE units ADD COLUMN color TEXT")
        self._lock = threading.Lock()

    def _free_color(self) -> str:
        used = {r["color"] for r in self._conn.execute("SELECT color FROM units")}
        free = [c for c in PALETTE if c not in used]
        return free[0] if free else PALETTE[len(used) % len(PALETTE)]

    def key_of(self, unit: str) -> str | None:
        row = self._conn.execute("SELECT public_key FROM units WHERE unit = ?", (unit,)).fetchone()
        return row["public_key"] if row else None

    def claim(self, unit: str, public_key: str) -> bool:
        """True si l'unité est libre ou déjà à cette clé, False si un autre terminal la tient."""
        with self._lock:
            current = self.key_of(unit)
            if current is not None:
                return current == public_key
            self._conn.execute(
                "INSERT INTO units (unit, public_key, claimed_at, color) VALUES (?, ?, ?, ?)",
                (unit, public_key, datetime.now(timezone.utc).isoformat(timespec="seconds"), self._free_color()),
            )
            self._conn.commit()
            return True

    def transfer(self, unit: str, public_key: str) -> None:
        """Donne l'unité à une nouvelle clé (l'ancien terminal a planté ou est fermé)."""
        with self._lock:
            self._conn.execute(
                "UPDATE units SET public_key = ?, claimed_at = ? WHERE unit = ?",
                (public_key, datetime.now(timezone.utc).isoformat(timespec="seconds"), unit),
            )
            self._conn.commit()

    def release(self, unit: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM units WHERE unit = ?", (unit,))
            self._conn.commit()

    def all(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT unit, public_key, claimed_at, color FROM units ORDER BY unit")]
