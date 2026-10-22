"""约会站分类器的纯逻辑（不碰窗口/网络）。"""
from __future__ import annotations

import json

import pytest

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


def test_goldenagesouls_is_recognised_by_its_own_landing_markers():
    #: 2026-10-22 真站：goldenagesouls.com 是另一种落地屏（option-man / gender / birth-date /
    #: terms__checkbox），没有 step-content-item-*，也没有 age-* / image-button-*。
    got = classify.classify_census(_census(
        testids=["option-man", "option-woman", "gender-male", "birth-date"],
        classes=["terms__checkbox", "reg-form__body"]))
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
    #: delachat 与 goldenagesouls 的标记**不互斥**（两者都不否对方的标记）。
    got = classify.classify_census(_census(
        classes=["terms__button", "step_active", "terms__checkbox"],
        testids=["option-man", "gender-male", "birth-date"]))
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


def _eval_from(pages):
    """按顺序返回 census 的假 eval：pages 是 dict 列表。
    `MARK_START_JS` 那一下（带 `data-sf-start`）固定回 "marked"，不占 pages。"""
    seq = list(pages)

    def _call(js):
        if "data-sf-start" in js:
            return "marked"
        if not seq:
            raise AssertionError("eval 调多了")
        return json.dumps(seq.pop(0))
    return _call


def test_census_with_start_does_not_click_when_the_first_screen_matches():
    page = _census(testids=["step-content-item-Basic", "option-x", "legal-checkbox"],
                   hash="#c3RlcDE=")
    clicked = []
    got = classify.census_with_start(_eval_from([page]), lambda: clicked.append(1))
    assert got["clicked_start"] is False
    assert clicked == []


def test_census_with_start_clicks_once_when_unknown_and_returns_the_new_census():
    first = _census(buttons=["Start"], start_candidates=[{"text": "Start", "tag": "button"}])
    second = _census(testids=["step-content-item-Basic", "option-x", "legal-checkbox"],
                     hash="#c3RlcDE=")
    clicked = []
    got = classify.census_with_start(_eval_from([first, second]),
                                     lambda: clicked.append(1))
    assert got["clicked_start"] is True
    assert got["clicked_text"] == "Start"
    assert clicked == [1]
    assert got["census"] == second


def test_census_with_start_does_not_click_with_no_candidate_or_no_click_flag():
    page = _census(buttons=["Start"], start_candidates=[])
    got = classify.census_with_start(_eval_from([page]), lambda: None)
    assert got["clicked_start"] is False

    page2 = _census(start_candidates=[{"text": "Start", "tag": "button"}])
    clicked = []
    got2 = classify.census_with_start(_eval_from([page2]), lambda: clicked.append(1),
                                      allow_click=False)
    assert got2["clicked_start"] is False and clicked == []


def test_census_with_start_reports_when_the_click_does_not_change_the_page():
    page = _census(start_candidates=[{"text": "Start", "tag": "button"}])
    got = classify.census_with_start(_eval_from([page, page]),
                                     lambda: None, wait_seconds=0.0)
    assert got["clicked_start"] is True
    assert "没变" in got["note"]


class _FakeClient:
    def __init__(self, rows, link_result=None, update_rows=True):
        self.rows = rows
        self.link_result = link_result or {"site": "x", "config_id": 101, "created": True}
        self.update_rows = update_rows
        self.linked = []

    def form_config_sites(self):
        return self.rows

    def form_config_link(self, site, config_id, *, operator, reason=""):
        self.linked.append((site, config_id, operator, reason))
        if isinstance(self.link_result, Exception):
            raise self.link_result
        if self.update_rows:
            #: 真后端是加性 upsert —— 回读能看见这一行。
            self.rows = [r for r in self.rows if r.get("site") != site] + [
                {"site": site, "config_id": config_id, "status": 1}]
        return self.link_result


JAPAN = _census(url="https://soulmatemeets.com/land/sp/23d247da/",
                testids=["step-content-item-Basic", "option-x", "legal-checkbox"],
                hash="#c3RlcDE=")
#: delachat + goldenagesouls 的标记**不互斥**，用来造 ambiguous。
AMBIGUOUS = _census(classes=["terms__button", "step_active", "terms__checkbox"],
                    testids=["option-man", "gender-male", "birth-date"])
