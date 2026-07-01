"""No-browser smoke tests for V5 local dynamic helpers.

Run:
    pytest -q tests/test_v5_local_tools.py
"""
from dynamic_agents import (
    _normalise_fixed_code,
    _summarize_behave_report,
    repair_comparison_agent,
)


def test_failed_behave_report_yields_partial_step_evidence():
    report = [{
        "elements": [{
            "steps": [
                {"keyword": "Given ", "name": "the app is open", "result": {"status": "passed"}},
                {"keyword": "When ", "name": "the user drags an item", "result": {"status": "failed"}},
                {"keyword": "Then ", "name": "the total changes", "result": {"status": "skipped"}},
            ]
        }]
    }]
    summary = _summarize_behave_report(report)
    assert summary["steps_total"] == 3
    assert summary["steps_passed"] == 1
    assert summary["steps_failed"] == 1
    assert summary["dynamic_coverage_score"] < 100


def test_fixed_code_normalizer_removes_markdown_fence():
    assert _normalise_fixed_code("```python\nassert actual == expected\n```") == "assert actual == expected"


def test_before_after_comparison_detects_mutation_improvement():
    result = repair_comparison_agent({
        "execution_status": "PASSED",
        "validation_execution_status": "PASSED",
        "dynamic_coverage_score": 100.0,
        "validation_dynamic_coverage_score": 100.0,
        "mutation_score": 33.33,
        "validation_mutation_score": 66.67,
    })
    assert result["repair_validation_status"] == "IMPROVED_FAULT_DETECTION"
    assert result["repair_delta_mutation"] == 33.34


def test_before_after_comparison_detects_execution_regression():
    result = repair_comparison_agent({
        "execution_status": "PASSED",
        "validation_execution_status": "TEST_FAILED",
        "dynamic_coverage_score": 100.0,
        "validation_dynamic_coverage_score": 40.0,
        "mutation_score": 50.0,
        "validation_mutation_score": None,
    })
    assert result["repair_validation_status"] == "REGRESSED_EXECUTABILITY"
