"""FailoverGuard pure decision core.

One pure core (R5, mirroring Agent Auth Gateway's ``_decide_action`` +
``simulate_policy`` precedent) is shared by the validator, the scenario
test harness, and the report generator. Nothing in this module performs
I/O, network calls, or live provider probing: it evaluates declarative
config/policy metadata deterministically (R6).

Decision vocabulary (R1) is the fleet triad:
``allow`` / ``deny`` / ``require_approval``.
Every decision is a ``(decision, reason)`` pair (R7).

Evaluation is fail-closed (R2): hard blocks first, eligibility last.
Nothing is eligible by default.
"""

from __future__ import annotations

import fnmatch
import json

ALLOW = "allow"
DENY = "deny"
REQUIRE_APPROVAL = "require_approval"

KNOWN_FAILURES = {"timeout", "http_5xx", "hang"}
OPERATION_CLASSES = {
    "read_only_inference",
    "stateful_inference",
    "tool_call_side_effect",
    "unknown_outcome",
}

_SEVERITY = {ALLOW: 0, REQUIRE_APPROVAL: 1, DENY: 2}


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def worst(decisions):
    """Return the most severe decision from an iterable (fail-closed)."""
    result = ALLOW
    for d in decisions:
        if _SEVERITY.get(d, 2) > _SEVERITY[result]:
            result = d
    return result


# --------------------------------------------------------------------------
# Config accessors (declarative metadata only — R6)
# --------------------------------------------------------------------------

def _index(items):
    return {item.get("name"): item for item in items or [] if isinstance(item, dict)}


def get_workflow(config, name):
    return _index(config.get("workflows")).get(name)


def get_provider(config, name):
    return _index(config.get("providers")).get(name)


def get_model(config, name):
    return _index(config.get("models")).get(name)


def route_target(config, workflow, role):
    """Return (provider_dict, model_dict) for a workflow role, or (None, None)."""
    route = (workflow or {}).get(role) or {}
    return get_provider(config, route.get("provider")), get_model(config, route.get("model"))


def reconciliation_step(config, policy, workflow_name):
    """A state-reconciliation step may be declared on the workflow or in policy."""
    wf = get_workflow(config, workflow_name) or {}
    if wf.get("reconciliation_step"):
        return wf["reconciliation_step"]
    steps = (policy or {}).get("reconciliation_steps") or {}
    return steps.get(workflow_name)


# --------------------------------------------------------------------------
# Policy named rules (R3 shape: name, patterns, params [{param, op, value}], mode)
# --------------------------------------------------------------------------

_OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _rule_matches(rule, workflow_name, context):
    patterns = rule.get("patterns") or []
    if patterns and not any(fnmatch.fnmatch(workflow_name or "", p) for p in patterns):
        return False
    for param in rule.get("params") or []:
        op = _OPS.get(param.get("op"))
        if op is None or param.get("param") not in context:
            return False
        try:
            if not op(context[param["param"]], param.get("value")):
                return False
        except TypeError:
            return False
    return True


# --------------------------------------------------------------------------
# The pure decision core
# --------------------------------------------------------------------------

