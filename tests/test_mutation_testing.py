"""Unit and small-fixture tests for the general mutation engine."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from mutation_testing import (
    BooleanNegationOperator,
    ConditionalBoundaryOperator,
    DomAttributeValueOperator,
    EventHandlerOperator,
    MutationRecord,
    MutationCandidate,
    NumericLiteralOperator,
    RelationalOperator,
    StringLiteralOperator,
    UiTextOperator,
    apply_mutation,
    calculate_mutation_metrics,
    discover_mutations,
    run_mutation_campaign,
    validate_mutated_project,
    verdict_for_execution,
)


def _operator_names(operator, source: str, filename: str) -> list[str]:
    return [
        candidate.operator
        for candidate in operator.generate(source, filename)
    ]


def test_numeric_literal_operator_supports_python_and_javascript():
    operator = NumericLiteralOperator()
    assert operator.name in _operator_names(operator, "limit = 10\n", "app.py")
    assert operator.name in _operator_names(operator, "const limit = 10;", "app.js")


def test_string_literal_operator_supports_python_and_javascript():
    operator = StringLiteralOperator()
    assert operator.name in _operator_names(operator, "label = 'Ready'\n", "app.py")
    assert operator.name in _operator_names(operator, 'const label = "Ready";', "app.js")


def test_boolean_negation_operator_supports_python_and_javascript():
    operator = BooleanNegationOperator()
    assert operator.name in _operator_names(operator, "enabled = True\n", "app.py")
    assert operator.name in _operator_names(operator, "const enabled = false;", "app.js")


def test_conditional_boundary_operator_supports_python_and_javascript():
    operator = ConditionalBoundaryOperator()
    assert operator.name in _operator_names(operator, "ok = value >= 3\n", "app.py")
    assert operator.name in _operator_names(operator, "const ok = value <= 3;", "app.js")


def test_relational_operator_supports_python_and_javascript():
    operator = RelationalOperator()
    assert operator.name in _operator_names(operator, "ok = value == 3\n", "app.py")
    assert operator.name in _operator_names(operator, "const ok = value === 3;", "app.js")


def test_event_handler_operator_supports_js_and_inline_html():
    operator = EventHandlerOperator()
    js = "button.addEventListener('click', handleClick);"
    html = '<button onclick="submitForm()">Save</button>'
    assert operator.name in _operator_names(operator, js, "app.js")
    assert operator.name in _operator_names(operator, html, "index.html")


def test_dom_attribute_value_operator_mutates_one_bounded_attribute():
    source = '<button aria-expanded="false" data-state="ready">Save</button>'
    candidates = list(
        DomAttributeValueOperator().generate(source, "index.html")
    )
    assert candidates
    first = candidates[0]
    assert source[first.location["start_offset"]:first.location["end_offset"]] == first.original_code
    assert first.replacement_code != first.original_code


def test_ui_text_operator_ignores_script_and_mutates_visible_text():
    source = "<script>const label = 'Internal';</script><button>Save</button>"
    candidates = list(UiTextOperator().generate(source, "index.html"))
    assert len(candidates) == 1
    assert candidates[0].original_code == "Save"


def test_selection_is_seeded_bounded_and_skips_minified_files(tmp_path: Path):
    (tmp_path / "app.js").write_text(
        "const a = 1; const b = 2; const c = true;",
        encoding="utf-8",
    )
    (tmp_path / "vendor.min.js").write_text("const hidden=1;", encoding="utf-8")
    first = discover_mutations(
        tmp_path,
        max_mutants_per_file=2,
        max_total_mutants=2,
        seed=42,
    )
    second = discover_mutations(
        tmp_path,
        max_mutants_per_file=2,
        max_total_mutants=2,
        seed=42,
    )
    assert first == second
    assert len(first) == 2
    assert all(item.source_file == "app.js" for item in first)
    assert len({item.mutant_id for item in first}) == 2


def test_apply_mutation_changes_only_the_selected_span(tmp_path: Path):
    target = tmp_path / "app.py"
    target.write_text("left = 1\nright = 1\n", encoding="utf-8")
    candidate = next(
        iter(NumericLiteralOperator().generate(target.read_text(), "app.py"))
    )
    apply_mutation(tmp_path, candidate)
    assert target.read_text(encoding="utf-8") == "left = 2\nright = 1\n"


def test_verdict_logic():
    assert verdict_for_execution("PASSED") == "SURVIVED"
    assert verdict_for_execution("TEST_FAILED") == "KILLED"
    assert verdict_for_execution("TIMEOUT") == "TIMEOUT"
    assert verdict_for_execution("HARNESS_BROWSER_ERROR") == "EXECUTION_ERROR"


def _record(verdict: str, operator: str = "OP") -> dict:
    return {
        "operator": operator,
        "execution_verdict": verdict,
    }


def test_invalid_mutants_are_excluded_from_denominator():
    metrics = calculate_mutation_metrics([
        _record("KILLED"),
        _record("SURVIVED"),
        _record("INVALID"),
        _record("INVALID"),
    ])
    assert metrics["total_mutants_generated"] == 4
    assert metrics["valid_mutants"] == 2
    assert metrics["invalid_mutants"] == 2
    assert metrics["mutation_score"] == 50.0
    assert metrics["per_operator_breakdown"]["OP"]["invalid_mutants"] == 2


def test_syntactically_broken_python_mutant_is_invalid(tmp_path: Path):
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    mutant = MutationCandidate(
        mutant_id="M0001",
        operator="TEST_INVALID",
        source_file="app.py",
        location={
            "line": 1,
            "column": 8,
            "start_offset": 8,
            "end_offset": 9,
        },
        original_code="1",
        replacement_code="(",
        mutation_description="Create an invalid fixture mutant.",
    )
    apply_mutation(tmp_path, mutant)
    validation = validate_mutated_project(
        tmp_path, mutant, timeout_seconds=2
    )
    assert validation["valid"] is False
    assert validation["status"] == "SYNTAX_INVALID"


def test_javascript_validation_timeout_marks_mutant_invalid(
    monkeypatch, tmp_path: Path
):
    source = tmp_path / "app.js"
    source.write_text("const value = 1;", encoding="utf-8")
    mutant = next(
        iter(NumericLiteralOperator().generate(source.read_text(), "app.js"))
    )
    apply_mutation(tmp_path, mutant)
    monkeypatch.setattr(shutil, "which", lambda executable: "node")

    def timeout_runner(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    validation = validate_mutated_project(
        tmp_path,
        mutant,
        timeout_seconds=2,
        subprocess_runner=timeout_runner,
    )
    assert validation["valid"] is False
    assert validation["status"] == "VALIDATION_TIMEOUT"


def test_javascript_validation_uses_absolute_path_with_source_cwd(
    monkeypatch, tmp_path: Path
):
    app_dir = tmp_path / "mutant-workspace" / "source_project"
    app_dir.mkdir(parents=True)
    source = app_dir / "script.js"
    source.write_text("const value = 1;", encoding="utf-8")
    mutant = next(
        iter(NumericLiteralOperator().generate(source.read_text(), "script.js"))
    )
    apply_mutation(app_dir, mutant)
    monkeypatch.setattr(shutil, "which", lambda executable: "node")
    observed = {}

    def fake_runner(command, **kwargs):
        observed["command"] = command
        observed["cwd"] = kwargs["cwd"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    validation = validate_mutated_project(
        app_dir,
        mutant,
        timeout_seconds=2,
        subprocess_runner=fake_runner,
    )

    checked_path = Path(observed["command"][-1])
    assert validation["valid"] is True
    assert checked_path.is_absolute()
    assert checked_path == source.resolve()
    assert Path(observed["cwd"]) == app_dir


def _fixture_callbacks(tmp_path: Path, execution_status: str):
    cleanup_root = tmp_path / "workspaces"
    cleanup_root.mkdir()

    def create_workspace(state, label, reference_dir):
        workspace = cleanup_root / label
        app_dir = workspace / "app"
        artifact_dir = tmp_path / "results" / label
        shutil.copytree(reference_dir, app_dir)
        artifact_dir.mkdir(parents=True)
        return SimpleNamespace(
            workspace=workspace,
            app_dir=app_dir,
            app_index=app_dir / "index.html",
            artifact_dir=artifact_dir,
        )

    def write_test_project(prepared, feature_text, test_code):
        return None

    def run_test(prepared, state, timeout):
        return {
            "execution_status": execution_status,
            "execution_stdout_tail": "fixture stdout",
            "execution_stderr_tail": "fixture stderr",
            "execution_duration_seconds": 0.01,
            "execution_command": "python -m behave",
            "execution_environment": {"fixture": True},
        }

    return cleanup_root, create_workspace, write_test_project, run_test


def _campaign_state(tmp_path: Path) -> dict:
    return {
        "case_uid": "fixture-case",
        "execution_status": "PASSED",
        "execution_command": "python -m behave",
        "execution_artifact_dir": str(tmp_path / "baseline"),
        "excutable_test_test_case": "Feature: fixture",
        "max_mutants": 1,
        "mutation_max_mutants_per_file": 1,
        "mutation_seed": 7,
        "mutation_include_patterns": ["**/*.py"],
        "mutant_timeout_seconds": 2,
    }


def _fixture_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "index.html").write_text("<html></html>", encoding="utf-8")
    (project / "logic.py").write_text("message = 'ready'\n", encoding="utf-8")
    return project


def test_small_fixture_killed_mutant_and_score(tmp_path: Path):
    project = _fixture_project(tmp_path)
    cleanup_root, create, write, run = _fixture_callbacks(
        tmp_path, "TEST_FAILED"
    )
    result = run_mutation_campaign(
        _campaign_state(tmp_path),
        "generated test code",
        project,
        create_workspace=create,
        write_test_project=write,
        run_test=run,
        report_filename="mutation.json",
        run_namespace="fixture",
        cleanup_root=cleanup_root,
    )
    assert result["killed_mutants"] == 1
    assert result["valid_mutants"] == 1
    assert result["invalid_mutants"] == 0
    assert result["mutation_score"] == 100.0
    assert project.joinpath("logic.py").read_text() == "message = 'ready'\n"
    assert Path(result["mutation_report_path"]).is_file()


def test_mocked_mutant_timeout_is_separate_and_not_scored(tmp_path: Path):
    project = _fixture_project(tmp_path)
    cleanup_root, create, write, run = _fixture_callbacks(
        tmp_path, "TIMEOUT"
    )
    result = run_mutation_campaign(
        _campaign_state(tmp_path),
        "generated test code",
        project,
        create_workspace=create,
        write_test_project=write,
        run_test=run,
        report_filename="timeout-mutation.json",
        run_namespace="fixture-timeout",
        cleanup_root=cleanup_root,
    )
    assert result["invalid_mutants"] == 0
    assert result["timeout_mutants"] == 1
    assert result["valid_mutants"] == 0
    assert result["mutation_score"] is None


def test_dynamic_agent_requires_passing_original_baseline(monkeypatch):
    import dynamic_agents

    monkeypatch.setattr(
        dynamic_agents,
        "_run_general_mutation_evaluation",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("campaign must not run")
        ),
    )
    result = dynamic_agents.mutation_agent({
        "enable_mutation": True,
        "execution_status": "TEST_FAILED",
    })
    assert result["mutation_status"] == "SKIPPED_BASELINE_NOT_PASS"


def test_dynamic_agent_delegates_to_general_engine(monkeypatch):
    import dynamic_agents

    expected = {
        "mutation_status": "PASSED",
        "mutation_score": 100.0,
    }
    monkeypatch.setattr(
        dynamic_agents,
        "_run_general_mutation_evaluation",
        lambda *args, **kwargs: expected,
    )
    result = dynamic_agents.mutation_agent({
        "enable_mutation": True,
        "execution_status": "PASSED",
        "executable_test_code": "assert True",
    })
    assert result == expected


def test_graph_runs_mutation_after_coverage():
    graph_source = Path("graph.py").read_text(encoding="utf-8")
    assert (
        'workflow.add_edge("coverage_node", '
        '"branch_coverage_analysis_node")' in graph_source
    )
    assert (
        'workflow.add_edge("branch_coverage_analysis_node", '
        '"mutation_planning_node")'
        in graph_source
    )
    assert (
        'workflow.add_edge("mutation_planning_node", "mutation_node")'
        in graph_source
    )


def test_consensus_accepts_general_mutation_metric():
    from tests.test_v5_1_scope_logic import load_agents_module

    agents = load_agents_module()
    result = agents.consensus_agent({
        "syntax_passed": True,
        "total_requirements_count": 1,
        "covered_requirements": ["R1"],
        "partially_covered_requirements": [],
        "assertion_score": 100,
        "maintainability_score": 100,
        "hallucination_count": 0,
        "test_smells": [],
        "static_actions_count": 1,
        "static_assertions_count": 1,
        "execution_status": "PASSED",
        "mutation_scope_status": "GENERAL_MUTATION_SET",
        "relevant_mutation_score": 50.0,
    })
    assert "GENERAL_MUTATION" in result["score_mode"]
    assert result["overall_score"] < result["static_overall_score"]
