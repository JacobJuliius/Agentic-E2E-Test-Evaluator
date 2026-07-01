"""Branch coverage evaluator public API."""

from coverage_agent import coverage_agent
from e2e_eval.dynamic.branch_coverage import (
    collect_branch_coverage,
    parse_coverage_payload,
    parse_coverage_report,
)

__all__ = [
    "coverage_agent",
    "collect_branch_coverage",
    "parse_coverage_payload",
    "parse_coverage_report",
]

