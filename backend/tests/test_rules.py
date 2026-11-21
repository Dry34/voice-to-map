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
