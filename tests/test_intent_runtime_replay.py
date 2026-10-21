"""Tests for the real-dump replay harness.

The harness is an experiment, not production code, but its case derivation and
classification still need to be pinned: a replay that quietly mislabels a wrong
action as a hit would be worse than no replay at all.
"""
from __future__ import annotations

import json

from experiments.intent_runtime.replay import (
    Case,
    collapse_repeats,
    derive_cases,
    replay,
    summarize,
)
from agent.browser_runtime import Intent


def radio(text, selector):
    return {"selector": selector, "alternates": [], "stability": "low", "text": text,
            "role": "radio", "region": "main", "visible": True, "selected": False,
            "nearby_text": [], "frame_path": ["main"]}


def field(selector, label="", placeholder="", hint=""):
    return {"selector": selector, "alternates": [], "stability": "high",
            "label": label, "placeholder": placeholder, "hint": hint,
            "type": "text", "frame_path": ["main"]}


def action(text, selector, role="button", region="main"):
    return {"selector": selector, "alternates": [], "stability": "high", "text": text,
            "role": role, "region": region, "visible": True, "frame_path": ["main"]}


def model(*, actions=(), fields=(), url="https://example.test/step"):
    return {"url": url, "page_text": "", "actions": list(actions), "fields": list(fields)}


def test_collapse_repeats_handles_real_option_text():
    assert collapse_repeats("Better sleepBetter sleepBetter sleep") == "Better sleep"
    assert collapse_repeats("18-2418-2418-24") == "18-24"
    assert collapse_repeats("Continue") == "Continue"


def test_derive_choose_cases_uses_the_state_question_and_the_observed_option():
    page = model(actions=[
        radio("More energyMore energyMore energy", "#opt-1"),
        radio("Better sleepBetter sleepBetter sleep", "#opt-2"),
    ])
    state = {"uls": [{"question": "What other health goal would you like to focus on?",
                      "lis": [{"text": "Better sleep/Better sleep/Better sleep"}]}]}
    cases = derive_cases(page, state=state, model_path="step.json")
    choose = [c for c in cases if c.intent.kind == "choose"]
    assert len(choose) == 1
    assert choose[0].intent.question == "What other health goal would you like to focus on?"
    assert choose[0].intent.answer == "Better sleep"
    assert choose[0].expected == ("#opt-2",)


def test_derive_choose_cases_skips_an_option_that_matches_no_observed_action():
    page = model(actions=[radio("More energyMore energyMore energy", "#opt-1")])
    state = {"uls": [{"question": "Goal?", "lis": [
        {"text": "More energy/More energy/More energy"},
        {"text": "Better sleep/Better sleep/Better sleep"},
    ]}]}
    cases = derive_cases(page, state=state, model_path="step.json")
    assert [c.intent.answer for c in cases if c.intent.kind == "choose"] == ["More energy"]


def test_derive_choose_cases_prefers_the_option_role_over_a_same_text_nav_link():
    page = model(actions=[
        radio("Better sleepBetter sleepBetter sleep", "#opt"),
        action("Better sleepBetter sleepBetter sleep", "#nav-link", role="a", region="nav"),
    ])
    state = {"uls": [{"question": "Goal?", "lis": [
        {"text": "Better sleep/Better sleep/Better sleep"}]}]}
    cases = derive_cases(page, state=state, model_path="step.json")
    choose = [c for c in cases if c.intent.kind == "choose"]
    assert len(choose) == 1
    assert choose[0].expected == ("#opt",)


def test_derive_choose_cases_recovers_a_truncated_observed_option():
    long = "Bones, joints + muscles"
    page = model(actions=[
        radio((long * 3)[:50], "#bones"),
        radio("Gut healthGut healthGut health", "#gut"),
    ])
    state = {"uls": [{"question": "Goal?", "lis": [{"text": (long + "/") * 3}]}]}
    cases = derive_cases(page, state=state, model_path="step.json")
    choose = [c for c in cases if c.intent.kind == "choose"]
    assert len(choose) == 1
    assert choose[0].expected == ("#bones",)


