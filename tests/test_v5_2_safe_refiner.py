"""Offline tests for V5.2 scope precision and safe-refiner acceptance."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_agents_module():
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules.setdefault("dotenv", dotenv)

    lc_google = types.ModuleType("langchain_google_genai")

    class FakeLLM:
        def __init__(self, *args, **kwargs):
            pass

        def invoke(self, *args, **kwargs):
            raise AssertionError("LLM must not be invoked by local routing tests")

    lc_google.ChatGoogleGenerativeAI = FakeLLM
    sys.modules.setdefault("langchain_google_genai", lc_google)

    lc_messages = types.ModuleType("langchain_core.messages")

    class FakeMessage:
        def __init__(self, content):
            self.content = content

    lc_messages.SystemMessage = FakeMessage
    lc_messages.HumanMessage = FakeMessage
    sys.modules.setdefault("langchain_core.messages", lc_messages)

    spec = importlib.util.spec_from_file_location("agents_v52_test", ROOT / "agents.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_bdd_scope_ignores_generic_step_definitions():
    import dynamic_agents as dyn

    state = {
        "excutable_test_test_case": """Scenario: Add product three
Given product-item-3 is visible
When the user drags product-item-3 to drop-area
Then the price \"$35\" is displayed""",
        "fine_grained_reqs": "Products 1, 2 and 3 can be added.",
    }
    code = """
from behave import when, then
@when('the user drags product-item-1 or product-item-2 or {product_id}')
def generic(context, product_id):
    pass
@then('the cart is correct')
def verify(context):
    assert context.value == "$35"
"""
    scope = dyn._extract_test_scope(state, code)
    assert scope["primary_source"] == "BDD_SCENARIO"
    assert scope["product_indices"] == ["3"]
    assert "1" not in scope["product_indices"]
    assert "2" not in scope["product_indices"]


def test_precise_scope_marks_other_product_out_of_scope():
    import dynamic_agents as dyn

    source = (
        '<li data-testid="product-item-1"><p data-testid="product-price-1">$40</p></li>\n'
        '<li data-testid="product-item-3"><p data-testid="product-price-3">$35</p></li>'
    )
    state = {"excutable_test_test_case": 'Scenario: x\nGiven product-item-3\nThen the price "$35"'}
    scope = dyn._extract_test_scope(state, "")
    p40 = source.index("$40")
    p35 = source.index("$35")
    m40 = dyn.MutationSpec("M1", "index.html", p40, p40 + 3, "$40", "$41", "PRICE_PLUS_ONE", 1)
    m35 = dyn.MutationSpec("M2", "index.html", p35, p35 + 3, "$35", "$36", "PRICE_PLUS_ONE", 2)
    assert dyn._classify_mutant_scope(m40, source, scope)["scope_relation"] == "OUT_OF_SCOPE"
    assert dyn._classify_mutant_scope(m35, source, scope)["scope_relation"] == "RELEVANT"


def test_low_static_score_alone_does_not_trigger_refiner():
    agents = load_agents_module()
    state = {
        "syntax_passed": True,
        "enable_refiner": True,
        "execution_status": "PASSED",
        "should_refine": False,
        "relevant_mutants_survived": 0,
        "relevant_mutation_score": 100.0,
        "static_overall_score": 0.0,
    }
    assert agents.should_run_refiner(state) is False


def test_relevant_mutant_gap_triggers_refiner():
    agents = load_agents_module()
    state = {
        "syntax_passed": True,
        "enable_refiner": True,
        "execution_status": "PASSED",
        "should_refine": False,
        "relevant_mutants_survived": 1,
        "relevant_mutation_score": 50.0,
        "relevant_mutation_refine_threshold": 80.0,
    }
    assert agents.should_run_refiner(state) is True


def test_report_scope_claim_is_detected():
    agents = load_agents_module()
    assert agents._report_claims_feature_change("Added a new Scenario Outline to cover more products.")
    assert not agents._report_claims_feature_change("Did not add a new scenario; feature file remains unchanged.")


def test_safety_gate_rejects_regression_and_restores_original():
    import dynamic_agents as dyn

    result = dyn.repair_safety_gate_agent({
        "executable_test_code": "assert True",
        "fixed_code": "assert False",
        "refiner_status": "GENERATED",
        "enable_refinement_validation": True,
        "execution_status": "PASSED",
        "validation_execution_status": "TEST_FAILED",
    })
    assert result["refiner_status"] == "REJECTED_REGRESSION"
    assert result["fixed_code"] == "assert True"
    assert result["refiner_candidate_code"] == "assert False"
    assert result["refiner_accepted"] is False


def test_safety_gate_accepts_validated_candidate():
    import dynamic_agents as dyn

    result = dyn.repair_safety_gate_agent({
        "executable_test_code": "assert True",
        "fixed_code": "assert x == 1",
        "refiner_status": "GENERATED",
        "enable_refinement_validation": True,
        "execution_status": "PASSED",
        "validation_execution_status": "PASSED",
    })
    assert result["refiner_status"] == "ACCEPTED_VALIDATED"
    assert result["fixed_code"] == "assert x == 1"
    assert result["refiner_accepted"] is True
