from typing import TypedDict, List
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv
from agents import (
    syntax_linter_agent,
    requirement_alignment_agent,
    assertion_quality_agent,
    hallucination_smell_agent,
    maintainability_agent,   
    critic_agent,
    consensus_agent,
    refiner_agent
)

load_dotenv()

# ==========================================
# 1. Define the State Dictionary (V3.1 - Enhanced Context)
# ==========================================
class E2EEvalState(TypedDict):
    # --- Base Inputs ---
    fine_grained_reqs: str
    executable_test_code: str
    
    # --- NEW CONTEXT INPUTS ---
    excutable_test_test_case: str  # BDD intent
    requirement_summary: str       # High-level context
    prompt: str                    # Original LLM generation prompt for UI specs

    # --- Syntax Gatekeeper Outputs ---
    syntax_passed: bool
    error_message: str

    # --- AST Static Metrics ---
    static_actions_count: int
    static_assertions_count: int
    static_hardcoded_selectors: List[str]
    static_magic_numbers: List[float]
    static_action_methods: List[str]
    static_assertion_methods: List[str]

    # --- Requirement Agent Outputs ---
    total_requirements_count: int
    covered_requirements: List[str]
    partially_covered_requirements: List[str]
    missing_requirements: List[str]

    # --- Assertion Agent Outputs ---
    assertion_score: int
    strong_assertions: List[str]
    weak_assertions: List[str]
    missing_assertions_rationale: str

    # --- Hallucination & Smell Agent Outputs ---
    hallucination_count: int
    hallucinations: List[str]
    test_smells: List[str]

    # --- Maintainability Agent Outputs ---
    maintainability_score: int
    hardcoded_selector_issues: List[str]
    duplication_issues: List[str]
    naming_issues: List[str]
    readability_issues: List[str]
    maintainability_rationale: str

    # --- Critic Agent Outputs (Reflection) ---
    critic_feedback: str
    revision_count: int
    has_conflict: bool

    # --- Consensus Agent Outputs ---
    overall_score: float
    requirement_coverage: float
    final_reasoning: str

    # --- Refiner Agent Outputs (Self-Healing) ---
    improvement_report: str
    fixed_code: str


# ==========================================
# 2. Define Conditional Routing Logic
# ==========================================
def route_after_syntax(state: E2EEvalState):
    if state.get("syntax_passed", False):
        return "requirement_node"
    else:
        return "consensus_node"

def route_after_critic(state: E2EEvalState):
    if state.get("has_conflict", False) and state.get("revision_count", 0) < 2:
        return "requirement_node"
    else:
        return "consensus_node"

# ==========================================
# 3. Build Graph Orchestration
# ==========================================
workflow = StateGraph(E2EEvalState)

workflow.add_node("syntax_node",          syntax_linter_agent)
workflow.add_node("requirement_node",     requirement_alignment_agent)
workflow.add_node("assertion_node",       assertion_quality_agent)
workflow.add_node("hallucination_node",   hallucination_smell_agent)
workflow.add_node("maintainability_node", maintainability_agent)
workflow.add_node("critic_node",          critic_agent)
workflow.add_node("consensus_node",       consensus_agent)
workflow.add_node("refiner_node",         refiner_agent)

workflow.set_entry_point("syntax_node")

workflow.add_conditional_edges(
    "syntax_node",
    route_after_syntax,
    {
        "requirement_node": "requirement_node",
        "consensus_node":   "consensus_node",
    }
)

workflow.add_edge("requirement_node",     "assertion_node")
workflow.add_edge("assertion_node",       "hallucination_node")
workflow.add_edge("hallucination_node",   "maintainability_node")
workflow.add_edge("maintainability_node", "critic_node")

workflow.add_conditional_edges(
    "critic_node",
    route_after_critic,
    {
        "requirement_node": "requirement_node",
        "consensus_node":   "consensus_node",
    }
)

workflow.add_edge("consensus_node", "refiner_node")
workflow.add_edge("refiner_node", END)

app = workflow.compile()