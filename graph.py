"""LangGraph orchestration for V5.1 scope-aware hybrid E2E test evaluation.

LLM nodes are serial and conditionally routed. Local execution, BDD-step diagnostics,
Python source coverage, mutation testing, and validation do not consume model/API calls.
"""
from __future__ import annotations

from typing import Any, List, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph

from e2e_eval.schemas import EvaluationStateBase, standardized_agent
from coverage_agent import coverage_agent
from agents import (
    assertion_quality_agent,
    consensus_agent,
    critic_agent,
    critic_skip_agent,
    dynamic_analysis_skip_agent,
    dynamic_evidence_analyst_agent,
    hallucination_smell_agent,
    maintainability_agent,
    refiner_agent,
    refiner_skip_agent,
    requirement_alignment_agent,
    should_run_critic,
    should_run_dynamic_analysis,
    should_run_refiner,
    syntax_linter_agent,
)
from dynamic_agents import (
    dynamic_coverage_agent,
    execution_agent,
    mutation_agent,
    repair_comparison_agent,
    repair_safety_gate_agent,
    validation_coverage_agent,
    validation_execution_agent,
    validation_mutation_agent,
)

load_dotenv()


class E2EEvalState(EvaluationStateBase, total=False):
    # Inputs / execution configuration
    fine_grained_reqs: str
    executable_test_code: str
    excutable_test_test_case: str
    requirement_summary: str
    prompt: str
    reference_answer: str
    source_project_dir: str
    reference_workspace_root: str
    reference_network_enabled: bool
    reference_timeout_seconds: int
    reference_expected_patterns: List[str]
    case_uid: str
    benchmark_id: str
    enable_dynamic: bool
    enable_mutation: bool
    enable_dynamic_analyst: bool
    enable_critic: bool
    enable_refiner: bool
    enable_refinement_validation: bool
    enable_refinement_mutation_validation: bool
    max_mutants: int
    mutation_max_mutants_per_file: int
    mutation_seed: int
    mutation_include_patterns: List[str]
    mutation_exclude_patterns: List[str]
    mutation_project_validation_command: List[str]
    mutation_keep_workspaces: bool
    execution_timeout_seconds: int
    mutant_timeout_seconds: int
    relevant_mutation_refine_threshold: float
    headless: bool
    enable_coverage: bool
    coverage_timeout_seconds: int
    coverage_source_dir: str
    coverage_include_patterns: List[str]
    coverage_exclude_patterns: List[str]
    coverage_branch_enabled: bool
    coverage_include_tests: bool

    # Static deterministic evidence
    syntax_passed: bool
    error_message: str
    static_actions_count: int
    static_assertions_count: int
    static_hardcoded_selectors: List[str]
    static_stable_selectors: List[str]
    static_medium_risk_selectors: List[str]
    static_brittle_selectors: List[str]
    static_magic_numbers: List[float]
    static_action_methods: List[str]
    static_assertion_methods: List[str]

    # Static LLM evidence
    total_requirements_count: int
    covered_requirements: List[str]
    partially_covered_requirements: List[str]
    missing_requirements: List[str]
    assertion_score: int
    strong_assertions: List[str]
    weak_assertions: List[str]
    missing_assertions_rationale: str
    hallucination_count: int
    hallucinations: List[str]
    test_smells: List[str]
    maintainability_score: int
    hardcoded_selector_issues: List[str]
    duplication_issues: List[str]
    naming_issues: List[str]
    readability_issues: List[str]
    maintainability_rationale: str

    # Baseline dynamic tool evidence
    execution_status: str
    execution_success: bool
    execution_return_code: int
    execution_duration_seconds: float
    execution_stdout_tail: str
    execution_stderr_tail: str
    execution_failed_steps: List[dict[str, Any]]
    execution_report_summary: str
    execution_command: str
    execution_environment: dict[str, Any]
    execution_artifact_dir: str
    workspace_dir: str
    behave_report_path: str
    dynamic_coverage_status: str
    dynamic_coverage_score: float | None
    step_success_coverage: float | None
    action_step_coverage: float | None
    oracle_step_coverage: float | None
    dynamic_steps_total: int
    dynamic_steps_executed: int
    dynamic_steps_passed: int
    dynamic_steps_failed: int
    dynamic_steps_skipped: int
    dynamic_actions_total: int
    dynamic_actions_passed: int
    dynamic_oracles_total: int
    dynamic_oracles_passed: int
    dynamic_coverage_detail: str

    # Deterministic Python source coverage (separate from BDD step diagnostics)
    coverage_status: str
    coverage_execution_status: str
    total_line_coverage: float | None
    total_branch_coverage: float | None
    covered_lines: dict[str, List[int]]
    missing_lines: dict[str, List[int]]
    covered_branches: dict[str, List[List[int]]]
    missing_branches: dict[str, List[List[int]]]
    source_files_measured: List[str]
    coverage_command_run: str
    coverage_stdout_summary: str
    coverage_stderr_summary: str
    coverage_failure_reason: str
    coverage_report_path: str
    coverage_artifact_dir: str
    coverage_result: dict[str, Any]
    reference_resolution: dict[str, Any]
    source_origin: str
    input_source_project_dir: str
    resolved_source_project_dir: str
    resolved_entrypoint: str
    local_override_diagnostic: str

    mutation_status: str
    mutation_score: float | None  # raw score: suite aggregation only
    mutants_total: int
    mutants_killed: int
    mutants_survived: int
    mutants_timeout: int
    mutants_inconclusive: int
    mutation_scope_status: str
    relevant_mutation_score: float | None  # scope-aware score: single-test quality
    relevant_mutants_total: int
    relevant_mutants_killed: int
    relevant_mutants_survived: int
    relevant_mutants_timeout: int
    relevant_mutants_inconclusive: int
    out_of_scope_mutants_total: int
    out_of_scope_mutants_survived: int
    uncertain_mutants_total: int
    uncertain_mutants_survived: int
    test_scope: dict[str, Any]
    mutation_report_path: str
    surviving_mutants: List[str]
    killed_mutants: int
    killed_mutant_report: List[str]
    total_mutants_generated: int
    valid_mutants: int
    survived_mutants: int
    invalid_mutants: int
    per_operator_breakdown: dict[str, Any]
    surviving_mutant_report: List[dict[str, Any]]
    mutation_metrics: dict[str, Any]
    scope_relevant_surviving_mutants: List[str]
    scope_out_of_scope_surviving_mutants: List[str]
    scope_uncertain_surviving_mutants: List[str]
    mutation_records: List[dict[str, Any]]
    mutation_detail: str

    # Dynamic Evidence Analyst (conditional LLM reader)
    dynamic_analysis_status: str
    dynamic_quality_label: str
    failure_category: str
    root_cause: str
    dynamic_evidence_summary: str
    fault_detection_gaps: List[str]
    analyst_relevant_surviving_mutants: List[str]
    analyst_out_of_scope_surviving_mutants: List[str]
    analyst_uncertain_surviving_mutants: List[str]
    prioritized_repairs: List[str]
    should_refine: bool

    # Critic / consensus / refiner
    critic_status: str
    critic_feedback: str
    critic_score_caveat: str
    revision_count: int
    has_conflict: bool
    requirement_coverage: float
    static_overall_score: float
    overall_score: float
    score_mode: str
    final_reasoning: str
    improvement_report: str
    fixed_code: str
    refiner_status: str
    refiner_scope: str
    refiner_scope_violation: bool
    refiner_scope_violation_detail: str
    refiner_candidate_code: str
    refiner_accepted: bool
    refiner_rejection_reason: str

    # Refiner validation tools and deterministic before/after comparison
    refined_syntax_passed: bool
    validation_execution_status: str
    validation_execution_success: bool
    validation_execution_return_code: int
    validation_execution_duration_seconds: float
    validation_execution_stdout_tail: str
    validation_execution_stderr_tail: str
    validation_execution_failed_steps: List[dict[str, Any]]
    validation_execution_command: str
    validation_execution_environment: dict[str, Any]
    validation_execution_artifact_dir: str
    validation_workspace_dir: str
    validation_behave_report_path: str
    validation_dynamic_coverage_status: str
    validation_dynamic_coverage_score: float | None
    validation_step_success_coverage: float | None
    validation_action_step_coverage: float | None
    validation_oracle_step_coverage: float | None
    validation_steps_total: int
    validation_steps_passed: int
    validation_steps_failed: int
    validation_coverage_detail: str
    validation_mutation_status: str
    validation_mutation_score: float | None  # raw validation score
    validation_relevant_mutation_score: float | None
    validation_mutation_scope_status: str
    validation_mutants_total: int
    validation_mutants_killed: int
    validation_mutants_survived: int
    validation_relevant_mutants_total: int
    validation_relevant_mutants_killed: int
    validation_relevant_mutants_survived: int
    validation_mutants_timeout: int
    validation_mutants_inconclusive: int
    validation_mutation_report_path: str
    validation_surviving_mutants: List[str]
    validation_mutation_detail: str
    repair_validation_status: str
    repair_delta_coverage: float | None
    repair_delta_mutation: float | None
    repair_comparison_summary: str


