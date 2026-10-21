# Intent Runtime feasibility experiment

## Result

The small deterministic experiment supports continuing with the architecture:
keep business intent in the site flow and resolve page implementation inside a
shared browser runtime.

| Scenario | selector-rich flow | intent-rich flow |
| --- | --- | --- |
| unchanged page | completed | completed |
| dynamic IDs | failed | completed |
| inserted wrapper | failed | completed |
| changed class | failed | completed |
| moved button / competing selector | wrong action | completed |
| same-name button elsewhere | wrong action | completed |
| changed page context | failed | completed |
| combined changes | failed | completed |

Across all eight scenarios the intent flow made zero wrong actions and zero
model calls. When presented with two genuinely indistinguishable `Next`
buttons, it abstained with an `ambiguous` result instead of clicking one by DOM
order.

## What was tested

The demo uses the same kinds of structured evidence already available from the
current observation layer: visible text, role, label, nearby question, page
region, and selectors. It runs a small HomeBuddy-shaped flow:

1. fill the ZIP field;
2. choose `Walk-in shower` for the project question;
3. advance the questionnaire.

The selector baseline captures implementation details. The intent flow calls
`fill`, `choose`, and `advance`; a deterministic runtime scores the candidates.

Run it with:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_intent_runtime_demo.py -q --basetemp ..\pytest-intent-demo
.venv\Scripts\python.exe -m experiments.intent_runtime.demo
```

## Boundaries

This is a resolution-layer feasibility test, not a live browser or full
HomeBuddy proof. Its page observations and mutations are synthetic, and it does
not exercise CDP, iframe/shadow traversal, navigation timing, or the complete
33-step production flow.

The next decision gate should connect these same runtime calls to the existing
CDP observation output and replay one real HomeBuddy path against captured or
live pages. Keep deterministic resolution first; only route an ambiguous
candidate set to an optional decision model. Normal unambiguous replay should
continue to consume zero model tokens.

## Live-site probe (2026-09-23)

The production HomeBuddy URL loaded successfully in a real browser and exposed
its rendered accessibility tree and DOM snapshot. The live page contains two
visible `Enter ZIP Code` textboxes, two `Get estimate` buttons, and a carousel
action named `next slide`, while also presenting several question-specific
button groups. This is direct evidence that text or document-order matching is
insufficient: resolving the first ZIP step requires the nearby heading/region,
and questionnaire advancement must exclude carousel navigation.

The intent boundary was able to identify the top ZIP field from the surrounding
`What Is Your ZIP Code?` region without using a captured selector. A synthetic
ZIP fill was attempted, and the subsequent observation confirmed focus on that
specific field. The browser bridge timed out before returning a readable field
value, so the fill result could not be proved and the `Get estimate` action was
not clicked. No personal details were entered and no lead was submitted.

Therefore the live probe validates real-page observation and the need/usefulness
of contextual intent resolution, but it does **not** yet establish a successful
end-to-end replay. A conclusive run needs a stable CDP session (the repository's
CDP binary or a machine with its required Go toolchain) so every action can be
followed by a value/selection/page-transition receipt.
