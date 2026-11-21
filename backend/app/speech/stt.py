"""Transcription audio -> texte avec Voxtral (Mistral). Responsable : B.

Le navigateur envoie l'enregistrement tel quel (webm sur Chrome, mp4 sur Safari) :
Voxtral accepte ces formats, pas besoin de conversion.
"""

import httpx

from ..config import settings

TRANSCRIBE_URL = "https://api.mistral.ai/v1/audio/transcriptions"


class TranscriptionError(RuntimeError):
    pass


async def transcribe(audio: bytes, filename: str = "annonce.webm", mime_type: str = "audio/webm") -> str:
    if not settings.mistral_api_key:
        raise TranscriptionError("Pas de clé Mistral : transcription impossible.")
    data = {"model": settings.stt_model}
    if settings.stt_language:
        # Fixer la langue améliore nettement le résultat (« Alphatou » -> « Alpha 2 »).
        data["language"] = settings.stt_language
    headers = {"Authorization": f"Bearer {settings.mistral_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(TRANSCRIBE_URL, headers=headers, data=data,
                                  files={"file": (filename, audio, mime_type)})
            r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise TranscriptionError(f"Voxtral a refusé l'audio (HTTP {exc.response.status_code}).") from exc
    except httpx.HTTPError as exc:
        raise TranscriptionError("Voxtral injoignable.") from exc
    return (r.json().get("text") or "").strip()
