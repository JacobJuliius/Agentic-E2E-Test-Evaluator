from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from e2e_eval.dynamic.mutation_analysis import analyze_survived_mutants
from e2e_eval.dynamic.mutation_operators import default_operator_registry
from e2e_eval.dynamic.mutation_operators import (
    select_proposed_candidates_with_lifecycle,
)
from e2e_eval.dynamic.mutation_planner import (
    plan_mutations,
    validate_proposals,
)
from e2e_eval.dynamic.mutation_runner import run_single_mutant
from mutation_testing import (
    MutationCandidate,
    calculate_mutation_metrics,
    calculate_relevant_mutation_metrics,
    classify_mutant_relevance,
    run_mutation_campaign,
)


def _patch(replacement: str = "2") -> MutationCandidate:
    return MutationCandidate(
        mutant_id="M0001",
        operator="NUMERIC_LITERAL_PERTURBATION",
        source_file="script.js",
        location={
            "line": 1,
            "column": 14,
            "start_offset": 14,
            "end_offset": 15,
        },
        original_code="1",
        replacement_code=replacement,
        mutation_description="fixture patch",
    )


def _prepared(tmp_path: Path) -> SimpleNamespace:
    workspace = tmp_path / "artifacts" / "workspaces" / "M0001"
    app = workspace / "source_project"
    artifact = tmp_path / "artifacts" / "results" / "M0001"
    app.mkdir(parents=True)
    artifact.mkdir(parents=True)
    (app / "script.js").write_text("const value = 1;", encoding="utf-8")
    return SimpleNamespace(
        workspace=workspace,
        app_dir=app,
        artifact_dir=artifact,
    )


def _run(tmp_path: Path, status: str, patch: MutationCandidate | None = None):
    prepared = _prepared(tmp_path)

    def apply(app_dir, candidate):
        target = app_dir / candidate.source_file
        source = target.read_text(encoding="utf-8")
        start = candidate.location["start_offset"]
        end = candidate.location["end_offset"]
        if source[start:end] != candidate.original_code:
            raise ValueError("invalid patch")
        target.write_text(
            source[:start] + candidate.replacement_code + source[end:],
            encoding="utf-8",
        )

    def validate(*args, **kwargs):
        return {
            "valid": True,
            "status": "VALID",
            "commands": [],
            "failure_reason": "",
        }

    result = run_single_mutant(
        state={"excutable_test_test_case": "Feature: fixture"},
        patch=patch or _patch(),
        prepared=prepared,
        test_code="original generated test",
        timeout_seconds=2,
        project_validation_command=(),
        apply_patch=apply,
        validate_project=validate,
        write_test_project=lambda *args: None,
        run_test=lambda *args: {
            "execution_status": status,
            "execution_return_code": 0 if status == "PASSED" else 1,
            "execution_stdout": "full stdout",
            "execution_stderr": "full stderr",
            "execution_duration_seconds": 0.01,
            "execution_command": "fixture-runner",
            "execution_environment": {"fixture": True},
        },
    )
    return prepared, result


@pytest.mark.parametrize(
    ("status", "verdict"),
    [
        ("TEST_FAILED", "KILLED"),
        ("PASSED", "SURVIVED"),
        ("TIMEOUT", "TIMEOUT"),
    ],
)
def test_runner_verdicts_and_full_log_artifacts(
    tmp_path: Path, status: str, verdict: str
):
    _, result = _run(tmp_path, status)
    assert result["execution_verdict"] == verdict
    assert Path(result["stdout_path"]).read_text() == "full stdout"
    assert Path(result["stderr_path"]).read_text() == "full stderr"
    assert Path(result["patch_path"]).is_file()


