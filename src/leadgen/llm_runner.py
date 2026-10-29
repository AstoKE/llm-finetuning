"""Runs extraction jobs in a background thread and appends every record to a JSONL file."""

import json
import re
import threading
from collections.abc import Callable
from pathlib import Path

import httpx

from leadgen.extract import DEFAULT_MODEL, OLLAMA_URL, run_extraction

MODEL_NAME = re.compile(r"^(?!.*\.\.)[\w.\-:/]{1,80}$")  # allows hf.co/user/model:tag, rejects ".."


class Rejected(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class LlmRunner:
    def __init__(
        self,
        store: Path,
        ollama_url: str = OLLAMA_URL,
        extract: Callable[..., dict] = run_extraction,
    ):
        self.store = store
        self.ollama_url = ollama_url
        self._extract = extract
        self._lock = threading.Lock()
        self.job: dict = {"running": False, "total": 0, "done": 0, "current": None}

    def records(self) -> list[dict]:
        if not self.store.exists():
            return []
        return [json.loads(line) for line in self.store.read_text(encoding="utf-8").splitlines() if line.strip()]

    def status(self, places: list[dict]) -> dict:
        try:
            tags = httpx.get(f"{self.ollama_url}/api/tags", timeout=2).json()
            ollama = {"up": True, "models": sorted(m["name"] for m in tags.get("models", []))}
        except (httpx.HTTPError, ValueError):
            ollama = {"up": False, "models": []}
        done = {r["place_id"] for r in self.records() if r.get("place_id")}
        with_web = [p for p in places if p.get("web")]
        return {
            "ollama": ollama,
            "default_model": DEFAULT_MODEL,
            "job": dict(self.job),
            "with_website": len(with_web),
            "extracted": len([p for p in with_web if p["id"] in done]),
        }

    def start_url(self, url: str, model: str) -> None:
        self._start([{"url": url, "id": None, "name": None}], model)

    def start_places(self, places: list[dict], model: str, limit: int, ids: list[str] | None) -> None:
        done = {r["place_id"] for r in self.records() if r.get("place_id")}
        wanted = set(ids) if ids is not None else None
        todo = [
            {"url": p["web"], "id": p["id"], "name": p.get("name")}
            for p in places
            if p.get("web") and p["id"] not in done and (wanted is None or p["id"] in wanted)
        ][: max(1, min(limit, 200))]
        if not todo:
            raise Rejected("no places with a website left to process")
        self._start(todo, model)

    def _start(self, items: list[dict], model: str) -> None:
        if not MODEL_NAME.match(model):
            raise Rejected("bad model name")
        if not self._lock.acquire(blocking=False):
            raise Rejected("an extraction job is already running", 409)
        self.job = {"running": True, "total": len(items), "done": 0, "current": None}
        threading.Thread(target=self._work, args=(items, model), daemon=True).start()

    def _work(self, items: list[dict], model: str) -> None:
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            with httpx.Client(timeout=20, follow_redirects=False) as http:
                for item in items:
                    self.job["current"] = item["url"]
                    record = self._extract(
                        item["url"], model, http, place_id=item["id"], name=item["name"], ollama_url=self.ollama_url
                    )
                    with self.store.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    self.job["done"] += 1
        finally:
            self.job["running"] = False
            self.job["current"] = None
            self._lock.release()
