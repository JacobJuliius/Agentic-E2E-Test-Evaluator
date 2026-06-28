from __future__ import annotations

import json
from pathlib import Path

import dynamic_agents
import reference_resolver
from mutation_testing import (
    MutationCandidate,
    calculate_relevant_mutation_metrics,
    classify_mutant_relevance,
    extract_test_scope,
)


def _source_project(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "index.html").write_text(
        "<html><body>fixture</body></html>", encoding="utf-8"
    )
    return path


def test_local_source_override_avoids_remote_resolver_and_uses_canonical_workspace(
    monkeypatch, tmp_path: Path
):
    local = _source_project(tmp_path / "source_projcet")
    monkeypatch.setattr(
        reference_resolver,
        "resolve_reference_source",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("remote/cache resolver must not be called")
        ),
    )
    monkeypatch.setattr(
        dynamic_agents, "ARTIFACT_ROOT", tmp_path / "artifacts"
    )
    prepared = dynamic_agents._new_workspace({
        "source_project_dir": str(local),
        "reference_answer": "https://example.invalid/reference/",
        "reference_network_enabled": False,
        "case_uid": "local-override",
        "benchmark_id": "Bench",
    }, "baseline")

    assert prepared.app_dir.name == "source_project"
    assert prepared.app_index == prepared.app_dir / "index.html"
    assert prepared.source_metadata["source_origin"] == "LOCAL_SOURCE_OVERRIDE"
    assert prepared.source_metadata["resolved_source_project_dir"] == str(
        local.resolve()
    )


def test_invalid_local_override_falls_back_to_cache_resolver(
    monkeypatch, tmp_path: Path
):
    fallback = _source_project(tmp_path / "cached" / "wrapped")

    def fake_resolver(*args, **kwargs):
        return {
            "resolution_status": "success",
            "source_url": "https://example.test/ref",
            "project_identifier": "ref",
            "local_path": str(fallback.parent),
            "cache_hit": True,
            "retrieval_method": "cache",
            "validation_result": {"valid": True},
            "failure_reason": "",
        }

    monkeypatch.setattr(
        reference_resolver, "resolve_reference_source", fake_resolver
    )
    result = reference_resolver.resolve_project_source(
        source_project_dir=str(tmp_path / "missing"),
        reference_url="https://example.test/ref",
        workspace_root=tmp_path / "artifacts",
        allow_network=False,
    )
    assert result["resolution_status"] == "success"
    assert result["source_origin"] == "REFERENCE_CACHE"
    assert result["resolved_source_project_dir"] == str(fallback.resolve())
    assert "does not exist" in result["local_override_diagnostic"]


def test_product_two_mutant_is_out_of_scope_for_product_one():
    source = """
    <li data-testid="product-item-2">
      <span data-testid="product-title-2">Other title</span>
    </li>
    """
    start = source.index("Other title")
    mutant = MutationCandidate(
        mutant_id="M1",
        operator="UI_TEXT_MUTATION",
        source_file="index.html",
        location={
            "line": 3, "column": 44,
            "start_offset": start, "end_offset": start + len("Other title"),
        },
        original_code="Other title",
        replacement_code="__MUTATED_TEXT__",
        mutation_description="fixture",
    )
    scope = extract_test_scope(
        'When the user selects data-testid "product-item-1"', ""
    )
    result = classify_mutant_relevance(mutant, source, scope)
    assert result["scope_relation"] == "OUT_OF_SCOPE"


def test_unrelated_image_is_out_of_scope_for_drop_area():
    source = (
        '<img src="img4.jpg" data-testid="product-image-4"/>'
        '<div data-testid="drop-area"></div>'
    )
    start = source.index("img4.jpg")
    mutant = MutationCandidate(
        mutant_id="M2",
        operator="DOM_ATTRIBUTE_VALUE_MUTATION",
        source_file="index.html",
        location={
            "line": 1, "column": 10,
            "start_offset": start, "end_offset": start + len("img4.jpg"),
        },
        original_code="img4.jpg",
        replacement_code="img4-mutated.jpg",
        mutation_description="fixture",
    )
    scope = extract_test_scope(
        'Then data-testid "drop-area" remains unchanged', ""
    )
    result = classify_mutant_relevance(mutant, source, scope)
    assert result["scope_relation"] == "OUT_OF_SCOPE"


def test_zero_relevant_valid_mutants_has_null_score():
    result = calculate_relevant_mutation_metrics([
        {
            "scope_relation": "OUT_OF_SCOPE",
            "execution_verdict": "SURVIVED",
        },
        {
            "scope_relation": "UNCERTAIN",
            "execution_verdict": "KILLED",
        },
    ])
    assert result["relevant_mutation_score"] is None
    assert result["mutation_scope_status"] == "NO_RELEVANT_MUTANTS"


def test_hallucination_agent_receives_scenario_only_literal(monkeypatch):
    from test_v5_1_scope_logic import load_agents_module

    agents = load_agents_module()
    scenario_literal = "Seat-Z9"

    def fake_invoke(messages, *args, **kwargs):
        rendered = "\n".join(message.content for message in messages)
        assert f"Active Gherkin Scenario / Test Case" in rendered
        assert scenario_literal in rendered
        return json.dumps({
            "hallucination_count": 0,
            "hallucinations": [],
            "test_smells": [],
        })

    monkeypatch.setattr(agents, "invoke_with_retry", fake_invoke)
    result = agents.hallucination_smell_agent({
        "syntax_passed": True,
        "fine_grained_reqs": "The user can select an available seat.",
        "prompt": "A seat-selection interface.",
        "excutable_test_test_case": (
            f'When the user selects "{scenario_literal}"\n'
            f'Then "{scenario_literal}" is visible'
        ),
        "executable_test_code": f'assert selected == "{scenario_literal}"',
    })
    assert result["hallucination_count"] == 0
