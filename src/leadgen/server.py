"""Local dashboard server: live progress, incoming records, and buttons that start `leadgen details` jobs.

Binds to 127.0.0.1 only. POSTs need a custom header (a cross-origin page cannot send one without a
preflight, which this server never answers) and a loopback Host header (blocks DNS rebinding).
"""

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from leadgen.dashboard import REPORT_NAME, build, list_reports, load, save_report
from leadgen.extract import DEFAULT_MODEL, OLLAMA_URL
from leadgen.llm_runner import LlmRunner, Rejected

RECENT = 8
ACTIVE_WINDOW = 15  # seconds since the output file last grew => a job (even one we did not start) is running
SLACK = 50  # extra requests allowed over the number of places, for retries


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _id(row: dict) -> str:
    return row.get("place_id") or row["id"]


class App:
    def __init__(self, raw: Path, ollama_url: str = OLLAMA_URL, llm: LlmRunner | None = None):
        self.raw = raw
        self.llm = llm or LlmRunner(raw.parent / "llm" / "extractions.jsonl", ollama_url)
        self.ids = raw / "place_ids.jsonl"
        self.files = {
            "pro": (self.ids, raw / "details_pro.jsonl"),
            "enterprise": (raw / "enterprise_input.jsonl", raw / "details_enterprise.jsonl"),
        }
        self.procs: dict[str, subprocess.Popen] = {}
        self.logs = raw.parent / "logs"
        self.reports = raw.parent / "reports"

    def places(self) -> list[dict]:
        return load(self.ids, *(out for _, out in self.files.values()))

    def run_llm(self, body: dict) -> None:
        model = body.get("model") or DEFAULT_MODEL
        if body.get("mode") == "url":
            url = body.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                raise Rejected("url must start with http:// or https://")
            self.llm.start_url(url, model)
        elif body.get("mode") == "places":
            ids = body.get("ids")
            if ids is not None and not isinstance(ids, list):
                raise Rejected("ids must be a list")
            self.llm.start_places(self.places(), model, int(body.get("limit", 5)), ids)
        else:
            raise Rejected("unknown mode")

    def save_report(self) -> Path:
        return save_report(self.raw, self.reports)

    def _save_when_done(self, proc: subprocess.Popen) -> None:
        proc.wait()
        self.save_report()

    def _active(self, tier: str) -> bool:
        proc = self.procs.get(tier)
        if proc and proc.poll() is None:
            return True
        out = self.files[tier][1]
        return out.exists() and time.time() - out.stat().st_mtime < ACTIVE_WINDOW

    def _stage(self, tier: str) -> dict:
        inp, out = self.files[tier]
        wanted = {_id(r) for r in _rows(inp)}
        done = len({_id(r) for r in _rows(out)} & wanted)
        stage = {"done": done, "total": len(wanted), "running": self._active(tier), "error": None}
        proc = self.procs.get(tier)
        if proc and proc.poll() not in (None, 0):
            log = self.logs / f"{tier}.log"
            lines = log.read_text(encoding="utf-8").strip().splitlines() if log.exists() else []
            stage["error"] = lines[-1] if lines else f"exit code {proc.returncode}"
        return stage

    def progress(self) -> dict:
        newest = max(
            (out for _, out in self.files.values() if out.exists()),
            key=lambda p: p.stat().st_mtime,
            default=None,
        )
        recent = []
        for row in reversed(_rows(newest)[-RECENT:] if newest else []):
            recent.append(
                {
                    "name": (row.get("displayName") or {}).get("text"),
                    "type": row.get("primaryType"),
                    "status": row.get("businessStatus"),
                    "phone": row.get("nationalPhoneNumber"),
                    "web": row.get("websiteUri"),
                }
            )
        return {
            "ids": len(_rows(self.ids)),
            "pro": self._stage("pro"),
            "enterprise": self._stage("enterprise"),
            "recent": recent,
        }

    def start(self, tier: str, ids: list[str] | None) -> None:
        if tier not in self.files:
            raise Rejected("unknown tier")
        if any(self._active(t) for t in self.files):
            raise Rejected("a fetch is already running", 409)
        inp, out = self.files[tier]
        if tier == "enterprise":
            known = {_id(r) for r in _rows(self.ids)}
            wanted = [i for i in dict.fromkeys(ids or []) if i in known]
            if not wanted:
                raise Rejected("no known place ids given")
            inp.write_text("".join(json.dumps({"place_id": i}) + "\n" for i in wanted), encoding="utf-8")
        total = len(_rows(inp))
        if not total:
            raise Rejected("nothing to fetch")
        self.logs.mkdir(parents=True, exist_ok=True)
        with (self.logs / f"{tier}.log").open("w", encoding="utf-8") as log:
            self.procs[tier] = subprocess.Popen(
                [
                    sys.executable, "-c", "from leadgen.cli import main; main()",
                    "details", "--tier", tier, "--in", str(inp), "--out", str(out),
                    "--max-requests", str(total + SLACK),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        threading.Thread(target=self._save_when_done, args=(self.procs[tier],), daemon=True).start()


def make_handler(app: App, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status: int = 200) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            if self.headers.get("Host") not in allowed_hosts:
                return self._json({"error": "bad host"}, 403)
            if self.path == "/":
                html = build(app.ids, None, None, live=True)
                self._send(200, html.encode(), "text/html; charset=utf-8")
            elif self.path == "/api/progress":
                self._json(app.progress())
            elif self.path == "/api/places":
                self._json(app.places())
            elif self.path == "/api/llm/status":
                self._json(app.llm.status(app.places()))
            elif self.path == "/api/llm/results":
                self._json(app.llm.records()[-100:][::-1])
            elif self.path == "/api/reports":
                self._json(list_reports(app.reports))
            elif self.path.startswith("/reports/") and REPORT_NAME.match(self.path.removeprefix("/reports/")):
                path = app.reports / self.path.removeprefix("/reports/")
                if path.is_file():
                    self._send(200, path.read_bytes(), "text/html; charset=utf-8")
                else:
                    self._json({"error": "not found"}, 404)
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if self.headers.get("Host") not in allowed_hosts or self.headers.get("X-Leadgen") != "1":
                return self._json({"error": "forbidden"}, 403)
            if self.path == "/api/save-report":
                return self._json({"name": app.save_report().name})
            if self.path not in ("/api/run", "/api/llm/run"):
                return self._json({"error": "not found"}, 404)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if self.path == "/api/run":
                    app.start(body.get("tier", ""), body.get("ids"))
                else:
                    app.run_llm(body)
            except Rejected as exc:
                return self._json({"error": str(exc)}, exc.status)
            except (ValueError, AttributeError):
                return self._json({"error": "bad request"}, 400)
            self._json({"ok": True})

    return Handler


def serve(raw: Path, port: int) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(App(raw), port))
    print(f"Dashboard: http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
