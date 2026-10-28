import json

from leadgen.dashboard import build, merge


def test_merge_layers_tiers_and_keeps_id_only_places():
    ids = [{"place_id": "a", "queries": ["halı saha"]}, {"place_id": "b", "queries": ["tenis kortu"]}]
    pro = [{"id": "a", "displayName": {"text": "Yıldız Halı Saha"}, "primaryType": "sports_complex",
            "location": {"latitude": 39.9, "longitude": 32.8}}]
    ent = [{"id": "a", "displayName": {"text": "Yıldız Halı Saha"}, "websiteUri": "https://example.com",
            "nationalPhoneNumber": "0312 000 00 00"}]

    places = {p["id"]: p for p in merge(ids, pro, ent)}

    assert places["a"]["name"] == "Yıldız Halı Saha" and places["a"]["lat"] == 39.9
    assert places["a"]["web"] == "https://example.com" and places["a"]["queries"] == ["halı saha"]
    assert places["b"]["detail"] is False


def test_build_embeds_data_and_cannot_break_out_of_script_tag(tmp_path):
    ids = tmp_path / "ids.jsonl"
    pro = tmp_path / "pro.jsonl"
    ids.write_text(json.dumps({"place_id": "a", "queries": []}) + "\n", encoding="utf-8")
    pro.write_text(json.dumps({"id": "a", "displayName": {"text": "</script><b>x"}}) + "\n", encoding="utf-8")

    html = build(ids, pro, None)

    assert "</script><b>x" not in html
    assert "<\\/script><b>x" in html


def test_report_is_saved_with_count_in_name_and_listed(tmp_path):
    from datetime import datetime

    from leadgen.dashboard import list_reports, save_report

    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "place_ids.jsonl").write_text('{"place_id": "a"}\n{"place_id": "b"}\n', encoding="utf-8")

    path = save_report(raw, tmp_path / "reports", now=datetime(2026, 10, 28, 17, 30, 5))

    assert path.name == "report-20261028-173005-2.html"
    assert "Leaflet 1.9.4" in path.read_text(encoding="utf-8")  # map library is embedded, not fetched from a CDN
    assert list_reports(tmp_path / "reports") == [{"name": path.name, "places": 2}]
    (tmp_path / "reports" / "notes.html").write_text("x")
    assert len(list_reports(tmp_path / "reports")) == 1