def test_runner_invalid_patch_is_not_executed(tmp_path: Path):
    prepared = _prepared(tmp_path)
    mismatch = _patch()
    mismatch = MutationCandidate(
        **{
            **mismatch.__dict__,
            "original_code": "999",
        }
    )
    result = run_single_mutant(
        state={"excutable_test_test_case": "Feature: fixture"},
        patch=mismatch,
        prepared=prepared,
        test_code="original generated test",
        timeout_seconds=2,
        project_validation_command=(),
        apply_patch=lambda app_dir, candidate: (_ for _ in ()).throw(
            ValueError("source span mismatch")
        ),
        validate_project=lambda *args, **kwargs: pytest.fail(
            "invalid patch must not validate"
        ),
        write_test_project=lambda *args: None,
        run_test=lambda *args: pytest.fail("invalid mutant must not execute"),
    )
    assert result["execution_verdict"] == "INVALID"
    assert result["execution_status"] == "PATCH_INVALID"


def test_runner_syntax_invalid_mutant_is_not_executed(tmp_path: Path):
    prepared = _prepared(tmp_path)
    result = run_single_mutant(
        state={"excutable_test_test_case": "Feature: fixture"},
        patch=_patch(),
        prepared=prepared,
        test_code="original generated test",
        timeout_seconds=2,
        project_validation_command=(),
        apply_patch=lambda *args: None,
        validate_project=lambda *args, **kwargs: {
            "valid": False,
            "status": "SYNTAX_INVALID",
            "commands": [],
            "failure_reason": "invalid syntax",
        },
        write_test_project=lambda *args: None,
        run_test=lambda *args: pytest.fail("invalid mutant must not execute"),
    )
    assert result["execution_verdict"] == "INVALID"


def test_each_mutant_workspace_is_isolated_from_source(tmp_path: Path):
    original = tmp_path / "reference"
    original.mkdir()
    (original / "script.js").write_text("const value = 1;", encoding="utf-8")
    first = _prepared(tmp_path / "first")
    second = _prepared(tmp_path / "second")
    shutil.copy2(original / "script.js", first.app_dir / "script.js")
    shutil.copy2(original / "script.js", second.app_dir / "script.js")
    (first.app_dir / "script.js").write_text("const value = 2;", encoding="utf-8")
    assert (original / "script.js").read_text() == "const value = 1;"
    assert (second.app_dir / "script.js").read_text() == "const value = 1;"


def test_operator_registry_contains_all_required_families():
    registry = default_operator_registry()
    assert set(registry.families()) == {
        "comparison_boundary",
        "boolean_conditional_negation",
        "event_handler",
        "arithmetic_constant",
        "displayed_text_value",
        "dom_attribute",
        "state_update",
    }
    conditional = list(
        registry.for_families(["boolean_conditional_negation"])[1].generate(
            "if (enabled) { submit(); }", "script.js"
        )
    )
    state_updates = list(
        registry.for_families(["state_update"])[0].generate(
            "count = count + 1;", "script.js"
        )
    )
    assert conditional and conditional[0].replacement_code == "!(enabled)"
    assert state_updates and state_updates[0].replacement_code == "count"


def test_aggregation_five_way_math_and_operator_stats():
    records = [
        {"operator": "A", "execution_verdict": verdict}
        for verdict in (
            "KILLED", "SURVIVED", "INVALID", "TIMEOUT", "EXECUTION_ERROR"
        )
    ]
    result = calculate_mutation_metrics(records)
    assert result["mutation_score"] == 50.0
    assert result["total_mutants"] == 5
    assert result["timeout"] == 1
    assert result["execution_error"] == 1
    assert result["operator_stats"]["A"]["valid_mutants"] == 2


def test_planner_rejects_file_write_commands_and_traversal(tmp_path: Path):
    (tmp_path / "script.js").write_text("if (value >= 1) {}", encoding="utf-8")
    payload = {"proposals": [
        {
            "mutation_target": "boundary",
            "source_file": "script.js",
            "line": 1,
            "operator_family": "comparison_boundary",
            "requirement_relevance": "required limit",
            "rationale": "checks the edge",
            "confidence": 0.9,
        },
        {
            "mutation_target": "unsafe",
            "source_file": "../outside.js",
            "line": 1,
            "operator_family": "state_update",
            "requirement_relevance": "x",
            "rationale": "x",
            "confidence": 1,
            "command": "write file",
        },
    ]}
    proposals = validate_proposals(payload, tmp_path, max_proposals=5)
    assert len(proposals) == 1
    assert proposals[0].source_file == "script.js"


