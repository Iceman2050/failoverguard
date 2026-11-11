"""FailoverGuard — offline policy validator + test harness for AI provider failover."""

from .core import (ALLOW, DENY, REQUIRE_APPROVAL, build_report,
                   decide_operation, run_scenarios, validate_config)

__version__ = "0.1.0"
__all__ = ["ALLOW", "DENY", "REQUIRE_APPROVAL", "build_report",
           "decide_operation", "run_scenarios", "validate_config"]
