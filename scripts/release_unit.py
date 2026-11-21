"""Libère une unité dont le navigateur a perdu sa clé (données effacées, autre machine).

    backend/.venv/bin/python scripts/release_unit.py "ALPHA 1"
    backend/.venv/bin/python scripts/release_unit.py --list
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import settings  # noqa: E402
from app.store.units import UnitRegistry  # noqa: E402

registry = UnitRegistry(settings.db_path)
if len(sys.argv) < 2 or sys.argv[1] == "--list":
    for u in registry.all():
        print(f"{u['unit']}  (réservée le {u['claimed_at']})")
else:
    unit = " ".join(sys.argv[1:]).upper()
    registry.release(unit)
    print(f"{unit} libérée.")
