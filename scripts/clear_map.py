"""Remet la carte à zéro : plus aucune unité, plus aucun ennemi.

Le journal n'efface rien : on ajoute un événement « cleared ». Les cartes rejouent
le journal et vident tout à cet endroit ; l'historique avant reste consultable.
Les navigateurs ouverts doivent être rechargés.

    backend/.venv/bin/python scripts/clear_map.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import settings  # noqa: E402
from app.store.journal import Journal  # noqa: E402
from app.store.units import UnitRegistry  # noqa: E402

journal = Journal(settings.db_path)
registry = UnitRegistry(settings.db_path)
for u in registry.all():
    registry.release(u["unit"])
    print(f"{u['unit']} libérée")
journal.append(unit="SYSTÈME", transcript=None, intent={"type": "clear"}, lat=None, lon=None,
               uncertainty_m=None, resolved_landmark=None, status="cleared", origin_node=settings.node_mode)
print("Carte remise à zéro. Intégrité :", journal.verify())
