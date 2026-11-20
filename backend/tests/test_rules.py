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
