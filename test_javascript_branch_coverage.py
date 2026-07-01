"""Tests for executable JavaScript frontend branch coverage."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import e2e_eval.dynamic.branch_coverage as branch_module
from coverage_agent import coverage_agent
from dynamic_agents import execution_agent


FIXTURE = Path("tests/fixtures/js_branch_app").resolve()


def _istanbul_payload() -> dict:
    return {
        "script.js": {
            "path": "script.js",
            "statementMap": {
                "0": {
                    "start": {"line": 2, "column": 2},
                    "end": {"line": 6, "column": 3},
                }
            },
            "s": {"0": 1},
            "branchMap": {
                "0": {
                    "type": "if",
                    "locations": [
                        {
                            "start": {"line": 2, "column": 2},
                            "end": {"line": 4, "column": 3},
                        },
                        {
                            "start": {"line": 4, "column": 9},
                            "end": {"line": 6, "column": 3},
                        },
                    ],
                }
            },
            "b": {"0": [1, 0]},
        }
    }


def _state(case_uid: str) -> dict:
    return {
        "syntax_passed": True,
        "enable_dynamic": True,
        "enable_coverage": True,
        "enable_branch_coverage_analysis": False,
        "source_project_dir": str(FIXTURE),
        "reference_answer": "",
        "reference_network_enabled": False,
        "case_uid": case_uid,
        "benchmark_id": "js_branch_fixture",
        "excutable_test_test_case": (
            "Feature: JavaScript branch behavior\n"
            "  Scenario: positive choice\n"
            "    Given the JavaScript branch app is open\n"
            "    When the positive choice is clicked\n"
            "    Then the positive result is displayed\n"
        ),
        "executable_test_code": (
            "from behave import given, when, then\n"
            "from selenium import webdriver\n"
            "from selenium.webdriver.common.by import By\n\n"
            "@given('the JavaScript branch app is open')\n"
            "def open_app(context):\n"
            "    context.driver = webdriver.Chrome()\n"
            "    context.driver.get('file://index.html')\n\n"
            "@when('the positive choice is clicked')\n"
            "def click_positive(context):\n"
            "    context.driver.find_element(By.ID, 'positive').click()\n\n"
            "@then('the positive result is displayed')\n"
            "def assert_positive(context):\n"
            "    actual = context.driver.find_element(By.ID, 'result').text\n"
            "    assert actual == 'positive'\n"
        ),
        "headless": True,
        "execution_timeout_seconds": 45,
        "coverage_timeout_seconds": 45,
        "coverage_branch_enabled": True,
    }


def test_istanbul_parser_detects_one_covered_and_one_uncovered_branch():
    result = branch_module.parse_istanbul_coverage_payload(
        _istanbul_payload(), app_dir=FIXTURE
    )

    assert result["covered_branches"] == 1
    assert result["total_branches"] == 2
    assert result["branch_coverage_percent"] == 50.0
    assert len(result["uncovered_branches"]) == 1
    assert result["uncovered_branches"][0]["branch_kind"] == "if"
    assert result["uncovered_branches"][0]["from_source"] == "} else {"


def test_javascript_adapter_returns_unavailable_without_node(
    monkeypatch,
):
    state = _state("js-adapter-node-unavailable")
    state["execution_status"] = "PASSED"
    monkeypatch.setattr(branch_module.shutil, "which", lambda name: None)

    result = coverage_agent(state)["branch_coverage_result"]

    assert result["coverage_status"] == "UNAVAILABLE"
    assert result["execution_status"] == "INSTRUMENTATION_DEPENDENCY_MISSING"
    assert result["coverage_adapter"] == "selenium_istanbul"
    assert result["source_language"] == "javascript"
    assert result["branch_coverage_percent"] is None


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="Node.js is required for the real JavaScript coverage fixture.",
)
def test_real_selenium_fixture_covers_one_branch_and_misses_one():
    probe = subprocess.run(
        [
            "node",
            "-e",
            "require('istanbul-lib-instrument'); process.stdout.write('ok')",
        ],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if probe.returncode != 0:
        pytest.skip("istanbul-lib-instrument is unavailable to Node.js.")

    state = _state("js-branch-real-selenium-fixture")
    baseline = execution_agent(state)
    if baseline["execution_status"] == "HARNESS_BROWSER_ERROR":
        pytest.skip("Chrome/Selenium browser instrumentation is unavailable.")
    assert baseline["execution_status"] == "PASSED"
    state.update(baseline)

    result = coverage_agent(state)["branch_coverage_result"]

    assert result["coverage_status"] == "SUCCESS"
    assert result["coverage_adapter"] == "selenium_istanbul"
    assert result["source_language"] == "javascript"
    assert result["instrumentation_status"] == "COLLECTED"
    assert result["covered_branches"] >= 1
    assert result["total_branches"] >= 2
    assert result["branch_coverage_percent"] is not None
    assert any(
        branch["branch_kind"] == "if"
        for branch in result["uncovered_branches"]
    )
    assert Path(result["artifact_paths"]["raw_coverage"]).is_file()
