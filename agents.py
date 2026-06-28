"""
Description: This file contains all core Agent nodes for the Agentic Test Evaluation System.
Includes:
1. Syntax & Linter Agent (Static Syntax Checker + AST Static Metrics) [ENHANCED]
2. Requirement Alignment Agent (Requirement Coverage Evaluator)
3. Assertion Quality Agent (Assertion Quality Evaluator)
4. Hallucination & Smell Agent (Noise & Bad Practice Filter)
5. Maintainability Agent (Code Quality Inspector) [NEW]
6. Critic Agent (Debate & Conflict Resolution)
7. Consensus Agent (Final Score Aggregator) [ENHANCED]
8. Refiner Agent (Self-Healing & Reporting)
"""

import ast
import json
import re
import time
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
from dotenv import load_dotenv
from e2e_eval.runtime.utils import as_bool as _as_bool

load_dotenv()

# Initialize the LLM
llm = ChatGoogleGenerativeAI(
    model="gemini-3.1-flash-lite",
    temperature=0
)

# ==========================================
# Utility Functions
# ==========================================

# One process-level guard. The pipeline is serial, so no lock is required.
# With all optional LLM nodes enabled: Requirement + Assertion + Hallucination +
# Maintainability + Dynamic Analyst + Critic + Refiner = 7 calls per valid case.
# Conditional routing keeps typical batches well below quota; 470 leaves recovery margin below 500 RPD.
LLM_RPD_SOFT_LIMIT = int(__import__("os").getenv("E2E_LLM_RPD_SOFT_LIMIT", "470"))
_llm_successful_calls = 0


def invoke_with_retry(prompt, max_retries: int = 3, wait_time: int = 60, agent_name: str = "LLM agent") -> str:
    """Invoke Gemini under one global serial RPM/RPD guard.

    The graph intentionally keeps model nodes serial.  Dynamic execution, coverage,
    mutation, and refinement validation are local Python tools and make no API calls.
    ``E2E_LLM_RPD_SOFT_LIMIT`` protects a batch from exceeding the free-tier daily quota;
    it counts successful calls in this Python process, not requests made before this run.
    """
    global _llm_successful_calls

    if _llm_successful_calls >= LLM_RPD_SOFT_LIMIT:
        raise RuntimeError(
            f"LLM soft budget reached ({_llm_successful_calls}/{LLM_RPD_SOFT_LIMIT}). "
            "Stop the batch or lower E2E_MAX_CASES; do not exceed the 500-RPD quota."
        )

    for attempt in range(max_retries):
        try:
            res = llm.invoke(prompt)
            raw_content = res.content
            if isinstance(raw_content, list):
                text_parts = []
                for block in raw_content:
                    if isinstance(block, str):
                        text_parts.append(block)
                    elif isinstance(block, dict) and "text" in block:
                        text_parts.append(block["text"])
                result = "".join(text_parts)
            else:
                result = raw_content

            _llm_successful_calls += 1
            # 15 RPM = one request each 4 seconds.  Five seconds keeps margin.
            time.sleep(5)
            return result
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                if attempt < max_retries - 1:
                    print(
                        f"\n⚠️ {agent_name}: rate limit hit. Waiting {wait_time}s... "
                        f"({attempt + 2}/{max_retries} attempt)"
                    )
                    time.sleep(wait_time)
                else:
                    print(f"\n❌ {agent_name}: failed after {max_retries} attempts.")
                    raise
            else:
                raise


def clean_json_output(raw_output: str) -> str:
    """Helper function to strip markdown formatting from LLM outputs."""
    raw_output = raw_output.strip()
    if raw_output.startswith("```json"):
        raw_output = raw_output[7:]
    elif raw_output.startswith("```"):
        raw_output = raw_output[3:]
    if raw_output.endswith("```"):
        raw_output = raw_output[:-3]
    return raw_output.strip()


# ==========================================
# NEW: AST-based Static Metrics Extractor
# ==========================================

def extract_static_metrics(code: str) -> dict:
    """Extract deterministic static evidence from Python Selenium/Playwright tests.

    V3.2 changes:
    - Count native Python ``assert`` statements (``ast.Assert``), not only assertion calls.
    - Classify selectors into stable / medium / brittle rather than treating every CSS selector
      as brittle. ``data-testid`` and ``data-test`` are stable test hooks.
    - Preserve ``hardcoded_selectors`` as a backward-compatible alias for brittle selectors.
    """
    metrics = {
        "actions_count": 0,
        "assertions_count": 0,
        "hardcoded_selectors": [],  # legacy alias: brittle selectors only
        "stable_selectors": [],
        "medium_risk_selectors": [],
        "brittle_selectors": [],
        "magic_numbers": [],
        "action_methods": [],
        "assertion_methods": [],
    }

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return metrics

    ACTION_METHODS = {
        "click", "fill", "type", "press", "select_option", "check", "uncheck",
        "hover", "focus", "tap", "double_click", "right_click", "drag_to",
        "navigate", "goto", "reload", "go_back", "go_forward",
        "find_element", "find_elements", "send_keys", "submit",
    }
    ASSERTION_METHODS = {
        "expect", "assert_", "to_be_visible", "to_have_text", "to_have_value",
        "to_be_enabled", "to_be_checked", "to_contain_text", "to_have_url",
        "to_have_title", "assertEqual", "assertTrue", "assertFalse", "assertIn",
        "assertIsNotNone", "assertRaises",
    }

    def classify_selector(selector: str) -> str:
        normalized = selector.strip().lower()
        # Preferred, explicit test hooks
        if "data-testid" in normalized or "data-test" in normalized:
            return "stable"
        # XPath and positional DOM paths break easily after UI refactors
        if (normalized.startswith("//") or normalized.startswith("xpath=")
                or "by.xpath" in normalized or ":nth-child" in normalized
                or ":nth-of-type" in normalized):
            return "brittle"
        # CSS classes / IDs are not automatically invalid, but couple tests to presentation.
        if (normalized.startswith("#") or normalized.startswith(".")
                or "[class" in normalized or "by.css_selector" in normalized):
            return "medium"
        return "unknown"

    # Count native Python assert statements and assertion/action method calls.
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            metrics["assertions_count"] += 1
            metrics["assertion_methods"].append("python_assert")

        if isinstance(node, ast.Call):
            method_name = None
            if isinstance(node.func, ast.Attribute):
                method_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                method_name = node.func.id

            if method_name in ACTION_METHODS:
                metrics["actions_count"] += 1
                metrics["action_methods"].append(method_name)
            if method_name in ASSERTION_METHODS:
                metrics["assertions_count"] += 1
                metrics["assertion_methods"].append(method_name)

            # Selenium locator calls: classify the locator argument when it is a string literal.
            if method_name in {"find_element", "find_elements"} and len(node.args) >= 2:
                locator = node.args[1]
                if isinstance(locator, ast.Constant) and isinstance(locator.value, str):
                    kind = classify_selector(locator.value)
                    if kind == "stable":
                        metrics["stable_selectors"].append(locator.value)
                    elif kind == "medium":
                        metrics["medium_risk_selectors"].append(locator.value)
                    elif kind == "brittle":
                        metrics["brittle_selectors"].append(locator.value)

    MAGIC_NUMBER_WHITELIST = {200, 201, 204, 301, 302, 400, 401, 403, 404, 500, 502, 503}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            if (node.value not in (0, 1, -1, True, False)
                    and abs(node.value) > 10
                    and node.value not in MAGIC_NUMBER_WHITELIST):
                metrics["magic_numbers"].append(node.value)

    for key in ("stable_selectors", "medium_risk_selectors", "brittle_selectors", "magic_numbers"):
        metrics[key] = sorted(set(metrics[key]), key=str)
    metrics["hardcoded_selectors"] = metrics["brittle_selectors"].copy()
    return metrics