def route_after_syntax(state: E2EEvalState) -> str:
    return "requirement_node" if state.get("syntax_passed", False) else "consensus_node"


def route_after_execution(state: E2EEvalState) -> str:
    # Preserve Behave diagnostics for genuine test failures; harness states bypass tools.
    if state.get("execution_status") in {"PASSED", "TEST_FAILED"}:
        return "dynamic_coverage_node"
    return route_after_dynamic_tools(state)


def route_after_dynamic_tools(state: E2EEvalState) -> str:
    if should_run_dynamic_analysis(state):
        return "dynamic_analyst_node"
    if should_run_critic(state):
        return "critic_node"
    return "dynamic_analyst_skip_node"


def route_after_dynamic_analyst(state: E2EEvalState) -> str:
    return "critic_node" if should_run_critic(state) else "critic_skip_node"


def route_after_critic_or_skip(state: E2EEvalState) -> str:
    return "consensus_node"


def route_after_consensus(state: E2EEvalState) -> str:
    return "refiner_node" if should_run_refiner(state) else "refiner_skip_node"


def route_after_refiner(state: E2EEvalState) -> str:
    generated = state.get("refiner_status") == "GENERATED"
    validation_enabled = bool(state.get("enable_refinement_validation", False))
    return "validation_execution_node" if generated and validation_enabled else "repair_safety_gate_node"


