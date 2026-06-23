"""
Description: This file contains all core Agent nodes for the Agentic Test Evaluation System.
Includes:
1. Syntax & Linter Agent (Static Syntax Checker)
2. Requirement Alignment Agent (Requirement Coverage Evaluator)
3. Assertion Quality Agent (Assertion Quality Evaluator)
4. Hallucination & Smell Agent (Noise & Bad Practice Filter)
5. Consensus Agent (Final Score Aggregator)
"""

import ast
import json
import os
import time
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage
from dotenv import load_dotenv

load_dotenv()

# Initialize the LLM
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash", 
    temperature=0
)

def invoke_with_retry(prompt: str, max_retries: int = 3, wait_time: int = 60) -> str:
    """wrapper function to invoke the LLM with retry logic for handling rate limits."""
    for attempt in range(max_retries):
        try:
            # Directly invoke the LLM with the provided prompt
            res = llm.invoke(prompt)
            return res.content
        except Exception as e:
            error_msg = str(e)
            # to check if the error is due to rate limiting or resource exhaustion
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                if attempt < max_retries - 1:
                    print(f"\n⚠️ {wait_time}  {attempt + 2}/{max_retries} th attempt...")
                    time.sleep(wait_time)
                else:
                    print(f"\n❌  {max_retries} th time failed.")
                    raise e
            else:
                # For other exceptions, raise immediately
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

def syntax_linter_agent(state: dict) -> dict:
    """
    The Gatekeeper: Statically checks the generated Playwright code for severe syntax errors.
    """
    test_code = state.get("executable_test_code", "")
    
    try:
        # Attempt to parse the code into an Abstract Syntax Tree (AST)
        ast.parse(test_code)
        return {
            "syntax_passed": True,
            "error_message": ""
        }
    except SyntaxError as e:
        # Block execution if parsing fails
        return {
            "syntax_passed": False,
            "error_message": f"Fatal syntax error detected: {str(e)}"
        }

def requirement_alignment_agent(state: dict) -> dict:
    """Requirement Alignment Agent: Evaluates business requirement coverage."""
    reqs = state.get("fine_grained_reqs", "")
    code = state.get("executable_test_code", "")
    
    system_prompt = """
    You are an Expert QA Automation Architect acting as the "Requirement Alignment Agent".
    Evaluate how well the Playwright test script covers the business requirements.
    
    Evaluation Workflow:
    1. Identify Atomic Requirements.
    2. Analyze Code Mapping for actions and assertions.
    3. Categorize Coverage into Covered, Partially Covered, or Missing.
       
    Output Constraint: You MUST return ONLY a valid JSON object matching the schema below.
    
    Expected JSON Schema: 
    {{
        "total_requirements_count": 0, 
        "covered_requirements": ["<req>"], 
        "partially_covered_requirements": ["<req and brief reason>"], 
        "missing_requirements": ["<req>"] 
    }}
    """
    
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Requirements:\n{reqs}\n\nCode:\n{code}")
    ]
    
    try:
        response = invoke_with_retry(messages)
        cleaned_output = clean_json_output(response)
        parsed_result = json.loads(cleaned_output)
        
        return {
            "total_requirements_count": parsed_result.get("total_requirements_count", 0),
            "covered_requirements": parsed_result.get("covered_requirements", []),
            "partially_covered_requirements": parsed_result.get("partially_covered_requirements", []),
            "missing_requirements": parsed_result.get("missing_requirements", [])
        }
    except Exception as e:
        print(f"[Requirement Agent Error]: {str(e)}")
        return {
            "total_requirements_count": 0,
            "covered_requirements": [],
            "partially_covered_requirements": [],
            "missing_requirements": [f"Evaluation failed: {str(e)}"]
        }