# ==========================================
# Agent 1: Syntax & Linter Agent (ENHANCED)
# ==========================================

def syntax_linter_agent(state: dict) -> dict:
    """
    The Gatekeeper (ENHANCED):
    - Statically checks the generated test code for severe syntax errors (Rule-based).
    - Extracts objective AST static metrics for downstream agents (Hybrid Evaluation).
    """
    test_code = state.get("executable_test_code", "")

    # Step 1: Syntax check (rule-based)
    try:
        ast.parse(test_code)
        syntax_passed = True
        error_message = ""
    except SyntaxError as e:
        syntax_passed = False
        error_message = f"Fatal syntax error detected: {str(e)}"

    # Step 2: Static metrics extraction (rule-based, runs regardless of syntax result)
    static_metrics = extract_static_metrics(test_code)

    return {
        "syntax_passed": syntax_passed,
        "error_message": error_message,
        # Inject static metrics into state for downstream agents to consume
        "static_actions_count": static_metrics["actions_count"],
        "static_assertions_count": static_metrics["assertions_count"],
        "static_hardcoded_selectors": static_metrics["hardcoded_selectors"],
        "static_stable_selectors": static_metrics["stable_selectors"],
        "static_medium_risk_selectors": static_metrics["medium_risk_selectors"],
        "static_brittle_selectors": static_metrics["brittle_selectors"],
        "static_magic_numbers": static_metrics["magic_numbers"],
        "static_action_methods": static_metrics["action_methods"],
        "static_assertion_methods": static_metrics["assertion_methods"],
    }


# ==========================================
# Agent 2: Requirement Alignment Agent
# ==========================================

def requirement_alignment_agent(state: dict) -> dict:
    reqs = state.get("fine_grained_reqs", "")
    code = state.get("executable_test_code", "")
    critic_feedback = state.get("critic_feedback", "")
    req_summary = state.get("requirement_summary", "")          
    test_case_bdd = state.get("excutable_test_test_case", "")

    feedback_prompt = (
        f"\n\n[CRITIC FEEDBACK FROM PREVIOUS ROUND]\n{critic_feedback}\n"
        "Please review your previous evaluation and adjust if necessary to resolve the conflict."
        if critic_feedback and state.get("revision_count", 0) > 0 else ""
    )

    system_prompt = f"""
    You are an Expert QA Automation Architect acting as the "Requirement Alignment Agent".
    Evaluate how well the End-to-end test script covers the business requirements.
    {feedback_prompt}

    Output Constraint: You MUST return ONLY a valid JSON object matching the schema below.
    Expected JSON Schema:
    {{
        "total_requirements_count": 0,
        "covered_requirements": ["<req>"],
        "partially_covered_requirements": ["<req and brief reason>"],
        "missing_requirements": ["<req>"]
    }}
    """
    
    # 注入 Summary 了解全局，注入 BDD Test Case 理解测试目的
    context_content = (
        f"Requirement Summary (Overview):\n{req_summary}\n\n"
        f"Fine-Grained Requirements:\n{reqs}\n\n"
        f"Expected BDD Test Case (Testing Intent):\n{test_case_bdd}\n\n"
        f"Code to Evaluate:\n{code}"
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context_content)
    ]

    try:
        response = invoke_with_retry(messages)
        parsed_result = json.loads(clean_json_output(response))
        return {
            "total_requirements_count": parsed_result.get("total_requirements_count", 0),
            "covered_requirements": parsed_result.get("covered_requirements", []),
            "partially_covered_requirements": parsed_result.get("partially_covered_requirements", []),
            "missing_requirements": parsed_result.get("missing_requirements", [])
        }
    except Exception as e:
        return {
            "total_requirements_count": 0,
            "covered_requirements": [],
            "partially_covered_requirements": [],
            "missing_requirements": [f"Error: {str(e)}"]
        }


# ==========================================
# Agent 3: Assertion Quality Agent
# ==========================================

