import json
import os
import threading
import time
from http.server import ThreadingHTTPServer

import httpx
import pytest

from leadgen.llm_runner import LlmRunner
from leadgen.server import App, make_handler


def write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def fake_extract(url, model, http, place_id=None, name=None, ollama_url=None):
    return {"ts": "2026-10-29T10:00:00+00:00", "place_id": place_id, "name": name, "url": url, "model": model,
            "ok": True, "error": None, "chars": 5, "seconds": 0.1, "facts": {}, "dropped": []}


@pytest.fixture
def server(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    write(raw / "place_ids.jsonl", [{"place_id": "a"}, {"place_id": "b"}, {"place_id": "c"}])
    write(raw / "details_pro.jsonl", [{"id": "a", "displayName": {"text": "Yıldız"}, "primaryType": "gym"}])
    os.utime(raw / "details_pro.jsonl", (0, 0))  # a fresh file would read as "a fetch is running"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: None)
    port = srv.server_address[1]
    runner = LlmRunner(raw.parent / "llm" / "out.jsonl", "http://127.0.0.1:1", extract=fake_extract)
    srv.RequestHandlerClass = make_handler(App(raw, llm=runner), port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", raw
    srv.shutdown()


def test_progress_counts_and_recent_feed(server):
    url, _ = server
    data = httpx.get(f"{url}/api/progress").json()

    assert data["ids"] == 3
    assert data["pro"]["done"] == 1 and data["pro"]["total"] == 3
    assert data["enterprise"]["total"] == 0
    assert data["recent"][0]["name"] == "Yıldız"


def test_places_endpoint_merges_details(server):
    url, _ = server
    places = {p["id"]: p for p in httpx.get(f"{url}/api/places").json()}
    assert places["a"]["name"] == "Yıldız" and places["b"]["detail"] is False


def test_post_requires_custom_header_and_loopback_host(server):
    url, _ = server
    assert httpx.post(f"{url}/api/run", json={"tier": "pro"}).status_code == 403
    bad_host = httpx.post(f"{url}/api/run", json={"tier": "pro"}, headers={"X-Leadgen": "1", "Host": "evil.example"})
    assert bad_host.status_code == 403


def test_enterprise_rejects_unknown_ids_without_starting_a_job(server):
    url, raw = server
    resp = httpx.post(f"{url}/api/run", json={"tier": "enterprise", "ids": ["zzz"]}, headers={"X-Leadgen": "1"})
    assert resp.status_code == 400
    assert not (raw / "enterprise_input.jsonl").exists()


def test_live_page_is_served(server):
    url, _ = server
    page = httpx.get(url)
    assert page.status_code == 200 and "const LIVE = true;" in page.text


def test_save_report_endpoint_lists_and_serves_report(server):
    url, raw = server
    assert httpx.post(f"{url}/api/save-report").status_code == 403

    name = httpx.post(f"{url}/api/save-report", headers={"X-Leadgen": "1"}).json()["name"]

    assert httpx.get(f"{url}/api/reports").json() == [{"name": name, "places": 3}]
    page = httpx.get(f"{url}/reports/{name}")
    assert page.status_code == 200 and "const LIVE = false;" in page.text


def test_report_route_rejects_path_traversal(server):
    url, _ = server
    for bad in ("../place_ids.jsonl", "..%2Fraw%2Fplace_ids.jsonl", "report-1.html"):
        assert httpx.get(f"{url}/reports/{bad}").status_code == 404


def test_llm_status_reports_ollama_down_and_website_coverage(server):
    url, _ = server
    status = httpx.get(f"{url}/api/llm/status").json()
    assert status["ollama"] == {"up": False, "models": []}
    assert status["with_website"] == 0 and status["job"]["running"] is False


def test_llm_run_validates_input_and_requires_header(server):
    url, _ = server
    run = lambda body, **kw: httpx.post(f"{url}/api/llm/run", json=body, headers={"X-Leadgen": "1"}, **kw)  # noqa: E731
    assert httpx.post(f"{url}/api/llm/run", json={"mode": "url", "url": "https://x.example"}).status_code == 403
    assert run({"mode": "url", "url": "file:///etc/passwd"}).status_code == 400
    assert run({"mode": "places", "limit": 3}).status_code == 400  # no place has a website yet
    assert run({"mode": "nope"}).status_code == 400
    assert run({"mode": "places", "limit": "abc"}).status_code == 400


def test_llm_url_run_end_to_end_with_fake_extractor(server):
    url, _ = server
    resp = httpx.post(f"{url}/api/llm/run", json={"mode": "url", "url": "https://yildiz.example/"}, headers={"X-Leadgen": "1"})
    assert resp.status_code == 200
    for _ in range(200):
        results = httpx.get(f"{url}/api/llm/results").json()
        if results:
            break
        time.sleep(0.01)
    assert results[0]["url"] == "https://yildiz.example/" and results[0]["model"] == "qwen3.5:9b"
    assert 'id="view-llm"' in httpx.get(url).text


def test_label_endpoint_saves_gold_and_reports_accuracy(server):
    url, raw = server
    facts = {"page_kind": "venue_site", "is_sports_venue": True, "venue_type": "halisaha", "sports": ["futbol"],
             "phones": ["0532 111 22 33"], "emails": [], "whatsapp": None, "instagram": None, "online_booking": "yes"}
    record = {"ts": "t1", "url": "https://x.example/", "place_id": "a", "name": "X", "model": "m", "ok": True,
              "error": None, "chars": 1, "seconds": 1, "facts": facts, "dropped": [], "flags": []}
    llm_dir = raw.parent / "llm"
    llm_dir.mkdir(exist_ok=True)
    (llm_dir / "out.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    post = lambda body, **kw: httpx.post(f"{url}/api/llm/label", json=body, headers={"X-Leadgen": "1"}, **kw)  # noqa: E731

    assert httpx.post(f"{url}/api/llm/label", json={}).status_code == 403
    assert post({"key": "nope", "status": "unsure"}).status_code == 404
    assert post({"key": "t1|https://x.example/", "status": "labeled", "gold": dict(facts, venue_type="uydurma")}).status_code == 400

    assert post({"key": "t1|https://x.example/", "status": "labeled", "gold": dict(facts, online_booking="no")}).status_code == 200
    state = httpx.get(f"{url}/api/llm/labels").json()
    assert state["labels"]["t1|https://x.example/"]["corrected"] == ["online_booking"]
    assert state["accuracy"]["labeled"] == 1 and state["accuracy"]["fields"]["online_booking"] == [0, 1]
