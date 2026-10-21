from __future__ import annotations

import copy

from agent.browser_runtime import BrowserRuntime, Flow, Intent, Resolver


def field(selector="#zip-v1", value="", *, region="questionnaire",
          near="What is your ZIP Code?"):
    return {"selector": selector, "alternates": [], "stability": "high",
            "label": "Enter ZIP Code", "placeholder": "ZIP Code", "type": "text",
            "nearby_text": [near], "region": region,
            "visible": True, "value": value, "frame_path": ["main"]}


def action(selector, text, *, near, region="questionnaire", selected=None):
    return {"selector": selector, "alternates": [], "stability": "high", "text": text,
            "role": "button", "nearby_text": [near], "region": region, "visible": True,
            "selected": selected, "frame_path": ["main"]}


class FakeDriver:
    def __init__(self, pages, *, expose_values=True):
        self.pages = copy.deepcopy(pages)
        self.index = 0
        self.calls = []
        self.expose_values = expose_values

    def observe(self):
        return copy.deepcopy(self.pages[self.index])

    def form(self, selector, *, value, frame_id=""):
        self.calls.append(("form", selector, value, frame_id))
        current = self.pages[self.index]
        hit = next((x for x in current["fields"] if selector in [x["selector"]] + x["alternates"]), None)
        if not hit:
            return {"ok": False}
        if self.expose_values:
            hit["value"] = value
        return {"ok": True}

    def click(self, selector, *, frame_id=""):
        self.calls.append(("click", selector, frame_id))
        current = self.pages[self.index]
        hit = next((x for x in current["actions"] if selector in [x["selector"]] + x["alternates"]), None)
        if not hit:
            return {"ok": False}
        if hit["text"] == "Walk-in shower":
            hit["selected"] = True
        else:
            self.index += 1
        return {"ok": True}


def page(text, *, fields=(), actions=(), url="https://homebuddy.test/funnel"):
    return {"url": url, "page_text": text, "fields": list(fields), "actions": list(actions)}


def test_duplicate_zip_fields_are_resolved_by_question_context_not_document_order():
    model = page("What is your ZIP Code?", fields=[
        field("#footer-zip", region="footer", near="ZIP Code for a firm local estimate"),
        field("#header-zip")])
    got = Resolver().resolve(
        model, Intent.fill("zip", "60601", question="What is your ZIP Code?"))
    assert got.status == "selected"
    assert got.candidate.selector == "#header-zip"
    assert "nearby question" in got.evidence


def test_advance_excludes_carousel_next_on_the_real_homebuddy_shape():
    model = page("What type of project?", actions=[
        action("#slide", "next slide", near="Select your shower style", region="main"),
        action("#next", "Next", near="What type of project?"),
    ])
    got = Resolver().resolve(model, Intent.advance("What type of project?"))
    assert got.status == "selected"
    assert got.candidate.selector == "#next"


def test_indistinguishable_buttons_abstain_before_any_click():
    model = page("Current question", actions=[
        action("#a", "Next", near="Current question"),
        action("#b", "Next", near="Current question"),
    ])
    driver = FakeDriver([model])
    receipt = BrowserRuntime(driver).execute(Intent.advance("Current question"))
    assert receipt.status == "ambiguous"
    assert driver.calls == []


def test_fill_requires_observed_value_round_trip():
    driver = FakeDriver([page("ZIP", fields=[field()])])
    receipt = BrowserRuntime(driver).execute(Intent.fill("zip", "60601"))
    assert receipt.ok is True
    assert receipt.reason == "field value round-tripped"
    assert driver.calls == [("form", "#zip-v1", "60601", "")]


def test_unobservable_fill_is_not_reported_as_success():
    model = page("ZIP", fields=[field()])
    model["fields"][0].pop("value")
    driver = FakeDriver([model], expose_values=False)
    receipt = BrowserRuntime(driver).execute(Intent.fill("zip", "60601"))
    assert receipt.status == "unverified"
    assert receipt.verified is False


def test_structured_negative_driver_receipt_is_never_treated_as_success():
    assert BrowserRuntime._driver_ok('{"ok":false,"message":"not found"}') is False
    assert BrowserRuntime._driver_ok('{"ok":true}') is True


def test_thin_flow_stops_on_first_unverified_step_and_uses_zero_model_calls():
    pages = [
        page("What is your ZIP Code?", fields=[field()]),
    ]
    runtime = BrowserRuntime(FakeDriver(pages))
    flow = Flow([Intent.fill("zip", "60601"), Intent.advance("ZIP")])
    receipts = flow.run(runtime)
    assert len(receipts) == 2
    assert receipts[0].ok is True
    assert receipts[1].status == "not_found"
    assert runtime.resolver.model_calls == 0