def assertion_quality_agent(state: dict) -> dict:
    if not state.get("syntax_passed", True):
        return {
            "assertion_score": 0,
            "strong_assertions": [],
            "weak_assertions": ["Skipped: Syntax failed."],
            "missing_assertions_rationale": "Code syntax invalid."
        }

    fine_grained_reqs = state.get("fine_grained_reqs", "")
    code = state.get("executable_test_code", "")
    test_case_bdd = state.get("excutable_test_test_case", "") # NEW
    critic_feedback = state.get("critic_feedback", "")

    # Inject static metrics as grounding context for the LLM
    static_assertions_count = state.get("static_assertions_count", "N/A")
    static_assertion_methods = state.get("static_assertion_methods", [])
    static_context = (
        f"\n\n[STATIC ANALYSIS CONTEXT (Objective, Rule-based)]\n"
        f"AST scan found {static_assertions_count} assertion construct(s), including Python assert statements: {static_assertion_methods}.\n"
        f"Use this as a factual grounding when evaluating assertion quality and completeness."
    )

    feedback_prompt = (
        f"\n\n[CRITIC FEEDBACK FROM PREVIOUS ROUND]\n{critic_feedback}\n"
        "Please review your previous evaluation and adjust if necessary to resolve the conflict."
        if critic_feedback and state.get("revision_count", 0) > 0 else ""
    )

    system_prompt = f"""You are an Expert QA Automation Architect acting as the "Assertion Quality Agent".
    Strictly evaluate the quality, robustness, and business relevance of the assertions in the test code.
    Use the provided BDD Test Case to understand the true intent of the assertions.
    {static_context}
    {feedback_prompt}

    Output Constraint: Return ONLY a valid JSON object matching the schema below. No markdown.
    Expected JSON Schema:
    {{
      "assertion_score": 0,
      "strong_assertions": ["<List assertions strong>"],
      "weak_assertions": ["<List assertions weak>"],
      "missing_assertions_rationale": "<Describe assertions missing>"
    }}
    """
    
    context_content = (
        f"Requirements:\n{fine_grained_reqs}\n\n"
        f"BDD Test Case (Testing Intent):\n{test_case_bdd}\n\n"
        f"Test Code:\n{code}"
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context_content)
    ]

    try:
        response = invoke_with_retry(messages)
        parsed_result = json.loads(clean_json_output(response))
        return {
            "assertion_score": parsed_result.get("assertion_score", 0),
            "strong_assertions": parsed_result.get("strong_assertions", []),
            "weak_assertions": parsed_result.get("weak_assertions", []),
            "missing_assertions_rationale": parsed_result.get("missing_assertions_rationale", "")
        }
    except Exception as e:
        return {
            "assertion_score": 0,
            "strong_assertions": [],
            "weak_assertions": ["Error"],
            "missing_assertions_rationale": str(e)
        }


# ==========================================
# Agent 4: Hallucination & Smell Agent
# ==========================================

def hallucination_smell_agent(state: dict) -> dict:
    if not state.get("syntax_passed", True):
        return {
            "hallucination_count": 0,
            "hallucinations": ["Skipped"],
            "test_smells": ["Skipped"]
        }

    fine_grained_reqs = state.get("fine_grained_reqs", "")
    code = state.get("executable_test_code", "")
    original_prompt = state.get("prompt", "")  # NEW
    critic_feedback = state.get("critic_feedback", "")

    feedback_prompt = (
        f"\n\n[CRITIC FEEDBACK FROM PREVIOUS ROUND]\n{critic_feedback}\n"
        "Please review your previous evaluation and adjust if necessary to resolve the conflict."
        if critic_feedback and state.get("revision_count", 0) > 0 else ""
    )

    system_prompt = f"""You are an Expert QA Automation Architect acting as the "Hallucination & Smell Agent".
    Focus ONLY on:
    1. Fabricated business actions (hallucinations): actions in the test code that have NO basis in the requirements OR the Original Prompt (which contains UI structure details).
    2. Basic test smells related to test logic: e.g., time.sleep() abuse, test interdependencies, missing teardown.

    Do NOT evaluate selector quality or code maintainability — that is handled by a separate Maintainability Agent.
    Before calling an action a "hallucination", check if it was explicitly mentioned in the Original Prompt UI details.
    {feedback_prompt}

    Output Constraint: Return ONLY a valid JSON object.
    Expected JSON Schema:
    {{
      "hallucination_count": 0,
      "hallucinations": ["<List actions fabricated>"],
      "test_smells": ["<List bad practices>"]
    }}
    """
    
    # 注入生成代码时的原始 Prompt
    context_content = (
        f"Original Context Prompt (UI & System Details):\n{original_prompt}\n\n"
        f"Fine-Grained Requirements:\n{fine_grained_reqs}\n\n"
        f"Test Code:\n{code}"
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=context_content)
    ]

    try:
        response = invoke_with_retry(messages)
        parsed_result = json.loads(clean_json_output(response))
        return {
            "hallucination_count": parsed_result.get("hallucination_count", 0),
            "hallucinations": parsed_result.get("hallucinations", []),
            "test_smells": parsed_result.get("test_smells", [])
        }
    except Exception as e:
        return {
            "hallucination_count": 0,
            "hallucinations": [f"Error: {str(e)}"],
            "test_smells": []
        }

# ==========================================
# Agent 5: Maintainability Agent (NEW)
# ==========================================

