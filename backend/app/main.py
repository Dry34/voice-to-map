"""Point d'entrée. Lancer depuis backend/ : uvicorn app.main:app --reload"""

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import ROOT

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Voice-to-Map")
app.include_router(router)
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
