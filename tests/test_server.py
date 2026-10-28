import json
import os
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from leadgen.server import App, make_handler


def write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.fixture
def server(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    write(raw / "place_ids.jsonl", [{"place_id": "a"}, {"place_id": "b"}, {"place_id": "c"}])
    write(raw / "details_pro.jsonl", [{"id": "a", "displayName": {"text": "Yıldız"}, "primaryType": "gym"}])
    os.utime(raw / "details_pro.jsonl", (0, 0))  # a fresh file would read as "a fetch is running"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **k: None)
    port = srv.server_address[1]
    srv.RequestHandlerClass = make_handler(App(raw), port)
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