def maintainability_agent(state: dict) -> dict:
    """
    NEW — Maintainability Agent: The SonarQube Inspector.
    Evaluates the structural and stylistic quality of the test code like a code reviewer.
    Runs serially after hallucination_agent (parallel removed to respect 15 RPM limit).

    Uses static metrics from Syntax Agent as objective grounding (Hybrid Evaluation).
    """
    if not state.get("syntax_passed", True):
        return {
            "maintainability_score": 0,
            "hardcoded_selector_issues": ["Skipped: Syntax failed."],
            "duplication_issues": [],
            "naming_issues": [],
            "readability_issues": [],
            "maintainability_rationale": "Skipped due to syntax failure."
        }

    code = state.get("executable_test_code", "")
    critic_feedback = state.get("critic_feedback", "")

    # Provide the objective static findings as grounding context
    stable_selectors = state.get("static_stable_selectors", [])
    medium_risk_selectors = state.get("static_medium_risk_selectors", [])
    brittle_selectors = state.get("static_brittle_selectors", state.get("static_hardcoded_selectors", []))
    magic_numbers = state.get("static_magic_numbers", [])
    actions_count = state.get("static_actions_count", "N/A")
    assertions_count = state.get("static_assertions_count", "N/A")

    static_context = f"""
[STATIC ANALYSIS CONTEXT (Objective, Rule-based — treat as facts)]
- Stable test-hook selectors detected by AST scan: {stable_selectors if stable_selectors else 'None found'}
- Medium-risk presentation-coupled selectors: {medium_risk_selectors if medium_risk_selectors else 'None found'}
- Brittle selectors detected by AST scan: {brittle_selectors if brittle_selectors else 'None found'}
- Magic numbers detected: {magic_numbers if magic_numbers else 'None found'}
- Total action calls: {actions_count}
- Total assertion calls: {assertions_count}
Use these facts to anchor your evaluation. Do not contradict them.
"""

    feedback_prompt = (
        f"\n\n[CRITIC FEEDBACK FROM PREVIOUS ROUND]\n{critic_feedback}\n"
        "Please review your previous evaluation and adjust if necessary to resolve the conflict."
        if critic_feedback and state.get("revision_count", 0) > 0 else ""
    )

    system_prompt = f"""You are an Expert QA Automation Architect acting as the "Maintainability Agent" — like SonarQube for test code.
    Your job is to evaluate the STRUCTURAL and STYLISTIC quality of the test code for long-term maintainability.
    {static_context}
    {feedback_prompt}

    Evaluate these 4 dimensions:
    1. **Selector Robustness**: Treat data-testid/data-test selectors as stable test hooks. Flag XPath, positional selectors, and presentation-coupled class/ID selectors proportionately; do not penalize a selector merely because it is CSS.
    2. **Code Duplication**: Is there repeated logic that should be extracted into helpers or Page Objects?
    3. **Naming Quality**: Are test names, variable names, and step descriptions clear and descriptive?
    4. **Readability**: Are magic numbers used? Is the code structured and easy to follow?

    Score from 0-100, where 100 = perfectly maintainable production-grade test code.

    Output Constraint: Return ONLY a valid JSON object. No markdown.
    Expected JSON Schema:
    {{
      "maintainability_score": 0,
      "hardcoded_selector_issues": ["<List specific brittle selectors found>"],
      "duplication_issues": ["<List duplicated code blocks>"],
      "naming_issues": ["<List poor names>"],
      "readability_issues": ["<List magic numbers, unclear structures>"],
      "maintainability_rationale": "<Overall explanation of the score>"
    }}
    """

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Test Code:\n{code}")
    ]

    try:
        response = invoke_with_retry(messages)
        parsed_result = json.loads(clean_json_output(response))
        return {
            "maintainability_score": parsed_result.get("maintainability_score", 0),
            "hardcoded_selector_issues": parsed_result.get("hardcoded_selector_issues", []),
            "duplication_issues": parsed_result.get("duplication_issues", []),
            "naming_issues": parsed_result.get("naming_issues", []),
            "readability_issues": parsed_result.get("readability_issues", []),
            "maintainability_rationale": parsed_result.get("maintainability_rationale", "")
        }
    except Exception as e:
        return {
            "maintainability_score": 0,
            "hardcoded_selector_issues": [f"Error: {str(e)}"],
            "duplication_issues": [],
            "naming_issues": [],
            "readability_issues": [],
            "maintainability_rationale": f"Agent error: {str(e)}"
        }



# ==========================================
# Agent 6: Dynamic Evidence Analyst (LLM, scope-aware and budget-aware)
# ==========================================

def _as_number(value):
    return float(value) if isinstance(value, (int, float)) else None


def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _dynamic_analysis_skip(status: str, detail: str) -> dict:
    return {
        "dynamic_analysis_status": status,
        "dynamic_quality_label": "NOT_NEEDED" if status == "SKIPPED_NOT_NEEDED" else "INCONCLUSIVE",
        "failure_category": "NOT_ANALYZED",
        "root_cause": detail,
        "dynamic_evidence_summary": detail,
        "fault_detection_gaps": [],
        "analyst_relevant_surviving_mutants": [],
        "analyst_out_of_scope_surviving_mutants": [],
        "analyst_uncertain_surviving_mutants": [],
        "prioritized_repairs": [],
        "should_refine": False,
    }


def should_run_dynamic_analysis(state: dict) -> bool:
    """Run the LLM analyst only when local evidence needs semantic interpretation.

    Normal passing tests with no relevant/uncertain surviving mutants do not consume an
    API call. Out-of-scope survivors are explicitly excluded: they are suite evidence,
    not automatically a weakness of this single test.
    """
    if not state.get("syntax_passed", True):
        return False
    if not _as_bool(state.get("enable_dynamic"), False):
        return False
    if not _as_bool(state.get("enable_dynamic_analyst"), True):
        return False

    execution = str(state.get("execution_status", "NOT_RUN"))
    if execution in {"TEST_FAILED", "TIMEOUT"}:
        return True
    if execution != "PASSED":
        # HARNESS_* and unavailable tooling are deterministic environment statuses.
        return False

    return (
        int(state.get("relevant_mutants_survived", 0) or 0) > 0
        or int(state.get("uncertain_mutants_survived", 0) or 0) > 0
    )


def dynamic_analysis_skip_agent(state: dict) -> dict:
    if not _as_bool(state.get("enable_dynamic_analyst"), True):
        return _dynamic_analysis_skip("SKIPPED_DISABLED", "Dynamic Evidence Analyst disabled by configuration.")
    if state.get("execution_status") == "PASSED" and int(state.get("out_of_scope_mutants_survived", 0) or 0) > 0:
        return _dynamic_analysis_skip(
            "SKIPPED_NOT_NEEDED",
            "Baseline passed. Surviving mutants were classified as out-of-scope for this test and retained only for suite-level analysis.",
        )
    return _dynamic_analysis_skip(
        "SKIPPED_NOT_NEEDED",
        "No failed execution or relevant/uncertain surviving mutation required semantic diagnosis.",
    )