def test_planner_and_analysis_unavailable_retain_deterministic_contract(
    tmp_path: Path,
):
    (tmp_path / "script.js").write_text("let value = 1;", encoding="utf-8")
    planned = plan_mutations(
        requirements="R1",
        generated_test="assert value",
        source_dir=tmp_path,
        source_metadata={},
        invoke_model=None,
        max_proposals=1,
    )
    assert planned["mutation_planning_status"] == "ANALYSIS_UNAVAILABLE"

    analyzed = analyze_survived_mutants(
        requirements="R1",
        test_code="assert value",
        records=[{"mutant_id": "M1", "execution_verdict": "SURVIVED"}],
        invoke_model=None,
    )
    assert analyzed["mutation_analysis_status"] == "ANALYSIS_UNAVAILABLE"
    assert analyzed["mutation_survivor_analyses"][0]["likely_requirement_gap"] is None


def test_survivor_analysis_cannot_override_runner_verdict():
    result = analyze_survived_mutants(
        requirements="Saving must update the visible status.",
        test_code="click save",
        records=[{
            "mutant_id": "M1",
            "execution_verdict": "SURVIVED",
            "operator": "STATE_UPDATE_MUTATION",
        }],
        invoke_model=lambda *args: json.dumps({"survivors": [{
            "mutant_id": "M1",
            "likely_requirement_gap": True,
            "reason": "No status assertion.",
            "missing_observation": "Visible saved state.",
            "execution_verdict": "KILLED",
        }]}),
    )
    assert result["mutation_survivor_analyses"][0]["execution_verdict"] == "SURVIVED"


def test_graph_mutation_agents_skip_when_disabled_or_baseline_unavailable():
    import dynamic_agents

    planned = dynamic_agents.mutation_planning_agent({
        "enable_mutation": False,
        "execution_status": "PASSED",
    })
    assert planned["mutation_planning_status"] == "SKIPPED_DISABLED"
    unavailable = dynamic_agents.mutation_planning_agent({
        "enable_mutation": True,
        "execution_status": "TEST_FAILED",
    })
    assert unavailable["mutation_planning_status"] == "SKIPPED_BASELINE_NOT_PASS"


def test_stylesheet_rel_proposal_is_rejected_as_nonfunctional(
    tmp_path: Path,
):
    source = (
        '<link rel="stylesheet" href="https://cdn.example/app.css">\n'
        '<button id="save">Save</button>'
    )
    (tmp_path / "index.html").write_text(source, encoding="utf-8")
    proposals = [{
        "proposal_id": "P0001",
        "mutation_target": "stylesheet relation",
        "source_file": "index.html",
        "line_start": 1,
        "line_end": 1,
        "operator_family": "dom_attribute",
        "confidence": 1.0,
    }]
    candidates, lifecycle = select_proposed_candidates_with_lifecycle(
        tmp_path, proposals, max_total_mutants=3
    )
    assert candidates == []
    assert lifecycle[0]["status"] == "REJECTED"
    assert "stylesheet" in lifecycle[0]["rejection_reason"].lower()

    rel_start = source.index("stylesheet")
    mutant = MutationCandidate(
        mutant_id="M1",
        operator="DOM_ATTRIBUTE_VALUE_MUTATION",
        source_file="index.html",
        location={
            "line": 1,
            "column": rel_start,
            "start_offset": rel_start,
            "end_offset": rel_start + len("stylesheet"),
        },
        original_code="stylesheet",
        replacement_code="stylesheet__MUTATED__",
        mutation_description="fixture",
        proposal_id="P0001",
    )
    relevance = classify_mutant_relevance(
        mutant,
        source,
        {"has_concrete_scope": True, "scenario_literals": ["save"]},
    )
    assert relevance["scope_relation"] == "OUT_OF_SCOPE"
    metrics = calculate_relevant_mutation_metrics([{
        **mutant.__dict__,
        "execution_verdict": "SURVIVED",
        "scope_relation": "OUT_OF_SCOPE",
        "included_in_raw_score": False,
        "included_in_relevant_score": False,
    }])
    assert metrics["relevant_mutants_total"] == 0


