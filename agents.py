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

load_dotenv()

# Initialize the LLM
llm = ChatGoogleGenerativeAI(
    model="gemini-3.1-flash-lite",
    temperature=0
)

# ==========================================
# Utility Functions
# ==========================================

def invoke_with_retry(prompt, max_retries: int = 3, wait_time: int = 60) -> str:
    """
    Wrapper function to invoke the LLM with retry logic for handling rate limits.

    Rate-limit strategy (15 RPM = 1 request per 4 seconds):
    - After every successful call, sleep 4 seconds before returning.
      This ensures the caller never fires the next LLM call sooner than 4s later,
      keeping the pipeline safely under the 15 RPM ceiling even in serial mode.
    - On a 429 / RESOURCE_EXHAUSTED error, back off for `wait_time` seconds and retry.
    """
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
            # ── Rate-limit guard: 15 RPM ≈ 1 req / 4 s ──
            time.sleep(4)
            return result
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                if attempt < max_retries - 1:
                    print(f"\n⚠️ Rate limit hit. Waiting {wait_time}s... ({attempt + 2}/{max_retries} attempt)")
                    time.sleep(wait_time)
                else:
                    print(f"\n❌ Failed after {max_retries} attempts.")
                    raise e
            else:
                raise e

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
    """
    Rule-based static analysis using Python's AST module.
    Extracts objective, countable metrics from the test code without any LLM call.
    This is the 'Rule-based' half of the Hybrid Evaluation strategy.
    """
    metrics = {
        "actions_count": 0,
        "assertions_count": 0,
        "hardcoded_selectors": [],
        "magic_numbers": [],
        "action_methods": [],
        "assertion_methods": [],
    }

    try:
        tree = ast.parse(code)
    except SyntaxError:
        # If syntax is broken, static analysis can't proceed — return empty metrics
        return metrics

    # Playwright/Selenium action and assertion method names to detect
    ACTION_METHODS = {
        "click", "fill", "type", "press", "select_option", "check", "uncheck",
        "hover", "focus", "tap", "double_click", "right_click", "drag_to",
        "navigate", "goto", "reload", "go_back", "go_forward",
        "find_element", "find_elements", "send_keys", "submit",
    }
    ASSERTION_METHODS = {
        "expect", "assert_", "to_be_visible", "to_have_text", "to_have_value",
        "to_be_enabled", "to_be_checked", "to_contain_text", "to_have_url",
        "to_have_title", "assert", "assertEqual", "assertTrue", "assertFalse",
        "assertIn", "assertIsNotNone", "assertRaises",
    }

    # Walk AST to find method calls
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            method_name = None

            if isinstance(func, ast.Attribute):
                method_name = func.attr
            elif isinstance(func, ast.Name):
                method_name = func.id

            if method_name:
                if method_name in ACTION_METHODS:
                    metrics["actions_count"] += 1
                    metrics["action_methods"].append(method_name)
                if method_name in ASSERTION_METHODS:
                    metrics["assertions_count"] += 1
                    metrics["assertion_methods"].append(method_name)

    # Detect hardcoded/brittle CSS/XPath selectors using regex
    # Patterns like: "#some-id", ".some-class", "xpath=//...", "[data-testid=...]"
    BRITTLE_SELECTOR_PATTERNS = [
        r'"\s*#[\w\-]+\s*"',          # "#id"
        r'"\s*\.[\w\-]+\s*"',          # ".class"
        r'"xpath=//.*?"',               # "xpath=//..."
        r'"//.*?"',                     # "//..." (bare XPath)
        r'"\[class[^"]*\]"',            # "[class=...]"
        r'By\.XPATH,\s*["\'].*?["\']', # Selenium By.XPATH
        r'By\.CSS_SELECTOR,\s*["\'].*?["\']',  # Selenium By.CSS_SELECTOR
    ]
    for pattern in BRITTLE_SELECTOR_PATTERNS:
        found = re.findall(pattern, code)
        metrics["hardcoded_selectors"].extend(found)

    # Detect magic numbers (bare integer/float literals not equal to 0 or 1)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            if node.value not in (0, 1, -1, True, False) and abs(node.value) > 1:
                metrics["magic_numbers"].append(node.value)

    # Deduplicate
    metrics["hardcoded_selectors"] = list(set(metrics["hardcoded_selectors"]))
    metrics["magic_numbers"] = list(set(metrics["magic_numbers"]))

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
        f"AST scan found {static_assertions_count} assertion call(s): {static_assertion_methods}.\n"
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
    Runs in parallel with the other LLM evaluators.

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
    hardcoded_selectors = state.get("static_hardcoded_selectors", [])
    magic_numbers = state.get("static_magic_numbers", [])
    actions_count = state.get("static_actions_count", "N/A")
    assertions_count = state.get("static_assertions_count", "N/A")

    static_context = f"""
[STATIC ANALYSIS CONTEXT (Objective, Rule-based — treat as facts)]
- Hardcoded/brittle selectors detected by AST scan: {hardcoded_selectors if hardcoded_selectors else 'None found'}
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
    1. **Selector Robustness**: Are locators fragile (e.g., raw CSS IDs, XPath)? Prefer data-testid or accessible roles.
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
# Agent 6: Critic Agent
# ==========================================

def critic_agent(state: dict) -> dict:
    """Critic Agent: Reviews the evaluations from the 4 parallel agents for logical conflicts."""
    if not state.get("syntax_passed", True):
        return {
            "has_conflict": False,
            "critic_feedback": "Skipped due to syntax failure.",
            "revision_count": state.get("revision_count", 0)
        }

    current_revision = state.get("revision_count", 0)

    eval_summary = {
        "Requirement Evaluation": state.get("missing_requirements", []),
        "Assertion Evaluation": state.get("weak_assertions", []),
        "Hallucination Evaluation": state.get("hallucinations", []),
        "Maintainability Evaluation": {
            "score": state.get("maintainability_score", 0),
            "selector_issues": state.get("hardcoded_selector_issues", []),
            "rationale": state.get("maintainability_rationale", "")
        }
    }

    system_prompt = """
    You are the "Critic Agent", the Chief Inspector of the QA jury.
    Review the evaluations from the Requirement, Assertion, Hallucination, and Maintainability agents.
    Look for logical contradictions, e.g.:
    - Requirement Agent says 'Login is missing', but Hallucination Agent says 'Login is fabricated/hallucinated'.
    - Assertion Agent says 'no assertions found', but Maintainability Agent describes assertion readability issues (implying assertions exist).
    - Maintainability Agent flags a selector as brittle, but Hallucination Agent also flags the same action as a hallucination (double penalty risk).

    If there is a conflict, set "has_conflict" to true and provide "critic_feedback" detailing what needs to be resolved.
    If they are generally consistent, set "has_conflict" to false.

    Expected JSON Schema:
    {
        "has_conflict": true or false,
        "critic_feedback": "<Detailed feedback to resolve conflicts>"
    }
    """

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Current Evaluations:\n{json.dumps(eval_summary, indent=2)}")
    ]

    try:
        response = invoke_with_retry(messages)
        parsed_result = json.loads(clean_json_output(response))
        return {
            "has_conflict": parsed_result.get("has_conflict", False),
            "critic_feedback": parsed_result.get("critic_feedback", "No conflicts detected."),
            "revision_count": current_revision + 1
        }
    except Exception as e:
        print(f"[Critic Agent Error]: {str(e)}")
        return {
            "has_conflict": False,
            "critic_feedback": "Error analyzing conflict.",
            "revision_count": current_revision + 1
        }


# ==========================================
# Agent 7: Consensus Agent (ENHANCED)
# ==========================================

def consensus_agent(state: dict) -> dict:
    """
    Consensus Agent (ENHANCED): Aggregates evaluations from all 4 agents
    and calculates the final weighted score, now including Maintainability.
    """
    if not state.get("syntax_passed", True):
        return {
            "requirement_coverage": 0,
            "overall_score": 0,
            "final_reasoning": "Fatal Error: Syntax check failed."
        }

    # --- Requirement Coverage (base score) ---
    total_reqs = state.get("total_requirements_count", 1)
    covered = len(state.get("covered_requirements", []))
    partial = len(state.get("partially_covered_requirements", []))
    req_coverage_score = ((covered + 0.5 * partial) / max(total_reqs, 1)) * 100

    # --- Penalties from LLM agents ---
    weak_assert_penalty = len(state.get("weak_assertions", [])) * 5
    hallucination_penalty = state.get("hallucination_count", 0) * 10
    test_smell_penalty = len(state.get("test_smells", [])) * 5

    # --- NEW: Maintainability penalty (normalized from 0-100 score) ---
    # A maintainability score of 100 = 0 penalty, score of 0 = max 15 point penalty
    maintainability_score = state.get("maintainability_score", 100)
    maintainability_penalty = round((1 - maintainability_score / 100) * 15, 2)

    # --- NEW: Static metric bonuses/penalties ---
    static_assertions = state.get("static_assertions_count", 0)
    static_actions = state.get("static_actions_count", 0)
    # If no assertions at all detected by AST, apply additional objective penalty
    static_no_assertion_penalty = 10 if static_assertions == 0 and static_actions > 0 else 0

    total_penalty = (
        weak_assert_penalty
        + hallucination_penalty
        + test_smell_penalty
        + maintainability_penalty
        + static_no_assertion_penalty
    )

    overall_score = max(0, min(100, req_coverage_score - total_penalty))

    reasoning = (
        f"Coverage: {round(req_coverage_score, 2)}/100. "
        f"Total Penalty: -{round(total_penalty, 2)} "
        f"(Weak Assertions: -{weak_assert_penalty}, "
        f"Hallucinations: -{hallucination_penalty}, "
        f"Smells: -{test_smell_penalty}, "
        f"Maintainability: -{maintainability_penalty}, "
        f"No-Assertion Penalty: -{static_no_assertion_penalty}). "
        f"[Static Metrics] Actions: {static_actions}, Assertions: {static_assertions}. "
        f"Maintainability Score: {maintainability_score}/100."
    )

    return {
        "requirement_coverage": round(req_coverage_score, 2),
        "overall_score": round(overall_score, 2),
        "final_reasoning": reasoning
    }


# ==========================================
# Agent 8: Refiner Agent
# ==========================================

def refiner_agent(state: dict) -> dict:
    """Refiner Agent: Generates a diagnostic report and self-heals (fixes) the test code."""
    reqs = state.get("fine_grained_reqs", "")
    code = state.get("executable_test_code", "")
    reasoning = state.get("final_reasoning", "")
    syntax_error = state.get("error_message", "")
    maintainability_issues = {
        "selector_issues": state.get("hardcoded_selector_issues", []),
        "duplication_issues": state.get("duplication_issues", []),
        "naming_issues": state.get("naming_issues", []),
        "readability_issues": state.get("readability_issues", []),
    }
    static_metrics = {
        "actions_count": state.get("static_actions_count", 0),
        "assertions_count": state.get("static_assertions_count", 0),
        "hardcoded_selectors": state.get("static_hardcoded_selectors", []),
        "magic_numbers": state.get("static_magic_numbers", []),
    }

    system_prompt = """
    You are the "Refiner Agent", an Elite Test Engineer.
    Based on the evaluator's reasoning, requirements, static analysis findings, and the original code, you must:
    1. Create a structured Markdown 'Improvement Report' summarizing ALL issues and fixes (including maintainability and static metric findings).
    2. Provide the completely rewritten, 'Fixed Code' (Playwright or Selenium/Behave) that heals all flaws:
       - Fixes syntax errors
       - Adds missing assertions
       - Removes hallucinated steps
       - Replaces brittle selectors with data-testid or accessible role locators
       - Eliminates magic numbers (use named constants)
       - Removes code duplication (extract helpers/Page Objects where appropriate)

    Output Constraint: Return ONLY a valid JSON object. Escape all newlines as \\n and double quotes as \\" within strings.
    Expected JSON Schema:
    {
      "improvement_report": "## QA Diagnostic Report\\n...",
      "fixed_code": "import re\\nfrom playwright.sync_api import Page\\n..."
    }
    """
    test_case_bdd = state.get("excutable_test_test_case", "")
    
    context = (
        f"Requirements:\n{reqs}\n\n"
        f"BDD Test Case (Intent):\n{test_case_bdd}\n\n"   # <--- 加上这一行
        f"Original Code:\n{code}\n\n"
        f"Evaluation Summary/Syntax Error:\n{reasoning}\n{syntax_error}\n\n"
        f"Maintainability Issues:\n{json.dumps(maintainability_issues, indent=2)}\n\n"
        f"Static Metrics (Objective):\n{json.dumps(static_metrics, indent=2)}"
    )
    

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=context)]

    try:
        response = invoke_with_retry(messages)
        parsed_result = json.loads(clean_json_output(response))
        return {
            "improvement_report": parsed_result.get("improvement_report", "No report generated."),
            "fixed_code": parsed_result.get("fixed_code", code)
        }
    except Exception as e:
        print(f"[Refiner Agent Error]: {str(e)}")
        return {
            "improvement_report": f"Failed to generate report: {str(e)}",
            "fixed_code": code
        }