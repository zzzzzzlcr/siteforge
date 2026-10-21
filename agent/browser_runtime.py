"""Shared, deterministic browser runtime for intent-rich site flows.

This module deliberately contains no site selectors and makes no model calls.  A
site flow states *what* it wants; the runtime maps that intent to one candidate
from the existing ``cdp observe`` PageModel.  If the evidence does not identify
one candidate, it abstains before any browser mutation.

It is the first migration seam away from copying page-resolution policy into
every generated site artifact.  Existing templates remain unchanged until this
runtime has passed real-browser shadow runs.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, Sequence


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _parts(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [_norm(item) for item in value if _norm(item)]
    one = _norm(value)
    return [one] if one else []


@dataclasses.dataclass(frozen=True)
class Intent:
    """One business operation.  It intentionally has no selector field."""

    kind: str
    field: str = ""
    question: str = ""
    answer: str = ""
    goal: str = ""
    value: Any = None

    @classmethod
    def fill(cls, field: str, value: Any, *, question: str = "") -> "Intent":
        return cls("fill", field=field, question=question, value=value)

    @classmethod
    def choose(cls, question: str, answer: str) -> "Intent":
        return cls("choose", question=question, answer=answer)

    @classmethod
    def advance(cls, question: str = "") -> "Intent":
        return cls("advance", question=question)

    @classmethod
    def verify(cls, goal: str) -> "Intent":
        return cls("verify", goal=goal)


@dataclasses.dataclass(frozen=True)
class Candidate:
    source: str
    selector: str
    alternates: tuple[str, ...]
    frame_id: str
    text: str
    label: str
    placeholder: str
    role: str
    region: str
    nearby_text: tuple[str, ...]
    visible: bool
    disabled: bool
    occluded_by: str
    stability: str
    value: Any = None
    selected: Any = None

    @property
    def selectors(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(x for x in (self.selector,) + self.alternates if x))


@dataclasses.dataclass(frozen=True)
class Resolution:
    status: str
    candidate: Optional[Candidate] = None
    score: int = 0
    runner_up: int = 0
    evidence: tuple[str, ...] = ()
    reason: str = ""


@dataclasses.dataclass(frozen=True)
class Receipt:
    status: str
    intent: Intent
    resolution: Optional[Resolution] = None
    selector: str = ""
    frame_id: str = ""
    before: str = ""
    after: str = ""
    verified: bool = False
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "verified"


class Driver(Protocol):
    """Narrow adapter over CDP.  Production can wrap ``CDPHelper`` or MCP."""

    def observe(self) -> Mapping[str, Any]: ...
    def click(self, selector: str, *, frame_id: str = "") -> Any: ...
    def form(self, selector: str, *, value: Any, frame_id: str = "") -> Any: ...


FIELD_ALIASES = {
    "zip": ("zip", "zip code", "zipcode", "postal code", "postcode"),
    "postal_code": ("postal code", "postcode", "zip", "zip code"),
    "email": ("email", "email address", "e-mail"),
    "phone": ("phone", "phone number", "mobile", "telephone"),
    "full_name": ("full name", "name"),
}
FORWARD_WORDS = (
    "next", "continue", "get estimate", "get started", "start", "proceed",
    "submit my request", "see results", "show results",
)
BACK_WORDS = ("back", "previous", "prev", "go back")
NON_FLOW_WORDS = ("next slide", "previous slide", "carousel", "close", "dismiss")
REGION_BONUS = {"questionnaire": 30, "form": 25, "main": 15, "body": 5}
STABILITY_BONUS = {"high": 6, "medium": 3, "low": 0}


def _frame_id(raw: Mapping[str, Any]) -> str:
    path = list(raw.get("frame_path") or [])
    rest = [str(item) for item in path if str(item) and str(item) != "main"]
    return rest[0] if len(rest) == 1 else ""


def candidates(model: Mapping[str, Any]) -> list[Candidate]:
    """Normalize the current PageModel once; do not reinterpret it per intent."""

    out: list[Candidate] = []
    for source in ("fields", "actions"):
        for raw in model.get(source) or []:
            label = raw.get("label") or raw.get("aria_label") or raw.get("hint") or ""
            out.append(Candidate(
                source=source[:-1],
                selector=str(raw.get("selector") or ""),
                alternates=tuple(str(x) for x in (raw.get("alternates") or []) if x),
                frame_id=_frame_id(raw),
                text=str(raw.get("text") or raw.get("aria_label") or ""),
                label=str(label or ""),
                placeholder=str(raw.get("placeholder") or ""),
                role=str(raw.get("role") or ("textbox" if source == "fields" else "")),
                region=str(raw.get("region") or ""),
                nearby_text=tuple(str(x) for x in (raw.get("nearby_text") or []) if x),
                visible=bool(raw.get("visible", True)),
                disabled=bool(raw.get("disabled", False)),
                occluded_by=str(raw.get("occluded_by") or ""),
                stability=str(raw.get("stability") or ""),
                value=raw.get("value"),
                selected=raw.get("selected"),
            ))
    return out


def fingerprint(model: Mapping[str, Any]) -> str:
    """Small transition identity: semantics and state, never selector spelling."""

    facts = {
        "url": _norm(model.get("url")),
        "text": _norm(model.get("page_text")),
        "elements": [
            (_norm(c.text), _norm(c.label), _norm(c.role), _norm(c.region),
             _norm(c.value), c.selected)
            for c in candidates(model)
        ],
    }
    raw = json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class Resolver:
    """Resolve exactly one semantic candidate or abstain."""

    def __init__(self, *, minimum_score: int = 45, minimum_margin: int = 10):
        self.minimum_score = minimum_score
        self.minimum_margin = minimum_margin
        self.model_calls = 0

    @staticmethod
    def _usable(candidate: Candidate) -> bool:
        blocked = _norm(candidate.occluded_by)
        return (candidate.visible and not candidate.disabled
                and (not blocked or blocked == "offscreen") and bool(candidate.selectors))

    @staticmethod
    def _contains_any(text: str, words: Iterable[str]) -> bool:
        return any(word and word in text for word in words)

    def _score_fill(self, candidate: Candidate, intent: Intent) -> tuple[int, list[str]]:
        if candidate.source != "field":
            return -1000, []
        wanted = _norm(intent.field)
        aliases = tuple(_norm(x) for x in FIELD_ALIASES.get(wanted, (wanted,)))
        identity = _norm(" ".join((candidate.label, candidate.placeholder, candidate.role)))
        nearby = _norm(" ".join(candidate.nearby_text))
        score, why = 0, []
        if identity in aliases:
            score += 80; why.append("field identity exact")
        elif self._contains_any(identity, aliases):
            score += 55; why.append("field identity")
        if self._contains_any(nearby, aliases):
            score += 35; why.append("nearby question")
        question = _norm(intent.question)
        if question and question in nearby:
            score += 60; why.append("requested question context")
        score += REGION_BONUS.get(_norm(candidate.region), 0)
        if REGION_BONUS.get(_norm(candidate.region), 0):
            why.append("active region")
        score += STABILITY_BONUS.get(_norm(candidate.stability), 0)
        return score, why

    def _score_choose(self, candidate: Candidate, intent: Intent) -> tuple[int, list[str]]:
        if candidate.source != "action":
            return -1000, []
        answer, question = _norm(intent.answer), _norm(intent.question)
        text = _norm(" ".join((candidate.text, candidate.label)))
        nearby = _norm(" ".join(candidate.nearby_text))
        score, why = 0, []
        if answer and text == answer:
            score += 85; why.append("answer exact")
        elif answer and answer in text:
            score += 60; why.append("answer text")
        if question and question in nearby:
            score += 45; why.append("question context")
        if _norm(candidate.role) in ("option", "radio", "button", "checkbox"):
            score += 5; why.append("answer role")
        score += REGION_BONUS.get(_norm(candidate.region), 0)
        return score, why

    def _score_advance(self, candidate: Candidate, intent: Intent) -> tuple[int, list[str]]:
        if candidate.source != "action":
            return -1000, []
        text = _norm(" ".join((candidate.text, candidate.label)))
        nearby = _norm(" ".join(candidate.nearby_text))
        question = _norm(intent.question)
        score, why = 0, []
        if text in FORWARD_WORDS:
            score += 65; why.append("forward text exact")
        elif self._contains_any(text, FORWARD_WORDS):
            score += 35; why.append("forward text")
        if self._contains_any(text, BACK_WORDS + NON_FLOW_WORDS):
            score -= 120; why.append("non-flow navigation")
        if _norm(candidate.role) == "button":
            score += 5; why.append("button role")
        region = _norm(candidate.region)
        score += REGION_BONUS.get(region, 0)
        if REGION_BONUS.get(region, 0):
            why.append("active region")
        if region in ("header", "footer", "nav", "aside"):
            score -= 60; why.append("outside flow")
        if question and question in nearby:
            score += 45; why.append("question context")
        return score, why

    def resolve(self, model: Mapping[str, Any], intent: Intent) -> Resolution:
        if intent.kind == "verify":
            found = _norm(intent.goal) in _norm(model.get("page_text"))
            return Resolution("selected" if found else "not_found", score=100 if found else 0,
                              evidence=("goal text",) if found else (),
                              reason="goal is present" if found else "goal is absent")
        scorer: Callable[[Candidate, Intent], tuple[int, list[str]]] = {
            "fill": self._score_fill,
            "choose": self._score_choose,
            "advance": self._score_advance,
        }.get(intent.kind)  # type: ignore[assignment]
        if scorer is None:
            return Resolution("invalid", reason="unknown intent: %s" % intent.kind)
        ranked = []
        for candidate in candidates(model):
            if not self._usable(candidate):
                continue
            score, evidence = scorer(candidate, intent)
            if score > 0:
                ranked.append((score, candidate, tuple(evidence)))
        ranked.sort(key=lambda row: (-row[0], -STABILITY_BONUS.get(_norm(row[1].stability), 0)))
        if not ranked or ranked[0][0] < self.minimum_score:
            return Resolution("not_found", score=ranked[0][0] if ranked else 0,
                              reason="no candidate reached the semantic threshold")
        top = ranked[0]
        runner = ranked[1][0] if len(ranked) > 1 else 0
        tied = len(ranked) > 1 and top[0] == runner
        too_close = len(ranked) > 1 and top[0] - runner < self.minimum_margin
        if tied or too_close:
            return Resolution("ambiguous", score=top[0], runner_up=runner,
                              evidence=top[2],
                              reason="top semantic candidates are not separated safely")
        return Resolution("selected", candidate=top[1], score=top[0], runner_up=runner,
                          evidence=top[2], reason="unique semantic winner")


class BrowserRuntime:
    """Execute intents through one shared driver and return evidence-bearing receipts."""

    def __init__(self, driver: Driver, resolver: Optional[Resolver] = None):
        self.driver = driver
        self.resolver = resolver or Resolver()

    @staticmethod
    def _driver_ok(raw: Any) -> bool:
        if isinstance(raw, Mapping):
            return bool(raw.get("ok"))
        original = str(raw or "").strip()
        if original.startswith("{"):
            try:
                decoded = json.loads(original)
            except (TypeError, ValueError):
                decoded = None
            if isinstance(decoded, Mapping) and "ok" in decoded:
                return decoded.get("ok") is True
        text = _norm(original)
        return not text.startswith("error") and ("ok" in text or "success" in text)

    def execute(self, intent: Intent) -> Receipt:
        before_model = self.driver.observe()
        before = fingerprint(before_model)
        resolution = self.resolver.resolve(before_model, intent)
        if intent.kind == "verify":
            return Receipt("verified" if resolution.status == "selected" else "unverified",
                           intent, resolution, before=before, after=before,
                           verified=resolution.status == "selected", reason=resolution.reason)
        if resolution.status != "selected" or resolution.candidate is None:
            return Receipt(resolution.status, intent, resolution, before=before,
                           reason=resolution.reason)

        candidate = resolution.candidate
        attempted: list[str] = []
        used = ""
        for selector in candidate.selectors:
            attempted.append(selector)
            if intent.kind == "fill":
                raw = self.driver.form(selector, value=intent.value, frame_id=candidate.frame_id)
            else:
                raw = self.driver.click(selector, frame_id=candidate.frame_id)
            if self._driver_ok(raw):
                used = selector
                break
        if not used:
            return Receipt("action_failed", intent, resolution, frame_id=candidate.frame_id,
                           before=before, reason="all selectors failed: %s" % attempted)

        after_model = self.driver.observe()
        after = fingerprint(after_model)
        verified, reason = self._verify_effect(intent, candidate, before_model, after_model)
        return Receipt("verified" if verified else "unverified", intent, resolution,
                       selector=used, frame_id=candidate.frame_id, before=before, after=after,
                       verified=verified, reason=reason)

    def _verify_effect(self, intent: Intent, chosen: Candidate,
                       before: Mapping[str, Any], after: Mapping[str, Any]) -> tuple[bool, str]:
        if intent.kind == "fill":
            again = self.resolver.resolve(
                after, Intent.fill(intent.field, intent.value, question=intent.question))
            if again.status != "selected" or again.candidate is None:
                return False, "field cannot be identified uniquely after fill"
            if again.candidate.value is None:
                return False, "observer did not expose the field value"
            same = str(again.candidate.value) == str(intent.value)
            return same, "field value round-tripped" if same else "field value did not round-trip"
        if intent.kind == "choose":
            again = self.resolver.resolve(after, intent)
            if again.status == "selected" and again.candidate is not None:
                if again.candidate.selected is True:
                    return True, "selected state round-tripped"
            if fingerprint(before) != fingerprint(after):
                return True, "page state changed after answer"
            return False, "no selected-state or page-change evidence"
        changed = fingerprint(before) != fingerprint(after)
        return changed, "page state changed" if changed else "no transition evidence"


class Flow:
    """A thin business flow: ordered intents and no browser implementation detail."""

    def __init__(self, intents: Sequence[Intent]):
        self.intents = tuple(intents)

    def run(self, runtime: BrowserRuntime) -> list[Receipt]:
        receipts: list[Receipt] = []
        for intent in self.intents:
            receipt = runtime.execute(intent)
            receipts.append(receipt)
            if not receipt.ok:
                break
        return receipts

