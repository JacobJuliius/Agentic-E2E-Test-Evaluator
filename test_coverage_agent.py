"""Unit tests for the deterministic coverage.py agent.

Subprocess execution is mocked: these tests validate orchestration, status
classification, report parsing, and graceful failure without launching Chrome.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import coverage_agent as module


def _state(source_dir: Path) -> dict:
    return {
        "syntax_passed": True,
        "enable_coverage": True,
        "coverage_source_dir": str(source_dir),
        "coverage_branch_enabled": True,
        "coverage_timeout_seconds": 10,
        "case_uid": "coverage-unit-test",
        "excutable_test_test_case": (
            "Feature: sample\nScenario: sample\nGiven the app is open"
        ),
        "executable_test_code": (
            "from behave import given\n"
            "@given('the app is open')\n"
            "def step(context):\n"
            "    assert True\n"
        ),
        "headless": True,
    }


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.html").write_text("<html></html>", encoding="utf-8")
    (source / "app.py").write_text(
        "def choose(value):\n"
        "    if value:\n"
        "        return 1\n"
        "    return 0\n",
        encoding="utf-8",
    )
    return source


def _completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(
        returncode=returncode, stdout=stdout, stderr=stderr
    )


def _report_payload() -> dict:
    return {
        "files": {
            "app.py": {
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


def test_successful_coverage_collection(monkeypatch, tmp_path: Path):
    source = _source(tmp_path)
    monkeypatch.setattr(module, "_coverage_available", lambda: True)

    def fake_run(command, **kwargs):
        if "json" in command:
            report_path = Path(command[command.index("-o") + 1])
            report_path.write_text(
                json.dumps(_report_payload()), encoding="utf-8"
            )
        return _completed(stdout="ok")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.coverage_agent(_state(source))

    assert result["coverage_status"] == "success"
    assert result["coverage_execution_status"] == "test_passed"
    assert result["total_line_coverage"] == 75.0
    assert result["total_branch_coverage"] == 50.0
    assert result["missing_branches"]["app.py"] == [[2, 4]]
    assert result["source_files_measured"] == ["app.py"]
    assert result["coverage_result"]["execution_status"] == "test_passed"
    assert "command_run" in result["coverage_result"]


def test_test_failure_retains_partial_coverage(monkeypatch, tmp_path: Path):
    source = _source(tmp_path)
    monkeypatch.setattr(module, "_coverage_available", lambda: True)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _completed(returncode=1, stderr="scenario failed")
        report_path = Path(command[command.index("-o") + 1])
        report_path.write_text(json.dumps(_report_payload()), encoding="utf-8")
        return _completed()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.coverage_agent(_state(source))

    assert result["coverage_status"] == "success"
    assert result["coverage_execution_status"] == "test_failed"
    assert "partial coverage evidence" in result["coverage_failure_reason"]


def test_timeout_is_structured_failure(monkeypatch, tmp_path: Path):
    source = _source(tmp_path)
    monkeypatch.setattr(module, "_coverage_available", lambda: True)

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.coverage_agent(_state(source))

    assert result["coverage_status"] == "failed"
    assert result["coverage_execution_status"] == "timeout"
    assert "timed out" in result["coverage_failure_reason"]


def test_missing_coverage_dependency_skips(monkeypatch, tmp_path: Path):
    source = _source(tmp_path)
    monkeypatch.setattr(module, "_coverage_available", lambda: False)
    result = module.coverage_agent(_state(source))

    assert result["coverage_status"] == "skipped"
    assert result["coverage_execution_status"] == "dependency_missing"
    assert "not installed" in result["coverage_failure_reason"]


def test_missing_source_directory_skips(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(module, "_coverage_available", lambda: True)
    result = module.coverage_agent(_state(tmp_path / "missing"))

    assert result["coverage_status"] == "skipped"
    assert result["coverage_execution_status"] == "source_missing"
    assert "does not exist" in result["coverage_failure_reason"]


def test_graph_places_coverage_before_mutation():
    graph_source = Path("graph.py").read_text(encoding="utf-8")
    assert 'workflow.add_node("coverage_node", coverage_agent)' in graph_source
    assert 'workflow.add_edge("dynamic_coverage_node", "coverage_node")' in graph_source
    assert 'workflow.add_edge("coverage_node", "mutation_node")' in graph_source
