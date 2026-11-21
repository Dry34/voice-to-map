import sqlite3

from app.store.journal import Journal


def _append(j: Journal, text: str) -> dict:
    return j.append(unit="ALPHA 2", transcript=text, intent={"relation": "at"}, lat=48.85, lon=2.35,
                    uncertainty_m=30, resolved_landmark="Église", status="confirmed", origin_node="central")


def test_chain_is_valid(tmp_path):
    j = Journal(tmp_path / "e.db")
    first = _append(j, "one")
    second = _append(j, "two")
    assert second["prev_hash"] == first["hash"]
    assert j.verify() == {"ok": True, "count": 2, "signed": 0, "broken_at": None, "reason": None}


def test_update_and_delete_are_refused(tmp_path):
    j = Journal(tmp_path / "e.db")
    _append(j, "one")
    conn = sqlite3.connect(tmp_path / "e.db")
    for sql in ("UPDATE events SET transcript = 'x'", "DELETE FROM events"):
        try:
            conn.execute(sql)
            raise AssertionError("modification acceptée")
        except sqlite3.IntegrityError:
            pass


def test_tampering_is_detected(tmp_path):
    j = Journal(tmp_path / "e.db")
    for t in ("one", "two", "three"):
        _append(j, t)
    conn = sqlite3.connect(tmp_path / "e.db")
    conn.execute("DROP TRIGGER events_no_update")
    conn.execute("UPDATE events SET lat = 0 WHERE seq = 2")
    conn.commit()
    assert j.verify() == {"ok": False, "count": 1, "signed": 0, "broken_at": 2, "reason": "hash"}


def test_last_position(tmp_path):
    j = Journal(tmp_path / "e.db")
    assert j.last_position("ALPHA 2") is None
    _append(j, "one")
    assert j.last_position("ALPHA 2") == (48.85, 2.35)
