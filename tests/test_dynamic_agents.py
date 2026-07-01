import json
from pathlib import Path

from dynamic_agents import (
    _apply_mutant,
    _chrome_harness_shim,
    _classify_result,
    _discover_mutants,
    _summarize_behave_report,
    dynamic_coverage_agent,
    execution_agent,
    mutation_agent,
)


def test_classify_passed():
    assert _classify_result(0, "1 scenario passed", "") == "PASSED"


def test_browser_shim_routes_loopback_entrypoint_to_isolated_app():
    shim = _chrome_harness_shim()
    assert '"localhost", "127.0.0.1", "::1"' in shim
    assert '_parsed.path.rstrip("/") in {"", "/index.html"}' in shim
    assert "E2E_APP_INDEX_URI" in shim


def test_missing_inputs_is_harness_error_not_test_failure():
    result = execution_agent({"syntax_passed": True, "enable_dynamic": True})
    assert result["execution_status"] == "HARNESS_INPUT_ERROR"


def test_syntax_failure_skips_execution():
    assert execution_agent({"syntax_passed": False})["execution_status"] == "SKIPPED_SYNTAX_FAILED"


def test_dynamic_coverage_by_bdd_phase():
    report = [{
        "elements": [{"steps": [
            {"keyword": "Given", "result": {"status": "passed"}},
            {"keyword": "When", "result": {"status": "passed"}},
            {"keyword": "Then", "result": {"status": "passed"}},
            {"keyword": "And", "result": {"status": "passed"}},
        ]}]
    }]
    summary = _summarize_behave_report(report)
    assert summary["steps_total"] == 4
    assert summary["dynamic_coverage_score"] == 100.0
    assert summary["action_step_coverage"] == 100.0
    assert summary["oracle_step_coverage"] == 100.0


def test_dynamic_coverage_requires_report_when_baseline_failed():
    result = dynamic_coverage_agent({"execution_status": "TEST_FAILED"})
    assert result["dynamic_coverage_status"] == "INCONCLUSIVE_REPORT_MISSING"


def test_dynamic_coverage_reads_json_report(tmp_path: Path):
    report = [{"elements": [{"steps": [
        {"keyword": "Given", "result": {"status": "passed"}},
        {"keyword": "When", "result": {"status": "passed"}},
        {"keyword": "Then", "result": {"status": "passed"}},
    ]}]}]
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    result = dynamic_coverage_agent({"execution_status": "PASSED", "behave_report_path": str(path)})
    assert result["dynamic_coverage_status"] == "PASSED"
    assert result["dynamic_steps_passed"] == 3


def test_mutation_discovery_and_apply(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        '<div data-testid="price">$40</div><script>if (ready && total >= 0) ok = true;</script>',
        encoding="utf-8",
    )
    mutants = _discover_mutants(tmp_path, max_mutants=20)
    assert any(mutant.operator == "PRICE_PLUS_ONE" for mutant in mutants)
    assert any(mutant.original == "&&" for mutant in mutants)
    price_mutant = next(mutant for mutant in mutants if mutant.operator == "PRICE_PLUS_ONE")
    _apply_mutant(tmp_path, price_mutant)
    assert "$41" in (tmp_path / "index.html").read_text(encoding="utf-8")


def test_mutation_is_explicit_opt_in():
    result = mutation_agent({"execution_status": "PASSED"})
    assert result["mutation_status"] == "SKIPPED_DISABLED"


def test_mutation_skips_without_baseline_pass():
    result = mutation_agent({"execution_status": "TIMEOUT", "enable_mutation": True})
    assert result["mutation_status"] == "SKIPPED_BASELINE_NOT_PASS"
