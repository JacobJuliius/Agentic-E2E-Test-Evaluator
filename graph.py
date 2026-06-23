from typing import TypedDict, List
from langgraph.graph import StateGraph, END
from dotenv import load_dotenv
from agents import (
    syntax_linter_agent,    
    requirement_alignment_agent,
    assertion_quality_agent,
    hallucination_smell_agent,
    consensus_agent
)

load_dotenv()

# ==========================================
# 1. Define the State Dictionary (Flat Keys)
# ==========================================
class E2EEvalState(TypedDict):
    # --- Base Inputs ---
    fine_grained_reqs: str
    executable_test_code: str
    
    # --- Syntax Gatekeeper Outputs ---
    syntax_passed: bool
    error_message: str
    
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
    
    # --- Consensus Agent Outputs ---
    overall_score: float
    requirement_coverage: float
    final_reasoning: str

# ==========================================
# 2. Define Conditional Routing Logic
# ==========================================
def route_after_syntax(state: E2EEvalState):
    """
    Routing logic post-syntax check:
    If passed -> Trigger the three parallel business evaluators.
    If failed -> Route directly to consensus for a final 0 score.
    """
    if state.get("syntax_passed", False):
        return ["requirement_node", "assertion_node", "hallucination_node"]
    else:
        return ["consensus_node"]

# ==========================================
# 3. Build Graph Orchestration
# ==========================================
workflow = StateGraph(E2EEvalState)

# Add Nodes
workflow.add_node("syntax_node", syntax_linter_agent)
workflow.add_node("requirement_node", requirement_alignment_agent)
workflow.add_node("assertion_node", assertion_quality_agent)
workflow.add_node("hallucination_node", hallucination_smell_agent)
workflow.add_node("consensus_node", consensus_agent)

# Set Entry Point
workflow.set_entry_point("syntax_node")

# Add Conditional Edges
workflow.add_conditional_edges(
    "syntax_node",
    route_after_syntax,
    {
        "requirement_node": "requirement_node",
        "assertion_node": "assertion_node",
        "hallucination_node": "hallucination_node",
        "consensus_node": "consensus_node"
    }
)

# Merge parallel evaluators into consensus
workflow.add_edge("requirement_node", "consensus_node")
workflow.add_edge("assertion_node", "consensus_node")
workflow.add_edge("hallucination_node", "consensus_node")

# Set Exit Point
workflow.add_edge("consensus_node", END)

# Compile Application
app = workflow.compile()