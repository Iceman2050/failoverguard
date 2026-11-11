"""Every row of the FailoverGuard decision table, against the pure core."""

from failoverguard.core import ALLOW, DENY, REQUIRE_APPROVAL, decide_operation


def op(workflow="ticket-summarization", **kw):
    base = {"workflow": workflow, "failure_type": "timeout", "elapsed_ms": 100}
    base.update(kw)
    return base


def test_safe_read_only_timeout_allows(config, policy):
    r = decide_operation(config, policy, op(side_effect_state="none"))
    assert r["decision"] == ALLOW
    assert "validated retry" in r["reason"]


def test_safe_read_only_5xx_and_hang_allow(config, policy):
    for failure in ("http_5xx", "hang"):
        r = decide_operation(config, policy, op(failure_type=failure, side_effect_state="none"))
        assert r["decision"] == ALLOW, failure


def test_model_lacking_capability_denies(config_copy, policy):
    config_copy["models"][3]["capabilities"] = ["text_generation"]  # refund secondary
    r = decide_operation(config_copy, policy, op("refund-processing"))
    assert r["decision"] == DENY
    assert "tool_calling" in r["reason"]


def test_data_location_violation_denies(config, policy):
    r = decide_operation(config, policy,
                         op(required_data_locations=["eu"], side_effect_state="none"))
    assert r["decision"] == DENY
    assert "data-location" in r["reason"]


def test_side_effect_occurred_requires_approval(config, policy):
    r = decide_operation(config, policy,
                         op("refund-processing", side_effect_state="occurred"))
    assert r["decision"] == REQUIRE_APPROVAL
    assert "reconcile" in r["reason"]


def test_unknown_failure_requires_approval(config, policy):
    r = decide_operation(config, policy, op(failure_type="mystery_failure",
                                            side_effect_state="none"))
    assert r["decision"] == REQUIRE_APPROVAL


def test_no_approved_capacity_requires_approval(config, policy):
    r = decide_operation(config, policy,
                         op(secondary_capacity_available=False, side_effect_state="none"))
    assert r["decision"] == REQUIRE_APPROVAL
    assert "no approved capacity" in r["reason"]


def test_deadline_expired_denies(config, policy):
    r = decide_operation(config, policy, op(elapsed_ms=8000, side_effect_state="none"))
    assert r["decision"] == DENY
    assert "deadline expired" in r["reason"]


def test_hard_blocks_beat_everything(config, policy):
    # Capability missing AND deadline expired AND unknown failure: still deny.
    r = decide_operation(config, policy,
                         op(elapsed_ms=99999, failure_type="unknown",
                            required_capabilities=["nope"], side_effect_state="occurred"))
    assert r["decision"] == DENY


def test_same_timeout_different_decision_per_class(config, policy):
    read_only = decide_operation(config, policy, op(side_effect_state="none"))
    refund = decide_operation(config, policy, op("refund-processing"))
    assert read_only["decision"] == ALLOW
    assert refund["decision"] == REQUIRE_APPROVAL


def test_unknown_outcome_class_reconciles_first(config, policy):
    r = decide_operation(config, policy,
                         op(operation_class="unknown_outcome", side_effect_state="none"))
    assert r["decision"] == REQUIRE_APPROVAL


def test_stateful_without_reconciliation_step_requires_approval(config, policy):
    r = decide_operation(config, policy,
                         op(operation_class="stateful_inference", side_effect_state="none"))
    assert r["decision"] == REQUIRE_APPROVAL
    assert "state-reconciliation" in r["reason"]


def test_stateful_with_reconciliation_step_allows(config, policy):
    policy = dict(policy, reconciliation_steps={"ticket-summarization": "replay-session-log"})
    r = decide_operation(config, policy,
                         op(operation_class="stateful_inference", side_effect_state="none"))
    assert r["decision"] == ALLOW
    assert "replay-session-log" in r["reason"]


def test_tool_call_confirmed_no_side_effect_allows(config, policy):
    r = decide_operation(config, policy,
                         op("refund-processing", side_effect_state="none",
                            estimated_cost_cents=10))
    assert r["decision"] == ALLOW


def test_named_policy_rule_fires(config, policy):
    # large-refund-needs-approval: refund-* with estimated_cost_cents >= 100.
    r = decide_operation(config, policy,
                         op("refund-processing", side_effect_state="none",
                            estimated_cost_cents=150))
    assert r["decision"] == REQUIRE_APPROVAL
    assert "large-refund-needs-approval" in r["reason"]


def test_per_switch_cap_blocks_overflow(config, policy):
    r = decide_operation(config, policy, op(side_effect_state="none",
                                            estimated_cost_cents=9999))
    assert r["decision"] == REQUIRE_APPROVAL


def test_unknown_workflow_denies(config, policy):
    r = decide_operation(config, policy, op("no-such-workflow"))
    assert r["decision"] == DENY


def test_unknown_operation_class_escalates(config, policy):
    r = decide_operation(config, policy, op(operation_class="teleportation"))
    assert r["decision"] == REQUIRE_APPROVAL