def dynamic_evidence_analyst_agent(state: dict) -> dict:
    """Interpret deterministic runtime evidence without changing any measured outcome."""
    if not should_run_dynamic_analysis(state):
        return dynamic_analysis_skip_agent(state)

    scope_relevant = _as_list(state.get("scope_relevant_surviving_mutants", []))
    scope_out = _as_list(state.get("scope_out_of_scope_surviving_mutants", []))
    scope_uncertain = _as_list(state.get("scope_uncertain_surviving_mutants", []))
    dynamic_evidence = {
        "execution": {
            "status": state.get("execution_status", "NOT_RUN"),
            "success": state.get("execution_success", False),
            "failed_steps": state.get("execution_failed_steps", []),
            "stderr_tail": str(state.get("execution_stderr_tail", ""))[-1600:],
        },
        "bdd_step_execution_diagnostic": {
            "status": state.get("dynamic_coverage_status", "NOT_RUN"),
            "score": state.get("dynamic_coverage_score"),
            "action_step_success": state.get("action_step_coverage"),
            "oracle_step_success": state.get("oracle_step_coverage"),
            "meaning": "This confirms which BDD steps executed; it does not measure requirement completeness, product-space coverage, or JavaScript branch coverage.",
        },
        "mutation": {
            "raw_suite_style_score": state.get("mutation_score"),
            "scope_status": state.get("mutation_scope_status", "NO_DYNAMIC_SCOPE_EVIDENCE"),
            "test_scope": state.get("test_scope", {}),
            "relevant_score": state.get("relevant_mutation_score"),
            "relevant": {
                "total": state.get("relevant_mutants_total", 0),
                "killed": state.get("relevant_mutants_killed", 0),
                "survived": state.get("relevant_mutants_survived", 0),
                "surviving_records": scope_relevant,
            },
            "out_of_scope": {
                "total": state.get("out_of_scope_mutants_total", 0),
                "survived": state.get("out_of_scope_mutants_survived", 0),
                "surviving_records": scope_out,
            },
            "uncertain": {
                "total": state.get("uncertain_mutants_total", 0),
                "survived": state.get("uncertain_mutants_survived", 0),
                "surviving_records": scope_uncertain,
            },
            "records": state.get("mutation_records", [])[:12],
        },
    }

    system_prompt = """
You are the Dynamic Evidence Analyst in a hybrid E2E-test evaluator.
You read deterministic tool evidence; you MUST NOT alter or invent execution, coverage,
or mutation measurements.

Interpretation rules:
1. BDD step-execution coverage only reports whether the current Given/When/Then steps
   ran. It is diagnostic evidence, not proof of broad business coverage.
2. Mutation records carry a deterministic scope_relation. Only RELEVANT surviving
   mutants can be called a fault-detection gap for this individual test.
3. OUT_OF_SCOPE surviving mutants belong to other product/features and must be described
   as potential requirement-suite evidence, never as a missing assertion in this test.
4. UNCERTAIN mutants need a caveat; do not fabricate a defect.
5. HARNESS_* / missing-tool statuses are environment evidence, not generated-test defects.

Return ONLY valid JSON:
{
  "dynamic_quality_label": "STRONG|MODERATE|WEAK|INCONCLUSIVE",
  "failure_category": "NONE|TEST_LOGIC|ASSERTION_GAP|ENVIRONMENT_OR_HARNESS|MIXED|INCONCLUSIVE",
  "root_cause": "brief evidence-based explanation",
  "dynamic_evidence_summary": "brief synthesis",
  "fault_detection_gaps": ["only evidence-supported gaps"],
  "prioritized_repairs": ["only code-level repairs within the current scenario"],
  "should_refine": true
}
"""
    context = (
        f"Fine-grained requirements:\n{state.get('fine_grained_reqs', '')}\n\n"
        f"BDD scenario:\n{state.get('excutable_test_test_case', '')}\n\n"
        f"Generated test code:\n{state.get('executable_test_code', '')}\n\n"
        f"Deterministic dynamic evidence:\n{json.dumps(dynamic_evidence, ensure_ascii=False, indent=2)}"
    )
    try:
        response = invoke_with_retry(
            [SystemMessage(content=system_prompt), HumanMessage(content=context)],
            agent_name="Dynamic Evidence Analyst",
        )
        parsed = json.loads(clean_json_output(response))
        label = str(parsed.get("dynamic_quality_label", "INCONCLUSIVE")).upper()
        category = str(parsed.get("failure_category", "INCONCLUSIVE")).upper()
        return {
            "dynamic_analysis_status": "ANALYZED",
            "dynamic_quality_label": label if label in {"STRONG", "MODERATE", "WEAK", "INCONCLUSIVE"} else "INCONCLUSIVE",
            "failure_category": category if category in {
                "NONE", "TEST_LOGIC", "ASSERTION_GAP", "ENVIRONMENT_OR_HARNESS", "MIXED", "INCONCLUSIVE"
            } else "INCONCLUSIVE",
            "root_cause": str(parsed.get("root_cause", "No root cause returned.")),
            "dynamic_evidence_summary": str(parsed.get("dynamic_evidence_summary", "No dynamic summary returned.")),
            "fault_detection_gaps": _as_list(parsed.get("fault_detection_gaps", [])),
            # These lists remain deterministic, not LLM-invented.
            "analyst_relevant_surviving_mutants": scope_relevant,
            "analyst_out_of_scope_surviving_mutants": scope_out,
            "analyst_uncertain_surviving_mutants": scope_uncertain,
            "prioritized_repairs": _as_list(parsed.get("prioritized_repairs", [])),
            "should_refine": bool(parsed.get("should_refine", False)),
        }
    except Exception as exc:
        return _dynamic_analysis_skip("ANALYSIS_ERROR", f"Dynamic analyst error: {exc}")


# ==========================================
# Agent 7: Critic Agent (conditional LLM reconciliation)
# ==========================================

def should_run_critic(state: dict) -> bool:
    """Run Critic only when a material interpretation conflict is plausible."""
    if not state.get("syntax_passed", True) or not _as_bool(state.get("enable_critic"), True):
        return False
    execution = str(state.get("execution_status", "NOT_RUN"))
    analyst_category = str(state.get("failure_category", "NOT_ANALYZED"))
    relevant_score = _as_number(state.get("relevant_mutation_score"))
    assertion_score = _as_number(state.get("assertion_score")) or 0.0
    static_assertions = int(state.get("static_assertions_count", 0) or 0)

    if execution in {"TEST_FAILED", "TIMEOUT"} and state.get("dynamic_analysis_status") == "ANALYZED":
        return True
    if analyst_category in {"TEST_LOGIC", "ASSERTION_GAP", "MIXED"}:
        return True
    if relevant_score is not None and relevant_score < 60.0 and assertion_score >= 70.0:
        return True
    if static_assertions == 0 and assertion_score >= 60.0:
        return True
    return False


