from pathlib import Path
import importlib.util
import sys

MODULE_PATH = Path(__file__).resolve().parents[1] / "dynamic_agents.py"
spec = importlib.util.spec_from_file_location(
    "dynamic_agents_v53_test", MODULE_PATH
)
dynamic_agents = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = dynamic_agents
spec.loader.exec_module(dynamic_agents)


def test_draggable_false_mutant_is_discovered_and_mutates_value_only():
    source = '<div data-testid="drop-area" draggable="false">Drop here</div>'
    mutants = dynamic_agents._mutation_specs_from_draggable_false(source, "index.html")
    assert len(mutants) == 1
    mutant = mutants[0]
    assert mutant.operator == "DRAGGABLE_FALSE_TO_TRUE"
    assert mutant.original == "false"
    assert mutant.replacement == "true"
    changed = source[:mutant.start] + mutant.replacement + source[mutant.end:]
    assert 'data-testid="drop-area"' in changed
    assert 'draggable="true"' in changed


def test_drag_target_makes_draggable_mutant_relevant():
    state = {
        "excutable_test_test_case": (
            'Feature: drag safety\n'
            '  Scenario: non-draggable drop area\n'
            '    Given the page is loaded\n'
            '    When the user tries to drag the element with data-testid "drop-area"\n'
            '    Then the element with data-testid "drop-area" should not start a drag operation\n'
        ),
    }
    scope = dynamic_agents._extract_test_scope(state, "")
    assert "drop-area" in scope["element_ids"]
    assert scope["primary_source"] == "BDD_SCENARIO"

    source = '<div data-testid="drop-area" draggable="false">Drop here</div>'
    mutant = dynamic_agents._mutation_specs_from_draggable_false(source, "index.html")[0]
    classified = dynamic_agents._classify_mutant_scope(mutant, source, scope)
    assert classified["scope_relation"] == "RELEVANT"


def test_drag_scenario_prioritizes_drag_mutation(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "index.html").write_text(
        '<div data-testid="drop-area" draggable="false"></div>'
        '<li data-testid="product-item-1"><span>$40</span></li>',
        encoding="utf-8",
    )
    mutants = dynamic_agents._discover_mutants(
        app, max_mutants=1, prefer_drag_behaviour=True
    )
    assert len(mutants) == 1
    assert mutants[0].operator == "DRAGGABLE_FALSE_TO_TRUE"
