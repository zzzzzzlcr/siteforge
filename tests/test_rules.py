import re

import pytest
from agent import rules


def test_normalize_accepts_the_first_batch():
    got = rules.normalize_rules({
        "phone_prefixes": ["04", "0207", "04"],
        "dob": {"random": True, "format": "DD/MM/YYYY"},
        "password": "strong",
    })
    assert got["phone_prefixes"] == ["04", "0207"]
    assert got["dob"] == {"random": True, "format": "DD/MM/YYYY"}
    assert got["password"] == "strong"


def test_normalize_accepts_a_uk_prefix_without_the_trunk_zero():
    """★ 2026-10-23（运营）：英国号去掉长途 0 是 `20 7040 XXXX` —— 前缀 `20` 得收。

    运行时补到 10 位 ⇒ 后 **8 位**随机，正是运营给的口径。
    """
    assert rules.normalize_rules({"phone_prefixes": ["20"]}) == {"phone_prefixes": ["20"]}
    #: 混合输入也照收：澳洲 `04` 与英国 `20` 各算一个前缀。
    assert rules.normalize_rules({"phone_prefixes": ["04", "20"]}) == {
        "phone_prefixes": ["04", "20"]}


def test_normalize_rejects_unknown_rules_and_bad_formats():
    with pytest.raises(rules.RuleError):
        rules.normalize_rules({"phone_prefixes": ["4"]})
    with pytest.raises(rules.RuleError):
        rules.normalize_rules({"dob": {"random": True, "format": "03/10/1970"}})
    with pytest.raises(rules.RuleError):
        rules.normalize_rules({"webhook": "x"})


def test_parse_date_format_reads_page_text_but_not_a_date_value():
    assert rules.parse_date_format("Date of birth (DD/MM/YYYY)") == "DD/MM/YYYY"
    assert rules.parse_date_format("MM-DD-YYYY") == "MM-DD-YYYY"
    assert rules.parse_date_format("3/10/1970") is None


def _fill(name, label, selector):
    return {"name": name, "source": name, "kind": "value", "label": label,
            "target": {"label": label, "placeholder": None,
                       "hint": None, "selectors": [selector]}}


def test_apply_phone_dob_password_ignores_nothing_and_sets_rules():
    fills = {
        "phone": _fill("phone", "PhoneNumber", "#phone"),
        "phone_confirm": _fill("phone_confirm", "Confirm phone", "#phone2"),
        "dob": _fill("dob", "Date of birth (DD/MM/YYYY)", "#dob"),
        "password": _fill("password", "Password", "#password"),
    }
    got = rules.apply_to_fills(fills, {
        "phone_prefixes": ["04", "0207"],
        "dob": {"random": True},
        "password": "strong",
    })
    assert got["fills"]["phone"]["rule"] == "phone_prefix"
    assert got["fills"]["phone"]["prefixes"] == ["04", "0207"]
    assert got["fills"]["phone_confirm"]["rule"] == "phone_prefix"
    assert got["fills"]["dob"]["rule"] == "dob_random"
    assert got["fills"]["dob"]["date_format"] == "DD/MM/YYYY"
    assert got["fills"]["password"]["rule"] == "password_strong"
    assert got["applied"] == ["phone:04", "phone:0207", "dob_random", "password_strong"]
    assert got["needs_format"] == []
    assert got["unmatched"] == []


def test_apply_reports_unmatched_and_needs_format():
    got = rules.apply_to_fills(
        {"email": _fill("email", "Email", "#email")},
        {"phone_prefixes": ["04"], "dob": {"random": True}},
    )
    assert got["unmatched"] == ["phone:04", "dob_random"]
    assert got["needs_format"] == []

    only_dob = rules.apply_to_fills(
        {"dob": _fill("dob", "Date of birth", "#dob")},
        {"dob": {"random": True}},
    )
    assert only_dob["needs_format"] == ["dob"]
    assert only_dob["fills"]["dob"].get("rule") is None


def test_matchers_do_not_overmatch_place_of_birth_or_hotel():
    assert not rules.is_dob_fill({"name": "place_of_birth", "label": "Place of birth"})
    assert not rules.is_phone_fill({"name": "hotel", "label": "Hotel"})
    assert rules.is_phone_fill({"name": "phone_number", "label": "Phone"})
    assert rules.is_dob_fill({"name": "dob", "label": "Date of birth"})
    assert rules.is_dob_fill({"name": "birthday", "label": "Birthday"})


def test_run_request_keeps_rules_and_normalize_rejects_unknown():
    from agent.service import RunRequest
    body = RunRequest(url="https://example.test", mode="build",
                      success_text="Thank you",
                      rules={"phone_prefixes": ["04", "0207"]})
    assert body.rules == {"phone_prefixes": ["04", "0207"]}
    with pytest.raises(rules.RuleError):
        rules.normalize_rules({"phone_prefixes": ["4"]})


def test_an_explicit_phone_from_hints_beats_the_random_pool():
    """★ 2026-10-29（job-31cc47a0dbe3）：人说「电话填2014237916」，产物却填了随机池。"""
    assert rules.phone_from_hints(["电话填2014237916"]) == "2014237916"
    assert rules.phone_from_hints(["phone number输入框填2014237916"]) == "2014237916"
    assert rules.phone_from_hints(["电话 201-423-7916"]) == "2014237916"
    assert rules.phone_from_hints(["点 Continue", "下一步"]) == ""
    assert rules.normalize_rules({"phone": "2014237916"}) == {"phone": "2014237916"}
    got = rules.apply_to_fills(
        {"phone": _fill("phone", "Phone number", "#phone")},
        {"phone": "2014237916", "phone_prefixes": ["20"]},
    )
    assert got["fills"]["phone"]["rule"] == "phone_exact"
    assert got["fills"]["phone"]["value"] == "2014237916"
    assert "prefixes" not in got["fills"]["phone"]
    assert got["applied"] == ["phone_exact:2014237916"]


def test_a_us_prefix_is_padded_to_a_shape_the_site_accepts():
    """`20` + 8 位随机：交换局首位不许 0/1（站方按 US 校验会拒）。"""
    got = [rules.pad_phone_prefix("20") for _ in range(200)]
    assert all(re.fullmatch(r"20\d{8}", v) for v in got)
    assert all(v[3] in "23456789" for v in got), got[:5]


def test_explore_uses_the_same_phone_the_artifact_will():
    """探路与产物同一个号（整号 > 前缀 > 人话里的号）。"""
    assert rules.phone_for_explore({"phone": "2014237916"}, []) == "2014237916"
    assert rules.phone_for_explore({}, ["电话填2014237916"]) == "2014237916"
    got = rules.phone_for_explore({"phone_prefixes": ["20"]}, [])
    assert got.startswith("20") and len(got) == 10 and got[3] in "23456789"