def decide_operation(config, policy, operation):
    """Classify one operation and return its failover eligibility decision.

    ``operation`` keys: workflow, operation_class (defaults to the
    workflow's), failure_type (default ``unknown``), side_effect_state
    (``none`` | ``occurred`` | ``unknown``; defaults to ``none`` for
    read-only and ``unknown`` for every other class), elapsed_ms,
    estimated_cost_cents, secondary_capacity_available (bool or None =
    derive from the secondary provider's declared approved capacity),
    required_capabilities / required_data_locations (scenario overrides).
    """
    policy = policy or {}
    wf_name = operation.get("workflow")
    wf = get_workflow(config, wf_name)
    if wf is None:
        return _result(DENY, f"workflow '{wf_name}' is not defined in the route config",
                       operation)

    op_class = operation.get("operation_class") or wf.get("operation_class")
    if op_class not in OPERATION_CLASSES:
        return _result(REQUIRE_APPROVAL,
                       f"unknown operation class '{op_class}' — stop and escalate, never allow",
                       operation, wf_name)

    failure = operation.get("failure_type") or "unknown"
    elapsed = operation.get("elapsed_ms") or 0
    provider, model = route_target(config, wf, "secondary")

    # --- R2 step 1: hard blocks, before any eligibility consideration ---
    deadline = wf.get("deadline_ms")
    if deadline is not None and elapsed >= deadline:
        return _result(DENY,
                       f"overall workflow deadline expired ({elapsed}ms >= {deadline}ms) — do not retry",
                       operation, wf_name)

    if model is None:
        return _result(DENY, "workflow has no approved secondary model to fail over to",
                       operation, wf_name)
    required = operation.get("required_capabilities") or wf.get("required_capabilities") or []
    declared = model.get("capabilities")
    if not declared:
        return _result(DENY,
                       f"secondary model '{model.get('name')}' declares no capabilities — cannot validate a switch",
                       operation, wf_name)
    missing = [c for c in required if c not in declared]
    if missing:
        return _result(DENY,
                       f"secondary model '{model.get('name')}' lacks required capability: {', '.join(missing)} (block: capability missing)",
                       operation, wf_name)

    required_locations = (operation.get("required_data_locations")
                          or wf.get("data_location_required") or [])
    if isinstance(required_locations, str):
        required_locations = [required_locations]
    location = (provider or {}).get("data_location")
    if required_locations:
        if not location:
            return _result(DENY,
                           "secondary provider declares no data-location — cannot prove the requirement (block: data-location unverifiable)",
                           operation, wf_name)
        if location not in required_locations:
            return _result(DENY,
                           f"data-location requirement violated: secondary provider is in '{location}', workflow requires one of {required_locations} (block: data-location violated)",
                           operation, wf_name)

    # --- R3: named policy rules (deny / require_approval modes) ---
    context = {
        "elapsed_ms": elapsed,
        "estimated_cost_cents": operation.get("estimated_cost_cents") or 0,
        "deadline_ms": deadline or 0,
    }
    for rule in policy.get("rules") or []:
        if not rule.get("enabled", True):
            continue
        mode = rule.get("mode")
        if mode in (DENY, REQUIRE_APPROVAL) and _rule_matches(rule, wf_name, context):
            return _result(mode,
                           f"policy rule '{rule.get('name')}' matched (mode={mode})",
                           operation, wf_name)

    # --- R4: budget / capacity, pre-switch and fail-closed ---
    caps = policy.get("caps") or {}
    per_switch_cap = caps.get("per_switch_cap_cents")
    cost = operation.get("estimated_cost_cents") or 0
    if per_switch_cap is not None and cost > per_switch_cap:
        return _result(REQUIRE_APPROVAL,
                       f"estimated switch cost {cost} cents exceeds the per-switch cap of {per_switch_cap} cents — queue or stop, never silent overflow",
                       operation, wf_name)
    capacity = operation.get("secondary_capacity_available")
    if capacity is None:
        capacity = bool(provider and (provider.get("approved_capacity_requests_per_min") or 0) > 0)
    if not capacity:
        return _result(REQUIRE_APPROVAL,
                       "secondary route has no approved capacity — queue or stop, never silent overflow",
                       operation, wf_name)

    # --- Unknown failure: stop / escalate, never allow (R2) ---
    if failure not in KNOWN_FAILURES:
        return _result(REQUIRE_APPROVAL,
                       f"unknown provider failure '{failure}' — stop and escalate, never blind-retry",
                       operation, wf_name)

    # --- Side effects and unknown outcomes: reconcile before retry ---
    side_effect = operation.get("side_effect_state")
    if side_effect is None:
        side_effect = "none" if op_class == "read_only_inference" else "unknown"
    if op_class == "unknown_outcome" or side_effect == "unknown":
        return _result(REQUIRE_APPROVAL,
                       "the request's outcome is unknown — reconcile before retry (a blind retry could duplicate an external side effect)",
                       operation, wf_name)
    if side_effect == "occurred":
        return _result(REQUIRE_APPROVAL,
                       "request already caused an external side effect — reconcile before retry",
                       operation, wf_name)

    # --- Stateful work is not blind-retry eligible ---
    if op_class == "stateful_inference":
        step = reconciliation_step(config, policy, wf_name)
        if not step:
            return _result(REQUIRE_APPROVAL,
                           "stateful inference carries session state / partial output and the policy provides no state-reconciliation step — not blind-retry eligible",
                           operation, wf_name)
        return _result(ALLOW,
                       f"validated retry eligible via approved secondary; state-reconciliation step '{step}' is declared in the policy",
                       operation, wf_name)

    # --- Eligibility, granted only at the end ---
    if op_class == "tool_call_side_effect":
        return _result(ALLOW,
                       "validated retry eligible via approved secondary; side effect confirmed none, so no duplicate effect is possible",
                       operation, wf_name)
    return _result(ALLOW,
                   f"safe read-only inference {failure} — validated retry eligible via approved secondary",
                   operation, wf_name)


