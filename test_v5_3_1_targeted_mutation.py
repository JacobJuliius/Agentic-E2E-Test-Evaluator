from pathlib import Path
import importlib.util
import sys

MODULE_PATH = Path(__file__).with_name('dynamic_agents.py')
spec = importlib.util.spec_from_file_location('dynamic_agents_v531', MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def drag_state():
    return {
        'excutable_test_test_case': (
            'Feature: drag safety\n'
            '  Scenario: non-draggable drop area\n'
            '    Given the page is loaded\n'
            '    When the user tries to drag the element with data-testid "drop-area"\n'
            '    Then the element with data-testid "drop-area" should not start a drag operation\n'
        )
    }


def test_missing_draggable_attribute_generates_generic_insertion_mutant():
    source = '<div data-testid="drop-area">Drop here</div>'
    mutants = module._mutation_specs_add_missing_draggable(
        source, 'index.html', {'drop-area'}
    )
    assert len(mutants) == 1
    mutant = mutants[0]
    assert mutant.operator == 'DRAGGABLE_MISSING_TO_TRUE'
    assert mutant.original == ''
    changed = source[:mutant.start] + mutant.replacement + source[mutant.end:]
    assert '<div draggable="true" data-testid="drop-area">' in changed


def test_missing_attribute_mutant_is_relevant_to_explicit_dom_target():
    source = '<div data-testid="drop-area">Drop here</div>'
    scope = module._extract_test_scope(drag_state(), '')
    mutant = module._mutation_specs_add_missing_draggable(
        source, 'index.html', {'drop-area'}
    )[0]
    result = module._classify_mutant_scope(mutant, source, scope)
    assert result['scope_relation'] == 'RELEVANT'


def test_drag_scenario_selects_target_local_dom_mutant_before_global_prices(tmp_path):
    app = tmp_path / 'app'
    app.mkdir()
    (app / 'index.html').write_text(
        '<div data-testid="drop-area">Drop here</div>'
        '<li data-testid="product-item-1"><span>$40</span></li>'
        '<li data-testid="product-item-2"><span>$120</span></li>',
        encoding='utf-8',
    )
    scope = module._extract_test_scope(drag_state(), '')
    mutants = module._discover_mutants(
        app,
        max_mutants=3,
        test_scope=scope,
        prefer_drag_behaviour=True,
    )
    assert len(mutants) == 1
    assert mutants[0].operator == 'DRAGGABLE_MISSING_TO_TRUE'


def test_price_scenario_keeps_global_price_portfolio_when_no_target_local_drag_operator(tmp_path):
    app = tmp_path / 'app'
    app.mkdir()
    (app / 'index.html').write_text(
        '<div data-testid="drop-area">Drop here</div>'
        '<li data-testid="product-item-1"><span>$40</span></li>',
        encoding='utf-8',
    )
    scope = {'element_ids': ['product-item-1'], 'product_indices': ['1'], 'prices': ['$40']}
    mutants = module._discover_mutants(app, 2, test_scope=scope, prefer_drag_behaviour=False)
    assert mutants[0].operator == 'PRICE_PLUS_ONE'
