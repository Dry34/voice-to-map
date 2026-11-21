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
    claimed_at  TEXT NOT NULL
);
"""


class UnitRegistry:
    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

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
                "INSERT INTO units (unit, public_key, claimed_at) VALUES (?, ?, ?)",
                (unit, public_key, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            self._conn.commit()
            return True

    def release(self, unit: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM units WHERE unit = ?", (unit,))
            self._conn.commit()

    def all(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT unit, claimed_at FROM units ORDER BY unit")]
