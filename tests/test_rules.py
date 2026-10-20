import pytest

from agent import rules


def test_normalize_accepts_the_first_batch():
    got = rules.normalize_rules({
        "phone_prefixes": ["02", "07", "02"],
        "dob": {"random": True, "format": "DD/MM/YYYY"},
        "password": "strong",
    })
    assert got["phone_prefixes"] == ["02", "07"]
    assert got["dob"] == {"random": True, "format": "DD/MM/YYYY"}
    assert got["password"] == "strong"


def test_normalize_rejects_unknown_rules_and_bad_formats():
    with pytest.raises(rules.RuleError):
        rules.normalize_rules({"phone_prefixes": ["04"]})
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
        "phone_prefixes": ["02", "07"],
        "dob": {"random": True},
        "password": "strong",
    })
    assert got["fills"]["phone"]["rule"] == "phone_prefix"
    assert got["fills"]["phone"]["prefixes"] == ["02", "07"]
    assert got["fills"]["phone_confirm"]["rule"] == "phone_prefix"
    assert got["fills"]["dob"]["rule"] == "dob_random"
    assert got["fills"]["dob"]["date_format"] == "DD/MM/YYYY"
    assert got["fills"]["password"]["rule"] == "password_strong"
    assert got["applied"] == ["phone:02", "phone:07", "dob_random", "password_strong"]
    assert got["needs_format"] == []
    assert got["unmatched"] == []


def test_apply_reports_unmatched_and_needs_format():
    got = rules.apply_to_fills(
        {"email": _fill("email", "Email", "#email")},
        {"phone_prefixes": ["02"], "dob": {"random": True}},
    )
    assert got["unmatched"] == ["phone:02", "dob_random"]
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
                      rules={"phone_prefixes": ["02", "07"]})
    assert body.rules == {"phone_prefixes": ["02", "07"]}
    with pytest.raises(rules.RuleError):
        rules.normalize_rules({"phone_prefixes": ["04"]})