def test_baseline_passing_fixture_generates_multiple_distinct_mutants(
    tmp_path: Path,
):
    project = tmp_path / "project"
    project.mkdir()
    (project / "index.html").write_text(
        """
<button id="save" data-state="ready">Save</button>
<p id="status">Ready</p>
<script>
let count = 1;
let enabled = true;
if (count >= 1) { count = count + 1; }
document.getElementById("save").onclick = function () {
  document.getElementById("status").textContent = "Saved";
};
</script>
""".strip(),
        encoding="utf-8",
    )
    workspace_root = tmp_path / "artifacts" / "workspaces"
    workspace_root.mkdir(parents=True)

    def create_workspace(state, label, reference_dir):
        workspace = workspace_root / label
        app_dir = workspace / "source_project"
        artifact_dir = tmp_path / "artifacts" / "results" / label
        shutil.copytree(reference_dir, app_dir)
        artifact_dir.mkdir(parents=True)
        return SimpleNamespace(
            workspace=workspace,
            app_dir=app_dir,
            app_index=app_dir / "index.html",
            artifact_dir=artifact_dir,
        )

    result = run_mutation_campaign(
        {
            "case_uid": "multi-fixture",
            "execution_status": "PASSED",
            "execution_command": "fixture",
            "excutable_test_test_case": 'Scenario: click "save"',
            "max_mutants": 5,
            "mutation_max_mutants_per_file": 20,
            "mutation_seed": 17,
            "mutant_timeout_seconds": 2,
            "mutation_planning_status": "PLANNED",
            "mutation_proposals": [{
                "proposal_id": "P0001",
                "mutation_target": "impossible line match",
                "source_file": "index.html",
                "line_start": 1,
                "line_end": 1,
                "operator_family": "state_update",
                "confidence": 1.0,
            }],
        },
        "click save and assert Saved",
        project,
        create_workspace=create_workspace,
        write_test_project=lambda *args: None,
        run_test=lambda *args: {
            "execution_status": "PASSED",
            "execution_return_code": 0,
            "execution_stdout": "ok",
            "execution_stderr": "",
            "execution_duration_seconds": 0.01,
            "execution_command": "fixture",
            "execution_environment": {},
        },
        report_filename="mutation_report.json",
        run_namespace="fixture",
        cleanup_root=workspace_root,
    )
    assert 3 <= result["total_mutants_generated"] <= 5
    identities = {
        (
            item["source_file"],
            item["location"]["start_offset"],
            item["operator"],
        )
        for item in result["mutation_records"]
    }
    assert len(identities) == result["total_mutants_generated"]
    assert result["operator_stats"]
    assert result["requirement_relevance_breakdown"]
    assert all(
        item["proposal_id"] for item in result["mutation_records"]
    )
    assert all(
        "included_in_raw_score" in item
        and "included_in_relevant_score" in item
        and "exclusion_reason" in item
        and "candidate_source" in item
        for item in result["mutation_records"]
    )
    assert result["mutation_candidate_sources"]

    rejected = next(
        item for item in result["mutation_proposal_lifecycle"]
        if item["proposal_id"] == "P0001"
    )
    assert rejected["status"] == "REJECTED"
    assert rejected["rejection_reason"]
    report = json.loads(
        Path(result["mutation_report_path"]).read_text(encoding="utf-8")
    )
    assert report["planning"]["proposal_lifecycle"]
    assert any(
        item["status"] == "EXECUTED"
        for item in report["planning"]["proposal_lifecycle"]
    )


def test_graph_state_keeps_mutation_aggregates_for_csv_export():
    graph_source = Path("graph.py").read_text(encoding="utf-8")
    main_source = Path("main.py").read_text(encoding="utf-8")
    for field in (
        "operator_stats",
        "requirement_relevance_breakdown",
        "per_operator_breakdown",
        "mutation_proposal_lifecycle",
        "mutation_candidate_sources",
    ):
        assert field in graph_source
    for column in (
        "dynamic_operator_stats",
        "dynamic_requirement_relevance_breakdown",
        "dynamic_per_operator_breakdown",
        "dynamic_mutation_proposal_lifecycle",
        "dynamic_mutation_candidate_sources",
    ):
        assert column in main_source