def route_after_validation_execution(state: E2EEvalState) -> str:
    if state.get("validation_execution_status") in {"PASSED", "TEST_FAILED"}:
        return "validation_coverage_node"
    return "repair_safety_gate_node"


# Standardize status/score/rationale/artifact/evidence reporting while preserving
# every historical node output used by scoring, CSV exports, and routing.
syntax_linter_agent = standardized_agent(
    "syntax", syntax_linter_agent,
    status_key="syntax_passed", rationale_key="error_message",
)
requirement_alignment_agent = standardized_agent(
    "requirement_alignment", requirement_alignment_agent,
)
assertion_quality_agent = standardized_agent(
    "assertion_quality", assertion_quality_agent,
    score_key="assertion_score",
)
hallucination_smell_agent = standardized_agent(
    "hallucination_smell", hallucination_smell_agent,
)
maintainability_agent = standardized_agent(
    "maintainability", maintainability_agent,
    score_key="maintainability_score",
)
execution_agent = standardized_agent(
    "execution", execution_agent, status_key="execution_status",
    rationale_key="execution_report_summary",
)
dynamic_coverage_agent = standardized_agent(
    "bdd_step_coverage", dynamic_coverage_agent,
    status_key="dynamic_coverage_status",
    score_key="dynamic_coverage_score",
)
coverage_agent = standardized_agent(
    "python_coverage", coverage_agent, status_key="coverage_status",
    score_key="total_branch_coverage", rationale_key="coverage_failure_reason",
)
mutation_agent = standardized_agent(
    "mutation", mutation_agent, status_key="mutation_status",
    score_key="mutation_score", rationale_key="mutation_detail",
)
dynamic_evidence_analyst_agent = standardized_agent(
    "dynamic_evidence_analyst", dynamic_evidence_analyst_agent,
    status_key="dynamic_analysis_status",
    rationale_key="dynamic_evidence_summary",
)
dynamic_analysis_skip_agent = standardized_agent(
    "dynamic_evidence_analyst", dynamic_analysis_skip_agent,
    status_key="dynamic_analysis_status",
)
critic_agent = standardized_agent(
    "critic", critic_agent, status_key="critic_status",
    rationale_key="critic_feedback",
)
critic_skip_agent = standardized_agent(
    "critic", critic_skip_agent, status_key="critic_status",
)
consensus_agent = standardized_agent(
    "consensus", consensus_agent, score_key="overall_score",
    rationale_key="final_reasoning",
)
refiner_agent = standardized_agent(
    "refiner", refiner_agent, status_key="refiner_status",
    rationale_key="improvement_report",
)
refiner_skip_agent = standardized_agent(
    "refiner", refiner_skip_agent, status_key="refiner_status",
)
validation_execution_agent = standardized_agent(
    "validation_execution", validation_execution_agent,
    status_key="validation_execution_status",
)
validation_coverage_agent = standardized_agent(
    "validation_coverage", validation_coverage_agent,
    status_key="validation_dynamic_coverage_status",
    score_key="validation_dynamic_coverage_score",
)
validation_mutation_agent = standardized_agent(
    "validation_mutation", validation_mutation_agent,
    status_key="validation_mutation_status",
    score_key="validation_mutation_score",
)
repair_safety_gate_agent = standardized_agent(
    "repair_safety_gate", repair_safety_gate_agent,
    status_key="refiner_status", rationale_key="refiner_rejection_reason",
)
repair_comparison_agent = standardized_agent(
    "repair_comparison", repair_comparison_agent,
    status_key="repair_validation_status",
    rationale_key="repair_comparison_summary",
)


