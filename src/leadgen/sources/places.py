"""Google Places API (New) client, built around SKU cost.

Billing tiers (developers.google.com/maps/billing-and-pricing/pricing, checked 2026-10-27):
  Text Search Essentials (IDs Only)  free, unlimited      -> discovery
  Place Details Pro                  5,000 free / month   -> triage (name, type, status)
  Place Details Enterprise           1,000 free / month   -> phone, website, rating, hours
A request is billed at the highest tier among the fields in its mask, so the masks below are fixed
constants: adding a field to one silently changes what every call costs.
"""

import time
from collections.abc import Callable
from typing import Any

import httpx

from leadgen.geo import Rect

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
RESULT_CAP = 60  # Text Search returns at most 60 results per query across all pages

IDS_ONLY_MASK = "places.id,nextPageToken"
PRO_FIELDS = (
    "id",
    "displayName",
    "primaryType",
    "types",
    "businessStatus",
    "formattedAddress",
    "location",
    "googleMapsUri",
)
ENTERPRISE_FIELDS = PRO_FIELDS + (
    "nationalPhoneNumber",
    "internationalPhoneNumber",
    "websiteUri",
    "regularOpeningHours",
    "rating",
    "userRatingCount",
)
TIERS = {"pro": PRO_FIELDS, "enterprise": ENTERPRISE_FIELDS}

# (free calls per month, USD per 1,000 after that) for Place Details
DETAILS_PRICING = {"pro": (5_000, 17.0), "enterprise": (1_000, 20.0)}


class BudgetExceeded(RuntimeError):
    pass


class PlacesError(RuntimeError):
    pass


class PlacesClient:
    def __init__(
        self,
        api_key: str,
        max_requests: int,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._key = api_key
        self._max = max_requests
        self._http = client or httpx.Client(timeout=30)
        self._sleep = sleep
        self.requests = 0

    def _send(self, method: str, url: str, mask: str, **kwargs: Any) -> dict:
        headers = {"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": mask}
        for attempt in range(4):
            if self.requests >= self._max:
                raise BudgetExceeded(f"max_requests={self._max} reached")
            self.requests += 1
            resp = self._http.request(method, url, headers=headers, **kwargs)
            if resp.status_code in (429, 500, 503) and attempt < 3:
                self._sleep(2**attempt)
                continue
            if resp.status_code >= 400:
                raise PlacesError(f"{resp.status_code} {resp.text[:300]}")
            return resp.json()
        raise AssertionError("unreachable")

    def search_ids(self, query: str, rect: Rect) -> tuple[list[str], bool]:
        """All place IDs for `query` inside `rect`; the flag is True when the 60-result cap was hit."""
        ids: list[str] = []
        token: str | None = None
        while True:
            body: dict[str, Any] = {
                "textQuery": query,
                "locationRestriction": rect.to_api(),
                "languageCode": "tr",
                "regionCode": "TR",
                "pageSize": 20,
            }
            if token:
                body["pageToken"] = token
            data = self._send("POST", SEARCH_URL, IDS_ONLY_MASK, json=body)
            ids += [p["id"] for p in data.get("places", [])]
            token = data.get("nextPageToken")
            if not token:
                return ids, len(ids) >= RESULT_CAP

    def details(self, place_id: str, tier: str) -> dict:
        return self._send(
            "GET",
            DETAILS_URL.format(place_id=place_id),
            ",".join(TIERS[tier]),
            params={"languageCode": "tr", "regionCode": "TR"},
        )


def discover(
    client: PlacesClient,
    queries: list[str],
    tiles: list[Rect],
    min_size: float,
) -> dict[str, set[str]]:
    """Place ID -> queries that found it. A saturated tile is split until `min_size` so nothing is cut off at 60."""
    found: dict[str, set[str]] = {}

    def visit(query: str, tile: Rect) -> None:
        ids, saturated = client.search_ids(query, tile)
        for place_id in ids:
            found.setdefault(place_id, set()).add(query)
        if saturated and tile.size > min_size:
            for sub in tile.split():
                visit(query, sub)

    for tile in tiles:
        for query in queries:
            visit(query, tile)
    return found


def estimate_details_cost(n: int, tier: str) -> float:
    free, per_1000 = DETAILS_PRICING[tier]
    return max(0, n - free) * per_1000 / 1000
