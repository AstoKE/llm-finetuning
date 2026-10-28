# leadgen

Finds sports venues (halı saha, spor tesisi, ...) in Ankara and collects their contact details as sales leads for MatchUp.
Long-term goal: a locally fine-tuned Qwen model that extracts venue facts from web pages.

## Run

```bash
cp .env.example .env            # then put your key in .env
uv sync
PYTHONPATH= uv run pytest       # PYTHONPATH= only needed in shells with ROS sourced

# 1. Discover place IDs over an Ankara tile grid (free IDs-only SKU)
uv run --env-file .env leadgen discover
# 2. Triage with Place Details Pro (5,000 free/month), then drop irrelevant places from the file by hand or script
uv run --env-file .env leadgen details --tier pro --in data/raw/place_ids.jsonl --out data/raw/details_pro.jsonl --dry-run
# 3. Phone/website/rating only for the places that survived triage (Enterprise: 1,000 free/month)
uv run --env-file .env leadgen details --tier enterprise --in data/raw/details_pro_filtered.jsonl --out data/raw/details_enterprise.jsonl --dry-run
```

Drop `--dry-run` to actually call the API. Both commands are resumable and capped by `--max-requests`.

## Google API key (before the first real run)

1. Google Cloud Console: create a project, enable **Places API (New)**, attach a billing account (required even for the free tier).
2. APIs & Services -> Credentials -> create an API key and restrict it to *Places API (New)*. Do not restrict by HTTP referrer: this is a server-side script.
3. **Set a hard spend limit**: Quotas & System Limits -> Places API (New) -> lower the per-day limits for Place Details, and add a budget alert in Billing. Code-level `--max-requests` guards are a second line of defence, not the first.

## Data and terms

- Google Places content may not be stored long-term beyond `place_id` (see Maps Platform Terms, section on caching; **read it, this has not been legally reviewed**). `data/` is gitignored; treat Google output as discovery seed, and keep final lead contact data sourced from the venues' own public pages.
- MatchUp ROADMAP risk R-7 already tracks map/venue data licensing; keep sources separated by provenance.