def _result(decision, reason, operation, workflow=None):
    return {
        "decision": decision,
        "reason": reason,
        "workflow": workflow or operation.get("workflow"),
        "operation_class": operation.get("operation_class"),
        "failure_type": operation.get("failure_type") or "unknown",
    }


# --------------------------------------------------------------------------
# Validation — structural + consistency findings as (decision, reason) rows (R7)
# --------------------------------------------------------------------------

def validate_config(config, policy):
    policy = policy or {}
    findings = []

    def add(subject, decision, reason):
        findings.append({"subject": subject, "decision": decision, "reason": reason})

    providers = config.get("providers") or []
    models = config.get("models") or []
    workflows = config.get("workflows") or []
    if not providers:
        add("config", DENY, "route config declares no providers")
    if not workflows:
        add("config", DENY, "route config declares no workflows")

    for p in providers:
        name = p.get("name", "?")
        if not p.get("data_location"):
            add(f"provider:{name}", DENY, "provider declares no data-location")
        else:
            add(f"provider:{name}", ALLOW, f"data-location declared ({p['data_location']})")
        if not p.get("approved_capacity_requests_per_min"):
            add(f"provider:{name}", REQUIRE_APPROVAL,
                "provider declares no approved capacity — switches to it would queue/stop")

    for m in models:
        name = m.get("name", "?")
        if get_provider(config, m.get("provider")) is None:
            add(f"model:{name}", DENY, f"model references unknown provider '{m.get('provider')}'")
        if not m.get("capabilities"):
            add(f"model:{name}", DENY, "model declares no capabilities")
        else:
            add(f"model:{name}", ALLOW, f"capabilities declared: {', '.join(m['capabilities'])}")

    for wf in workflows:
        name = wf.get("name", "?")
        for role in ("primary", "secondary"):
            route = wf.get(role)
            if not route:
                decision = DENY if role == "secondary" else REQUIRE_APPROVAL
                add(f"workflow:{name}", decision,
                    f"workflow has no approved {role} route" if role == "secondary"
                    else f"workflow has no {role} route declared")
                continue
            if get_provider(config, route.get("provider")) is None:
                add(f"workflow:{name}", DENY, f"{role} route references unknown provider '{route.get('provider')}'")
            if get_model(config, route.get("model")) is None:
                add(f"workflow:{name}", DENY, f"{role} route references unknown model '{route.get('model')}'")
        if wf.get("secondary"):
            add(f"workflow:{name}", ALLOW, "workflow has an approved secondary route")
            provider, model = route_target(config, wf, "secondary")
            if model is not None:
                required = wf.get("required_capabilities") or []
                declared = model.get("capabilities") or []
                missing = [c for c in required if c not in declared]
                if missing:
                    add(f"workflow:{name}", DENY,
                        f"secondary model '{model.get('name')}' lacks required capability: {', '.join(missing)}")
                elif required:
                    add(f"workflow:{name}", ALLOW,
                        f"secondary model declares all required capabilities: {', '.join(required)}")
            if provider is not None:
                required_locations = wf.get("data_location_required") or []
                if isinstance(required_locations, str):
                    required_locations = [required_locations]
                if required_locations and provider.get("data_location") not in required_locations:
                    add(f"workflow:{name}", DENY,
                        f"secondary provider data-location '{provider.get('data_location')}' violates workflow requirement {required_locations}")
        if not wf.get("deadline_ms"):
            add(f"workflow:{name}", DENY, "workflow sets no overall deadline — retries would be unbounded")
        else:
            add(f"workflow:{name}", ALLOW, f"deadline set ({wf['deadline_ms']}ms)")
        if wf.get("max_switch_cost_cents") is None:
            add(f"workflow:{name}", REQUIRE_APPROVAL, "workflow sets no switch cost cap (integer cents)")
        if wf.get("operation_class") not in OPERATION_CLASSES:
            add(f"workflow:{name}", REQUIRE_APPROVAL,
                f"workflow operation_class '{wf.get('operation_class')}' is not a known class")

    # Policy shape (R3) + integer-cents caps (R4)
    for key, value in (policy.get("caps") or {}).items():
        if not isinstance(value, int) or isinstance(value, bool):
            add(f"policy:caps.{key}", DENY, f"cap '{key}' must be integer cents, got {value!r}")
    for rule in policy.get("rules") or []:
        name = rule.get("name", "?")
        problems = []
        if not rule.get("name"):
            problems.append("rule has no name")
        if not isinstance(rule.get("patterns"), list):
            problems.append("patterns must be a list")
        for param in rule.get("params") or []:
            if not all(k in param for k in ("param", "op", "value")) or param.get("op") not in _OPS:
                problems.append(f"param {param!r} is not a {{param, op, value}} threshold")
        if rule.get("mode") not in (ALLOW, DENY, REQUIRE_APPROVAL):
            problems.append(f"mode '{rule.get('mode')}' is not in the decision triad")
        if problems:
            add(f"policy:rule:{name}", DENY, "; ".join(problems))
        else:
            add(f"policy:rule:{name}", ALLOW, "named rule is well-formed (name, patterns, params, mode)")

    return {"decision": worst(f["decision"] for f in findings), "findings": findings}


