import pytest
from pydantic import ValidationError

from leadgen.labels import LabelStore, accuracy, differs, record_key, validate_gold

FACTS = {
    "page_kind": "venue_site", "is_sports_venue": True, "venue_type": "halisaha", "sports": ["Futbol"],
    "phones": ["0532 111 22 33"], "emails": [], "whatsapp": "+905321112233",
    "instagram": "https://www.instagram.com/yildiz/?igsh=x", "online_booking": "yes",
}
RECORD = {"ts": "t1", "url": "https://yildiz.example/", "place_id": "p1", "facts": FACTS}


def test_formatting_differences_are_not_errors():
    gold = dict(FACTS, phones=["+90 532 111 22 33"], sports=["futbol"], whatsapp="0532 111 22 33", instagram="@Yildiz")
    assert differs(FACTS, gold) == []
    assert differs(FACTS, dict(gold, phones=["0312 000 00 00"], online_booking="no")) == ["phones", "online_booking"]


def test_gold_must_match_the_schema():
    with pytest.raises(ValidationError):
        validate_gold(dict(FACTS, venue_type="halisaha2"))
    assert validate_gold(FACTS)["venue_type"] == "halisaha"


def test_confirm_and_correct_are_recorded_and_relabeling_wins(tmp_path):
    store = LabelStore(tmp_path / "labels.jsonl")

    first = store.save(RECORD, "labeled", FACTS)
    assert first["corrected"] == []

    second = store.save(RECORD, "labeled", dict(FACTS, online_booking="no"), note="rezervasyon yok")
    assert second["corrected"] == ["online_booking"]
    assert store.all()[record_key(RECORD)]["corrected"] == ["online_booking"]
    assert len((tmp_path / "labels.jsonl").read_text().splitlines()) == 2  # history is kept


def test_unsure_has_no_gold_and_failed_records_cannot_be_labeled(tmp_path):
    store = LabelStore(tmp_path / "labels.jsonl")
    assert store.save(RECORD, "unsure", None)["gold"] is None
    with pytest.raises(ValueError):
        store.save(dict(RECORD, facts=None), "labeled", FACTS)
    with pytest.raises(ValueError):
        store.save(RECORD, "maybe", None)


def test_accuracy_counts_per_field_and_skips_unsure(tmp_path):
    store = LabelStore(tmp_path / "labels.jsonl")
    other = dict(RECORD, ts="t2")
    third = dict(RECORD, ts="t3")
    store.save(RECORD, "labeled", FACTS)
    store.save(other, "labeled", dict(FACTS, phones=["0312 000 00 00"], online_booking="no"))
    store.save(third, "unsure", None)

    result = accuracy([RECORD, other, third, dict(RECORD, ts="t4")], store.all())

    assert result["labeled"] == 2 and result["unsure"] == 1
    assert result["fields"]["phones"] == [1, 2] and result["fields"]["online_booking"] == [1, 2]
    assert result["fields"]["page_kind"] == [2, 2]
