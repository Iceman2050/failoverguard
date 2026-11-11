# FailoverGuard — OVERLAP GATE vs Agent Auth Gateway (AAG)

**Gate:** Idea 4, GODMODE v2.0 Five Business Ideas program — overlap inspection only.
**Date:** 2026-11-11 · **Inspection basis:** source code read on disk, not marketing pages.
**AAG source:** `~/workspace/platforms/agent-auth-gateway/` (live at authgateway.getingeniousai.com)

## VERDICT: PROCEED with reuse — a standalone repo is justified

FailoverGuard is **not** an expansion of, and must **not** be folded into, Agent Auth
Gateway. AAG demonstrably contains **no provider-failover eligibility validation in
substance**: no provider routes, no models, no endpoints, no health checks, no retry or
switching logic of any kind (evidence §3). What the two products genuinely share is a
*policy-engineering layer* — decision vocabulary, fail-closed evaluation order, policy
shape, and limit-check semantics — which FailoverGuard should deliberately reuse as
patterns so the fleet speaks one policy language. Everything that makes FailoverGuard
FailoverGuard (route configs, capability compatibility, data-location, side-effect /
reconciliation state, capacity, deadlines, offline eligibility tests) is new build.

---

## 1. What AAG actually is (from source)

AAG is a **runtime authorization gateway for agent actions**. An agent's calls are
presented to `POST /v1/proxy` as `{agent_id, action, estimated_cost, detail?, context?,
correlation_id?}` and the gateway returns a decision before the call proceeds
(`app/main.py`, `v1_proxy`, ~L8781; README "How it works").

- **Policy representation:** per-agent DB record — `daily_cap_cents`,
  `monthly_cap_cents`, `allowlist_json`, `denylist_json`, `risk_tier`
  (`app/db.py` agents table ~L248–253; `try_create_agent` L3243). A second, rule-based
  layer exists in the `policy_rules` table (`app/db.py` L487): `name`, `rule_type`,
  `patterns_json` (fnmatch patterns over action names), `params_json` (threshold
  conditions, e.g. `[{"param": "record_count", "op": ">=", "value": 100}]`), `mode`
  (`deny` | `require_approval`), `trip_kill_switch`, `enabled`.
- **Decision core:** `_decide_action` (`app/main.py` L748) — a *pure* function shared by
  the live path and the what-if simulator. Fail-closed order: lockdown → kill switch →
  denylist → spend caps → auto-throttle → allowlist → high-risk verbs → allow.
- **Decision outcomes (the fleet vocabulary):** `allow` / `deny` / `require_approval`.
  The commerce module names the same triad as constants: `VERDICT_ALLOW = "allow"`,
  `VERDICT_DENY = "deny"`, `VERDICT_HOLD = "require_approval"` (`app/commerce.py`
  L87–90). Every decision carries a human-readable reason string.
- **Limits that exist:** daily + monthly spend caps in integer cents, evaluated
  *before* the call and denying the call that would breach (`_decide_action` step 3);
  per-transaction limit (`per_txn_limit_cents`), merchant-category allowlists and
  velocity caps in the commerce transaction policy (`app/commerce.py` `decide()`
  L263ff); a hard proxy rate limit of 300 req/min per API key (`v1_proxy`; bounded
  in-memory `_BoundedRateLimiter`, L289); burn forecast (EMA burn →
  days-until-monthly-cap) with auto-throttle that degrades policy before the wall
  (README "Killer features"; `_decide_action` step 3b).
- **Offline replay precedent:** `simulate_policy` (`app/main.py` L2197) replays
  historical evaluations against an edited policy to show what flips — the same
  pure-core-plus-offline-harness architecture FailoverGuard proposes.
- **A "circuit breaker" that is NOT a provider circuit breaker:** `app/loop_breaker.py`
  is a runaway-*loop* breaker — it scores per-agent action sequences (repetition × cost
  velocity × zero-progress) and auto-halts a looping agent (approval entry + kill
  switch). It watches agent behaviour, never provider health.

## 2. REUSE MAP — shared components: adopt the pattern, not the product