workflow = StateGraph(E2EEvalState)
workflow.add_node("syntax_node", syntax_linter_agent)
workflow.add_node("requirement_node", requirement_alignment_agent)
workflow.add_node("assertion_node", assertion_quality_agent)
workflow.add_node("hallucination_node", hallucination_smell_agent)
workflow.add_node("maintainability_node", maintainability_agent)

# Local dynamic tools (no Gemini/API calls)
workflow.add_node("execution_node", execution_agent)
workflow.add_node("dynamic_coverage_node", dynamic_coverage_agent)
workflow.add_node("coverage_node", coverage_agent)
workflow.add_node("mutation_node", mutation_agent)

# Conditional LLM readers/reconcilers
workflow.add_node("dynamic_analyst_node", dynamic_evidence_analyst_agent)
workflow.add_node("dynamic_analyst_skip_node", dynamic_analysis_skip_agent)
workflow.add_node("critic_node", critic_agent)
workflow.add_node("critic_skip_node", critic_skip_agent)
workflow.add_node("consensus_node", consensus_agent)
workflow.add_node("refiner_node", refiner_agent)
workflow.add_node("refiner_skip_node", refiner_skip_agent)

# Local repair validation tools (no Gemini/API calls)
workflow.add_node("validation_execution_node", validation_execution_agent)
workflow.add_node("validation_coverage_node", validation_coverage_agent)
workflow.add_node("validation_mutation_node", validation_mutation_agent)
workflow.add_node("repair_safety_gate_node", repair_safety_gate_agent)
workflow.add_node("repair_comparison_node", repair_comparison_agent)

workflow.set_entry_point("syntax_node")
workflow.add_conditional_edges(
    "syntax_node",
    route_after_syntax,
    {"requirement_node": "requirement_node", "consensus_node": "consensus_node"},
)
workflow.add_edge("requirement_node", "assertion_node")
workflow.add_edge("assertion_node", "hallucination_node")
workflow.add_edge("hallucination_node", "maintainability_node")
workflow.add_edge("maintainability_node", "execution_node")
workflow.add_conditional_edges(
    "execution_node",
    route_after_execution,
    {
        "dynamic_coverage_node": "dynamic_coverage_node",
        "dynamic_analyst_node": "dynamic_analyst_node",
        "critic_node": "critic_node",
        "dynamic_analyst_skip_node": "dynamic_analyst_skip_node",
    },
)
workflow.add_edge("dynamic_coverage_node", "coverage_node")
workflow.add_edge("coverage_node", "mutation_node")
workflow.add_conditional_edges(
    "mutation_node",
    route_after_dynamic_tools,
    {
        "dynamic_analyst_node": "dynamic_analyst_node",
        "critic_node": "critic_node",
        "dynamic_analyst_skip_node": "dynamic_analyst_skip_node",
    },
)
workflow.add_conditional_edges(
    "dynamic_analyst_node",
    route_after_dynamic_analyst,
    {"critic_node": "critic_node", "critic_skip_node": "critic_skip_node"},
)
workflow.add_edge("dynamic_analyst_skip_node", "critic_skip_node")
workflow.add_edge("critic_node", "consensus_node")
workflow.add_edge("critic_skip_node", "consensus_node")
workflow.add_conditional_edges(
    "consensus_node",
    route_after_consensus,
    {"refiner_node": "refiner_node", "refiner_skip_node": "refiner_skip_node"},
)
workflow.add_conditional_edges(
    "refiner_node",
    route_after_refiner,
    {"validation_execution_node": "validation_execution_node", "repair_safety_gate_node": "repair_safety_gate_node"},
)
workflow.add_edge("refiner_skip_node", "repair_safety_gate_node")
workflow.add_conditional_edges(
    "validation_execution_node",
    route_after_validation_execution,
    {"validation_coverage_node": "validation_coverage_node", "repair_safety_gate_node": "repair_safety_gate_node"},
)
workflow.add_edge("validation_coverage_node", "validation_mutation_node")
workflow.add_edge("validation_mutation_node", "repair_safety_gate_node")
workflow.add_edge("repair_safety_gate_node", "repair_comparison_node")
workflow.add_edge("repair_comparison_node", END)

app = workflow.compile()
