"""Tests for deterministic branch collection and semantic interpretation."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import e2e_eval.dynamic.branch_coverage as branch_module
from coverage_agent import coverage_agent
from dynamic_agents import execution_agent
from e2e_eval.dynamic.branch_relevance import analyze_branch_relevance


def _report_payload() -> dict:
    return {
        "files": {
            "source_project/app.py": {
                "executed_lines": [1, 2, 3],
                "missing_lines": [4],
                "executed_branches": [[2, 3]],
                "missing_branches": [[2, 4]],
            }
        },
        "totals": {
            "num_statements": 4,
            "covered_lines": 3,
            "num_branches": 2,
            "covered_branches": 1,
        },
    }


def _base_state(source_dir: Path, case_uid: str) -> dict:
    return {
        "syntax_passed": True,
        "enable_dynamic": True,
        "enable_coverage": True,
        "enable_branch_coverage_analysis": False,
        "source_project_dir": str(source_dir),
        "reference_answer": "",
        "reference_network_enabled": False,
        "case_uid": case_uid,
        "benchmark_id": "branch_fixture",
        "excutable_test_test_case": (
            "Feature: branch behavior\n"
            "  Scenario: positive choice\n"
            "    Given the positive branch is selected\n"
        ),
        "executable_test_code": (
            "from behave import given\n"
            "from source_project.app import choose\n\n"
            "@given('the positive branch is selected')\n"
            "def step_positive(context):\n"
            "    assert choose(True) == 'positive'\n"
        ),
        "fine_grained_reqs": "A positive value returns the positive result.",
        "headless": True,
        "execution_timeout_seconds": 30,
        "coverage_timeout_seconds": 30,
        "coverage_branch_enabled": True,
    }


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.html").write_text("<html></html>", encoding="utf-8")
    (source / "app.py").write_text(
        "def choose(value):\n"
        "    if value:\n"
        "        return 'positive'\n"
        "    return 'negative'\n",
        encoding="utf-8",
    )
    return source


def test_successful_coverage_parsing(tmp_path: Path):
    workspace = tmp_path / "workspace"
    source_file = workspace / "source_project" / "app.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(
        "def choose(value):\n"
        "    if value:\n"
        "        return 1\n"
        "    return 0\n",
        encoding="utf-8",
    )
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps(_report_payload()), encoding="utf-8")

    result = branch_module.parse_coverage_report(
        report, workspace=workspace
    )

    assert result["coverage_status"] == "SUCCESS"
    assert result["branch_coverage_percent"] == 50.0
    assert result["covered_branches"] == 1
    assert result["total_branches"] == 2
    assert result["uncovered_branches"] == [{
        "branch_id": "source_project/app.py:2->4",
        "file": "source_project/app.py",
        "from_line": 2,
        "to_line": 4,
        "from_source": "if value:",
        "to_source": "return 0",
    }]


def test_unavailable_coverage_report(tmp_path: Path):
    result = branch_module.parse_coverage_report(
        tmp_path / "missing.json"
    )

    assert result["coverage_status"] == "UNAVAILABLE"
    assert result["branch_coverage_percent"] is None
    assert result["execution_status"] == "REPORT_UNAVAILABLE"


def test_malformed_coverage_report(tmp_path: Path):
    report = tmp_path / "coverage.json"
    report.write_text("{not-json", encoding="utf-8")

    result = branch_module.parse_coverage_report(report)

    assert result["coverage_status"] == "MALFORMED"
    assert result["branch_coverage_percent"] is None
    assert "Malformed coverage report" in result["rationale"]


def test_timeout_is_structured_failure(monkeypatch, tmp_path: Path):
    source = _source(tmp_path)
    state = _base_state(source, "branch-timeout")
    baseline = execution_agent(state)
    assert baseline["execution_status"] == "PASSED"
    state.update(baseline)
    monkeypatch.setattr(branch_module, "coverage_available", lambda: True)

    def timeout_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(branch_module.subprocess, "run", timeout_run)
    result = coverage_agent(state)["branch_coverage_result"]

    assert result["coverage_status"] == "TIMEOUT"
    assert result["execution_status"] == "TIMEOUT"
    assert result["logs"]["timed_out"] is True
    assert result["branch_coverage_percent"] is None


def test_failed_baseline_returns_explicit_unavailable(tmp_path: Path):
    state = _base_state(_source(tmp_path), "branch-failed-baseline")
    state["execution_status"] = "TEST_FAILED"

    result = coverage_agent(state)["branch_coverage_result"]

    assert result["coverage_status"] == "UNAVAILABLE"
    assert result["execution_status"] == "TEST_FAILED"
    assert result["branch_coverage_score"] is None


def test_semantic_agent_rejects_invented_branch_ids(monkeypatch):
    state = {
        "enable_branch_coverage_analysis": True,
        "fine_grained_reqs": "Positive and negative values are classified.",
        "branch_coverage_result": {
            "coverage_status": "SUCCESS",
            "execution_status": "PASSED",
            "branch_coverage_percent": 50.0,
            "covered_branches": 1,
            "total_branches": 2,
            "uncovered_branches": [{
                "branch_id": "app.py:2->4",
                "file": "app.py",
                "from_line": 2,
                "to_line": 4,
            }],
        },
    }
    response = json.dumps({
        "relevant_uncovered_branches": [
            {
                "branch_id": "app.py:2->4",
                "severity": "HIGH",
                "reason": "Negative behavior is required.",
            },
            {
                "branch_id": "invented.py:9->10",
                "severity": "HIGH",
                "reason": "Invented.",
            },
        ],
        "rationale": "The negative branch is requirement-relevant.",
        "branch_coverage_percent": 99,
    })
    result = analyze_branch_relevance(
        state, lambda system_prompt, context: response
    )

    assert result["branch_coverage_percent"] == 50.0
    assert result["branch_coverage_score"] == 50.0
    assert [
        item["branch_id"]
        for item in result["requirement_relevant_uncovered_branches"]
    ] == ["app.py:2->4"]


def test_end_to_end_fixture_detects_uncovered_branch(tmp_path: Path):
    state = _base_state(
        _source(tmp_path), "branch-real-end-to-end-fixture"
    )
    baseline = execution_agent(state)
    assert baseline["execution_status"] == "PASSED"
    state.update(baseline)

    result = coverage_agent(state)["branch_coverage_result"]

    assert result["coverage_status"] == "SUCCESS"
    assert result["execution_status"] == "PASSED"
    assert result["total_branches"] == 2
    assert result["covered_branches"] == 1
    assert result["branch_coverage_percent"] == 50.0
    assert len(result["uncovered_branches"]) == 1
    assert result["uncovered_branches"][0]["from_source"] == "if value:"


def test_graph_places_semantic_agent_before_mutation():
    graph_source = Path("graph.py").read_text(encoding="utf-8")
    assert 'workflow.add_node("coverage_node", coverage_agent)' in graph_source
    assert (
        'workflow.add_edge("coverage_node", '
        '"branch_coverage_analysis_node")' in graph_source
    )
    assert (
        'workflow.add_edge("branch_coverage_analysis_node", '
        '"mutation_planning_node")' in graph_source
    )
    assert (
        'workflow.add_edge("mutation_planning_node", "mutation_node")'
        in graph_source
    )
