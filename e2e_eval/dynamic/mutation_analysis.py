"""LLM interpretation of tool-derived surviving mutation evidence."""
from __future__ import annotations

import json
from typing import Any, Callable, Sequence

ModelInvoker = Callable[[str, str], str]


def analyze_survived_mutants(
    *,
    requirements: str,
    test_code: str,
    records: Sequence[dict[str, Any]],
    invoke_model: ModelInvoker | None,
) -> dict[str, Any]:
    survivors = [
        item for item in records
        if item.get("execution_verdict") == "SURVIVED"
    ]
    if not survivors:
        return {
            "mutation_analysis_status": "NO_SURVIVORS",
            "mutation_survivor_analyses": [],
            "requirement_relevant_survivors": [],
            "mutation_analysis_detail": "No survived mutants required interpretation.",
        }
    if invoke_model is None:
        unavailable = [{
            "mutant_id": item.get("mutant_id"),
            "analysis_status": "ANALYSIS_UNAVAILABLE",
            "likely_requirement_gap": None,
            "reason": "No LLM credentials/invoker configured.",
        } for item in survivors]
        return {
            "mutation_analysis_status": "ANALYSIS_UNAVAILABLE",
            "mutation_survivor_analyses": unavailable,
            "requirement_relevant_survivors": [],
            "mutation_analysis_detail": (
                "Deterministic survivor verdicts are retained; semantic "
                "test-gap analysis is unavailable."
            ),
        }
    compact = [{
        key: item.get(key)
        for key in (
            "mutant_id", "operator", "source_file", "location",
            "original_code", "replacement_code", "mutation_description",
            "scope_relation", "scope_reason", "source_scope",
            "execution_verdict",
        )
    } for item in survivors]
    evidence = {
        "requirements": requirements,
        "generated_e2e_test": test_code,
        "tool_derived_survivors": compact,
    }
    prompt = """
You interpret survived mutants in an E2E test evaluator. Every supplied
SURVIVED verdict is immutable tool evidence. Never change a verdict, invent a
mutant, or discuss killed/invalid/timeout/error mutants.

For each exact mutant_id, decide whether survival likely exposes a
requirement-relevant test adequacy gap. Return ONLY JSON:
{"survivors":[{
  "mutant_id":"exact supplied ID",
  "likely_requirement_gap":true,
  "reason":"brief requirement-grounded explanation",
  "missing_observation":"specific behavior the test likely fails to verify"
}]}
"""
    try:
        parsed = json.loads(invoke_model(
            prompt, json.dumps(evidence, ensure_ascii=False, indent=2)
        ))
        by_id = {str(item["mutant_id"]): item for item in survivors}
        analyses: list[dict[str, Any]] = []
        seen: set[str] = set()
        raw = parsed.get("survivors", []) if isinstance(parsed, dict) else []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            mutant_id = str(item.get("mutant_id", ""))
            if mutant_id not in by_id or mutant_id in seen:
                continue
            likely_gap = item.get("likely_requirement_gap")
            if not isinstance(likely_gap, bool):
                continue
            analyses.append({
                "mutant_id": mutant_id,
                "analysis_status": "ANALYZED",
                "likely_requirement_gap": likely_gap,
                "reason": str(item.get("reason", "")).strip()[:1000],
                "missing_observation": str(
                    item.get("missing_observation", "")
                ).strip()[:1000],
                "execution_verdict": "SURVIVED",
            })
            seen.add(mutant_id)
        for mutant_id in sorted(set(by_id) - seen):
            analyses.append({
                "mutant_id": mutant_id,
                "analysis_status": "INSUFFICIENT_MODEL_OUTPUT",
                "likely_requirement_gap": None,
                "reason": "The model returned no valid assessment.",
                "execution_verdict": "SURVIVED",
            })
        relevant = [
            item for item in analyses
            if item.get("likely_requirement_gap") is True
        ]
        return {
            "mutation_analysis_status": "ANALYZED",
            "mutation_survivor_analyses": analyses,
            "requirement_relevant_survivors": relevant,
            "mutation_analysis_detail": (
                f"{len(relevant)} of {len(survivors)} tool-derived survivors "
                "were assessed as likely requirement-relevant test gaps."
            ),
        }
    except Exception as exc:
        return {
            "mutation_analysis_status": "ANALYSIS_UNAVAILABLE",
            "mutation_survivor_analyses": [],
            "requirement_relevant_survivors": [],
            "mutation_analysis_detail": (
                "Deterministic survivor verdicts are retained; semantic "
                f"analysis failed: {exc}"
            ),
        }