| # | Component | Exists in AAG (evidence) | How FailoverGuard reuses it |
|---|-----------|--------------------------|------------------------------|
| R1 | **Decision vocabulary** | `allow` / `deny` / `require_approval` in `_decide_action` (`app/main.py` L748ff), `/v1/proxy` contract, `VERDICT_*` constants (`app/commerce.py` L87–90) | Adopt the same three outcomes as FailoverGuard's core: decision-table "block" → `deny`; "consider validated retry" → `allow` (conditional, reason-carrying); "stop/escalate", "queue/stop", "reconcile before retry" → the `require_approval` analogue (a human/system gate before any retry). One vocabulary across the fleet. |
| R2 | **Fail-closed evaluation order** | `_decide_action` steps 0–5: hard stops (lockdown, kill switch, denylist) before caps, caps before allowlist, allow last | Mirror the ordering principle in the eligibility engine: hard blocks first (capability missing, data-location violated, prior side effect unreconciled), then budget/deadline/capacity checks, eligibility granted only at the end. Nothing is eligible by default. |
| R3 | **Policy-file shape** | Agent record = caps + allow/deny lists + risk tier (`app/db.py` ~L248–253); rule objects = name + type + fnmatch `patterns_json` + threshold `params_json` + `mode` + `enabled` (`policy_rules`, L487) | FailoverGuard's policy file mirrors this shape: per-route caps and allow/deny route lists, plus named rule objects with patterns, parameter thresholds (`{"param": "deadline_ms", "op": "<", "value": …}`) and a mode. Developers who know one fleet policy format know the other. (Shape only — AAG's is a multi-tenant DB schema; FailoverGuard's is a local file. No code or schema is lifted.) |
| R4 | **Budget/limit check semantics** | Pre-call cap evaluation in integer cents, deny-the-breaching-call (`_decide_action` step 3); per-transaction limit + velocity caps (`app/commerce.py` `decide()`); burn forecast → auto-throttle degradation (step 3b) | Reuse the semantics for FailoverGuard's budget check: evaluate *before* the switch, fail closed on breach, integer money units. The burn-forecast/auto-throttle pattern is the precedent for the "secondary route has no approved capacity → queue/stop" row: a projected-exhaustion signal degrades eligibility before the wall, it does not discover it at the wall. |
| R5 | **Pure core + offline harness architecture** | `_decide_action` docstring: "Pure policy decision core — shared by the live path and the what-if simulator"; `simulate_policy` replay (L2197); public `/playground` runs the real engine on pasted logs (README) | Build FailoverGuard the same way: one pure eligibility core consumed by the route-config validator, the local test suite, and the policy report generator — never three reimplementations. |
| R6 | **Deterministic metadata checks, never content judgment** | Consent-gate rules check *presence* of attestation metadata in call context, explicitly never judge content (`app/main.py` L842ff, `ARTICLE5_CONSENT_GATE`) | FailoverGuard's capability and data-location checks follow the same doctrine: declarative metadata on the route/model (capabilities list, region) checked deterministically — no probing, no inference about provider internals. |
| R7 | **Reason-carrying decisions + report format** | Every AAG decision returns `(decision, reason)`; evaluations are logged with both (`evaluate_policy`, L1206) | FailoverGuard's human-readable policy report is a list of `(rule, outcome, reason)` rows in the same style — a reader of an AAG evaluation log can read a FailoverGuard report without translation. |
| R8 | **Breaker trip pattern (partial)** | `app/loop_breaker.py`: score → threshold → trip → halt + evidence-carrying queue entry + learning allowlist for legitimate retries | The *shape* (score, trip, halt with evidence, allowlist for known-legitimate retry patterns) informs FailoverGuard's circuit-breaker test scenarios. The semantics differ entirely — AAG's breaker detects looping agents; FailoverGuard's scenarios exercise provider-failure breakers. Pattern reuse, new implementation. |

## 3. MATERIALLY DIFFERENT — the new build (why this is not a fold-in)

- **Different subject.** AAG authorizes *agent actions* (tool/API calls, spend) at
  runtime. FailoverGuard validates *provider-continuity policies* (primary/secondary
  routes, model capability, data location, retry safety) offline, before switching is
  permitted. AAG's proxy request has **no provider, model, route, endpoint, region,
  capability, timeout, or retry field** (`v1_proxy` body, ~L8781) and its policy schema
  has none either (`app/db.py` agents table).
- **No failover logic exists in AAG at all.** A case-insensitive search for "failover"
  across `app/` returns zero hits in the engine (the only "circuit" in the codebase is
  the runaway-loop breaker, §1). AAG never health-checks a provider, never switches a
  model, never retries a call. Its own guides position provider consoles as an external
  outer fence and the gateway as per-agent refusal (`app/guides.py`
  "provider-caps-vs-per-agent-refusal") — complementary layers, not the same layer.
