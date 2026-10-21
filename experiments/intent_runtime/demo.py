"""HomeBuddy intent-runtime experiment (zero model calls).

This is deliberately isolated from production.  The input shape mirrors the useful part of
``cdp observe`` (text/role/label/near/region/selectors), while actions are executed against a
small deterministic page harness.  It answers one narrow question: can a thin business flow
survive implementation-level DOM mutations better than captured selectors without guessing?
"""
from __future__ import annotations

import copy
import dataclasses
import json
import re
from typing import Any


def norm(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


@dataclasses.dataclass
class Decision:
    status: str
    element_id: str = ""
    score: int = 0
    reason: str = ""


class IntentRuntime:
    """Deterministic resolver.  A tie is an abstention, never a document-order click."""

    model_calls = 0

    @staticmethod
    def _pick(scored: list[tuple[int, dict, list[str]]]) -> Decision:
        usable = [row for row in scored if row[0] > 0]
        if not usable:
            return Decision("not_found", reason="no candidate has positive semantic evidence")
        usable.sort(key=lambda row: row[0], reverse=True)
        best = usable[0][0]
        winners = [row for row in usable if row[0] == best]
        if len(winners) != 1:
            return Decision("ambiguous", score=best,
                            reason="top candidates are semantically tied: " + ", ".join(
                                str(row[1].get("id") or "?") for row in winners))
        score, element, why = winners[0]
        return Decision("selected", str(element.get("id") or ""), score, "; ".join(why))

    def fill(self, elements: list[dict], field: str) -> Decision:
        wanted = norm(field)
        aliases = {
            "zip": ("zip", "zipcode", "zip code", "postal code", "postcode"),
            "email": ("email", "email address"),
        }.get(wanted, (wanted,))
        scored = []
        for el in elements:
            if el.get("kind") != "field":
                continue
            identity = norm(" ".join(str(el.get(k) or "")
                                     for k in ("label", "name", "placeholder")))
            near = norm(el.get("near"))
            points, why = 0, []
            if any(alias == identity for alias in aliases):
                points += 80; why.append("accessible identity exactly names the field")
            elif any(alias in identity for alias in aliases):
                points += 55; why.append("accessible identity contains the field meaning")
            if any(alias in near for alias in aliases):
                points += 35; why.append("nearby question names the field")
            if norm(el.get("region")) in ("questionnaire", "main"):
                points += 10; why.append("inside the active questionnaire")
            scored.append((points, el, why))
        return self._pick(scored)

    def choose(self, elements: list[dict], *, question: str, answer: str) -> Decision:
        want_answer, want_question = norm(answer), norm(question)
        scored = []
        for el in elements:
            if el.get("kind") != "action":
                continue
            points, why = 0, []
            text, near = norm(el.get("text")), norm(el.get("near"))
            if text == want_answer:
                points += 80; why.append("answer text is exact")
            elif want_answer and want_answer in text:
                points += 50; why.append("answer text is contained")
            if want_question and want_question in near:
                points += 45; why.append("belongs to the requested question")
            if norm(el.get("role")) in ("option", "radio", "button"):
                points += 5; why.append("interactive answer role")
            scored.append((points, el, why))
        return self._pick(scored)

    def advance(self, elements: list[dict], *, question: str = "") -> Decision:
        want_question = norm(question)
        words = ("next", "continue", "get estimate", "submit my request", "submit")
        scored = []
        for el in elements:
            if el.get("kind") != "action":
                continue
            points, why = 0, []
            text, near = norm(el.get("text")), norm(el.get("near"))
            if text in words:
                points += 55; why.append("text expresses forward progress")
            if norm(el.get("role")) == "button":
                points += 5; why.append("button role")
            if norm(el.get("region")) == "questionnaire":
                points += 25; why.append("inside the active questionnaire")
            if want_question and want_question in near:
                points += 35; why.append("belongs to the current question")
            if norm(el.get("purpose")) in ("back", "navigation", "close"):
                points -= 100; why.append("negative navigation purpose")
            scored.append((points, el, why))
        return self._pick(scored)


class PageHarness:
    """Tiny action harness: a wrong selection is observable, not silently accepted."""

    def __init__(self, pages: list[list[dict]]):
        self.pages = copy.deepcopy(pages)
        self.stage = 0
        self.events: list[tuple[str, str]] = []
        self.wrong_actions = 0

    @property
    def elements(self) -> list[dict]:
        return self.pages[min(self.stage, len(self.pages) - 1)]

    def act(self, element_id: str, expected_purpose: str) -> bool:
        hit = next((el for el in self.elements if el.get("id") == element_id), None)
        if not hit or hit.get("purpose") != expected_purpose:
            self.wrong_actions += 1
            self.events.append((expected_purpose, "wrong:" + str(element_id)))
            return False
        self.events.append((expected_purpose, element_id))
        self.stage += 1
        return True

    def first_by_selector(self, selector: str) -> str:
        for el in self.elements:
            if selector in (el.get("selectors") or []):
                return str(el.get("id") or "")
        return ""


BASE_PAGES = [
    [
        {"id": "zip", "kind": "field", "label": "Enter ZIP Code", "name": "zip",
         "placeholder": "ZIP Code", "near": "Enter ZIP Code to get your estimate",
         "region": "questionnaire", "purpose": "fill_zip", "selectors": ["#zip-v1"]},
        {"id": "newsletter", "kind": "field", "label": "Email", "name": "email",
         "placeholder": "Your email", "near": "Newsletter", "region": "footer",
         "purpose": "newsletter", "selectors": ["#footer-email"]},
    ],
    [
        {"id": "walkin", "kind": "action", "text": "Walk-in shower", "role": "option",
         "near": "What type of project?", "region": "questionnaire", "purpose": "choose_project",
         "selectors": ["#project-walkin", ".choice.walkin"]},
        {"id": "bath", "kind": "action", "text": "Bath liner", "role": "option",
         "near": "What type of project?", "region": "questionnaire", "purpose": "other_answer",
         "selectors": ["#project-bath"]},
    ],
    [
        {"id": "next", "kind": "action", "text": "Next", "role": "button",
         "near": "What type of project?", "region": "questionnaire", "purpose": "advance",
         "selectors": ["#next-v1", ".btn.primary", "main>div:nth-child(4)>button"]},
    ],
    [
        {"id": "thankyou", "kind": "fact", "text": "Thank you", "purpose": "goal",
         "selectors": []},
    ],
]

SELECTOR_FLOW = [
    ("#zip-v1", "fill_zip"),
    ("#project-walkin", "choose_project"),
    (".btn.primary", "advance"),
]


def mutate(name: str) -> list[list[dict]]:
    pages = copy.deepcopy(BASE_PAGES)
    if name in ("dynamic_id", "combined"):
        pages[0][0]["selectors"] = ["#zip-random-9182"]
        pages[1][0]["selectors"] = ["#project-random-2241"]
        pages[2][0]["selectors"] = ["#next-random-6620"]
    if name in ("insert_wrapper", "combined"):
        pages[2][0]["selectors"] = ["main>section>div>div:nth-child(5)>button"]
    if name in ("class_change", "combined"):
        pages[1][0]["selectors"] = [".choice-card-v2"]
    if name in ("button_moved", "combined"):
        pages[2].insert(0, {"id": "nav-next", "kind": "action", "text": "Next", "role": "button",
                              "near": "Article navigation", "region": "header", "purpose": "navigation",
                              "selectors": [".btn.primary"]})
    if name in ("same_name", "combined"):
        pages[2].insert(0, {"id": "promo-next", "kind": "action", "text": "Next", "role": "button",
                              "near": "Related offers", "region": "aside", "purpose": "navigation",
                              "selectors": [".btn.primary"]})
    if name in ("context_swap", "combined"):
        # Same visual class, reversed document order.  Semantics survive in label/near/region.
        pages[0][0]["selectors"] = [".input-field"]
        pages[0][1]["selectors"] = [".input-field"]
        pages[0] = [pages[0][1], pages[0][0]]
    return pages


def run_selector_rich(pages: list[list[dict]]) -> dict:
    page = PageHarness(pages)
    for selector, purpose in SELECTOR_FLOW:
        if not page.act(page.first_by_selector(selector), purpose):
            break
    return {"completed": page.stage == 3, "wrong_actions": page.wrong_actions,
            "events": page.events, "model_calls": 0}


def run_intent_rich(pages: list[list[dict]]) -> dict:
    page, runtime = PageHarness(pages), IntentRuntime()
    decisions = [runtime.fill(page.elements, "zip")]
    if decisions[-1].status == "selected":
        page.act(decisions[-1].element_id, "fill_zip")
    if page.stage == 1:
        decisions.append(runtime.choose(page.elements, question="What type of project?",
                                        answer="Walk-in shower"))
        if decisions[-1].status == "selected":
            page.act(decisions[-1].element_id, "choose_project")
    if page.stage == 2:
        decisions.append(runtime.advance(page.elements, question="What type of project?"))
        if decisions[-1].status == "selected":
            page.act(decisions[-1].element_id, "advance")
    return {"completed": page.stage == 3, "wrong_actions": page.wrong_actions,
            "events": page.events, "decisions": [dataclasses.asdict(d) for d in decisions],
            "model_calls": runtime.model_calls}


def irreducible_ambiguity() -> Decision:
    runtime = IntentRuntime()
    candidates = [
        {"id": "a", "kind": "action", "text": "Next", "role": "button",
         "near": "Current question", "region": "questionnaire", "purpose": "advance"},
        {"id": "b", "kind": "action", "text": "Next", "role": "button",
         "near": "Current question", "region": "questionnaire", "purpose": "other"},
    ]
    return runtime.advance(candidates, question="Current question")


def results() -> dict:
    names = ("baseline", "dynamic_id", "insert_wrapper", "class_change", "button_moved",
             "same_name", "context_swap", "combined")
    rows = []
    for name in names:
        pages = BASE_PAGES if name == "baseline" else mutate(name)
        rows.append({"scenario": name, "selector_rich": run_selector_rich(pages),
                     "intent_rich": run_intent_rich(pages)})
    return {"rows": rows, "irreducible_ambiguity": dataclasses.asdict(irreducible_ambiguity())}


if __name__ == "__main__":
    print(json.dumps(results(), ensure_ascii=False, indent=2))