# --------------------------------------------------------------------------
# Scenario suite — simulated failures evaluated BY THE PURE CORE (R5, R8)
# --------------------------------------------------------------------------

def _breaker_evidence(policy, failure_count):
    """Trip-with-evidence idiom (R8 pattern only): score -> trip -> halt record.

    The score is the simulated consecutive-failure count; tripping is
    recorded as evidence. Provider-health semantics here are FailoverGuard's
    own — this is not AAG's runaway-loop breaker.
    """
    breaker = (policy or {}).get("circuit_breaker") or {}
    threshold = breaker.get("failure_threshold", 3)
    tripped = failure_count >= threshold
    return {
        "score": failure_count,
        "threshold": threshold,
        "tripped": tripped,
        "evidence": (f"{failure_count} consecutive simulated failures >= threshold {threshold}: "
                     "breaker tripped, primary halted with this evidence recorded"
                     if tripped else
                     f"{failure_count} simulated failure(s) below threshold {threshold}: breaker not tripped"),
    }


def run_scenarios(config, policy):
    policy = policy or {}
    by_class = {}
    for wf in config.get("workflows") or []:
        by_class.setdefault(wf.get("operation_class"), wf.get("name"))
    read_only = by_class.get("read_only_inference")
    tool_call = by_class.get("tool_call_side_effect")

    specs = []
    if read_only:
        specs += [
            ("read-only-timeout-retries", read_only,
             {"failure_type": "timeout"}, ALLOW, 1),
            ("read-only-5xx-retries", read_only,
             {"failure_type": "http_5xx"}, ALLOW, 1),
            ("repeated-timeouts-trip-breaker", read_only,
             {"failure_type": "timeout"}, ALLOW, 3),
            ("unknown-failure-escalates", read_only,
             {"failure_type": "unknown"}, REQUIRE_APPROVAL, 1),
            ("deadline-expired-denies", read_only,
             {"failure_type": "timeout", "elapsed_ms": 10 ** 9}, DENY, 1),
            ("capability-missing-denies", read_only,
             {"failure_type": "timeout", "required_capabilities": ["capability_not_declared_anywhere"]},
             DENY, 1),
            ("data-location-violated-denies", read_only,
             {"failure_type": "timeout", "required_data_locations": ["__nowhere__"]}, DENY, 1),
            ("no-capacity-queues", read_only,
             {"failure_type": "timeout", "secondary_capacity_available": False},
             REQUIRE_APPROVAL, 1),
        ]
    if tool_call:
        specs += [
            ("side-effect-timeout-reconciles-first", tool_call,
             {"failure_type": "timeout"}, REQUIRE_APPROVAL, 1),
            ("side-effect-occurred-reconciles-first", tool_call,
             {"failure_type": "timeout", "side_effect_state": "occurred"}, REQUIRE_APPROVAL, 1),
        ]
    # Cross-class contrast + outcome classes, evaluated on whichever workflows exist.
    any_wf = read_only or tool_call
    if any_wf:
        specs += [
            ("unknown-outcome-reconciles-first", any_wf,
             {"operation_class": "unknown_outcome", "failure_type": "timeout"},
             REQUIRE_APPROVAL, 1),
            ("stateful-without-reconciliation-step", any_wf,
             {"operation_class": "stateful_inference", "failure_type": "timeout",
              "side_effect_state": "none"}, REQUIRE_APPROVAL, 1),
        ]

    results = []
    for scenario_id, wf_name, overrides, expected, failure_count in specs:
        wf = get_workflow(config, wf_name) or {}
        operation = {"workflow": wf_name,
                     "operation_class": wf.get("operation_class"),
                     "failure_type": "timeout"}
        operation.update(overrides)
        outcome = decide_operation(config, policy, operation)
        results.append({
            "scenario": scenario_id,
            "workflow": wf_name,
            "operation_class": operation["operation_class"],
            "failure_type": operation["failure_type"],
            "decision": outcome["decision"],
            "reason": outcome["reason"],
            "expected": expected,
            "passed": outcome["decision"] == expected,
            "breaker": _breaker_evidence(policy, failure_count),
        })
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "failed": sum(1 for r in results if not r["passed"]),
        "results": results,
    }


