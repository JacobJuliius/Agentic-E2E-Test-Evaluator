"""
main.py
Description: The main entry point for the Agentic Test Evaluation System (V3).
Responsibilities: Load dataset CSV -> Process through LangGraph Multi-Agent network -> Export comprehensive evaluation report.

V3 Changes:
- Captures Maintainability Agent outputs (score, issues breakdown)
- Captures AST Static Metrics (actions count, assertions count, hardcoded selectors)
"""

import os
import pandas as pd
from tqdm import tqdm
from graph import app
from dotenv import load_dotenv
import time

load_dotenv()


def run_batch_evaluation(input_csv_path: str, output_csv_path: str):
    """
    Reads the test set CSV, evaluates rows sequentially using the multi-agent graph, and exports results.
    """
    print(f"🚀 [Initialization] Loading evaluation dataset: {input_csv_path}...")

    if not os.path.exists(input_csv_path):
        raise FileNotFoundError(f"❌ Input file not found: {input_csv_path}")

    df = pd.read_csv(input_csv_path)
    print(f"📊 [Dataset Loaded] Discovered {len(df)} test cases for evaluation.")

    # --- Output capture lists (V3: includes static metrics + maintainability) ---
    syntax_status_list = []
    requirement_scores = []
    assertion_scores = []
    hallucination_scores = []
    overall_scores = []
    consensus_reasonings = []

    # V2 channels
    critic_conflict_flags = []
    improvement_reports = []
    fixed_codes = []

    # V3: Static Metrics (Rule-based, from AST analysis)
    static_actions_list = []
    static_assertions_list = []
    static_selectors_list = []
    static_magic_numbers_list = []

    # V3: Maintainability Agent outputs
    maintainability_scores = []
    maintainability_rationales = []
    selector_issues_list = []
    duplication_issues_list = []
    naming_issues_list = []
    readability_issues_list = []

    print("\n🕵️ [Self-Reflective AI Jury Deployed] Initiating V3 Hybrid Evaluation Pipeline...\n")
    print("Pipeline: Syntax+AST → [Requirement ‖ Assertion ‖ Hallucination ‖ Maintainability] → Critic → Consensus → Refiner\n")

    for index, row in tqdm(df.iterrows(), total=len(df), desc="Evaluation Progress"):

        case_id = row.get("id", f"UNK_{index}")
        fine_grained_reqs = row.get("fine_grained_reqs", "")
        executable_test_code = row.get("excutable_test_step_code", "")
        test_case_bdd = row.get("excutable_test_test_case", "")
        req_summary = row.get("requirement_summary", "")
        prompt_text = row.get("prompt", "")

        initial_state = {
            "executable_test_code": str(executable_test_code),
            "fine_grained_reqs": str(fine_grained_reqs),
            "excutable_test_test_case": str(test_case_bdd), 
            "requirement_summary": str(req_summary),        
            "prompt": str(prompt_text),                     
            "revision_count": 0
        }

        try:
            final_state = app.invoke(initial_state)

            syntax_passed = final_state.get("syntax_passed", False)

            # --- Core metrics ---
            syntax_status_list.append("PASSED" if syntax_passed else "FAILED_SYNTAX")
            requirement_scores.append(final_state.get("requirement_coverage", 0.0))
            assertion_scores.append(final_state.get("assertion_score", 0))
            hallucination_scores.append(final_state.get("hallucination_count", 0))
            overall_scores.append(final_state.get("overall_score", 0.0))
            consensus_reasonings.append(final_state.get("final_reasoning", "No summary generated."))

            # --- V2 metrics ---
            critic_conflict_flags.append(final_state.get("has_conflict", False))
            improvement_reports.append(final_state.get("improvement_report", "No report"))
            fixed_codes.append(final_state.get("fixed_code", str(executable_test_code)))

            # --- V3: Static metrics (objective, rule-based) ---
            static_actions_list.append(final_state.get("static_actions_count", 0))
            static_assertions_list.append(final_state.get("static_assertions_count", 0))
            static_selectors_list.append(
                "; ".join(final_state.get("static_hardcoded_selectors", [])) or "None"
            )
            static_magic_numbers_list.append(
                "; ".join(str(n) for n in final_state.get("static_magic_numbers", [])) or "None"
            )

            # --- V3: Maintainability metrics ---
            maintainability_scores.append(final_state.get("maintainability_score", 0))
            maintainability_rationales.append(final_state.get("maintainability_rationale", ""))
            selector_issues_list.append(
                "; ".join(final_state.get("hardcoded_selector_issues", [])) or "None"
            )
            duplication_issues_list.append(
                "; ".join(final_state.get("duplication_issues", [])) or "None"
            )
            naming_issues_list.append(
                "; ".join(final_state.get("naming_issues", [])) or "None"
            )
            readability_issues_list.append(
                "; ".join(final_state.get("readability_issues", [])) or "None"
            )

        except Exception as e:
            print(f"\n❌ [Case Error] Case ID: {case_id} failed during workflow execution: {str(e)}")
            syntax_status_list.append("PIPELINE_ERROR")
            requirement_scores.append(0.0)
            assertion_scores.append(0)
            hallucination_scores.append(0)
            overall_scores.append(0.0)
            consensus_reasonings.append(f"Execution interrupted: {str(e)}")
            critic_conflict_flags.append(False)
            improvement_reports.append("Error during processing.")
            fixed_codes.append(str(executable_test_code))
            static_actions_list.append(0)
            static_assertions_list.append(0)
            static_selectors_list.append("Error")
            static_magic_numbers_list.append("Error")
            maintainability_scores.append(0)
            maintainability_rationales.append(f"Error: {str(e)}")
            selector_issues_list.append("Error")
            duplication_issues_list.append("Error")
            naming_issues_list.append("Error")
            readability_issues_list.append("Error")

        time.sleep(15)

    # --- Write all columns to the output DataFrame ---
    df["eval_syntax_status"]        = syntax_status_list
    df["eval_requirement_score"]    = requirement_scores
    df["eval_assertion_score"]      = assertion_scores
    df["eval_hallucination_count"]  = hallucination_scores
    df["eval_overall_score"]        = overall_scores
    df["eval_final_summary"]        = consensus_reasonings

    # V2
    df["eval_has_conflict"]         = critic_conflict_flags
    df["eval_improvement_report"]   = improvement_reports
    df["eval_fixed_code"]           = fixed_codes

    # V3: Static metrics
    df["static_actions_count"]          = static_actions_list
    df["static_assertions_count"]       = static_assertions_list
    df["static_hardcoded_selectors"]    = static_selectors_list
    df["static_magic_numbers"]          = static_magic_numbers_list

    # V3: Maintainability
    df["eval_maintainability_score"]     = maintainability_scores
    df["eval_maintainability_rationale"] = maintainability_rationales
    df["eval_selector_issues"]           = selector_issues_list
    df["eval_duplication_issues"]        = duplication_issues_list
    df["eval_naming_issues"]             = naming_issues_list
    df["eval_readability_issues"]        = readability_issues_list

    print(f"\n💾 [Data Persistence] Exporting V3 evaluation report to: {output_csv_path}...")
    output_dir = os.path.dirname(output_csv_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    df.to_csv(output_csv_path, index=False, encoding="utf-8-sig")
    print("✨ V3 Evaluation completed successfully!")
    print(f"   Columns exported: {len(df.columns)} total ({len([c for c in df.columns if c.startswith('eval_') or c.startswith('static_')])} evaluation columns)")


if __name__ == "__main__":
    INPUT_FILE  = "data/e2edev_sample.csv"
    OUTPUT_FILE = "data/evaluation_results_v3.csv"
    run_batch_evaluation(INPUT_FILE, OUTPUT_FILE)