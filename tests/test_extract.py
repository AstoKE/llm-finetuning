import json
import socket
import time

import httpx
import pytest

from leadgen import extract
from leadgen.extract import FetchError, Page, ask_model, fetch_page, ground, html_to_text, run_extraction
from leadgen.llm_runner import LlmRunner, Rejected

HTML = """<html><head><title>Yıldız Halı Saha</title><style>.x{}</style></head><body>
<script>var secret = "0555 000 00 00";</script>
<h1>Yıldız Halı Saha</h1><p>Rezervasyon: 0532 111 22 33</p>
<a href="tel:+905321112233">Ara</a>
<a href="https://www.instagram.com/yildizhalisaha/">Instagram</a>
<a href="/rezervasyon">Online Rezervasyon</a><a href="/hakkimizda">Hakkımızda</a>
</body></html>"""

FACTS = {
    "page_kind": "venue_site", "is_sports_venue": True, "venue_type": "halisaha", "sports": ["futbol"],
    "phones": ["0532 111 22 33"], "emails": [], "whatsapp": None,
    "instagram": "https://www.instagram.com/yildizhalisaha/", "online_booking": "yes",
    "booking_url": None, "evidence": "Rezervasyon: 0532 111 22 33",
}


def public_dns(monkeypatch, mapping=None):
    mapping = mapping or {}

    def fake(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping.get(host, "93.184.216.34"), 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_text_skips_scripts_and_keeps_contact_links():
    text = html_to_text(HTML, "https://yildiz.example/")
    assert "Yıldız Halı Saha" in text and "0532 111 22 33" in text
    assert "0555 000 00 00" not in text  # script content is not visible text
    assert "tel:+905321112233" in text and "instagram.com/yildizhalisaha" in text
    assert "https://yildiz.example/rezervasyon" in text
    assert "hakkimizda" not in text  # only contact/booking links are kept


def test_ground_drops_values_the_page_does_not_contain():
    text = html_to_text(HTML, "https://yildiz.example/")
    facts = dict(FACTS, phones=["0532 111 22 33", "0312 999 88 77"], emails=["info@uydurma.com"],
                 whatsapp="0555 123 45 67", instagram="@baskasi", booking_url="https://yildiz.example/rezervasyon")

    clean, dropped = ground(facts, text)

    assert clean["phones"] == ["0532 111 22 33"]
    assert clean["emails"] == [] and clean["whatsapp"] is None and clean["instagram"] is None
    assert clean["booking_url"] == "https://yildiz.example/rezervasyon"
    assert {d["field"] for d in dropped} == {"phones", "emails", "whatsapp", "instagram"}


def test_contacts_on_a_news_or_directory_page_are_not_attributed_to_the_venue():
    text = html_to_text(HTML, "https://haber.example/")
    for kind in ("directory_or_news", "other"):
        clean, dropped = ground(dict(FACTS, page_kind=kind), text)  # every value is literally on the page
        assert clean["phones"] == [] and clean["instagram"] is None
        assert {d["reason"] for d in dropped} == {f"page_kind={kind}"}
    clean, _ = ground(dict(FACTS, page_kind="social_profile"), text)
    assert clean["phones"] == ["0532 111 22 33"]


def test_phone_matches_regardless_of_formatting():
    clean, dropped = ground(dict(FACTS, phones=["+90 532 111 22 33"], instagram=None), "Tel: (0532) 111-22-33")
    assert clean["phones"] == ["+90 532 111 22 33"] and not dropped


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_returns_text_and_respects_robots(monkeypatch):
    public_dns(monkeypatch)

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    http = client_for(handler)
    assert "Yıldız Halı Saha" in fetch_page("https://yildiz.example/", http).text
    with pytest.raises(FetchError, match="robots"):
        fetch_page("https://yildiz.example/private/x", http)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x.example/", "javascript:alert(1)"])
def test_fetch_rejects_non_http_schemes(url):
    with pytest.raises(FetchError):
        fetch_page(url, client_for(lambda r: httpx.Response(200)))


def test_fetch_blocks_private_addresses_directly_and_via_redirect(monkeypatch):
    public_dns(monkeypatch, {"intranet.example": "10.0.0.5", "localhost": "127.0.0.1"})
    http = client_for(lambda r: httpx.Response(302, headers={"location": "http://intranet.example/admin"}))

    with pytest.raises(FetchError, match="non-public"):
        fetch_page("http://localhost:8765/", http)
    with pytest.raises(FetchError, match="non-public"):
        fetch_page("https://yildiz.example/", http)  # public host that redirects to a private one


def ollama_handler(content, seen=None):
    def handler(req):
        if seen is not None:
            seen.append(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": content}})

    return handler


def test_ask_model_sends_schema_and_disables_thinking():
    seen = []
    facts = ask_model(client_for(ollama_handler(json.dumps(FACTS), seen)), "qwen3.5:9b", Page("https://x/", "metin"))

    body = seen[0]
    assert facts.venue_type == "halisaha"
    assert body["model"] == "qwen3.5:9b" and body["think"] is False and body["options"]["temperature"] == 0
    assert "is_sports_venue" in body["format"]["properties"]
    assert "Metinde açıkça yazmayan" in body["messages"][0]["content"]


def test_run_extraction_records_grounded_facts(monkeypatch):
    public_dns(monkeypatch)

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": json.dumps(dict(FACTS, emails=["x@uydurma.com"]))}})
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    rec = run_extraction("https://yildiz.example/", "qwen3.5:9b", client_for(handler), place_id="p1", name="Yıldız")

    assert rec["ok"] and rec["place_id"] == "p1" and rec["chars"] > 0
    assert rec["facts"]["emails"] == [] and rec["dropped"] == [{"field": "emails", "value": "x@uydurma.com", "reason": "sayfada yok"}]


@pytest.mark.parametrize("content", ["not json", json.dumps({"is_sports_venue": True})])
def test_run_extraction_records_bad_model_output_instead_of_raising(monkeypatch, content):
    public_dns(monkeypatch)

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": content}})
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    rec = run_extraction("https://yildiz.example/", "m", client_for(handler))
    assert not rec["ok"] and rec["error"].startswith("model output")


def wait(runner):
    for _ in range(200):
        if not runner.job["running"]:
            return
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def fake_extract(url, model, http, place_id=None, name=None, ollama_url=None):
    return {"ts": "t", "place_id": place_id, "name": name, "url": url, "model": model, "ok": True,
            "error": None, "chars": 1, "seconds": 0.1, "facts": {}, "dropped": []}


def test_runner_processes_places_with_websites_and_skips_done(tmp_path):
    runner = LlmRunner(tmp_path / "llm" / "out.jsonl", ollama_url="http://127.0.0.1:1", extract=fake_extract)
    places = [{"id": "a", "web": "https://a.example", "name": "A"}, {"id": "b", "web": None},
              {"id": "c", "web": "https://c.example", "name": "C"}]

    runner.start_places(places, "qwen3.5:9b", limit=1, ids=None)
    wait(runner)
    assert [r["place_id"] for r in runner.records()] == ["a"]

    runner.start_places(places, "qwen3.5:9b", limit=10, ids=None)  # "a" is already done
    wait(runner)
    assert [r["place_id"] for r in runner.records()] == ["a", "c"]
    with pytest.raises(Rejected):
        runner.start_places(places, "qwen3.5:9b", limit=10, ids=None)  # nothing left

    status = runner.status(places)
    assert status["ollama"]["up"] is False and status["with_website"] == 2 and status["extracted"] == 2


def test_runner_refuses_second_job_and_bad_model_names(tmp_path):
    gate = []

    def slow(*a, **k):
        while not gate:
            time.sleep(0.01)
        return fake_extract(*a, **k)

    runner = LlmRunner(tmp_path / "out.jsonl", extract=slow)
    runner.start_url("https://x.example", "m")
    with pytest.raises(Rejected) as exc:
        runner.start_url("https://y.example", "m")
    assert exc.value.status == 409
    gate.append(1)
    wait(runner)
    with pytest.raises(Rejected):
        runner.start_url("https://x.example", "../../etc/passwd")


def test_review_flags_catch_contradiction_missing_sport_and_invented_evidence():
    from leadgen.extract import review_flags

    text = "Yıldız Halı Saha. Rezervasyon: 0532 111 22 33"
    assert review_flags(FACTS, text) == []  # evidence is a verbatim quote, record is consistent

    spam = dict(FACTS, is_sports_venue=False, venue_type="spor_kompleksi", online_booking="yes")
    assert any(f.startswith("çelişki") for f in review_flags(spam, text))
    assert any("hangi spor" in f for f in review_flags(dict(FACTS, sports=[]), text))
    assert "dayanak sayfada bulunamadı" in review_flags(dict(FACTS, evidence="Tesis 5 kortludur"), text)
    assert "dayanak sayfada bulunamadı" in review_flags(dict(FACTS, evidence=""), text)
    assert any(f.startswith("tesis dışı") for f in review_flags(dict(FACTS, page_kind="directory_or_news"), text))


def test_evidence_check_ignores_case_and_whitespace():
    from leadgen.extract import review_flags

    assert review_flags(dict(FACTS, evidence="REZERVASYON:  0532 111 22 33"), "x\nrezervasyon: 0532\n111 22 33") == []


def test_run_extraction_drops_placeholder_sports_and_adds_flags(monkeypatch):
    public_dns(monkeypatch)
    facts = dict(FACTS, sports=["bilinmiyor"], evidence="uydurma bir cümle")

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        if req.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": json.dumps(facts)}})
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    rec = run_extraction("https://yildiz.example/", "m", client_for(handler))

    assert rec["facts"]["sports"] == []
    assert "dayanak sayfada bulunamadı" in rec["flags"] and any("hangi spor" in f for f in rec["flags"])
