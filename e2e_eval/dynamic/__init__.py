"""Deterministic dynamic evaluator API."""

from e2e_eval.dynamic.branch_coverage import (
    collect_branch_coverage,
    parse_coverage_payload,
    parse_coverage_report,
)
from e2e_eval.dynamic.branch_relevance import analyze_branch_relevance
from e2e_eval.dynamic.mutation_analysis import analyze_survived_mutants
from e2e_eval.dynamic.mutation_operators import (
    OperatorRegistry,
    default_operator_registry,
)
from e2e_eval.dynamic.mutation_planner import plan_mutations
from e2e_eval.dynamic.mutation_runner import run_single_mutant

__all__ = [
    "analyze_branch_relevance",
    "analyze_survived_mutants",
    "collect_branch_coverage",
    "parse_coverage_payload",
    "parse_coverage_report",
    "OperatorRegistry",
    "default_operator_registry",
    "plan_mutations",
    "run_single_mutant",
]