ROWS = [{"site": "japansdates.com", "config_id": 101, "status": 1}]


def test_run_classify_read_only_does_not_link():
    client = _FakeClient(ROWS)
    got = classify.run_classify(
        "https://soulmatemeets.com/land/sp/23d247da/",
        census_fn=lambda: {"census": JAPAN, "clicked_start": False,
                           "clicked_text": "", "note": ""},
        site_rows=ROWS, client=client)
    assert got["exit"] == 0
    assert got["result"]["platform"] == "japansdates"
    assert got["result"]["config_id"] == 101
    assert got["result"]["site_key"] == "soulmatemeets.com/land/sp/23d247da"
    assert client.linked == []


def test_run_classify_unknown_or_ambiguous_exits_2_and_never_links():
    client = _FakeClient(ROWS)
    for c in (_census(), AMBIGUOUS):
        got = classify.run_classify("https://x.test/", census_fn=lambda c=c: {
            "census": c, "clicked_start": False, "clicked_text": "", "note": ""},
            site_rows=ROWS, client=client, apply=True)
        assert got["exit"] == 2, got
    assert client.linked == []


def test_run_classify_apply_links_then_reads_back():
    client = _FakeClient(ROWS)
    got = classify.run_classify(
        "https://soulmatemeets.com/land/sp/23d247da/",
        census_fn=lambda: {"census": JAPAN, "clicked_start": False,
                           "clicked_text": "", "note": ""},
        site_rows=ROWS, client=client, apply=True)
    assert got["exit"] == 0, got
    assert client.linked and client.linked[0][0] == "soulmatemeets.com/land/sp/23d247da"
    assert client.linked[0][1] == 101
    assert got["result"]["linked"] == {"created": True, "config_id": 101}


def test_run_classify_apply_refuses_when_readback_does_not_match():
    rows = [{"site": "japansdates.com", "config_id": 101, "status": 1},
            {"site": "soulmatemeets.com/land/sp/23d247da", "config_id": 66, "status": 1}]
    #: 写成功但回读仍是 66 —— 不许报成功。
    client = _FakeClient(rows, update_rows=False)
    got = classify.run_classify(
        "https://soulmatemeets.com/land/sp/23d247da/",
        census_fn=lambda: {"census": JAPAN, "clicked_start": False,
                           "clicked_text": "", "note": ""},
        site_rows=rows, client=client, apply=True)
    assert got["exit"] == 4, got


def test_run_classify_missing_anchor_exits_5():
    client = _FakeClient([])
    got = classify.run_classify(
        "https://soulmatemeets.com/land/sp/23d247da/",
        census_fn=lambda: {"census": JAPAN, "clicked_start": False,
                           "clicked_text": "", "note": ""},
        site_rows=[], client=client, apply=True)
    assert got["exit"] == 5, got


def test_wait_for_census_polls_until_the_page_has_content():
    pages = [_census(), _census(url="https://x.test/", title="X")]
    got = classify.wait_for_census(_eval_from(pages), timeout=0.1, poll=0.0)
    assert got["title"] == "X"


def test_wait_for_window_polls_until_cdp_answers():
    seq = [None, None, "https://x.test/"]
    calls = []

    def _ev(js):
        calls.append(js)
        return seq.pop(0) if seq else None

    assert classify.wait_for_window(_ev, timeout=0.1, poll=0.0) == "https://x.test/"
    assert "JSON.stringify" in calls[0]


def test_wait_for_window_returns_empty_on_timeout():
    assert classify.wait_for_window(lambda js: None, timeout=0.0) == ""


def test_census_has_content_tells_an_empty_shell_from_a_loaded_page():
    assert classify.census_has_content(_census()) is False
    assert classify.census_has_content(_census(title="X")) is True
    assert classify.census_has_content(_census(testids=["step-1"])) is True


def test_census_with_start_can_reuse_the_first_census():
    page = _census(testids=["step-content-item-Basic", "option-x", "legal-checkbox"],
                   hash="#c3RlcDE=")

    def _boom(js):
        raise AssertionError("不该再 eval 第一轮")

    got = classify.census_with_start(_boom, lambda: None, first=page)
    assert got["clicked_start"] is False
    assert got["census"] == page
