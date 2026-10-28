import json

import httpx
import pytest

from leadgen.geo import ANKARA, Rect, grid
from leadgen.sources import places
from leadgen.sources.places import BudgetExceeded, PlacesClient, discover, estimate_details_cost

TILE = Rect(39.9, 32.8, 39.95, 32.85)


def make_client(handler, max_requests=100) -> PlacesClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return PlacesClient("test-key", max_requests, client=http, sleep=lambda _: None)


def test_ids_only_mask_is_free_sku():
    # Any other field in the Text Search mask would bill the whole request at Pro/Enterprise.
    assert places.IDS_ONLY_MASK == "places.id,nextPageToken"


def test_enterprise_fields_are_superset_of_pro():
    assert set(places.PRO_FIELDS) < set(places.ENTERPRISE_FIELDS)
    assert "websiteUri" not in places.PRO_FIELDS
    assert "websiteUri" in places.ENTERPRISE_FIELDS


def test_search_paginates_and_sends_mask_and_restriction():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append((req.headers["x-goog-fieldmask"], body))
        if "pageToken" not in body:
            return httpx.Response(200, json={"places": [{"id": "a"}, {"id": "b"}], "nextPageToken": "t1"})
        return httpx.Response(200, json={"places": [{"id": "c"}]})

    ids, saturated = make_client(handler).search_ids("halı saha", TILE)

    assert ids == ["a", "b", "c"] and not saturated
    mask, first = seen[0]
    assert mask == places.IDS_ONLY_MASK
    assert first["locationRestriction"]["rectangle"]["low"] == {"latitude": 39.9, "longitude": 32.8}
    assert first["regionCode"] == "TR" and first["languageCode"] == "tr"
    assert seen[1][1]["pageToken"] == "t1"


def test_discover_splits_saturated_tile_and_dedupes():
    calls = []

    next_page = {None: "x", "x": "y"}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        rect = body["locationRestriction"]["rectangle"]
        size = rect["high"]["latitude"] - rect["low"]["latitude"]
        calls.append(size)
        if size > 0.03:  # big tile: 3 pages x 20 = 60 results -> saturated
            page = body.get("pageToken")
            payload = {"places": [{"id": f"big-{page}-{n}"} for n in range(20)]}
            if page in next_page:
                payload["nextPageToken"] = next_page[page]
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"places": [{"id": "shared"}]})

    found = discover(make_client(handler), ["halı saha"], [TILE], min_size=0.01)

    assert len(found) == 60 + 1  # 60 from the saturated tile + "shared" found by all four sub-tiles
    assert found["shared"] == {"halı saha"}
    assert min(calls) < max(calls)  # it did recurse into smaller tiles


def test_budget_guard_stops_before_exceeding():
    client = make_client(lambda req: httpx.Response(200, json={"places": []}), max_requests=2)
    client.search_ids("q", TILE)
    client.search_ids("q", TILE)
    with pytest.raises(BudgetExceeded):
        client.search_ids("q", TILE)


def test_retries_on_429_then_succeeds():
    responses = iter([httpx.Response(429), httpx.Response(200, json={"places": [{"id": "a"}]})])
    ids, _ = make_client(lambda req: next(responses)).search_ids("q", TILE)
    assert ids == ["a"]


def test_details_uses_tier_mask_without_places_prefix():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["mask"] = req.headers["x-goog-fieldmask"]
        seen["path"] = req.url.path
        return httpx.Response(200, json={"id": "abc"})

    make_client(handler).details("abc", "enterprise")

    assert seen["path"] == "/v1/places/abc"
    assert "websiteUri" in seen["mask"].split(",")
    assert not any(f.startswith("places.") for f in seen["mask"].split(","))


def test_cost_estimate_applies_free_tier():
    assert estimate_details_cost(900, "enterprise") == 0
    assert estimate_details_cost(3000, "enterprise") == pytest.approx(40.0)
    assert estimate_details_cost(6000, "pro") == pytest.approx(17.0)


def test_grid_covers_bounds():
    tiles = grid(ANKARA, 0.06)
    assert min(t.low_lat for t in tiles) == ANKARA.low_lat
    assert max(t.high_lng for t in tiles) == ANKARA.high_lng
    assert all(t.size <= 0.06 + 1e-9 for t in tiles)