def critic_skip_agent(state: dict) -> dict:
    if not _as_bool(state.get("enable_critic"), True):
        detail = "Critic disabled by configuration."
        status = "SKIPPED_DISABLED"
    else:
        detail = "No material static/dynamic conflict trigger; deterministic evidence is passed directly to Consensus."
        status = "SKIPPED_NOT_NEEDED"
    return {
        "has_conflict": False,
        "critic_status": status,
        "critic_feedback": detail,
        "critic_score_caveat": "None",
        "revision_count": state.get("revision_count", 0),
    }


def critic_agent(state: dict) -> dict:
    """Reconcile specialist outputs only when conditional routing finds a real ambiguity."""
    if not should_run_critic(state):
        return critic_skip_agent(state)

    current_revision = state.get("revision_count", 0)
    evidence = {
        "requirements": {
            "covered": state.get("covered_requirements", []),
            "partial": state.get("partially_covered_requirements", []),
            "missing": state.get("missing_requirements", []),
        },
        "assertions": {
            "score": state.get("assertion_score", 0),
            "strong": state.get("strong_assertions", []),
            "weak": state.get("weak_assertions", []),
            "ast_count": state.get("static_assertions_count", 0),
        },
        "tool_evidence": {
            "execution_status": state.get("execution_status", "NOT_RUN"),
            "bdd_step_execution_diagnostic": state.get("dynamic_coverage_score"),
            "raw_mutation_score_for_suite_analysis": state.get("mutation_score"),
            "scope_aware_mutation_score_for_this_test": state.get("relevant_mutation_score"),
            "scope_status": state.get("mutation_scope_status"),
            "relevant_survivors": state.get("scope_relevant_surviving_mutants", []),
            "out_of_scope_survivors": state.get("scope_out_of_scope_surviving_mutants", []),
            "uncertain_survivors": state.get("scope_uncertain_surviving_mutants", []),
        },
        "dynamic_analyst": {
            "status": state.get("dynamic_analysis_status", "NOT_RUN"),
            "quality_label": state.get("dynamic_quality_label", "INCONCLUSIVE"),
            "failure_category": state.get("failure_category", "INCONCLUSIVE"),
            "summary": state.get("dynamic_evidence_summary", ""),
            "gaps": state.get("fault_detection_gaps", []),
        },
    }
    system_prompt = """
You are the Critic Agent, chief inspector of a hybrid E2E test-quality jury.
Resolve only material contradictions between static judgments and deterministic dynamic evidence.

Rules:
- Only RELEVANT surviving mutants are an individual-test fault-detection gap.
- OUT_OF_SCOPE surviving mutants may improve a requirement-suite discussion but must not
  lower this single test's rating or trigger a repair.
- BDD step-execution coverage is diagnostic, not business-completeness or branch coverage.
- HARNESS statuses are environment evidence, not test-quality proof.
- Do not ask agents to re-run; the pipeline is a single serial pass under a 15 RPM limit.

Return ONLY valid JSON:
{
  "has_conflict": true,
  "critic_feedback": "specific reconciliation guidance",
  "critic_score_caveat": "short scoring caveat or None"
}
"""
    try:
        response = invoke_with_retry(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Evidence:\n{json.dumps(evidence, ensure_ascii=False, indent=2)}"),
            ],
            agent_name="Critic Agent",
        )
        parsed = json.loads(clean_json_output(response))
        return {
            "has_conflict": bool(parsed.get("has_conflict", False)),
            "critic_status": "ANALYZED",
            "critic_feedback": str(parsed.get("critic_feedback", "No material conflicts detected.")),
            "critic_score_caveat": str(parsed.get("critic_score_caveat", "None")),
            "revision_count": current_revision + 1,
        }
    except Exception as exc:
        return {
            "has_conflict": False,
            "critic_status": "ERROR",
            "critic_feedback": "Critic unavailable; preserve deterministic evidence.",
            "critic_score_caveat": f"Critic error: {exc}",
            "revision_count": current_revision + 1,
        }


# ==========================================
# Agent 8: Consensus Agent (scope-aware mutation; BDD coverage is diagnostic)
# ==========================================

