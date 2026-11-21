"""Signatures Ed25519 des annonces. Responsable : B.

Chaque navigateur crée sa propre paire de clés (WebCrypto, clé privée non exportable)
et réserve une unité avec sa clé publique. Ensuite, chaque annonce est signée :
le serveur refuse une annonce pour ALPHA 1 qui ne vient pas du terminal d'ALPHA 1.

Message signé, en texte :  voice-to-map|v1|<action>|<unité>|<ts_ms>|<nonce>|<sha256 du contenu>
"""

import base64
import hashlib
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PREFIX = "voice-to-map|v1"
MAX_SKEW_MS = 120_000  # horloges des terminaux décalées de 2 min au plus

_seen_nonces: dict[str, float] = {}


class SignatureError(Exception):
    pass


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_message(action: str, unit: str, ts_ms: int, nonce: str, content: bytes) -> str:
    return f"{PREFIX}|{action}|{unit}|{ts_ms}|{nonce}|{sha256_hex(content)}"


def verify_signature(public_key_b64: str, signature_b64: str, message: str) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        key.verify(base64.b64decode(signature_b64), message.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


MAX_LATE_MS = 24 * 3600 * 1000  # annonce gardée hors ligne : acceptée jusqu'à 24 h après


def check_fresh(ts_ms: int, nonce: str, late: bool = False) -> None:
    """Refuse les messages trop vieux et les rejeux du même message.

    late : annonce gardée par le terminal pendant une coupure réseau, envoyée au retour de la
    connexion. Elle peut dater de 24 h ; le rejeu est alors vérifié dans le journal (verify_request).
    """
    now = time.time()
    if ts_ms - now * 1000 > MAX_SKEW_MS:
        raise SignatureError("Horodatage dans le futur.")
    if now * 1000 - ts_ms > (MAX_LATE_MS if late else MAX_SKEW_MS):
        raise SignatureError("Horodatage trop éloigné de l'heure du serveur.")
    for n, exp in list(_seen_nonces.items()):
        if exp < now:
            del _seen_nonces[n]
    if not nonce or len(nonce) > 64 or nonce in _seen_nonces:
        raise SignatureError("Message déjà reçu (rejeu).")
    _seen_nonces[nonce] = now + 2 * MAX_SKEW_MS / 1000


def verify_request(*, action: str, unit: str, ts_ms: int, nonce: str, content: bytes,
                   public_key_b64: str, signature_b64: str, late: bool = False,
                   already_used=None) -> str:
    """Vérifie fraîcheur et signature. Renvoie le message signé (gardé dans le journal).

    already_used(message) -> bool : pour une annonce en différé, le message est-il déjà au journal ?
    """
    message = build_message(action, unit, ts_ms, nonce, content)
    if not verify_signature(public_key_b64, signature_b64, message):
        raise SignatureError("Signature invalide.")
    if late and already_used and already_used(message):
        raise SignatureError("Message déjà reçu (rejeu).")
    check_fresh(ts_ms, nonce, late=late)
    return message
