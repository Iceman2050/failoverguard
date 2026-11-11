from failoverguard.core import ALLOW, DENY, REQUIRE_APPROVAL, validate_config


def decisions_for(result, subject_part):
    return [f for f in result["findings"] if subject_part in f["subject"]]


def test_good_config_validates_allow(config, policy):
    result = validate_config(config, policy)
    assert result["decision"] == ALLOW
    assert all(f["decision"] == ALLOW for f in result["findings"])


def test_broken_config_capability_gap_is_denied(broken_config, policy):
    result = validate_config(broken_config, policy)
    assert result["decision"] == DENY
    hits = [f for f in result["findings"]
            if f["decision"] == DENY and "tool_calling" in f["reason"]]
    assert hits, result["findings"]


def test_missing_secondary_is_denied(config_copy, policy):
    del config_copy["workflows"][0]["secondary"]
    result = validate_config(config_copy, policy)
    assert result["decision"] == DENY
    assert decisions_for(result, "ticket-summarization")


def test_missing_data_location_is_denied(config_copy, policy):
    del config_copy["providers"][0]["data_location"]
    result = validate_config(config_copy, policy)
    assert result["decision"] == DENY


def test_missing_deadline_is_denied(config_copy, policy):
    del config_copy["workflows"][0]["deadline_ms"]
    result = validate_config(config_copy, policy)
    assert result["decision"] == DENY


def test_missing_switch_cap_needs_approval(config_copy, policy):
    del config_copy["workflows"][0]["max_switch_cost_cents"]
    result = validate_config(config_copy, policy)
    assert result["decision"] in (REQUIRE_APPROVAL, DENY)
    hits = [f for f in result["findings"] if "switch cost cap" in f["reason"]]
    assert hits and hits[0]["decision"] == REQUIRE_APPROVAL


def test_non_integer_cent_cap_is_denied(config, policy):
    policy = dict(policy, caps={"per_switch_cap_cents": 19.99})
    result = validate_config(config, policy)
    assert result["decision"] == DENY


def test_malformed_rule_is_denied(config, policy):
    policy = dict(policy, rules=[{"name": "bad", "patterns": "*",
                                  "params": [{"param": "x"}], "mode": "shrug"}])
    result = validate_config(config, policy)
    assert result["decision"] == DENY


def test_secondary_with_no_declared_capabilities_is_denied(config_copy, policy):
    config_copy["models"][1]["capabilities"] = []
    result = validate_config(config_copy, policy)
    assert result["decision"] == DENY