def consensus_agent(state: dict) -> dict:
    """Score deterministic evidence with gates and scope-aware test-level mutation.

    BDD coverage remains in the report because it diagnoses which steps ran, but it is
    intentionally not a direct quality-score component: when baseline execution passes,
    it is often 100% and otherwise duplicates executability evidence.
    """
    if not state.get("syntax_passed", True):
        return {
            "requirement_coverage": 0.0,
            "static_overall_score": 0.0,
            "overall_score": 0.0,
            "score_mode": "SYNTAX_FAILED",
            "final_reasoning": "Fatal error: generated code does not parse.",
        }

    total_reqs = max(int(state.get("total_requirements_count", 1) or 1), 1)
    covered = len(state.get("covered_requirements", []))
    partial = len(state.get("partially_covered_requirements", []))
    raw_req_coverage = ((covered + 0.5 * partial) / total_reqs) * 100.0
    req_coverage = max(0.0, min(100.0, raw_req_coverage))

    assertion_score = max(0.0, min(100.0, float(state.get("assertion_score", 0))))
    maintainability_score = max(0.0, min(100.0, float(state.get("maintainability_score", 0))))
    assertion_penalty = round((1 - assertion_score / 100.0) * 10.0, 2)
    hallucination_penalty = min(30.0, float(state.get("hallucination_count", 0)) * 10.0)
    smell_penalty = min(20.0, float(len(state.get("test_smells", []))) * 5.0)
    maintainability_penalty = round((1 - maintainability_score / 100.0) * 15.0, 2)
    no_assertion_penalty = 10.0 if (
        int(state.get("static_actions_count", 0)) > 0
        and int(state.get("static_assertions_count", 0)) == 0
    ) else 0.0
    penalties = assertion_penalty + hallucination_penalty + smell_penalty + maintainability_penalty + no_assertion_penalty
    static_score = max(0.0, min(100.0, req_coverage - penalties))

    execution_status = str(state.get("execution_status", "NOT_RUN"))
    bdd_diagnostic = state.get("dynamic_coverage_score")
    raw_mutation = state.get("mutation_score")
    scope_status = str(state.get("mutation_scope_status", "NO_DYNAMIC_SCOPE_EVIDENCE"))
    relevant_mutation = _as_number(state.get("relevant_mutation_score"))

    if execution_status == "PASSED":
        components: list[tuple[float, float, str]] = [
            (static_score, 0.60, "STATIC"),
            (100.0, 0.15, "EXECUTION"),
        ]
        if scope_status in {"SCOPE_AWARE", "GENERAL_MUTATION_SET"} and relevant_mutation is not None:
            label = (
                "SCOPE_AWARE_MUTATION"
                if scope_status == "SCOPE_AWARE"
                else "GENERAL_MUTATION"
            )
            components.append((max(0.0, min(100.0, relevant_mutation)), 0.25, label))
        denominator = sum(weight for _, weight, _ in components)
        overall = sum(value * weight for value, weight, _ in components) / denominator
        mode = "HYBRID_" + "_".join(name for _, _, name in components)
    elif execution_status in {"TEST_FAILED", "TIMEOUT"}:
        overall = min(static_score, 40.0)
        mode = "EXECUTION_FAILED_CAPPED"
    else:
        overall = static_score
        mode = "STATIC_ONLY_DYNAMIC_INCONCLUSIVE"

    analyst_label = state.get("dynamic_quality_label", "NOT_ANALYZED")
    analyst_summary = state.get("dynamic_evidence_summary", "No dynamic analyst output.")
    caveat = state.get("critic_score_caveat", "None")
    reasoning = (
        f"Static={round(static_score, 2)} from requirement coverage {round(req_coverage, 2)} "
        f"minus penalties {round(penalties, 2)} "
        f"(assertion={assertion_penalty}, hallucination={hallucination_penalty}, smells={smell_penalty}, "
        f"maintainability={maintainability_penalty}, no_assertion={no_assertion_penalty}). "
        f"Execution={execution_status}. BDD step-execution diagnostic={bdd_diagnostic} (not directly scored). "
        f"Raw mutation={raw_mutation} is retained for suite aggregation; scope-aware test mutation="
        f"{scope_status}:{relevant_mutation}. Mode={mode}. "
        f"Dynamic analyst={analyst_label}: {analyst_summary} Critic caveat={caveat}."
    )
    return {
        "requirement_coverage": round(req_coverage, 2),
        "static_overall_score": round(static_score, 2),
        "overall_score": round(max(0.0, min(100.0, overall)), 2),
        "score_mode": mode,
        "final_reasoning": reasoning,
    }


# ==========================================
# Agent 9: Refiner Agent (conditional, code-only scope)
# ==========================================

FEATURE_ARTIFACT_RE = re.compile(r"(?im)^\s*(feature\s*:|scenario(?:\s+outline)?\s*:|background\s*:)")
REPORT_SCOPE_CLAIM_RE = re.compile(
    r"(?is)(?:\b(?:add(?:ed)?|create(?:d)?|introduce(?:d)?|expand(?:ed)?|modify|modified|"
    r"update(?:d)?|parameteri[sz](?:ed|e)|convert(?:ed)?|change(?:d)?)\b.{0,100}?"
    r"\b(?:scenario(?:\s+outline)?|feature(?:\s+file)?|examples?)\b|"
    r"\b(?:new|additional)\s+scenario\b|\bscenario\s+outline\b)"
)


def _report_claims_feature_change(report: str) -> bool:
    """Detect positive claims about changing immutable BDD feature/scenario artifacts."""
    for match in REPORT_SCOPE_CLAIM_RE.finditer(str(report or '')):
        prefix = str(report)[max(0, match.start() - 32):match.start()].lower()
        # Allow negative disclaimers such as "did not add a new scenario".
        if re.search(r"\b(?:no|not|never|cannot|can't|did\s+not|does\s+not|without)\b", prefix):
            continue
        return True
    return False


def should_run_refiner(state: dict) -> bool:
    """Trigger code repair only for a concrete executable-test defect.

    Low static quality alone is reported but never auto-rewrites working Selenium code.
    This protects API budget and prevents cosmetic recommendations from causing regressions.
    """
    if not _as_bool(state.get("enable_refiner"), True):
        return False
    if not state.get("syntax_passed", True):
        return True

    execution = str(state.get("execution_status", "NOT_RUN"))
    if execution in {"TEST_FAILED", "TIMEOUT"}:
        return True
    if _as_bool(state.get("should_refine"), False):
        return True

    relevant_survivors = int(state.get("relevant_mutants_survived", 0) or 0)
    relevant_score = _as_number(state.get("relevant_mutation_score"))
    threshold = float(state.get("relevant_mutation_refine_threshold", 80.0))
    return (
        relevant_survivors > 0
        and relevant_score is not None
        and relevant_score < threshold
    )

def refiner_skip_agent(state: dict) -> dict:
    original_code = state.get("executable_test_code", "")
    if not _as_bool(state.get("enable_refiner"), True):
        detail = "Refiner disabled by configuration."
        status = "SKIPPED_DISABLED"
    else:
        detail = "No supported repair trigger: no syntax/runtime failure, Analyst repair request, or relevant mutation gap."
        status = "SKIPPED_NOT_NEEDED"
    return {
        "improvement_report": detail,
        "fixed_code": original_code,
        "refiner_status": status,
        "refiner_scope": "STEP_CODE_ONLY",
        "refiner_scope_violation": False,
        "refiner_scope_violation_detail": "",
    }


def _fixed_code_is_step_code_only(code: str) -> bool:
    return not bool(FEATURE_ARTIFACT_RE.search(code))


