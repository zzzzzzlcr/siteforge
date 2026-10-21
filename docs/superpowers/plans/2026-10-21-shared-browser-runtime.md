# Shared Browser Runtime migration plan

## Goal

Stop teaching every generated site artifact how browsers work.  Site code should
describe business intent; one versioned runtime should own observation,
resolution, browser actions, and evidence-based verification.

## Non-goals for the first slice

- Do not delete or rewrite `template.py` / `template_loop.py`.
- Do not add a decision model.  Normal replay remains zero-token.
- Do not auto-migrate production artifacts before real-browser shadow results.
- Do not turn a site-specific string into a global runtime rule.

## Target boundary

```text
thin Flow (fill / choose / advance / verify)
        |
shared BrowserRuntime
  observe -> resolve exactly one -> act -> observe -> verify receipt
        |
existing CDP PageModel and action commands
```

The runtime must abstain before mutation when the top candidates are tied or too
close.  Document order is never a semantic tiebreaker.

## Implemented in phase 1

- `agent/browser_runtime.py`
  - selector-free `Intent` contract;
  - optional current-question context for repeated fields such as HomeBuddy's
    two ZIP sections;
  - normalization of current `cdp observe` fields/actions;
  - deterministic resolver using accessible identity, question context, region,
    role, visibility, obstruction, and stability;
  - `ambiguous` / `not_found` abstention;
  - selector attempts only *after* selecting one semantic candidate;
  - before/after receipts and value/selection/transition verification;
  - thin ordered `Flow` runner;
  - explicit `model_calls = 0`.
- `tests/test_browser_runtime.py`
  - duplicate ZIP regions;
  - HomeBuddy carousel `next slide` versus questionnaire `Next`;
  - irreducible ambiguity causes zero browser actions;
  - field value round-trip is required;
  - a flow stops on the first unverified result.

## Phase 2: adapter and shadow run

1. Add an adapter over the existing production `CDPHelper` / MCP session.  The
   adapter only translates `observe`, `click`, and `form`; policy stays here.
2. Encode one HomeBuddy path as a thin Flow.  Stop before personal data and
   final submission for non-production tests.
3. Run old artifact and new flow against the same browser/session corpus:
   - 20 clean runs;
   - dynamic ID, wrapper, class, order, duplicate-name perturbations;
   - record success, wrong action, abstention, latency, observe calls, tokens.
4. Promotion gate:
   - zero wrong actions;
   - all successes have an action receipt and post-action evidence;
   - deterministic path uses zero model calls;
   - ambiguity abstains rather than falling back to DOM order.

## Phase 3: make artifacts thin

1. Add an opt-in emitter producing only Flow data plus business-specific hooks.
2. Version the shared runtime independently from site flows.
3. Move generic popup, wait, frame, shadow, and transition handling out of both
   templates and into the runtime one capability at a time.
4. After parity, stop embedding the copied runtime in newly generated artifacts.
5. Keep old standalone artifacts runnable during a measured migration window.

## Rule for future failures

Every failure must be classified before code is added:

1. **browser primitive missing** -> fix the shared runtime/CDP and add a generic
   contract test;
2. **business-flow difference** -> express it in the thin site Flow;
3. **irreducible ambiguity** -> abstain or invoke an explicitly configured local
   decision model on the bounded candidate list;
4. **site-only workaround** -> isolated site hook with expiry/owner, never a
   global text whitelist.

If a proposed fix needs parallel edits in exploration, two templates, lint, and
self-test, treat that as evidence the capability still lives at the wrong layer.
