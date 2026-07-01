"""Validated semantic interpretation of deterministic branch evidence."""
from __future__ import annotations

import json
from typing import Any, Callable

from e2e_eval.runtime.utils import as_bool

ModelInvoker = Callable[[str, str], str]


def _skip(
    state: dict[str, Any],
    status: str,
    rationale: str,
) -> dict[str, Any]:
    branch_result = dict(state.get("branch_coverage_result") or {})
    branch_result.update({
        "requirement_relevant_uncovered_branches": [],
        "branch_coverage_score": branch_result.get(
            "branch_coverage_percent"
        ),
        "rationale": rationale,
    })
    return {
        "branch_coverage_analysis_status": status,
        "branch_coverage_percent": branch_result.get(
            "branch_coverage_percent"
        ),
        "branch_covered_count": int(
            branch_result.get("covered_branches", 0) or 0
        ),
        "branch_total_count": int(
            branch_result.get("total_branches", 0) or 0
        ),
        "uncovered_branches": branch_result.get("uncovered_branches", []),
        "requirement_relevant_uncovered_branches": [],
        "branch_coverage_score": branch_result.get(
            "branch_coverage_score"
        ),
        "branch_coverage_rationale": rationale,
        "branch_coverage_result": branch_result,
    }


def analyze_branch_relevance(
    state: dict[str, Any],
    invoke_model: ModelInvoker | None,
) -> dict[str, Any]:
    """Interpret relevance while rejecting model-invented branch evidence."""
    branch_result = dict(state.get("branch_coverage_result") or {})
    collection_status = str(
        branch_result.get("coverage_status", "UNAVAILABLE")
    )
    if collection_status != "SUCCESS":
        return _skip(
            state,
            "UNAVAILABLE",
            branch_result.get(
                "rationale",
                "Deterministic branch coverage evidence is unavailable.",
            ),
        )

    uncovered = branch_result.get("uncovered_branches", [])
    percentage = branch_result.get("branch_coverage_percent")
    if not uncovered:
        return _skip(
            state,
            "NO_UNCOVERED_BRANCHES",
            "All measured target-source branches were covered; no semantic "
            "relevance assessment was needed.",
        )
    if not as_bool(
        state.get("enable_branch_coverage_analysis"), True
    ):
        return _skip(
            state,
            "SKIPPED_DISABLED",
            "Deterministic branch coverage was collected, but semantic "
            "business-relevance analysis is disabled.",
        )
    if invoke_model is None:
        return _skip(
            state,
            "ANALYSIS_ERROR",
            "Deterministic coverage remains valid, but no semantic model "
            "invoker was configured.",
        )

    actual_by_id = {
        str(item.get("branch_id")): item
        for item in uncovered
        if isinstance(item, dict) and item.get("branch_id")
    }
    evidence = {
        "requirements": state.get("fine_grained_reqs", ""),
        "requirement_summary": state.get("requirement_summary", ""),
        "generated_scenario": state.get(
            "excutable_test_test_case", ""
        ),
        "generated_test_code": state.get("executable_test_code", ""),
        "source_metadata": {
            "source_origin": state.get("source_origin", ""),
            "resolved_source_project_dir": state.get(
                "resolved_source_project_dir", ""
            ),
            "source_files_measured": branch_result.get(
                "source_files_measured", []
            ),
        },
        "tool_derived_coverage": {
            "branch_coverage_percent": percentage,
            "covered_branches": branch_result.get("covered_branches"),
            "total_branches": branch_result.get("total_branches"),
            "uncovered_branches": uncovered,
        },
    }
    system_prompt = """
You are the semantic Branch Coverage Agent in an E2E-test evaluator.
The supplied coverage numbers and uncovered branch locations are deterministic
tool evidence. You MUST NOT calculate, change, estimate, or invent coverage
numbers or branch locations.

Your only task is to identify which supplied uncovered branch IDs are relevant
to the stated business requirement and classify their business severity.
Use only branch IDs from the evidence. Omit technically uncovered branches that
are unrelated to the active requirement. If evidence is insufficient, do not
mark the branch relevant.

Return ONLY valid JSON:
{
  "relevant_uncovered_branches": [
    {
      "branch_id": "exact supplied ID",
      "severity": "HIGH|MEDIUM|LOW",
      "reason": "brief requirement-based explanation"
    }
  ],
  "rationale": "brief business-relevance synthesis"
}
"""
    try:
        parsed = json.loads(invoke_model(
            system_prompt,
            json.dumps(evidence, ensure_ascii=False, indent=2),
        ))
        relevant: list[dict[str, Any]] = []
        seen: set[str] = set()
        raw_relevant = parsed.get("relevant_uncovered_branches", [])
        if not isinstance(raw_relevant, list):
            raw_relevant = []
        for assessment in raw_relevant:
            if not isinstance(assessment, dict):
                continue
            branch_id = str(assessment.get("branch_id", ""))
            if branch_id not in actual_by_id or branch_id in seen:
                continue
            severity = str(
                assessment.get("severity", "LOW")
            ).upper()
            if severity not in {"HIGH", "MEDIUM", "LOW"}:
                severity = "LOW"
            relevant.append({
                **actual_by_id[branch_id],
                "severity": severity,
                "reason": str(assessment.get("reason", "")).strip(),
            })
            seen.add(branch_id)

        rationale = str(parsed.get("rationale", "")).strip()
        if not rationale:
            rationale = (
                f"{len(relevant)} of {len(uncovered)} tool-derived uncovered "
                "branches were assessed as requirement-relevant."
            )
        branch_result.update({
            "requirement_relevant_uncovered_branches": relevant,
            # The score remains tool-derived and is never supplied by the LLM.
            "branch_coverage_score": percentage,
            "rationale": rationale,
        })
        return {
            "branch_coverage_analysis_status": "ANALYZED",
            "branch_coverage_percent": percentage,
            "branch_covered_count": int(
                branch_result.get("covered_branches", 0) or 0
            ),
            "branch_total_count": int(
                branch_result.get("total_branches", 0) or 0
            ),
            "uncovered_branches": uncovered,
            "requirement_relevant_uncovered_branches": relevant,
            "branch_coverage_score": percentage,
            "branch_coverage_rationale": rationale,
            "branch_coverage_result": branch_result,
        }
    except Exception as exc:
        return _skip(
            state,
            "ANALYSIS_ERROR",
            "Deterministic coverage remains valid, but business-relevance "
            f"analysis failed: {exc}",
        )
