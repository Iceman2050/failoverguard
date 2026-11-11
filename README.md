# FailoverGuard

An **offline policy validator and test harness for AI provider continuity**.

You describe your AI routes (providers, models, primary/secondary per
workflow, model capabilities, provider data-locations, approved capacity)
and your failover policy as files. FailoverGuard checks that the policy is
complete and consistent, classifies individual operations against a
fail-closed decision table, and runs a built-in scenario suite of
*simulated* provider failures against your config — so you find out, before
an outage does it for you, whether a switch would be safe, blocked, or in
need of human reconciliation first.

Python 3.10+, standard library only, zero dependencies.

## What this is — and is not (boundaries)

- FailoverGuard is a **validator and test harness**. It is **not a router**:
  it never controls, proxies, or sits in live traffic, and it never calls a
  real provider. Every failure it evaluates is simulated from the operation
  or scenario description you give it.
- It validates and tests **policies**. It does not monitor live providers,
  does not measure provider health, and **does not guarantee uptime** of
  anything. A passing report means your *policy files* produce the safe
  decision for the simulated cases — nothing more.
- It does **not replace** Agent Auth Gateway (runtime authorization of
  agent actions), Agent Recovery Lab (failure-injection testing of workflow
  recovery behaviour), or ModelMinder (deprecation-driven model migration).
  Those are complementary layers; FailoverGuard covers provider-continuity
  policy, offline, before a switch is permitted.
- Capability and data-location checks are deterministic checks of the
  declarative metadata in your config files. Nothing is probed or inferred
  about provider internals.

## Install

```bash
pip install -e .
```

## Quick start (fixtures included)

```bash
# 1. Validate a route config + policy
failoverguard validate fixtures/helpdesk_routes.json fixtures/helpdesk_policy.json

# 2. Classify one operation (JSON file, JSON string, or class + flags)
failoverguard check fixtures/helpdesk_routes.json fixtures/helpdesk_policy.json \
  --operation fixtures/operations/summarization_timeout.json

# 3. Run the built-in circuit-breaker / failover scenario suite
failoverguard test fixtures/helpdesk_routes.json fixtures/helpdesk_policy.json --scenarios

# 4. Human-readable policy report (or --format json)
failoverguard report fixtures/helpdesk_routes.json fixtures/helpdesk_policy.json
```

The fixtures describe a fictional **HelpDesk AI** on generic "Provider A"
and "Provider B", with two workflows: read-only ticket summarization and
side-effecting refund processing. `fixtures/broken_routes.json` is a
deliberately broken variant whose refund secondary lacks the required
`tool_calling` capability — `validate` flags it `deny`, and the scenario
suite catches it.

## The decision table

Every eligibility outcome uses the fleet decision triad —
`allow` / `deny` / `require_approval` — always with a human-readable
reason. Hard blocks evaluate **before** any eligibility consideration
(fail-closed); eligibility is granted only at the very end.

| Situation | Decision |
|---|---|
| Safe read-only inference timeout / 5xx / hang | `allow` — validated retry via approved secondary |
| Secondary model lacks a required capability | `deny` (block: capability missing) |
| Data-location requirement violated | `deny` (block: data-location violated) |
| Overall workflow deadline expired | `deny` — do not retry |
| Request already caused an external side effect | `require_approval` — reconcile before retry |
| Unknown provider failure | `require_approval` — stop / escalate, never allow |
| Secondary route has no approved capacity | `require_approval` — queue or stop, never silent overflow |
| Outcome unknown (`unknown_outcome`) | `require_approval` — reconcile first |
| Stateful inference, no reconciliation step in policy | `require_approval` — not blind-retry eligible |

Operation classes: `read_only_inference`, `stateful_inference`,
`tool_call_side_effect`, `unknown_outcome`.

The same simulated timeout therefore yields different decisions per class —
by design: `allow` for ticket summarization, `require_approval` for refund
processing, because a blind retry of a refund could duplicate a payment.

## Files you write

**Route config** (JSON): `providers` (`name`, `data_location`,
`approved_capacity_requests_per_min`), `models` (`name`, `provider`,
`capabilities`), `workflows` (`name`, `operation_class`, `primary`,
`secondary`, `required_capabilities`, `data_location_required`,
`deadline_ms`, `max_switch_cost_cents`, optional `reconciliation_step`).

**Policy** (JSON): named rules in the fleet policy shape — each rule has a
`name`, `patterns` (fnmatch over workflow names), threshold `params`
(`[{param, op, value}]`), a `mode` from the decision triad, and `enabled` —
plus `caps` in **integer cents** (`per_switch_cap_cents`,
`daily_switch_budget_cents`), `reconciliation_steps` per workflow, and a
`circuit_breaker` block (`failure_threshold`, `window_seconds`) used as
trip-with-evidence in scenarios: score → trip → halt, with the evidence
recorded in the scenario result.

One pure decision core powers `validate`, `check`, `test`, and `report` —
the answer cannot drift between commands.

## Exit codes

- `validate`: `0` unless the overall decision is `deny` (then `1`).
- `test`: `0` when every scenario produces the safe decision, else `1`.
- `check` / `report`: `0`; the decision is in the output.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

## License

MIT. Built by Ingenious AI — https://getingeniousai.com
