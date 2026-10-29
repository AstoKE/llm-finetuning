"""Human labels for extraction records: the evaluation set now, and fine-tuning data later.

A label stores the gold values a person confirmed or corrected, so accuracy is measured against
something other than the model's own output.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from leadgen.extract import VenueFacts, instagram_handle

FIELDS = ["page_kind", "is_sports_venue", "venue_type", "sports", "phones", "emails", "whatsapp", "instagram", "online_booking"]
STATUSES = ("labeled", "unsure")


def record_key(record: dict) -> str:
    return f"{record['ts']}|{record['url']}"


def validate_gold(raw: dict) -> dict:
    """Gold uses the model's own schema, so a typo in a category cannot be saved."""
    full = VenueFacts.model_validate({**raw, "booking_url": None, "evidence": ""})
    return {f: getattr(full, f) for f in FIELDS}


def _phone_cores(values) -> frozenset:
    return frozenset(re.sub(r"\D", "", v)[-10:] for v in values or [] if re.sub(r"\D", "", v))


def _comparable(field: str, value):
    if field == "phones":
        return _phone_cores(value)
    if field in ("sports", "emails"):
        return frozenset(v.strip().lower() for v in value or [] if v.strip())
    if field == "whatsapp":
        digits = re.sub(r"\D", "", value or "")
        return digits[-10:] if len(digits) >= 10 else (value or "").strip().lower() or None
    if field == "instagram":
        return instagram_handle(value)
    return value


def differs(model_facts: dict, gold: dict) -> list[str]:
    return [f for f in FIELDS if _comparable(f, model_facts.get(f)) != _comparable(f, gold.get(f))]


class LabelStore:
    def __init__(self, path: Path):
        self.path = path

    def all(self) -> dict[str, dict]:
        """Latest label per record; relabeling appends a line and wins."""
        if not self.path.exists():
            return {}
        rows = (json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip())
        return {row["key"]: row for row in rows}

    def save(self, record: dict, status: str, gold: dict | None, note: str = "") -> dict:
        if status not in STATUSES:
            raise ValueError("unknown status")
        label = {
            "key": record_key(record), "place_id": record.get("place_id"), "url": record["url"],
            "status": status, "gold": None, "corrected": [], "note": note[:500],
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if status == "labeled":
            if not record.get("facts"):
                raise ValueError("a failed extraction has no values to label")
            label["gold"] = validate_gold(gold or {})
            label["corrected"] = differs(record["facts"], label["gold"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(label, ensure_ascii=False) + "\n")
        return label


def accuracy(records: list[dict], labels: dict[str, dict]) -> dict:
    """Per-field agreement between the model and the human gold values."""
    fields = {f: [0, 0] for f in FIELDS}
    labeled = unsure = 0
    for record in records:
        label = labels.get(record_key(record))
        if not label:
            continue
        if label["status"] == "unsure":
            unsure += 1
            continue
        labeled += 1
        wrong = set(differs(record["facts"], label["gold"]))
        for f in FIELDS:
            fields[f][1] += 1
            fields[f][0] += f not in wrong
    return {"labeled": labeled, "unsure": unsure, "fields": fields}
