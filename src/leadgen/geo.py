from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    low_lat: float
    low_lng: float
    high_lat: float
    high_lng: float

    @property
    def size(self) -> float:
        """Longer side in degrees (~111 km per degree of latitude)."""
        return max(self.high_lat - self.low_lat, self.high_lng - self.low_lng)

    def split(self) -> list["Rect"]:
        mid_lat = (self.low_lat + self.high_lat) / 2
        mid_lng = (self.low_lng + self.high_lng) / 2
        return [
            Rect(self.low_lat, self.low_lng, mid_lat, mid_lng),
            Rect(self.low_lat, mid_lng, mid_lat, self.high_lng),
            Rect(mid_lat, self.low_lng, self.high_lat, mid_lng),
            Rect(mid_lat, mid_lng, self.high_lat, self.high_lng),
        ]

    def to_api(self) -> dict:
        return {
            "rectangle": {
                "low": {"latitude": self.low_lat, "longitude": self.low_lng},
                "high": {"latitude": self.high_lat, "longitude": self.high_lng},
            }
        }


# Urban Ankara incl. Sincan, Etimesgut, Pursaklar, Mamak and Golbasi. Widen for the other provinces.
ANKARA = Rect(39.72, 32.45, 40.10, 33.05)


def grid(bounds: Rect, step: float) -> list[Rect]:
    tiles = []
    lat = bounds.low_lat
    while lat < bounds.high_lat:
        lng = bounds.low_lng
        while lng < bounds.high_lng:
            tiles.append(
                Rect(
                    lat,
                    lng,
                    min(lat + step, bounds.high_lat),
                    min(lng + step, bounds.high_lng),
                )
            )
            lng += step
        lat += step
    return tiles
