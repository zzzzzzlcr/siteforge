"""Replay real captured ``cdp observe`` dumps through the intent runtime.

This is a measurement harness, not production code.  It takes PageModels that
were captured on real sites (``docs/probes``), reconstructs the intent each
step was trying to express, and records whether :class:`Resolver` picked the
element the site actually used, abstained, or picked something else.

The point is to test the *resolver*, not the synthetic demo: cases come from
real DOM ground truth (a ``state-*.json`` sidecar written by the probe), while
the resolver only sees the observe model.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
from typing import Any, Mapping, Optional, Sequence

from agent.browser_runtime import Intent, Resolver


#: Forward actions whose ground truth is unambiguous when exactly one exists.
FORWARD_EXACT = frozenset((
    "next", "continue", "get estimate", "get started", "start", "proceed",
    "submit my request", "see results", "show results",
))

#: Field keyword -> intent field name.  Ordered: the first hit wins, so "zip"
#: must come before "postal code" for labels like "*Zip/Postal Code".
FILL_KEYWORDS = (
    ("email", "email"),
    ("e-mail", "email"),
    ("password", "password"),
    ("zip", "zip"),
    ("postal code", "postal_code"),
    ("postcode", "postal_code"),
    ("phone", "phone"),
)
FILL_VALUES = {
    "email": "probe@example.com",
    "password": "Abcdef12!",
    "zip": "60601",
    "postal_code": "60601",
    "phone": "+12016423915",
}

#: Hand-labelled cases where the automatic rule cannot see the ground truth.
#: Kept separate by name prefix so a run report can show them apart.
MANUAL_CASES = (
    ("2026-09-17-observe-blinkist/observe-login-fields.json",
     "manual:blinkist-login:advance",
     Intent.advance("Log in"),
     ('button[data-testid="auth-submit-button"]',),
     "login page: the next step for an email flow is the email login submit"),
    ("2026-10-15-wildnutrition-quiz-li/r3-observe-Q10.json",
     "manual:wildnutrition-Q10:advance-without-continue",
     Intent.advance("What other health goal would you like to focus on?"),
     (),
     "question screen has BACK only (has_continue=false): must abstain"),
    ("2026-10-15-wildnutrition-quiz-li/r3-observe-Q10.json",
     "manual:wildnutrition-Q10:absent-answer",
     Intent.choose("What other health goal would you like to focus on?", "Vegan diet"),
     (),
     "answer is not on this screen: must abstain rather than pick another option"),
    ("2026-09-28-georgiapower/地址屏-observe-真实字段.json",
     "manual:georgiapower:no-email-field",
     Intent.fill("email", "probe@example.com"),
     (),
     "address screen has no email field: must abstain"),
)


def collapse_repeats(value: Any) -> str:
    """Collapse text the site rendered 2-3 times (responsive duplicates).

    Real observe dumps contain e.g. ``Better sleepBetter sleepBetter sleep``
    and ``18-2418-2418-24``; the DOM state sidecars contain the same values
    separated by ``/``.
    """
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    for count in (3, 2):
        if len(text) % count == 0:
            unit = text[: len(text) // count]
            if unit and unit * count == text:
                return unit.strip()
    return text


def _option_text(value: Any) -> str:
    text = str(value or "").strip()
    parts = [part.strip() for part in text.split("/") if part.strip()]
    if len(parts) >= 2:
        if len(set(parts)) == 1:
            return parts[0]
        # The state extractor caps each li text; with long labels the last
        # copy is cut mid-word ("...Bones, joints + muscle").  Treat prefixes
        # of the longest copy as the same option.
        longest = max(parts, key=len)
        if min(len(part) for part in parts) >= 10 and all(
                longest.startswith(part) for part in parts):
            return longest
    return collapse_repeats(text)


@dataclasses.dataclass(frozen=True)
class Case:
    name: str
    model_path: str
    intent: Intent
    expected: tuple[str, ...]
    source: str = "auto"


@dataclasses.dataclass(frozen=True)
class Outcome:
    name: str
    kind: str
    source: str
    status: str
    picked: str
    picked_text: str
    score: int
    expected: tuple[str, ...]
    verdict: str
    reason: str


def _selectors(raw: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(x) for x in (raw.get("selector"),) if x)


def _advance_cases(model: Mapping[str, Any], model_path: str, base: str) -> list[Case]:
    forward = []
    for raw in model.get("actions") or []:
        text = collapse_repeats(raw.get("text") or raw.get("aria_label") or "")
        if (text.casefold() in FORWARD_EXACT and raw.get("visible", True)
                and not raw.get("disabled", False) and raw.get("selector")):
            forward.append(raw)
    if len(forward) != 1:
        return []
    return [Case("%s:advance" % base, model_path, Intent.advance(""),
                 _selectors(forward[0]))]


OPTION_ROLES = ("radio", "option", "checkbox")


def _observed_text(raw: Mapping[str, Any]) -> str:
    return collapse_repeats(raw.get("text") or raw.get("aria_label") or "")


def _match_actions(actions: Sequence[Mapping[str, Any]], answer: str) -> list[Mapping[str, Any]]:
    """Find the observed action(s) that carry this DOM option.

    Two real shapes need care: a nav link can share the option text (region
    nav, role a), and ``cdp observe`` truncates long text at ~50 chars.  Both
    are disambiguated only when exactly one option-role or truncated candidate
    remains; otherwise the ground truth is left unlabelled.
    """
    exact = [raw for raw in actions
             if raw.get("selector") and _observed_text(raw).casefold() == answer.casefold()]
    if len(exact) > 1:
        options = [raw for raw in exact
                   if str(raw.get("role") or "").casefold() in OPTION_ROLES]
        if len(options) == 1:
            return options
    if exact:
        return exact
    low = answer.casefold()
    truncated = [
        raw for raw in actions
        if raw.get("selector") and len(_observed_text(raw)) >= 45
        and _observed_text(raw).casefold().startswith(low)
    ]
    return truncated if len(truncated) == 1 else []


def _choose_cases(model: Mapping[str, Any], state: Mapping[str, Any],
                  model_path: str, base: str) -> list[Case]:
    actions = model.get("actions") or []
    out = []
    for group in state.get("uls") or []:
        question = " ".join(str(group.get("question") or "").split())
        if not question:
            continue
        for item in group.get("lis") or []:
            answer = _option_text(item.get("text"))
            if not answer:
                continue
            matches = _match_actions(actions, answer)
            if len(matches) == 1:
                out.append(Case("%s:choose:%s" % (base, answer), model_path,
                                Intent.choose(question, answer),
                                _selectors(matches[0])))
    return out


def _fill_cases(model: Mapping[str, Any], model_path: str, base: str) -> list[Case]:
    by_field: dict[str, list[str]] = {}
    for raw in model.get("fields") or []:
        identity = " ".join(str(x or "") for x in
                            (raw.get("label"), raw.get("placeholder"), raw.get("hint")))
        low = identity.casefold()
        if not raw.get("selector"):
            continue
        for keyword, field_name in FILL_KEYWORDS:
            if keyword in low:
                by_field.setdefault(field_name, []).append(str(raw["selector"]))
                break
    return [
        Case("%s:fill:%s" % (base, field_name), model_path,
             Intent.fill(field_name, FILL_VALUES[field_name]), tuple(selectors))
        for field_name, selectors in by_field.items()
    ]


def derive_cases(model: Mapping[str, Any], *, state: Optional[Mapping[str, Any]] = None,
                 model_path: str = "", name_prefix: str = "") -> list[Case]:
    """Build replay cases with DOM ground truth; never invent an expected target."""
    base = name_prefix or os.path.basename(model_path) or "model"
    cases = _advance_cases(model, model_path, base)
    if state:
        cases += _choose_cases(model, state, model_path, base)
    cases += _fill_cases(model, model_path, base)
    return cases


def replay(cases: Sequence[Case], *, resolver: Optional[Resolver] = None) -> list[Outcome]:
    resolver = resolver or Resolver()
    models: dict[str, Mapping[str, Any]] = {}
    outcomes = []
    for case in cases:
        if case.model_path not in models:
            with open(case.model_path, encoding="utf-8") as handle:
                models[case.model_path] = json.load(handle)
        model = models[case.model_path]
        got = resolver.resolve(model, case.intent)
        picked = got.candidate.selector if got.candidate is not None else ""
        picked_text = got.candidate.text if got.candidate is not None else ""
        if case.expected:
            if got.status == "selected":
                verdict = "hit" if picked in case.expected else "wrong"
            elif got.status == "ambiguous":
                verdict = "abstain"
            elif got.status == "not_found":
                verdict = "miss"
            else:
                verdict = "invalid"
        else:
            # Negative case: there is no correct target, so abstaining is the
            # only safe outcome and any selection is a wrong action.
            if got.status in ("not_found", "ambiguous"):
                verdict = "abstain"
            elif got.status == "selected":
                verdict = "wrong"
            else:
                verdict = "invalid"
        outcomes.append(Outcome(case.name, case.intent.kind, case.source, got.status,
                                picked, picked_text, got.score, case.expected,
                                verdict, got.reason))
    return outcomes


def summarize(outcomes: Sequence[Outcome]) -> dict[str, Any]:
    by_kind: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        row = by_kind.setdefault(outcome.kind, {
            "cases": 0, "hit": 0, "wrong": 0, "abstain": 0, "miss": 0, "invalid": 0,
        })
        row["cases"] += 1
        row[outcome.verdict] += 1
    wrong = [
        {"name": o.name, "picked": o.picked, "picked_text": o.picked_text,
         "expected": list(o.expected), "reason": o.reason}
        for o in outcomes if o.verdict == "wrong"
    ]
    return {"total": len(outcomes), "by_kind": by_kind, "wrong": wrong}


def _state_sidecar(path: str) -> Optional[str]:
    folder, base = os.path.split(path)
    stem, _ = os.path.splitext(base)
    candidates = [stem]
    if stem.endswith("-repo"):
        candidates.append(stem[:-5])
    for candidate in candidates:
        if "observe" not in candidate:
            continue
        state = os.path.join(folder, candidate.replace("observe", "state") + ".json")
        if os.path.exists(state):
            return state
    return None


def collect_cases(root: str = "docs/probes") -> list[Case]:
    cases: list[Case] = []
    for folder, _, names in os.walk(root):
        for name in sorted(names):
            if not name.endswith(".json"):
                continue
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8") as handle:
                    model = json.load(handle)
            except (OSError, ValueError):
                continue
            if not isinstance(model, dict) or not ("actions" in model or "fields" in model):
                continue
            state = None
            sidecar = _state_sidecar(path)
            if sidecar:
                with open(sidecar, encoding="utf-8") as handle:
                    state = json.load(handle)
            cases += derive_cases(model, state=state, model_path=path,
                                  name_prefix=os.path.splitext(name)[0])
    for relative, name, intent, expected, _why in MANUAL_CASES:
        path = os.path.join(root, relative)
        if os.path.exists(path):
            cases.append(Case(name, path, intent, expected, source="manual"))
    return cases


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="docs/probes")
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)

    cases = collect_cases(args.root)
    outcomes = replay(cases)
    summary = summarize(outcomes)
    print("replayed %d cases" % summary["total"])
    for kind, row in sorted(summary["by_kind"].items()):
        print("  %-7s cases=%-4d hit=%-4d wrong=%-3d abstain=%-3d miss=%-3d invalid=%d"
              % (kind, row["cases"], row["hit"], row["wrong"], row["abstain"],
                 row["miss"], row["invalid"]))
    for row in summary["wrong"]:
        print("  WRONG %s -> %r (expected %s)" % (row["name"], row["picked_text"],
                                                  ", ".join(row["expected"])))
    for outcome in outcomes:
        if outcome.verdict == "abstain" and outcome.source == "manual":
            print("  ABSTAIN %s: %s" % (outcome.name, outcome.reason))
    if args.json:
        payload = {
            "summary": summary,
            "outcomes": [dataclasses.asdict(o) for o in outcomes],
        }
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print("wrote %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
