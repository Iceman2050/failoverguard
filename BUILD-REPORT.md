# FailoverGuard — Build Report (v0.1.0, 2026-11-11)

Repo: https://github.com/Iceman2050/failoverguard
Built under the GODMODE v2.0 Five Business Ideas program, Idea 4.
Overlap gate: `OVERLAP-GATE.md` in this folder — verdict PROCEED with reuse
(standalone repo, not a fold-in to Agent Auth Gateway).

## Reuse map implementation (from OVERLAP-GATE.md)

- **R1 Decision vocabulary** — `failoverguard/core.py` exposes the AAG triad
  as constants `ALLOW` / `DENY` / `REQUIRE_APPROVAL` (`allow` / `deny` /
  `require_approval`). Every decision-table row maps onto it: validated
  retry → `allow`; capability missing / data-location violated / deadline
  expired → `deny` (with the richer label carried in the reason, e.g.
  "(block: capability missing)"); reconcile-first / queue / escalate →
  `require_approval`.
- **R2 Fail-closed ordering** — `decide_operation` evaluates hard blocks
  first (deadline → capability → data-location), then named policy rules,
  then budget/capacity, then unknown-failure and side-effect gates;
  `allow` is returned only at the very end. Unknown failure type and
  unknown operation class escalate, never allow. Unit test
  `test_hard_blocks_beat_everything` proves deny wins over every softer
  outcome combined.
- **R3 Policy-file shape** — the policy JSON mirrors AAG's named-rule
  objects: `{name, patterns (fnmatch), params: [{param, op, value}], mode,
  enabled}`, plus route allow/deny lists and caps in integer cents.
  `validate_config` rejects malformed rules and non-integer-cent caps.
  The fixture rule `large-refund-needs-approval` (refund-* with
  estimated_cost_cents >= 100 → require_approval) fires in tests.
- **R4 Budget-check semantics** — pre-switch and fail-closed: per-switch
  cap breach and "secondary has no approved capacity" both degrade to
  `require_approval` (queue or stop, never silent overflow) *before* any
  eligibility is granted — the burn-forecast/auto-throttle precedent
  applied to capacity.
- **R5 Pure core + offline harness** — one pure function,
  `decide_operation` in `core.py`, is consumed unchanged by `validate`,
  `check`, the scenario harness (`run_scenarios`), and the report
  generator (`build_report`). No I/O, no network, no live providers
  anywhere in the tool.
- **R6 (gate §2)** — capability/data-location checks read only declarative
  metadata from the config files; nothing is probed.
- **R7** — every finding and decision is a (subject/decision, reason) row;
  the text report prints them in that style.
- **R8 (pattern only)** — scenarios carry breaker evidence via
  `_breaker_evidence`: score (simulated consecutive failures) → trip at
  the policy threshold → halt with the evidence string recorded in the
  result. Provider-health semantics are FailoverGuard's own; AAG's
  runaway-loop breaker meaning was not imported.

## Suite

```
38 passed in 0.17s   (python3 -m pytest -q, Python 3.12.3 local)
```
Decision-table unit tests (every row), validation tests (good + broken
configs), scenario-suite tests, report tests, CLI smoke tests.

## Real fixture transcript (2026-11-11, local run)

Good config validates clean; the broken config is caught:

```
$ failoverguard validate fixtures/helpdesk_routes.json fixtures/helpdesk_policy.json
[allow] provider:Provider A: data-location declared (us)
...
[allow] workflow:refund-processing: secondary model declares all required capabilities: tool_calling
Overall: allow   (exit=0)

$ failoverguard validate fixtures/broken_routes.json fixtures/helpdesk_policy.json
[deny] workflow:refund-processing: secondary model 'refund-agent-b1-lite' lacks required capability: tool_calling
Overall: deny   (exit=1)
```

Same simulated timeout, different decision per operation class:

```
$ failoverguard check ... --operation fixtures/operations/summarization_timeout.json
allow — safe read-only inference timeout — validated retry eligible via approved secondary

$ failoverguard check ... --operation fixtures/operations/refund_timeout.json
require_approval — the request's outcome is unknown — reconcile before retry (a blind retry could duplicate an external side effect)
```

Scenario suite — good config 12/12 PASS (exit 0); broken config 10/12,
exit 1, the two refund scenarios flipping to `deny` on the capability hard
block:

```
[FAIL] side-effect-timeout-reconciles-first (refund-processing, timeout): deny (expected require_approval) — secondary model 'refund-agent-b1-lite' lacks required capability: tool_calling (block: capability missing)
Scenarios: 10/12 passed
```

Breaker trip-with-evidence (good config):

```
[PASS] repeated-timeouts-trip-breaker (ticket-summarization, timeout): allow (expected allow) | breaker: 3 consecutive simulated failures >= threshold 3: breaker tripped, primary halted with this evidence recorded
```

## Honesty notes

All failures are simulated; no live provider was called. Provider names in
fixtures are generic ("Provider A" / "Provider B") and the example company
is fictional. No provider outage statistics are claimed anywhere.
FailoverGuard validates policies — it does not monitor providers, route
traffic, or guarantee uptime.
