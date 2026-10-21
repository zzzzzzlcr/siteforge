from experiments.intent_runtime import demo


def test_intent_flow_survives_all_implementation_mutations_without_tokens():
    got = demo.results()
    assert all(row["intent_rich"]["completed"] for row in got["rows"]), got
    assert all(row["intent_rich"]["wrong_actions"] == 0 for row in got["rows"]), got
    assert all(row["intent_rich"]["model_calls"] == 0 for row in got["rows"]), got


def test_selector_flow_only_survives_the_unchanged_baseline():
    rows = {row["scenario"]: row for row in demo.results()["rows"]}
    assert rows["baseline"]["selector_rich"]["completed"] is True
    assert all(not rows[name]["selector_rich"]["completed"] for name in rows if name != "baseline")


def test_irreducible_ambiguity_abstains_instead_of_clicking_document_order():
    decision = demo.irreducible_ambiguity()
    assert decision.status == "ambiguous"
    assert decision.element_id == ""
