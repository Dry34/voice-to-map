from app.geo.geometry import destination, haversine
from app.intent.rules import parse


def test_south_of_church():
    i = parse("I am south of the church.")
    assert i["type"] == "position"
    assert i["relation"] == "south_of"
    assert i["landmark"] == {"name": "church", "kind": "church"}


def test_towards_street():
    i = parse("I moved towards 35th street.")
    assert i["type"] == "movement"
    assert i["relation"] == "towards"
    assert i["landmark"]["kind"] == "street"


def test_intersection():
    i = parse("At the corner of 35th and 8th Avenue.")
    assert i["relation"] == "intersection"
    assert i["landmarks"] == ["35th", "8th Avenue"]


def test_along_with_distance():
    i = parse("Moving north along 8th Avenue, about 200 meters.")
    assert i["relation"] == "along"
    assert i["direction"] == "N"
    assert i["distance_m"] == 200


def test_french():
    i = parse("Je suis au sud de l'église.")
    assert i["relation"] == "south_of"
    assert i["landmark"]["name"] == "église"


def test_destination_120m_south():
    start = (48.8584, 2.3470)
    end = destination(start, 180, 120)
    assert abs(haversine(start, end) - 120) < 0.5
    assert end[0] < start[0]


def test_crossing_of_two_streets():
    from app.geo.geometry import closest_pair
    horizontal = [[(48.860, 2.340), (48.860, 2.344)]]
    vertical = [[(48.858, 2.342), (48.862, 2.342)]]
    point, gap = closest_pair(horizontal, vertical)
    assert gap == 0.0
    assert abs(point[0] - 48.860) < 1e-9 and abs(point[1] - 2.342) < 1e-9


def test_spoken_callsign():
    i = parse("Alpha 1 et aux Invalides.")
    assert i["callsign"] == "ALPHA 1"
    i = parse("Bravo deux, I am south of the church.")
    assert i["callsign"] == "BRAVO 2"
    assert i["relation"] == "south_of"
    assert parse("I am at the Louvre.")["callsign"] is None


def test_normalize_callsign():
    from app.intent.rules import normalize_callsign
    assert normalize_callsign("alpha one") == "ALPHA 1"
    assert normalize_callsign("Charlie-3") == "CHARLIE 3"
    assert normalize_callsign("Tour Eiffel") is None


def test_enemy_near_landmark():
    i = parse("Contact, trois ennemis au nord de l'église.")
    assert i["subject"] == "enemy" and i["count"] == 3
    assert i["relation"] == "north_of" and i["landmark"]["kind"] == "église"


def test_enemy_relative_to_me():
    i = parse("Ennemi à 200 mètres au nord-est.")
    assert i["subject"] == "enemy"
    assert (i["relation"], i["direction"], i["distance_m"]) == ("offset", "NE", 200)


def test_own_position_is_not_enemy():
    assert parse("I am south of the church.")["subject"] == "self"


def test_enemy_sighting_does_not_move_unit(tmp_path):
    from app.store.journal import Journal
    j = Journal(tmp_path / "e.db")
    common = dict(transcript="x", uncertainty_m=30, resolved_landmark="x", status="confirmed", origin_node="central")
    j.append(unit="ALPHA 1", intent={"subject": "self"}, lat=48.85, lon=2.35, **common)
    j.append(unit="ALPHA 1", intent={"subject": "enemy"}, lat=48.90, lon=2.40, **common)
    assert j.last_position("ALPHA 1") == (48.85, 2.35)


def test_name_check_rejects_unrelated_place():
    from app.geo.osm import name_matches
    assert not name_matches("bois de Boulogne", "Jardins du Trocadéro")
    assert name_matches("Dame de Paris", "Cathédrale Notre-Dame de Paris")
    assert not name_matches("Dame de Paris", "Église Notre-Dame de Grâce de Passy")


def test_enemy_kill_and_ids():
    i = parse("E3 neutralisé.")
    assert (i["subject"], i["type"], i["enemy_id"]) == ("enemy", "kill", "E3")
    assert parse("Ennemi 2 abattu.")["enemy_id"] == "E2"
    i = parse("E4 au sud de l'église.")
    assert (i["subject"], i["enemy_id"], i["relation"]) == ("enemy", "E4", "south_of")