def test_derive_choose_cases_recovers_a_state_option_truncated_in_its_last_copy():
    long = "Perimenopause + menopause and related symptoms"
    page = model(actions=[
        radio((long * 3)[:50], "#bones"),
        radio("Gut healthGut healthGut health", "#gut"),
    ])
    state = {"uls": [{"question": "Goal?", "lis": [
        {"text": ((long + "/") * 3)[:72]}]}]}
    cases = derive_cases(page, state=state, model_path="step.json")
    choose = [c for c in cases if c.intent.kind == "choose"]
    assert len(choose) == 1
    assert choose[0].expected == ("#bones",)


def test_derive_advance_cases_requires_a_unique_target():
    two_next = model(actions=[action("Next", "#a"), action("Next", "#b")])
    assert [c for c in derive_cases(two_next, model_path="a.json") if c.intent.kind == "advance"] == []

    one_next = model(actions=[action("Previous", "#back"), action("Next", "#next", region="hero")])
    advance = [c for c in derive_cases(one_next, model_path="b.json") if c.intent.kind == "advance"]
    assert len(advance) == 1
    assert advance[0].expected == ("#next",)


def test_derive_fill_cases_maps_lead_field_labels_only():
    page = model(fields=[
        field("#email", placeholder="Email"),
        field("#zip", label="*Zip/Postal Code"),
        field("#search", label="Search our store"),
    ])
    fills = {c.intent.field: c.expected for c in derive_cases(page, model_path="c.json")
             if c.intent.kind == "fill"}
    assert fills == {"email": ("#email",), "zip": ("#zip",)}


def test_replay_treats_no_target_on_a_negative_case_as_a_correct_abstention(tmp_path):
    empty = model(actions=[action("← BACK", "#back"), action("Ask Luna", "#luna")])
    pickable = model(actions=[action("Continue", "#continue")])
    empty_path = tmp_path / "no-forward.json"
    empty_path.write_text(json.dumps(empty), encoding="utf-8")
    pickable_path = tmp_path / "has-forward.json"
    pickable_path.write_text(json.dumps(pickable), encoding="utf-8")

    outcomes = replay([
        Case("negative-ok", str(empty_path), Intent.advance("What goal?"), ()),
        Case("negative-wrong", str(pickable_path), Intent.advance("What goal?"), ()),
    ])
    assert outcomes[0].verdict == "abstain"
    assert outcomes[0].status == "not_found"
    assert outcomes[1].verdict == "wrong"


def test_replay_classifies_hit_wrong_abstain_and_miss(tmp_path):
    hit_model = model(fields=[field("#email", placeholder="Email")])
    wrong_model = model(actions=[action("Get started", "#start", role="button",
                                        region="main")])
    abstain_model = model(actions=[action("Next", "#a"), action("Next", "#b")])
    miss_model = model(actions=[action("Back", "#back")])
    paths = {}
    for name, page in [("hit", hit_model), ("wrong", wrong_model),
                       ("abstain", abstain_model), ("miss", miss_model)]:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(page), encoding="utf-8")
        paths[name] = str(path)

    cases = [
        Case("hit", paths["hit"], Intent.fill("email", "a@b.c"), ("#email",)),
        Case("wrong", paths["wrong"], Intent.advance("Log in"),
             ('button[data-testid="auth-submit-button"]',)),
        Case("abstain", paths["abstain"], Intent.advance("Q"), ("#a", "#b")),
        Case("miss", paths["miss"], Intent.fill("email", "a@b.c"), ("#email",)),
    ]
    outcomes = replay(cases)
    assert [o.verdict for o in outcomes] == ["hit", "wrong", "abstain", "miss"]

    summary = summarize(outcomes)
    assert summary["total"] == 4
    assert summary["by_kind"]["fill"] == {"cases": 2, "hit": 1, "wrong": 0,
                                          "abstain": 0, "miss": 1, "invalid": 0}
    assert summary["by_kind"]["advance"]["wrong"] == 1
    assert summary["wrong"][0]["name"] == "wrong"
