"""Point d'entrée. Lancer depuis backend/ : uvicorn app.main:app --reload"""

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import sim
from .api.routes import router
from .config import ROOT

logging.basicConfig(level=logging.INFO)



class FrontendFiles(StaticFiles):
    """Le front change souvent pendant le hackathon : le navigateur revérifie chaque fichier
    à chaque chargement (sinon il garde d'anciens modules JS et la page casse)."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(title="Voice-to-Map")
app.include_router(router)


@app.on_event("startup")
async def _start_simulation() -> None:
    sim.start()  # tourne en fond, ne fait rien tant que la simulation est coupée

app.mount("/", FrontendFiles(directory=ROOT / "frontend", html=True), name="frontend")
