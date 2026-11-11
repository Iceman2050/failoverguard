"""FailoverGuard CLI: validate / check / test / report.

All commands are offline. Failures are simulated from the operation and
scenario descriptions — no live provider is ever called.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .core import (DENY, build_report, decide_operation, format_report_text,
                   load_json, run_scenarios, validate_config)


def _emit(obj, fmt, text=None):
    if fmt == "json":
        print(json.dumps(obj, indent=2))
    else:
        print(text if text is not None else json.dumps(obj, indent=2))


def _load_operation(raw):
    """--operation accepts a JSON file path, a JSON object string, or a class name."""
    if raw and os.path.exists(raw):
        return load_json(raw)
    if raw and raw.strip().startswith("{"):
        return json.loads(raw)
    return {"operation_class": raw}


def cmd_validate(args):
    result = validate_config(load_json(args.route_config), load_json(args.policy))
    text = "\n".join(f"[{f['decision']}] {f['subject']}: {f['reason']}"
                     for f in result["findings"])
    text += f"\nOverall: {result['decision']}"
    _emit(result, args.format, text)
    return 1 if result["decision"] == DENY else 0


def cmd_check(args):
    operation = _load_operation(args.operation)
    if args.workflow:
        operation["workflow"] = args.workflow
    if args.failure:
        operation["failure_type"] = args.failure
    outcome = decide_operation(load_json(args.route_config), load_json(args.policy), operation)
    _emit(outcome, args.format, f"{outcome['decision']} — {outcome['reason']}")
    return 0


def cmd_test(args):
    result = run_scenarios(load_json(args.route_config), load_json(args.policy))
    lines = []
    for r in result["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        lines.append(f"[{mark}] {r['scenario']} ({r['workflow']}, {r['failure_type']}): "
                     f"{r['decision']} (expected {r['expected']}) — {r['reason']} "
                     f"| breaker: {r['breaker']['evidence']}")
    lines.append(f"Scenarios: {result['passed']}/{result['total']} passed")
    _emit(result, args.format, "\n".join(lines))
    return 0 if result["failed"] == 0 else 1


def cmd_report(args):
    report = build_report(load_json(args.route_config), load_json(args.policy))
    _emit(report, args.format, format_report_text(report))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="failoverguard",
        description="Offline policy validator + test harness for AI provider failover. "
                    "Validates policies against simulated failures; never routes live traffic.")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, fn in (("validate", cmd_validate), ("report", cmd_report)):
        p = sub.add_parser(name)
        p.add_argument("route_config")
        p.add_argument("policy")
        p.add_argument("--format", choices=["json", "text"], default="text")
        p.set_defaults(fn=fn)

    p = sub.add_parser("check")
    p.add_argument("route_config")
    p.add_argument("policy")
    p.add_argument("--operation", required=True,
                   help="operation class name, JSON object string, or path to a JSON operation file")
    p.add_argument("--workflow")
    p.add_argument("--failure", help="timeout | http_5xx | hang | unknown")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("test")
    p.add_argument("route_config")
    p.add_argument("policy")
    p.add_argument("--scenarios", action="store_true", required=True,
                   help="run the built-in circuit-breaker / failover scenario suite")
    p.add_argument("--format", choices=["json", "text"], default="text")
    p.set_defaults(fn=cmd_test)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
