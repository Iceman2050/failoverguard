import json
import pathlib

from failoverguard.cli import main
from failoverguard.core import build_report, format_report_text

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures"
GOOD = str(FIXTURES / "helpdesk_routes.json")
BROKEN = str(FIXTURES / "broken_routes.json")
POLICY = str(FIXTURES / "helpdesk_policy.json")


def test_report_lists_routes_classes_and_gaps(config, policy):
    report = build_report(config, policy)
    assert report["overall_decision"] == "allow"
    assert {r["workflow"] for r in report["routes"]} == {
        "ticket-summarization", "refund-processing"}
    eligibility = report["eligibility_by_operation_class"]
    assert eligibility["read_only_inference"]["decision"] == "allow"
    assert eligibility["tool_call_side_effect"]["decision"] == "require_approval"
    assert report["gaps"] == []
    text = format_report_text(report)
    assert "ticket-summarization" in text and "Gaps:" in text


def test_report_names_broken_config_gap(broken_config, policy):
    report = build_report(broken_config, policy)
    assert report["overall_decision"] == "deny"
    assert any("tool_calling" in g["reason"] for g in report["gaps"])


def test_cli_validate_good_and_broken(capsys):
    assert main(["validate", GOOD, POLICY]) == 0
    assert "Overall: allow" in capsys.readouterr().out
    assert main(["validate", BROKEN, POLICY]) == 1
    out = capsys.readouterr().out
    assert "deny" in out and "tool_calling" in out


def test_cli_check_contrast(capsys):
    assert main(["check", GOOD, POLICY, "--operation",
                 str(FIXTURES / "operations" / "summarization_timeout.json")]) == 0
    assert capsys.readouterr().out.startswith("allow")
    assert main(["check", GOOD, POLICY, "--operation",
                 str(FIXTURES / "operations" / "refund_timeout.json")]) == 0
    assert capsys.readouterr().out.startswith("require_approval")


def test_cli_test_scenarios(capsys):
    assert main(["test", GOOD, POLICY, "--scenarios"]) == 0
    out = capsys.readouterr().out
    assert "passed" in out and "FAIL" not in out
    assert main(["test", BROKEN, POLICY, "--scenarios"]) == 1
    capsys.readouterr()


def test_cli_report_json(capsys):
    assert main(["report", GOOD, POLICY, "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["name"].startswith("HelpDesk AI")
    assert data["eligibility_by_operation_class"]["unknown_outcome"]["decision"] == "require_approval"