def refiner_agent(state: dict) -> dict:
    """Propose replacement Behave step code; feature files are explicitly out of scope."""
    if not should_run_refiner(state):
        return refiner_skip_agent(state)

    original_code = state.get("executable_test_code", "")
    relevant_records = [
        record for record in _as_list(state.get("mutation_records", []))
        if str(record.get("scope_relation", "")) == "RELEVANT"
    ]
    dynamic_evidence = {
        "execution_status": state.get("execution_status", "NOT_RUN"),
        "failed_steps": state.get("execution_failed_steps", []),
        "stderr_tail": str(state.get("execution_stderr_tail", ""))[-1600:],
        "bdd_step_execution_diagnostic": state.get("dynamic_coverage_score"),
        "scope_aware_mutation": {
            "status": state.get("mutation_scope_status"),
            "score": state.get("relevant_mutation_score"),
            "relevant_survivors": state.get("scope_relevant_surviving_mutants", []),
            "out_of_scope_survivor_count": state.get("out_of_scope_mutants_survived", 0),
            "uncertain_survivors": state.get("scope_uncertain_surviving_mutants", []),
            "relevant_records": relevant_records,
        },
        "analyst": {
            "status": state.get("dynamic_analysis_status", "NOT_RUN"),
            "root_cause": state.get("root_cause", ""),
            "gaps": state.get("fault_detection_gaps", []),
            "repairs": state.get("prioritized_repairs", []),
        },
    }
    static_evidence = {
        "actions_count": state.get("static_actions_count", 0),
        "assertions_count": state.get("static_assertions_count", 0),
        "stable_selectors": state.get("static_stable_selectors", []),
        "medium_risk_selectors": state.get("static_medium_risk_selectors", []),
        "brittle_selectors": state.get("static_brittle_selectors", []),
        "magic_numbers": state.get("static_magic_numbers", []),
        "maintainability_issues": {
            "selector": state.get("hardcoded_selector_issues", []),
            "duplication": state.get("duplication_issues", []),
            "naming": state.get("naming_issues", []),
            "readability": state.get("readability_issues", []),
        },
    }

    system_prompt = """
You are the Refiner Agent, an elite Selenium/Behave E2E test engineer.
You may modify ONLY the Python Behave step-definition code supplied as Original step code.
The .feature file / BDD scenario is immutable in this pipeline.

Hard constraints:
- Do NOT output Feature:, Scenario:, Scenario Outline:, Background:, or any feature-file text.
- Do NOT claim in either the code OR the improvement report that you added, expanded, parameterized,
  or modified a Scenario, Scenario Outline, Examples block, Feature, or extra test. Those artifacts are immutable.
- If broader suite coverage is desirable, write it only as a "suite-level recommendation outside this code-only repair".
- Preserve the current scenario's behavior and Selenium/Behave framework style; never switch to Playwright.
- Treat only RELEVANT surviving mutants as a reason to add an assertion. Do not expand the
  test to unrelated products merely because raw suite-style mutants survived.
- If no code-only repair is justified, return the original code unchanged and say so.

Return ONLY valid JSON:
{
  "improvement_report": "## QA Diagnostic Report\\n...",
  "fixed_code": "from behave import given\\n..."
}
"""
    context = (
        f"Original UI/app prompt:\n{state.get('prompt', '')}\n\n"
        f"Fine-grained requirements:\n{state.get('fine_grained_reqs', '')}\n\n"
        f"Immutable BDD scenario:\n{state.get('excutable_test_test_case', '')}\n\n"
        f"Original step code:\n{original_code}\n\n"
        f"Consensus:\n{state.get('final_reasoning', '')}\n\n"
        f"Critic guidance:\n{state.get('critic_feedback', '')}\n\n"
        f"Static evidence:\n{json.dumps(static_evidence, ensure_ascii=False, indent=2)}\n\n"
        f"Dynamic evidence:\n{json.dumps(dynamic_evidence, ensure_ascii=False, indent=2)}"
    )
    try:
        response = invoke_with_retry(
            [SystemMessage(content=system_prompt), HumanMessage(content=context)],
            agent_name="Refiner Agent",
        )
        parsed = json.loads(clean_json_output(response))
        fixed = parsed.get("fixed_code", original_code)
        if not isinstance(fixed, str) or not fixed.strip():
            fixed = original_code
        report = str(parsed.get("improvement_report", "No report generated."))
        if _report_claims_feature_change(report):
            return {
                "improvement_report": (
                    "## QA Diagnostic Report\n"
                    "The proposed report was rejected because it claimed to modify an immutable BDD feature/scenario artifact. "
                    "Only Python step code may be changed in this pipeline.\n\n"
                    + report
                ),
                "fixed_code": original_code,
                "refiner_status": "REJECTED_REPORT_SCOPE_VIOLATION",
                "refiner_scope": "STEP_CODE_ONLY",
                "refiner_scope_violation": True,
                "refiner_scope_violation_detail": "Report claimed a feature/scenario-level change.",
            }
        if not _fixed_code_is_step_code_only(fixed):
            return {
                "improvement_report": (
                    "## QA Diagnostic Report\n"
                    "The proposed output was rejected because it included BDD feature-file content. "
                    "This pipeline validates step code only; the original code is retained.\n\n"
                    + report
                ),
                "fixed_code": original_code,
                "refiner_status": "REJECTED_FEATURE_SCOPE_VIOLATION",
                "refiner_scope": "STEP_CODE_ONLY",
                "refiner_scope_violation": True,
                "refiner_scope_violation_detail": "Fixed code contained feature/scenario text.",
            }
        if fixed.strip() == str(original_code).strip():
            return {
                "improvement_report": (
                    "## Scope\nOnly Python Behave step code was eligible for modification; the BDD feature file was unchanged.\n\n"
                    + report
                ),
                "fixed_code": original_code,
                "refiner_status": "NO_CODE_CHANGE",
                "refiner_scope": "STEP_CODE_ONLY",
                "refiner_scope_violation": False,
                "refiner_scope_violation_detail": "",
            }
        return {
            "improvement_report": (
                "## Scope\nOnly Python Behave step code was eligible for modification; the BDD feature file was unchanged.\n\n"
                + report
            ),
            "fixed_code": fixed,
            "refiner_status": "GENERATED",
            "refiner_scope": "STEP_CODE_ONLY",
            "refiner_scope_violation": False,
            "refiner_scope_violation_detail": "",
        }
    except Exception as exc:
        return {
            "improvement_report": f"Failed to generate report: {exc}",
            "fixed_code": original_code,
            "refiner_status": "ERROR",
            "refiner_scope": "STEP_CODE_ONLY",
            "refiner_scope_violation": False,
            "refiner_scope_violation_detail": "",
        }