- **New concepts with no AAG analogue:** route-config validation (primary/secondary
  provider routes); operation classification for retry safety (read-only vs
  side-effecting — AAG's `HIGH_RISK_VERBS` verb heuristic in `_decide_action` step 5 is
  only a distant cousin: it classifies *agent actions* for approval routing, not
  *provider operations* for retry eligibility); capability-compatibility checks between
  models; data-location constraints; side-effect/reconciliation state ("request already
  caused an external side effect → reconcile before retry"); workflow-deadline expiry
  (AAG has spend horizons, no per-workflow deadline concept); provider-failure
  circuit-breaker test scenarios.
- **Different deployment shape.** AAG is a hosted multi-tenant SaaS (workspaces, plans,
  billing, approval queues, email links). FailoverGuard is a local, open-source
  validator + test harness that never sits in traffic and never takes control of
  routing — by its own proposal it *permits or refuses* a failover policy, exactly the
  inverse posture of a runtime gateway.

**Fold-in test, answered:** AAG does not demonstrably do provider-failover eligibility
validation in any part — no feature, module, table, or test in the inspected source
validates a failover policy, a route config, a capability match, or a retry's safety
against a provider switch. The EXPANSION/FOLD-IN verdict is therefore not available.

## 4. Fleet neighbours checked (boundary clarity)

- **AgentBridge** (`~/workspace/github-repos-build/agent-bridge/`) — an *adapter layer*
  that normalizes AAG policy decisions and Agent Flight Recorder events into one
  evidence schema (`agent-bridge/evidence/v1`); its README states it "does not itself
  enforce policy, block actions, or sit in the execution path." Boundary: FailoverGuard
  is a validator, not an adapter — it must not be folded into AgentBridge. If
  FailoverGuard ever emits decision records for fleet consumption, the
  `agent-bridge/evidence/v1` shape is the compatibility target, and its
  decision/reason field conventions already match reuse item R7.
- **Agent Recovery Lab** (`~/workspace/github-repos-build/agent-recovery-lab/`) — a
  failure-injection and recovery-testing toolkit for agent workflows: it injects
  timeouts, duplicate webhooks and mid-run deaths and checks whether retry/resume/
  compensate is safe (idempotency, reconciliation). Adjacent to FailoverGuard's
  "reconcile before retry" row, but it *tests runtime recovery behaviour of workflows*;
  it has no provider routes, no model capabilities, no continuity policy. Boundary:
  Recovery Lab injects failures into workflows; FailoverGuard validates failover
  policy as code. Complementary, no overlapping deliverable.
- **ModelMinder** (`~/workspace/platforms/model-minder/`) — the fleet's nearest
  "model switching" neighbour: a deprecation radar that alerts before a model is
  retired and proves replacements with migration evals *before a planned switch*
  (its README). Trigger is vendor deprecation (planned migration), not runtime
  provider failure; no route config, no eligibility rules, no failover. No overlap.
- **AgentGate** (`~/workspace/platforms/agentgate/`) — a policy generator + conformance
  validator, but for a different policy domain: site owners publishing which agents may
  access a site (`/.well-known/agent-policy.json`, AGENT-POLICY v0.1). Its
  validator → conformance-report pattern is a second fleet precedent for FailoverGuard's
  human-readable report (R7); no shared components.
- **Fleet-wide sweep:** a search across `~/workspace/platforms/` and
  `~/workspace/github-repos-build/` for failover / circuit-breaker / provider-routing /
  model-switching terms returned no routing, failover, provider health-check, or
  model-switching implementation anywhere else in the fleet — the only hits were AAG's
  loop breaker (§1) and unrelated content. Nothing in the fleet already does what
  FailoverGuard proposes.

## 5. Gate conditions carried into any future build

1. Adopt R1–R7 deliberately (vocabulary, ordering, policy shape, check semantics,
   pure-core architecture, metadata doctrine, report format) — the reuse is at the
   *pattern* level; AAG code, schema, and tenancy are not imported into an open-source
   local tool.
2. Product boundary statements stay explicit in FailoverGuard's README, mirroring the
   honesty pattern of its neighbours: it validates policy and tests eligibility; it is
   not a router, never takes control of traffic, and does not replace AAG (runtime
   agent-action authorization), Agent Recovery Lab (workflow recovery testing), or
   ModelMinder (deprecation-driven migration).
3. This gate inspected source on 2026-11-11. If AAG later ships genuine provider-route
   or failover features, this gate must be re-run before FailoverGuard's scope is
   extended toward them.

*Evidence files read for this gate: `agent-auth-gateway/README.md`,
`agent-auth-gateway/app/main.py` (policy engine, proxy, simulator),
`agent-auth-gateway/app/db.py` (agents + policy_rules schemas),
`agent-auth-gateway/app/commerce.py`, `agent-auth-gateway/app/loop_breaker.py`,
`agent-auth-gateway/app/connector.py`, `agent-auth-gateway/app/guides.py` (provider-cap
positioning), `agent-bridge/README.md`, `agent-recovery-lab/README.md`,
`model-minder/README.md`, `agentgate/README.md`, plus fleet-wide term searches over
`~/workspace/platforms/` and `~/workspace/github-repos-build/`.*
