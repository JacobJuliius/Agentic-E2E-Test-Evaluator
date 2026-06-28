from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from e2e_eval.config import EvaluationConfig
from e2e_eval.reporting import write_evaluation_reports
from e2e_eval.schemas import standardized_agent


def test_config_builds_backward_compatible_graph_state():
    config = EvaluationConfig(
        enable_dynamic=True,
        enable_coverage=True,
        enable_mutation=True,
        mutation_seed=99,
    )
    state = config.build_state({
        "id": "Bench",
        "req_id": "2",
        "test_id": "3",
        "excutable_test_step_code": "assert True",
        "excutable_test_test_case": "Feature: x",
        "reference_answer": "C:/source",
    }, 7)
    assert state["executable_test_code"] == "assert True"
    assert state["case_uid"] == "Bench_req2_test3_row7"
    assert state["enable_coverage"] is True
    assert state["mutation_seed"] == 99


def test_standardized_agent_preserves_raw_output_and_adds_envelope():
    def evaluator(state):
        return {
            "tool_status": "PASSED",
            "tool_score": 75,
            "tool_detail": "Evidence collected.",
            "report_path": "artifacts/report.json",
        }

    wrapped = standardized_agent(
        "tool", evaluator,
        status_key="tool_status",
        score_key="tool_score",
        rationale_key="tool_detail",
    )
    result = wrapped({})
    assert result["tool_score"] == 75
    envelope = result["agent_results"]["tool"]
    assert envelope["status"] == "PASSED"
    assert envelope["score"] == 75.0
    assert envelope["artifacts"]["report_path"] == "artifacts/report.json"


def test_standardized_agent_contains_failure():
    def broken(state):
        raise RuntimeError("boom")

    result = standardized_agent("broken", broken)({})
    assert result["agent_results"]["broken"]["status"] == "failed"
    assert "RuntimeError" in result["broken_failure_reason"]


def test_report_writer_creates_csv_json_and_manifest(tmp_path: Path):
    frame = pd.DataFrame([{"id": "case", "score": 80.0}])
    config = EvaluationConfig(case_sleep_seconds=0)
    paths = write_evaluation_reports(
        frame, tmp_path / "report.csv", config=config
    )
    assert Path(paths["csv"]).is_file()
    assert Path(paths["json"]).is_file()
    assert Path(paths["config"]).is_file()
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert payload[0]["score"] == 80.0


def test_package_tree_exposes_expected_boundaries():
    expected = [
        "e2e_eval/config.py",
        "e2e_eval/schemas/results.py",
        "e2e_eval/schemas/state.py",
        "e2e_eval/reporting/writers.py",
        "e2e_eval/runtime/dependencies.py",
        "e2e_eval/dynamic/mutation.py",
        "e2e_eval/sources/resolver.py",
        "e2e_eval/orchestration/__init__.py",
        "e2e_eval/static/evaluators.py",
    ]
    assert all(Path(path).is_file() for path in expected)
