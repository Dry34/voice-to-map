"""Transcription audio -> texte. Responsable : B. Prévu samedi.

À faire :
- appeler l'API de transcription Mistral (Voxtral) avec le fichier webm reçu du navigateur ;
- renvoyer le texte, puis le passer à pipeline.process_report().
En attendant, le front peut utiliser la Web Speech API du navigateur et envoyer le texte.
"""


async def transcribe(audio: bytes, mime_type: str = "audio/webm") -> str:
    raise NotImplementedError("Transcription Voxtral prévue samedi (tâche B).")
