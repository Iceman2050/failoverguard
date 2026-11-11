from failoverguard.core import ALLOW, DENY, REQUIRE_APPROVAL, run_scenarios


def by_id(result):
    return {r["scenario"]: r for r in result["results"]}


def test_good_config_passes_whole_suite(config, policy):
    result = run_scenarios(config, policy)
    assert result["total"] >= 10
    assert result["failed"] == 0, result["results"]


def test_broken_config_fails_suite(broken_config, policy):
    result = run_scenarios(broken_config, policy)
    assert result["failed"] >= 1
    failed = [r for r in result["results"] if not r["passed"]]
    # The refund scenarios flip to deny (capability hard block) instead of
    # the expected require_approval — the suite catches the broken config.
    assert any(r["workflow"] == "refund-processing" for r in failed)


def test_timeout_contrast_inside_suite(config, policy):
    scenarios = by_id(run_scenarios(config, policy))
    assert scenarios["read-only-timeout-retries"]["decision"] == ALLOW
    assert scenarios["side-effect-timeout-reconciles-first"]["decision"] == REQUIRE_APPROVAL


def test_breaker_trips_with_evidence(config, policy):
    scenarios = by_id(run_scenarios(config, policy))
    breaker = scenarios["repeated-timeouts-trip-breaker"]["breaker"]
    assert breaker["tripped"] is True
    assert breaker["score"] >= breaker["threshold"]
    assert "breaker tripped" in breaker["evidence"]
    single = scenarios["read-only-timeout-retries"]["breaker"]
    assert single["tripped"] is False


def test_every_decision_table_row_has_a_scenario(config, policy):
    scenarios = by_id(run_scenarios(config, policy))
    decisions = {r["decision"] for r in scenarios.values()}
    assert decisions == {ALLOW, DENY, REQUIRE_APPROVAL}
    for scenario_id in ("deadline-expired-denies", "capability-missing-denies",
                        "data-location-violated-denies", "no-capacity-queues",
                        "unknown-failure-escalates", "unknown-outcome-reconciles-first",
                        "stateful-without-reconciliation-step"):
        assert scenario_id in scenarios, scenario_id
