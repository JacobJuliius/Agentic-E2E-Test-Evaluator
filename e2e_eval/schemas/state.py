"""Shared typed state fields used across graph nodes."""
from __future__ import annotations

from typing import Any, List, TypedDict


class AgentResultData(TypedDict, total=False):
    status: str
    score: float | None
    rationale: str
    artifacts: dict[str, str]
    evidence: dict[str, Any]
    failure_reason: str


class EvaluationInputs(TypedDict, total=False):
    fine_grained_reqs: str
    executable_test_code: str
    excutable_test_test_case: str
    requirement_summary: str
    prompt: str
    reference_answer: str
    source_project_dir: str
    case_uid: str
    benchmark_id: str
    enable_dynamic: bool
    enable_coverage: bool
    enable_mutation: bool
    reference_expected_patterns: List[str]


class EvaluationStateBase(TypedDict, total=False):
    agent_results: dict[str, AgentResultData]
