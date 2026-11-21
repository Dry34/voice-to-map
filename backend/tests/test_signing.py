import base64
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.security.signing import SignatureError, build_message, verify_request
from app.store.journal import Journal
from app.store.units import UnitRegistry


def make_key():
    priv = Ed25519PrivateKey.generate()
    pub = base64.b64encode(priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    return priv, pub


def sign(priv, action, unit, content):
    ts, nonce = int(time.time() * 1000), uuid.uuid4().hex
    sig = base64.b64encode(priv.sign(build_message(action, unit, ts, nonce, content).encode())).decode()
    return ts, nonce, sig


def test_valid_signature_and_replay():
    priv, pub = make_key()
    ts, nonce, sig = sign(priv, "report", "ALPHA 1", b"I am at the Louvre.")
    kw = dict(action="report", unit="ALPHA 1", ts_ms=ts, nonce=nonce, content=b"I am at the Louvre.",
              public_key_b64=pub, signature_b64=sig)
    assert verify_request(**kw).split("|")[3] == "ALPHA 1"
    with pytest.raises(SignatureError):  # même message rejoué
        verify_request(**kw)


def test_other_terminal_cannot_speak_for_unit():
    priv, pub = make_key()
    other, _ = make_key()
    ts, nonce, sig = sign(other, "report", "ALPHA 1", b"I am at the Louvre.")
    with pytest.raises(SignatureError):
        verify_request(action="report", unit="ALPHA 1", ts_ms=ts, nonce=nonce, content=b"I am at the Louvre.",
                       public_key_b64=pub, signature_b64=sig)


def test_tampered_content_rejected():
    priv, pub = make_key()
    ts, nonce, sig = sign(priv, "report", "ALPHA 1", b"I am at the Louvre.")
    with pytest.raises(SignatureError):
        verify_request(action="report", unit="ALPHA 1", ts_ms=ts, nonce=nonce, content=b"I am at Notre-Dame.",
                       public_key_b64=pub, signature_b64=sig)


def test_registry_first_come(tmp_path):
    reg = UnitRegistry(tmp_path / "t.db")
    assert reg.claim("ALPHA 1", "key-a")
    assert reg.claim("ALPHA 1", "key-a")
    assert not reg.claim("ALPHA 1", "key-b")
    reg.release("ALPHA 1")
    assert reg.claim("ALPHA 1", "key-b")


def test_journal_checks_signatures(tmp_path):
    j = Journal(tmp_path / "t.db")
    priv, pub = make_key()
    ts, nonce, sig = sign(priv, "report", "ALPHA 1", b"x")
    msg = build_message("report", "ALPHA 1", ts, nonce, b"x")
    common = dict(transcript="x", intent=None, lat=48.86, lon=2.34, uncertainty_m=30,
                  resolved_landmark="Louvre", status="confirmed", origin_node="central")
    j.append(unit="ALPHA 1", signed={"signed_message": msg, "signature": sig, "public_key": pub}, **common)
    j.append(unit="BRAVO 2", **common)  # ancien format, sans signature
    r = j.verify()
    assert r["ok"] and r["count"] == 2 and r["signed"] == 1

    # Un événement signé par ALPHA 1 mais rangé sous une autre unité est détecté.
    j2 = Journal(tmp_path / "t2.db")
    j2.append(unit="BRAVO 2", signed={"signed_message": msg, "signature": sig, "public_key": pub}, **common)
    assert j2.verify() == {"ok": False, "count": 0, "signed": 0, "broken_at": 1, "reason": "signature"}