def assertion_quality_agent(state: dict) -> dict:
    """Assertion Quality Agent: Scans and evaluates assertions (e.g., expect) in the code."""
    
    # Short-circuit mechanism
    if not state.get("syntax_passed", True):
        return {
            "assertion_score": 0,
            "strong_assertions": [],
            "weak_assertions": ["Skipped: Syntax check failed."],
            "missing_assertions_rationale": "Code syntax invalid, evaluation bypassed."
        }

    fine_grained_reqs = state.get("fine_grained_reqs", "")
    code = state.get("executable_test_code", "")

    prompt_template = """System Prompt: Assertion Quality Evaluator

    You are an Expert QA Automation Architect acting as the "Assertion Quality Agent".
    Strictly evaluate the quality, robustness, and business relevance of the assertions.

    Output Constraint: Return ONLY a valid JSON object.

    Expected JSON Schema:
    {{
      "assertion_score": 0,
      "strong_assertions": ["<List assertions strong>"],
      "weak_assertions": ["<List assertions weak>"],
      "missing_assertions_rationale": "<Describe assertions missing>"
    }}
    """

    prompt = PromptTemplate.from_template(prompt_template)
    formatted_prompt = prompt.format(code=state.get("executable_test_code", ""), reqs=state.get("fine_grained_reqs", ""))
    

    try:
        response = invoke_with_retry(formatted_prompt)
        cleaned_output = clean_json_output(response)
        parsed_result = json.loads(cleaned_output)

        return {
            "assertion_score": parsed_result.get("assertion_score", 0),
            "strong_assertions": parsed_result.get("strong_assertions", []),
            "weak_assertions": parsed_result.get("weak_assertions", []),
            "missing_assertions_rationale": parsed_result.get("missing_assertions_rationale", "")
        }

    except Exception as e:
        print(f"[Assertion Agent Error]: {str(e)}")
        return {
            "assertion_score": 0,
            "strong_assertions": [],
            "weak_assertions": ["JSON Parsing Error or API Timeout"],
            "missing_assertions_rationale": "Evaluation failed due to LLM response format issue."
        }

def hallucination_smell_agent(state: dict) -> dict:
    """Hallucination & Smell Agent: Identifies fabricated actions and bad code smells."""
    
    # Short-circuit mechanism
    if not state.get("syntax_passed", True):
        return {
            "hallucination_count": 0,
            "hallucinations": ["Skipped due to syntax error"],
            "test_smells": ["Skipped due to syntax error"]
        }

    fine_grained_reqs = state.get("fine_grained_reqs", "")
    code = state.get("executable_test_code", "")

    system_prompt = """
    You are an Expert QA Automation Architect acting as the "Hallucination & Smell Agent".
    Identify fabricated business actions (hallucinations) and bad testing practices (test smells).

    Output Constraint: Return ONLY a valid JSON object.

    Expected JSON Schema:
    {{
      "hallucination_count": 0,
      "hallucinations": ["<List actions fabricated>"],
      "test_smells": ["<List bad hardcoded like practices sleeps>"]
    }}
    """

    prompt_template = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("user", "Business Requirements:\n{reqs}\n\nTest Script:\n{code}")
    ])
    formatted_prompt = prompt_template.format(code=state.get("executable_test_code"), reqs=state.get("fine_grained_reqs"))
    
    
    try:
        response = invoke_with_retry(formatted_prompt)
        
        cleaned_output = clean_json_output(response)
        parsed_result = json.loads(cleaned_output)
        
        return {
            "hallucination_count": parsed_result.get("hallucination_count", 0),
            "hallucinations": parsed_result.get("hallucinations", []),
            "test_smells": parsed_result.get("test_smells", [])
        }
        
    except Exception as e:
        print(f"[Hallucination Agent Error]: {str(e)}")
        return {
            "hallucination_count": 0,
            "hallucinations": [f"Error parsing LLM output: {str(e)}"],
            "test_smells": []
        }
    
def consensus_agent(state: dict) -> dict:
    """Consensus Agent: The Orchestrator that aggregates evaluations and calculates scores."""
    
    # Immediate failure if syntax gatekeeper blocked execution
    if not state.get("syntax_passed", True):
        return {
            "requirement_coverage": 0,
            "overall_score": 0,
            "final_reasoning": "Fatal Error: Syntax check failed. The test script is not executable."
        }

    # Extract flat fields mapped by previous agents
    total_reqs = state.get("total_requirements_count", 1)
    covered = len(state.get("covered_requirements", []))
    partial = len(state.get("partially_covered_requirements", []))
    
    # Calculate Base Requirement Coverage
    req_coverage_score = ((covered + 0.5 * partial) / max(total_reqs, 1)) * 100

    # Calculate Penalties
    weak_assert_penalty = len(state.get("weak_assertions", [])) * 5
    hallucination_penalty = state.get("hallucination_count", 0) * 10
    test_smell_penalty = len(state.get("test_smells", [])) * 5

    total_penalty = weak_assert_penalty + hallucination_penalty + test_smell_penalty

    # Calculate Overall Score bounded between 0 and 100
    overall_score = req_coverage_score - total_penalty
    overall_score = max(0, min(100, overall_score))

    # Generate final reasoning report
    reasoning = (
        f"Base Requirement Coverage: {round(req_coverage_score, 2)}/100. "
        f"Total Penalties: -{total_penalty} "
        f"(Weak Assertions: -{weak_assert_penalty}, "
        f"Hallucinations: -{hallucination_penalty}, "
        f"Test Smells: -{test_smell_penalty})."
    )

    return {
        "requirement_coverage": round(req_coverage_score, 2),
        "overall_score": round(overall_score, 2),
        "final_reasoning": reasoning
    }