# --------------------------------------------------------------------------
# Report — routes, per-class eligibility, gaps (R7 rows)
# --------------------------------------------------------------------------

_CANONICAL = {
    "read_only_inference": {"failure_type": "timeout", "side_effect_state": "none"},
    "stateful_inference": {"failure_type": "timeout", "side_effect_state": "none"},
    "tool_call_side_effect": {"failure_type": "timeout"},
    "unknown_outcome": {"failure_type": "timeout"},
}


def build_report(config, policy):
    validation = validate_config(config, policy)
    routes = []
    for wf in config.get("workflows") or []:
        entry = {"workflow": wf.get("name"), "operation_class": wf.get("operation_class")}
        for role in ("primary", "secondary"):
            route = wf.get(role) or {}
            entry[role] = {"provider": route.get("provider"), "model": route.get("model")}
        routes.append(entry)

    # Eligibility summary: canonical operation per class, on the first
    # workflow declaring that class, evaluated by the same pure core.
    eligibility = {}
    for op_class, template in _CANONICAL.items():
        wf_name = next((w["name"] for w in config.get("workflows") or []
                        if w.get("operation_class") == op_class), None)
        if wf_name is None:
            eligibility[op_class] = {"decision": REQUIRE_APPROVAL,
                                     "reason": "no workflow in this config declares this operation class",
                                     "workflow": None}
            continue
        operation = {"workflow": wf_name, "operation_class": op_class}
        operation.update(template)
        outcome = decide_operation(config, policy, operation)
        eligibility[op_class] = {"decision": outcome["decision"],
                                 "reason": outcome["reason"], "workflow": wf_name}

    gaps = [f for f in validation["findings"] if f["decision"] != ALLOW]
    return {
        "name": config.get("name"),
        "overall_decision": validation["decision"],
        "routes": routes,
        "eligibility_by_operation_class": eligibility,
        "gaps": gaps,
        "findings": validation["findings"],
    }


def format_report_text(report):
    lines = [f"FailoverGuard policy report — {report.get('name') or '(unnamed config)'}",
             f"Overall validation decision: {report['overall_decision']}", "", "Routes:"]
    for r in report["routes"]:
        lines.append(f"  {r['workflow']} [{r['operation_class']}]")
        lines.append(f"    primary:   {r['primary']['provider']} / {r['primary']['model']}")
        lines.append(f"    secondary: {r['secondary']['provider']} / {r['secondary']['model']}")
    lines += ["", "Eligibility by operation class (canonical timeout operation):"]
    for op_class, e in report["eligibility_by_operation_class"].items():
        lines.append(f"  {op_class}: {e['decision']} — {e['reason']}")
    lines += ["", "Gaps:"]
    if report["gaps"]:
        for g in report["gaps"]:
            lines.append(f"  [{g['decision']}] {g['subject']}: {g['reason']}")
    else:
        lines.append("  none")
    lines += ["", "All findings (subject: decision — reason):"]
    for f in report["findings"]:
        lines.append(f"  [{f['decision']}] {f['subject']}: {f['reason']}")
    return "\n".join(lines)
