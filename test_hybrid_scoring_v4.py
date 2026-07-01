import ast
from pathlib import Path


def _load_consensus_agent():
    source = Path("agents.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    functions = [
        node for node in module.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_as_number", "consensus_agent"}
    ]
    isolated = ast.Module(body=functions, type_ignores=[])
    ast.fix_missing_locations(isolated)
    namespace = {}
    exec(compile(isolated, "agents.py", "exec"), namespace)
    return namespace["consensus_agent"]


def _clean_static_state():
    return {
        "syntax_passed": True,
        "total_requirements_count": 2,
        "covered_requirements": ["r1", "r2"],
        "partially_covered_requirements": [],
        "assertion_score": 100,
        "hallucination_count": 0,
        "test_smells": [],
        "maintainability_score": 100,
        "static_assertions_count": 1,
        "static_actions_count": 1,
    }


def test_hybrid_score_uses_execution_and_relevant_mutation():
    consensus = _load_consensus_agent()
    result = consensus({
        **_clean_static_state(),
        "execution_status": "PASSED",
        "mutation_status": "PASSED",
        "mutation_score": 60.0,
        "mutation_scope_status": "SCOPE_AWARE",
        "relevant_mutation_score": 60.0,
    })
    # 100*.60 + 100*.15 + 60*.25 = 90
    assert result["static_overall_score"] == 100.0
    assert result["overall_score"] == 90.0
    assert result["score_mode"] == "HYBRID_STATIC_EXECUTION_SCOPE_AWARE_MUTATION"


def test_real_execution_failure_caps_score():
    consensus = _load_consensus_agent()
    result = consensus({**_clean_static_state(), "execution_status": "TEST_FAILED"})
    assert result["overall_score"] == 40.0
    assert result["score_mode"] == "EXECUTION_FAILED_CAPPED"


def test_available_branch_coverage_is_a_hybrid_score_component():
    consensus = _load_consensus_agent()
    result = consensus({
        **_clean_static_state(),
        "execution_status": "PASSED",
        "branch_coverage_score": 50.0,
    })
    assert result["overall_score"] == 91.67
    assert result["score_mode"] == "HYBRID_STATIC_EXECUTION_BRANCH_COVERAGE"
    assert result["branch_coverage_included_in_scoring"] is True


def test_harness_error_stays_inconclusive_not_zero():
    consensus = _load_consensus_agent()
    result = consensus({**_clean_static_state(), "execution_status": "HARNESS_BROWSER_ERROR"})
    assert result["overall_score"] == 100.0
    assert result["score_mode"] == "STATIC_ONLY_DYNAMIC_INCONCLUSIVE"
    assert result["branch_coverage_included_in_scoring"] is False


def test_graph_has_dynamic_execution_coverage_mutation_path():
    graph = Path("graph.py").read_text(encoding="utf-8")
    assert 'workflow.add_edge("maintainability_node", "execution_node")' in graph
    assert 'workflow.add_edge("dynamic_coverage_node", "coverage_node")' in graph
    assert (
        'workflow.add_edge("coverage_node", '
        '"branch_coverage_analysis_node")' in graph
    )
    assert (
        'workflow.add_edge("branch_coverage_analysis_node", '
        '"mutation_planning_node")' in graph
    )
    assert (
        'workflow.add_edge("mutation_planning_node", "mutation_node")'
        in graph
    )
    assert '"mutation_node",' in graph
