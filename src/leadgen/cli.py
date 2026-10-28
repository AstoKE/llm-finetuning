import argparse
import json
import os
import sys
from pathlib import Path

from leadgen.geo import ANKARA, grid
from leadgen.sources.places import (
    BudgetExceeded,
    PlacesClient,
    discover,
    estimate_details_cost,
)

DEFAULT_QUERIES = [
    "halı saha",
    "spor tesisi",
    "spor kompleksi",
    "futbol sahası",
    "basketbol sahası",
    "voleybol sahası",
    "tenis kortu",
    "padel kortu",
]


def _client(max_requests: int) -> PlacesClient:
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        sys.exit("GOOGLE_MAPS_API_KEY is not set (copy .env.example to .env, run with `uv run --env-file .env`)")
    return PlacesClient(key, max_requests)


def _read_ids(path: Path) -> list[str]:
    ids = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        ids.append(row.get("place_id") or row["id"])
    return ids


def cmd_discover(args: argparse.Namespace) -> None:
    client = _client(args.max_requests)
    tiles = grid(ANKARA, args.step)
    print(f"{len(tiles)} tiles x {len(args.queries)} queries (IDs-only Text Search is free)")
    try:
        found = discover(client, args.queries, tiles, args.min_size)
    except BudgetExceeded as exc:
        sys.exit(f"stopped: {exc}; nothing written, raise --max-requests to continue")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for place_id, queries in sorted(found.items()):
            f.write(json.dumps({"place_id": place_id, "queries": sorted(queries)}, ensure_ascii=False) + "\n")
    print(f"{len(found)} unique places -> {args.out} ({client.requests} requests)")


def cmd_details(args: argparse.Namespace) -> None:
    ids = _read_ids(args.input)
    done = set(_read_ids(args.out)) if args.out.exists() else set()
    todo = [i for i in dict.fromkeys(ids) if i not in done]
    cost = estimate_details_cost(len(todo), args.tier)
    print(f"{len(todo)} places to fetch ({len(done)} already done), tier={args.tier}, est. cost ${cost:.2f} (free tier applied per month)")
    if args.dry_run or not todo:
        return
    client = _client(args.max_requests)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as f:
        try:
            for place_id in todo:
                f.write(json.dumps(client.details(place_id, args.tier), ensure_ascii=False) + "\n")
                f.flush()
        except BudgetExceeded as exc:
            print(f"stopped: {exc}; progress is saved, rerun to resume")
    print(f"{client.requests} requests made")


CORE_TYPES = ["athletic_field", "sports_complex", "sports_club", "stadium", "sports_activity_location"]


def select_ids(rows: list[dict], types: list[str], limit: int) -> list[str]:
    """Operational places of the given primary types; refuses to exceed `limit` so a paid tier cannot overrun it."""
    ids = [r["id"] for r in rows if r.get("businessStatus") == "OPERATIONAL" and r.get("primaryType") in types]
    if len(ids) > limit:
        raise ValueError(f"{len(ids)} places match but --limit is {limit}")
    return ids


def cmd_select(args: argparse.Namespace) -> None:
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    try:
        ids = select_ids(rows, args.types, args.limit)
    except ValueError as exc:
        sys.exit(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps({"place_id": i}) + "\n" for i in ids), encoding="utf-8")
    print(f"{len(ids)} places -> {args.out}")


def cmd_dashboard(args: argparse.Namespace) -> None:
    from leadgen.dashboard import save_report

    print(f"saved {save_report(args.raw, args.reports).resolve()}")


def cmd_serve(args: argparse.Namespace) -> None:
    from leadgen.server import serve

    serve(args.raw, args.port)


def main() -> None:
    parser = argparse.ArgumentParser(prog="leadgen")
    sub = parser.add_subparsers(required=True)

    d = sub.add_parser("discover", help="Collect place IDs over a tile grid (free IDs-only SKU)")
    d.add_argument("--queries", nargs="+", default=DEFAULT_QUERIES)
    d.add_argument("--step", type=float, default=0.06, help="initial tile size in degrees")
    d.add_argument("--min-size", type=float, default=0.005, help="stop splitting saturated tiles below this size")
    d.add_argument("--max-requests", type=int, default=5000)
    d.add_argument("--out", type=Path, default=Path("data/raw/place_ids.jsonl"))
    d.set_defaults(func=cmd_discover)

    t = sub.add_parser("details", help="Fetch Place Details for IDs (billed; see --dry-run)")
    t.add_argument("--tier", choices=["pro", "enterprise"], required=True)
    t.add_argument("--in", dest="input", type=Path, required=True, help="JSONL with place_id or id per line")
    t.add_argument("--out", type=Path, required=True)
    t.add_argument("--max-requests", type=int, default=1000)
    t.add_argument("--dry-run", action="store_true")
    t.set_defaults(func=cmd_details)

    c = sub.add_parser("select", help="Pick operational places of given types from the Pro details (input for the Enterprise tier)")
    c.add_argument("--in", dest="input", type=Path, default=Path("data/raw/details_pro.jsonl"))
    c.add_argument("--types", nargs="+", default=CORE_TYPES)
    c.add_argument("--limit", type=int, default=1000)
    c.add_argument("--out", type=Path, default=Path("data/raw/enterprise_core.jsonl"))
    c.set_defaults(func=cmd_select)

    b = sub.add_parser("dashboard", help="Save a static, timestamped HTML report of the current data")
    b.add_argument("--raw", type=Path, default=Path("data/raw"))
    b.add_argument("--reports", type=Path, default=Path("data/reports"))
    b.set_defaults(func=cmd_dashboard)

    s = sub.add_parser("serve", help="Live local dashboard (run with --env-file so its buttons can call the API)")
    s.add_argument("--raw", type=Path, default=Path("data/raw"))
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    args.func(args)