def test_enemy_state(tmp_path):
    from app.store.journal import Journal
    j = Journal(tmp_path / "e.db")
    common = dict(transcript="x", uncertainty_m=30, resolved_landmark="x", origin_node="central")
    assert j.next_enemy_id() == "E1"
    j.append(unit="A", intent={"subject": "enemy", "enemy_id": "E1"}, lat=1.0, lon=2.0, status="confirmed", **common)
    j.append(unit="A", intent={"subject": "enemy", "enemy_id": "E2"}, lat=3.0, lon=4.0, status="confirmed", **common)
    j.append(unit="A", intent={"subject": "enemy", "type": "kill", "enemy_id": "E1"}, lat=None, lon=None, status="enemy_down", **common)
    st = j.enemies()
    assert st["E1"]["alive"] is False and st["E2"]["alive"] is True and st["E2"]["lat"] == 3.0
    assert j.next_enemy_id() == "E3"


def test_clear_resets_positions_and_enemies(tmp_path):
    from app.store.journal import Journal
    j = Journal(tmp_path / "e.db")
    common = dict(transcript="x", uncertainty_m=30, resolved_landmark="x", origin_node="central")
    j.append(unit="A", intent={"subject": "self"}, lat=1.0, lon=2.0, status="confirmed", **common)
    j.append(unit="A", intent={"subject": "enemy", "enemy_id": "E1"}, lat=1.0, lon=2.0, status="confirmed", **common)
    j.append(unit="SYSTÈME", intent={"type": "clear"}, lat=None, lon=None, status="cleared", **common)
    assert j.last_position("A") is None and j.enemies() == {} and j.next_enemy_id() == "E1"


def test_kill_without_number():
    i = parse("J'ai tué l'ennemi.")
    assert (i["subject"], i["type"], i["enemy_id"]) == ("enemy", "kill", None)


def test_project_on_line():
    from app.geo.geometry import project_on_line
    line = [(48.860, 2.340), (48.860, 2.344)]  # rue est-ouest
    q, bearing = project_on_line((48.8603, 2.342), line)
    assert abs(q[0] - 48.860) < 1e-9 and abs(q[1] - 2.342) < 1e-6
    assert round(bearing) == 90


def test_multi_kill_parsing():
    i = parse("J'ai éliminé ennemi 1, ennemi 2, ennemi 3, ennemi 4.")
    assert (i["type"], i["scope"], i["enemy_ids"]) == ("kill", "listed", ["E1", "E2", "E3", "E4"])
    i = parse("Je viens d'exterminer les cinq ennemis.")
    assert (i["type"], i["scope"], i["count"]) == ("kill", "nearest", 5)
    assert parse("J'élimine les ennemis autour de moi.")["scope"] == "around_me"
    assert parse("Tous les ennemis sont neutralisés.")["scope"] == "all"


def test_closest_unit_tolerates_transcription():
    from app.geo.resolver import _closest_unit
    units = ["TOM 2", "EPSILON", "ALPHA 1"]
    assert _closest_unit("TOME 2", units) == "TOM 2"
    assert _closest_unit("TOM 3", units) is None      # numéro différent


def test_groups_join_leave(tmp_path):
    from app.store.journal import Journal
    j = Journal(tmp_path / "e.db")
    common = dict(transcript="x", lat=None, lon=None, uncertainty_m=None, resolved_landmark="G1", origin_node="central")
    j.append(unit="A 1", intent={"type": "group_join", "group_id": "G1", "members": ["A 1", "B 2"]}, status="group", **common)
    j.append(unit="C 3", intent={"type": "group_join", "group_id": "G1", "members": ["C 3", "A 1"]}, status="group", **common)
    assert j.groups() == {"G1": ["A 1", "B 2", "C 3"]}
    j.append(unit="B 2", intent={"type": "group_leave", "group_id": "G1"}, status="group", **common)
    assert j.groups() == {"G1": ["A 1", "C 3"]}
    j.append(unit="C 3", intent={"type": "left"}, status="left", **common)  # quitte la carte
    assert j.groups() == {}  # plus qu'une unité : escouade dissoute


def test_military_clock():
    i = parse("Enemy à 6h00.")
    assert (i["subject"], i["relation"], i["clock"]) == ("enemy", "offset", 6)
    i = parse("Contact à 3 heures, 200 mètres.")
    assert (i["clock"], i["distance_m"]) == (3, 200)


def test_bearing_between():
    from app.geo.geometry import bearing_between
    assert round(bearing_between((48.85, 2.35), (48.86, 2.35))) == 0
    assert round(bearing_between((48.85, 2.35), (48.84, 2.35))) == 180
