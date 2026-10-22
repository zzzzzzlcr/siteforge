"""约会站分类器的纯逻辑（不碰窗口/网络）。"""
from __future__ import annotations

from agent import classify


def _census(**kw):
    base = {"url": "https://x.test/", "title": "", "hash": "",
            "testids": [], "classes": [], "buttons": [], "links": [],
            "inputs": [], "body_text": "", "start_candidates": []}
    base.update(kw)
    return base


def test_japansdates_is_recognised_by_step_markers_and_base64_hash():
    got = classify.classify_census(_census(
        testids=["step-content-item-Basic", "step-content-item-ChooseAge",
                 "option-have-fun", "legal-checkbox", "continue-button"],
        hash="#c3RlcDE="))
    assert got["platform"] == "japansdates"
    assert got["confidence"] == "high"
    assert got["ambiguous"] is False
    assert any("step-content-item-" in e for e in got["matches"][0]["evidence"])


def test_goldenagesouls_is_recognised_by_age_and_image_buttons():
    got = classify.classify_census(_census(
        testids=["continue-button", "image-button-basic", "age-18-24"]))
    assert got["platform"] == "goldenagesouls"
    assert got["confidence"] == "high"


def test_delachat_is_recognised_by_class_tokens():
    got = classify.classify_census(_census(
        classes=["terms__button", "step_active"],
        testids=["continue-button"]))
    assert got["platform"] == "delachat"
    assert got["confidence"] == "high"


def test_a_plain_continue_button_is_not_enough():
    got = classify.classify_census(_census(testids=["continue-button"]))
    assert got["platform"] is None
    assert got["confidence"] == "unknown"


def test_two_matching_platforms_is_ambiguous():
    #: delachat 与 goldenagesouls 的标记**不互斥**（delachat 只否 step-content-item-）。
    got = classify.classify_census(_census(
        classes=["terms__button", "step_active"],
        testids=["continue-button", "image-button-basic"]))
    assert got["ambiguous"] is True
    assert got["platform"] is None
    assert {m["platform"] for m in got["matches"]} >= {"delachat", "goldenagesouls"}


def test_none_of_marker_vetoes_japansdates():
    got = classify.classify_census(_census(
        testids=["step-content-item-Basic", "option-have-fun", "legal-checkbox",
                 "continue-button", "image-button-basic"],
        hash="#c3RlcDE="))
    assert all(m["platform"] != "japansdates" for m in got["matches"])


def test_site_key_strips_scheme_query_fragment_and_trailing_slash():
    assert classify.site_key(
        "https://soulmatemeets.com/land/sp/23d247da/?id_visit_prev=1#c3RlcDE=") \
        == "soulmatemeets.com/land/sp/23d247da"
    assert classify.site_key("http://a.test/x/") == "a.test/x"
    assert classify.site_key("a.test/x?q=1") == "a.test/x"


import pytest


def test_resolve_config_uses_the_platform_anchor():
    rows = [{"site": "japansdates.com", "config_id": 101, "status": 1},
            {"site": "goldenagesouls.com", "config_id": 66, "status": 1}]
    assert classify.resolve_config("japansdates", rows) == 101
    assert classify.resolve_config("goldenagesouls", rows) == 66


def test_resolve_config_override_wins_and_must_be_positive():
    rows = [{"site": "japansdates.com", "config_id": 101, "status": 1}]
    assert classify.resolve_config("japansdates", rows, override=55) == 55
    with pytest.raises(classify.ClassifyError):
        classify.resolve_config("japansdates", rows, override=0)


def test_resolve_config_refuses_a_missing_anchor():
    with pytest.raises(classify.ClassifyError) as got:
        classify.resolve_config("japansdates", [])
    assert "锚点" in str(got.value)
