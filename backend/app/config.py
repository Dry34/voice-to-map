"""Configuration lue depuis le fichier .env à la racine du repo."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    mistral_api_key: str = os.getenv("MISTRAL_API_KEY", "")
    mistral_model: str = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
    stt_model: str = os.getenv("STT_MODEL", "voxtral-mini-latest")
    stt_language: str = os.getenv("STT_LANGUAGE", "fr")  # fr gère aussi l anglais ; vide = détection auto
    node_mode: str = os.getenv("NODE_MODE", "central")
    db_path: Path = ROOT / os.getenv("DB_PATH", "data/events.db")
    landmarks_path: Path = ROOT / "data" / "landmarks" / "zone.json"
    zone_name: str = os.getenv("ZONE_NAME", "Paris centre")
    zone_lat: float = float(os.getenv("ZONE_LAT", "48.8584"))
    zone_lon: float = float(os.getenv("ZONE_LON", "2.3470"))
    zone_radius_m: float = float(os.getenv("ZONE_RADIUS_M", "2000"))


settings = Settings()
