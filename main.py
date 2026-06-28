"""Batch entry point for the V5.2 scope-precise and safe-refinement E2E test evaluator.

LLM calls are serial and rate-limited inside agents.invoke_with_retry.
Dynamic execution, coverage, mutation, and refined-code validation are local tools.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from tqdm import tqdm

from e2e_eval.config import EvaluationConfig
from e2e_eval.reporting import (
    json_cell as _json_cell,
    join_cell as _join,
    write_evaluation_reports,
)
from graph import app

load_dotenv()


def _dynamic_summary_cell(result: dict[str, Any]) -> str:
    """Export a useful dynamic analyst summary even when an LLM omits that optional field."""
    summary = str(result.get("dynamic_evidence_summary", "") or "").strip()
    if summary and summary.lower() not in {"no dynamic summary returned.", "none", "n/a"}:
        return summary

    root_cause = str(result.get("root_cause", "") or "").strip()
    if root_cause and root_cause.lower() not in {"no root cause returned.", "none", "n/a"}:
        return root_cause

    status = str(result.get("dynamic_analysis_status", "NOT_RUN"))
    if status.startswith("SKIPPED"):
        return f"Dynamic Analyst {status}: no additional semantic diagnosis was required."
    if status == "ANALYZED":
        return "Dynamic Analyst completed, but the model returned no separate summary."
    return f"Dynamic Analyst status: {status}"


def run_batch_evaluation(
    input_csv_path: str,
    output_csv_path: str,
    config: EvaluationConfig | None = None,
) -> None:
    config = config or EvaluationConfig.from_env()
    input_path = Path(input_csv_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    df = pd.read_csv(input_path)
    if config.max_cases > 0:
        df = df.head(config.max_cases).copy()

    print(
        f"Loaded {len(df)} cases. dynamic={config.enable_dynamic}; "
        f"coverage={config.enable_coverage}; mutation={config.enable_mutation}; "
        f"dynamic_analyst={config.enable_dynamic_analyst}; "
        f"critic={config.enable_critic}; refiner={config.enable_refiner}; "
        f"refinement_validation={config.enable_refinement_validation}"
    )
    print("Deterministic tool order: execution -> BDD diagnostics -> Python coverage -> mutation")
    print(
        "Pipeline: Syntax+AST → Static LLM Jury → Execution → BDD Step Diagnostics → Mutation "
        "→ [conditional Dynamic Analyst/Critic] → Consensus → [conditional Refiner] "
        "→ ValidationExecution → ValidationCoverage → ValidationMutation → BeforeAfterComparison"
    )

    rows: list[dict[str, Any]] = []
    for index, row in tqdm(df.iterrows(), total=len(df), desc="Evaluation Progress"):
        original_code = str(row.get("excutable_test_step_code", ""))
        state = config.build_state(row, int(index))

        try:
            result = app.invoke(state)
        except Exception as exc:
            # One failure must not discard an entire batch. No artificial dynamic zero is created.
            result = {
                "syntax_passed": False,
                "overall_score": 0.0,
                "final_reasoning": f"PIPELINE_ERROR: {exc}",
                "execution_status": "PIPELINE_ERROR",
                "execution_stderr_tail": repr(exc),
                "fixed_code": original_code,
                "repair_validation_status": "NOT_VALIDATED",
            }

        rows.append({
            # Static fields
            "eval_syntax_status": "PASSED" if result.get("syntax_passed") else "FAILED_SYNTAX",
            "eval_requirement_score": result.get("requirement_coverage", 0.0),
            "eval_assertion_score": result.get("assertion_score", 0),
            "eval_hallucination_count": result.get("hallucination_count", 0),
            "eval_maintainability_score": result.get("maintainability_score", 0),
            "eval_static_overall_score": result.get("static_overall_score", result.get("overall_score", 0.0)),
            "eval_overall_score": result.get("overall_score", 0.0),
            "eval_score_mode": result.get("score_mode", "STATIC_ONLY"),
            "eval_final_summary": result.get("final_reasoning", ""),
            "agent_results": _json_cell(result.get("agent_results", {})),
            "eval_has_conflict": result.get("has_conflict", False),
            "eval_critic_status": result.get("critic_status", "NOT_RUN"),
            "eval_critic_feedback": result.get("critic_feedback", ""),
            "eval_critic_score_caveat": result.get("critic_score_caveat", ""),
            "eval_improvement_report": result.get("improvement_report", ""),
            "eval_refiner_status": result.get("refiner_status", "NOT_RUN"),
            "eval_refiner_scope": result.get("refiner_scope", "STEP_CODE_ONLY"),
            "eval_refiner_scope_violation": result.get("refiner_scope_violation", False),
            "eval_refiner_scope_violation_detail": result.get("refiner_scope_violation_detail", ""),
            "eval_refiner_accepted": result.get("refiner_accepted", False),
            "eval_refiner_rejection_reason": result.get("refiner_rejection_reason", ""),
            "eval_refiner_candidate_code": result.get("refiner_candidate_code", ""),
            "eval_fixed_code": result.get("fixed_code", original_code),
            "static_actions_count": result.get("static_actions_count", 0),
            "static_assertions_count": result.get("static_assertions_count", 0),
            "static_stable_selectors": _join(result.get("static_stable_selectors", [])),
            "static_medium_risk_selectors": _join(result.get("static_medium_risk_selectors", [])),
            "static_brittle_selectors": _join(result.get("static_brittle_selectors", [])),
            "static_magic_numbers": _join(result.get("static_magic_numbers", [])),
            "eval_selector_issues": _join(result.get("hardcoded_selector_issues", [])),
            "eval_duplication_issues": _join(result.get("duplication_issues", [])),
            "eval_naming_issues": _join(result.get("naming_issues", [])),
            "eval_readability_issues": _join(result.get("readability_issues", [])),

            # Baseline execution evidence
            "dynamic_execution_status": result.get("execution_status", "NOT_RUN"),
            "dynamic_execution_success": result.get("execution_success", False),
            "dynamic_execution_return_code": result.get("execution_return_code", -1),
            "dynamic_execution_seconds": result.get("execution_duration_seconds", 0.0),
            "dynamic_execution_stdout_tail": result.get("execution_stdout_tail", ""),
            "dynamic_execution_stderr_tail": result.get("execution_stderr_tail", ""),
            "dynamic_execution_failed_steps": _json_cell(result.get("execution_failed_steps", [])),
            "dynamic_execution_report_summary": result.get("execution_report_summary", ""),
            "dynamic_execution_artifact_dir": result.get("execution_artifact_dir", ""),
            "dynamic_behave_report_path": result.get("behave_report_path", ""),
            "dynamic_execution_command": result.get("execution_command", ""),
            "dynamic_execution_environment": _json_cell(
                result.get("execution_environment", {})
            ),

            # BDD step-execution diagnostics (reported, not directly score-weighted)
            "dynamic_coverage_status": result.get("dynamic_coverage_status", "NOT_RUN"),
            "dynamic_coverage_score": result.get("dynamic_coverage_score"),
            "dynamic_step_success_coverage": result.get("step_success_coverage"),
            "dynamic_action_step_coverage": result.get("action_step_coverage"),
            "dynamic_oracle_step_coverage": result.get("oracle_step_coverage"),
            "dynamic_steps_total": result.get("dynamic_steps_total", 0),
            "dynamic_steps_executed": result.get("dynamic_steps_executed", 0),
            "dynamic_steps_passed": result.get("dynamic_steps_passed", 0),
            "dynamic_steps_failed": result.get("dynamic_steps_failed", 0),
            "dynamic_steps_skipped": result.get("dynamic_steps_skipped", 0),
            "dynamic_actions_total": result.get("dynamic_actions_total", 0),
            "dynamic_actions_passed": result.get("dynamic_actions_passed", 0),
            "dynamic_oracles_total": result.get("dynamic_oracles_total", 0),
            "dynamic_oracles_passed": result.get("dynamic_oracles_passed", 0),
            "dynamic_coverage_detail": result.get("dynamic_coverage_detail", ""),

            # coverage.py evidence (Python source only; distinct from BDD diagnostics)
            "coverage_status": result.get("coverage_status", "skipped"),
            "coverage_execution_status": result.get(
                "coverage_execution_status", "not_run"
            ),
            "coverage_total_line": result.get("total_line_coverage"),
            "coverage_total_branch": result.get("total_branch_coverage"),
            "coverage_covered_lines": _json_cell(
                result.get("covered_lines", {})
            ),
            "coverage_missing_lines": _json_cell(
                result.get("missing_lines", {})
            ),
            "coverage_covered_branches": _json_cell(
                result.get("covered_branches", {})
            ),
            "coverage_missing_branches": _json_cell(
                result.get("missing_branches", {})
            ),
            "coverage_source_files_measured": _json_cell(
                result.get("source_files_measured", [])
            ),
            "coverage_command_run": result.get("coverage_command_run", ""),
            "coverage_stdout_summary": result.get(
                "coverage_stdout_summary", ""
            ),
            "coverage_stderr_summary": result.get(
                "coverage_stderr_summary", ""
            ),
            "coverage_failure_reason": result.get(
                "coverage_failure_reason", ""
            ),
            "coverage_report_path": result.get("coverage_report_path", ""),
            "coverage_artifact_dir": result.get("coverage_artifact_dir", ""),
            "coverage_result": _json_cell(result.get("coverage_result", {})),
            "reference_resolution": _json_cell(
                result.get("reference_resolution", {})
            ),
            "source_origin": result.get("source_origin", ""),
            "input_source_project_dir": result.get(
                "input_source_project_dir",
                state.get("source_project_dir", ""),
            ),
            "resolved_source_project_dir": result.get(
                "resolved_source_project_dir", ""
            ),
            "resolved_entrypoint": result.get("resolved_entrypoint", ""),
            "local_override_diagnostic": result.get(
                "local_override_diagnostic", ""
            ),

            # Mutation tool evidence
            "dynamic_mutation_status": result.get("mutation_status", "NOT_RUN"),
            "dynamic_mutation_score": result.get("mutation_score"),
            "dynamic_total_mutants_generated": result.get(
                "total_mutants_generated", result.get("mutants_total", 0)
            ),
            "dynamic_valid_mutants": result.get("valid_mutants", 0),
            "dynamic_invalid_mutants": result.get(
                "invalid_mutants", result.get("mutants_inconclusive", 0)
            ),
            "dynamic_per_operator_breakdown": _json_cell(
                result.get("per_operator_breakdown", {})
            ),
            "dynamic_surviving_mutant_report": _json_cell(
                result.get("surviving_mutant_report", [])
            ),
            "dynamic_mutants_total": result.get("mutants_total", 0),
            "dynamic_mutants_killed": result.get("mutants_killed", 0),
            "dynamic_mutants_survived": result.get("mutants_survived", 0),
            "dynamic_mutants_timeout": result.get("mutants_timeout", 0),
            "dynamic_mutants_inconclusive": result.get("mutants_inconclusive", 0),
            # Raw score remains for requirement-suite aggregation; scope-aware score
            # is the only mutation score used for single-test quality.
            "dynamic_mutation_scope_status": result.get("mutation_scope_status", "NO_DYNAMIC_SCOPE_EVIDENCE"),
            "dynamic_relevant_mutation_score": result.get("relevant_mutation_score"),
            "dynamic_relevant_mutants_total": result.get("relevant_mutants_total", 0),
            "dynamic_relevant_mutants_killed": result.get("relevant_mutants_killed", 0),
            "dynamic_relevant_mutants_survived": result.get("relevant_mutants_survived", 0),
            "dynamic_out_of_scope_mutants_total": result.get("out_of_scope_mutants_total", 0),
            "dynamic_out_of_scope_mutants_survived": result.get("out_of_scope_mutants_survived", 0),
            "dynamic_uncertain_mutants_total": result.get("uncertain_mutants_total", 0),
            "dynamic_uncertain_mutants_survived": result.get("uncertain_mutants_survived", 0),
            "dynamic_test_scope": _json_cell(result.get("test_scope", {})),
            "dynamic_scope_relevant_survivors": _join(result.get("scope_relevant_surviving_mutants", [])),
            "dynamic_scope_out_of_scope_survivors": _join(result.get("scope_out_of_scope_surviving_mutants", [])),
            "dynamic_scope_uncertain_survivors": _join(result.get("scope_uncertain_surviving_mutants", [])),
            "dynamic_mutation_report_path": result.get("mutation_report_path", ""),
            "dynamic_surviving_mutants": _join(result.get("surviving_mutants", [])),
            "dynamic_killed_mutants": _join(
                result.get(
                    "killed_mutant_report",
                    result.get("killed_mutants", []),
                )
            ),
            "dynamic_mutation_records": _json_cell(result.get("mutation_records", [])),
            "dynamic_mutation_detail": result.get("mutation_detail", ""),

            # Dynamic Evidence Analyst (LLM interpretation of tool evidence)
            "eval_dynamic_analysis_status": result.get("dynamic_analysis_status", "NOT_RUN"),
            "eval_dynamic_quality_label": result.get("dynamic_quality_label", "INCONCLUSIVE"),
            "eval_dynamic_failure_category": result.get("failure_category", "NOT_ANALYZED"),
            "eval_dynamic_root_cause": result.get("root_cause", ""),
            "eval_dynamic_evidence_summary": _dynamic_summary_cell(result),
            "eval_dynamic_fault_detection_gaps": _join(result.get("fault_detection_gaps", [])),
            "eval_dynamic_relevant_surviving_mutants": _join(result.get("analyst_relevant_surviving_mutants", [])),
            "eval_dynamic_out_of_scope_surviving_mutants": _join(result.get("analyst_out_of_scope_surviving_mutants", [])),
            "eval_dynamic_uncertain_surviving_mutants": _join(result.get("analyst_uncertain_surviving_mutants", [])),
            "eval_dynamic_prioritized_repairs": _join(result.get("prioritized_repairs", [])),
            "eval_dynamic_should_refine": result.get("should_refine", False),

            # Local before/after validation of Refiner output
            "validation_refined_syntax_passed": result.get("refined_syntax_passed", False),
            "validation_execution_status": result.get("validation_execution_status", "NOT_RUN"),
            "validation_execution_success": result.get("validation_execution_success", False),
            "validation_execution_return_code": result.get("validation_execution_return_code", -1),
            "validation_execution_seconds": result.get("validation_execution_duration_seconds", 0.0),
            "validation_execution_failed_steps": _json_cell(result.get("validation_execution_failed_steps", [])),
            "validation_execution_stdout_tail": result.get("validation_execution_stdout_tail", ""),
            "validation_execution_stderr_tail": result.get("validation_execution_stderr_tail", ""),
            "validation_execution_artifact_dir": result.get("validation_execution_artifact_dir", ""),
            "validation_dynamic_coverage_status": result.get("validation_dynamic_coverage_status", "NOT_RUN"),
            "validation_dynamic_coverage_score": result.get("validation_dynamic_coverage_score"),
            "validation_step_success_coverage": result.get("validation_step_success_coverage"),
            "validation_action_step_coverage": result.get("validation_action_step_coverage"),
            "validation_oracle_step_coverage": result.get("validation_oracle_step_coverage"),
            "validation_steps_total": result.get("validation_steps_total", 0),
            "validation_steps_passed": result.get("validation_steps_passed", 0),
            "validation_steps_failed": result.get("validation_steps_failed", 0),
            "validation_dynamic_mutation_status": result.get("validation_mutation_status", "NOT_RUN"),
            "validation_dynamic_mutation_score": result.get("validation_mutation_score"),
            "validation_relevant_mutation_score": result.get("validation_relevant_mutation_score"),
            "validation_mutation_scope_status": result.get("validation_mutation_scope_status", "NO_DYNAMIC_SCOPE_EVIDENCE"),
            "validation_mutants_total": result.get("validation_mutants_total", 0),
            "validation_relevant_mutants_total": result.get("validation_relevant_mutants_total", 0),
            "validation_relevant_mutants_killed": result.get("validation_relevant_mutants_killed", 0),
            "validation_relevant_mutants_survived": result.get("validation_relevant_mutants_survived", 0),
            "validation_mutants_killed": result.get("validation_mutants_killed", 0),
            "validation_mutants_survived": result.get("validation_mutants_survived", 0),
            "validation_mutants_timeout": result.get("validation_mutants_timeout", 0),
            "validation_mutants_inconclusive": result.get("validation_mutants_inconclusive", 0),
            "validation_mutation_report_path": result.get("validation_mutation_report_path", ""),
            "validation_surviving_mutants": _join(result.get("validation_surviving_mutants", [])),
            "repair_validation_status": result.get("repair_validation_status", "NOT_VALIDATED"),
            "repair_delta_coverage": result.get("repair_delta_coverage"),
            "repair_delta_mutation": result.get("repair_delta_mutation"),
            "repair_comparison_summary": result.get("repair_comparison_summary", ""),
        })

        # Additional case spacing changes no API-call count and preserves a safe global pace.
        time.sleep(config.case_sleep_seconds)

    result_df = pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    paths = write_evaluation_reports(
        result_df, output_csv_path, config=config
    )
    print(
        f"Saved {len(result_df)} evaluated cases to {paths['csv']} "
        f"and {paths['json']}"
    )


if __name__ == "__main__":
    active_config = EvaluationConfig.from_env()
    run_batch_evaluation(
        active_config.input_file,
        active_config.output_file,
        config=active_config,
    )
