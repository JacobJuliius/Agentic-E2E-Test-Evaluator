"""Pure local tests for V5.1 scope-aware mutation and conditional LLM routing."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_agents_module():
    """Load agents.py with tiny stubs so routing/score helpers can be tested offline."""
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

    spec = importlib.util.spec_from_file_location("agents_v51_test", ROOT / "agents.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_product_scope_prevents_cross_product_penalty():
    import dynamic_agents as dyn

    source = """
    <ul>
      <li data-testid='product-item-1'>
        <span data-testid='product-title-1'>A</span>
        <span data-testid='product-price-1'>$40</span>
      </li>
      <li data-testid='product-item-2'>
        <span data-testid='product-title-2'>B</span>
        <span data-testid='product-price-2'>$120</span>
      </li>
      <li data-testid='product-item-3'>
        <span data-testid='product-title-3'>C</span>
        <span data-testid='product-price-3'>$35</span>
      </li>
    </ul>
    """
    state = {
        "excutable_test_test_case": "When the user drags product-item-1 Then its price is $40",
        "fine_grained_reqs": "Verify product item 1 and its $40 price.",
    }
    test_code = "driver.find_element(By.CSS_SELECTOR, \"[data-testid='product-item-1']\")\nassert price == '$40'"
    scope = dyn._extract_test_scope(state, test_code)

    m1_pos = source.index("$40")
    m2_pos = source.index("$120")
    m1 = dyn.MutationSpec("M001", "index.html", m1_pos, m1_pos + 3, "$40", "$41", "PRICE_PLUS_ONE", 5)
    m2 = dyn.MutationSpec("M002", "index.html", m2_pos, m2_pos + 4, "$120", "$121", "PRICE_PLUS_ONE", 9)

    assert dyn._classify_mutant_scope(m1, source, scope)["scope_relation"] == "RELEVANT"
    assert dyn._classify_mutant_scope(m2, source, scope)["scope_relation"] == "OUT_OF_SCOPE"


def test_scope_aware_summary_separates_suite_from_test_score():
    import dynamic_agents as dyn

    records = [
        {"scope_relation": "RELEVANT", "verdict": "KILLED"},
        {"scope_relation": "OUT_OF_SCOPE", "verdict": "SURVIVED"},
        {"scope_relation": "OUT_OF_SCOPE", "verdict": "SURVIVED"},
    ]
    summary = dyn._scope_aware_mutation_summary(records)
    assert summary["mutation_scope_status"] == "SCOPE_AWARE"
    assert summary["relevant_mutation_score"] == 100.0
    assert summary["out_of_scope_mutants_survived"] == 2


def test_conditional_llm_routing_avoids_out_of_scope_calls():
    agents = load_agents_module()
    base = {
        "syntax_passed": True,
        "enable_dynamic": True,
        "enable_dynamic_analyst": True,
        "execution_status": "PASSED",
        "relevant_mutants_survived": 0,
        "uncertain_mutants_survived": 0,
        "out_of_scope_mutants_survived": 2,
    }
    assert agents.should_run_dynamic_analysis(base) is False

    relevant_gap = {**base, "relevant_mutants_survived": 1}
    assert agents.should_run_dynamic_analysis(relevant_gap) is True


def test_consensus_uses_scope_aware_not_raw_mutation_score():
    agents = load_agents_module()
    state = {
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
        "dynamic_coverage_score": 100.0,
        "mutation_score": 33.33,  # all three app mutants: suite-style raw score
        "mutation_scope_status": "SCOPE_AWARE",
        "relevant_mutation_score": 100.0,  # only the target product mutant
        "dynamic_quality_label": "NOT_NEEDED",
        "dynamic_evidence_summary": "No relevant mutant survived.",
        "critic_score_caveat": "None",
    }
    result = agents.consensus_agent(state)
    assert result["overall_score"] == 100.0
    assert "BDD step-execution diagnostic=100.0 (not directly scored)" in result["final_reasoning"]


def test_refiner_scope_rejects_feature_file_text():
    agents = load_agents_module()
    assert agents._fixed_code_is_step_code_only("@when('x')\ndef step(context):\n    pass")
    assert not agents._fixed_code_is_step_code_only("Feature: Cart\nScenario: Drag product")
