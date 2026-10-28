import pytest

from leadgen.cli import select_ids

ROWS = [
    {"id": "a", "primaryType": "stadium", "businessStatus": "OPERATIONAL"},
    {"id": "b", "primaryType": "gym", "businessStatus": "OPERATIONAL"},
    {"id": "c", "primaryType": "stadium", "businessStatus": "CLOSED_PERMANENTLY"},
    {"id": "d", "primaryType": "athletic_field"},
]


def test_select_keeps_only_operational_places_of_requested_types():
    assert select_ids(ROWS, ["stadium", "athletic_field"], 10) == ["a"]


def test_select_refuses_to_exceed_limit():
    with pytest.raises(ValueError):
        select_ids(ROWS + [{"id": "e", "primaryType": "stadium", "businessStatus": "OPERATIONAL"}], ["stadium"], 1)
