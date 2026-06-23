"""
main.py
Description: The main entry point for the Agentic Test Evaluation System.
Responsibilities: Load dataset CSV -> Process through LangGraph Multi-Agent network -> Export comprehensive evaluation report.
"""

import os
import pandas as pd
from tqdm import tqdm
from graph import app
from dotenv import load_dotenv

load_dotenv()

def run_batch_evaluation(input_csv_path: str, output_csv_path: str):
    """
    Reads the test set CSV, evaluates rows sequentially using the multi-agent graph, and exports results.
    """
    print(f"🚀 [Initialization] Loading evaluation dataset: {input_csv_path}...")
    
    if not os.path.exists(input_csv_path):
        raise FileNotFoundError(f"❌ Input file not found: {input_csv_path}")

    # 1. Load dataset with Pandas
    df = pd.read_csv(input_csv_path)
    print(f"📊 [Dataset Loaded] Discovered {len(df)} test cases for evaluation.")

    # 2. Initialize localized memory arrays designed to securely capture distinct agent output metric channels
    syntax_status_list = []
    requirement_scores = []
    assertion_scores = []
    hallucination_scores = []
    overall_scores = []
    consensus_reasonings = []

    # 3. Begin batch processing loop with tqdm progress bar
    print("\n🕵️ [Multi-Agent Jury Deployed] Initiating automated parallel evaluation pipeline...\n")
    for index, row in tqdm(df.iterrows(), total=len(df), desc="Evaluation Progress"):
        
        case_id = row.get("id", f"UNK_{index}")
        fine_grained_reqs = row.get("fine_grained_reqs", "")
        # Updated variable name to fix the typo from the CSV
        executable_test_code = row.get("excutable_test_step_code", "")

        # Construct Initial State for LangGraph
        initial_state = {
            "executable_test_code": str(executable_test_code),
            "fine_grained_reqs": str(fine_grained_reqs),
        }

        try:
            # 4. Invoke LangGraph engine
            final_state = app.invoke(initial_state)
            
            # 5. Extract finalized metrics from state output
            syntax_passed = final_state.get("syntax_passed", False)
            
            # Append scores directly from the flattened state fields
            syntax_status_list.append("PASSED" if syntax_passed else "FAILED_SYNTAX")
            requirement_scores.append(final_state.get("requirement_coverage", 0.0))
            assertion_scores.append(final_state.get("assertion_score", 0))
            hallucination_scores.append(final_state.get("hallucination_count", 0))
            overall_scores.append(final_state.get("overall_score", 0.0))
            consensus_reasonings.append(final_state.get("final_reasoning", "No summary generated."))

        except Exception as e:
            # Robust exception handling to prevent pipeline collapse
            print(f"\n❌ [Case Error] Case ID: {case_id} failed during workflow execution: {str(e)}")
            syntax_status_list.append("PIPELINE_ERROR")
            requirement_scores.append(0.0)
            assertion_scores.append(0)
            hallucination_scores.append(0)
            overall_scores.append(0.0)
            consensus_reasonings.append(f"Execution interrupted: {str(e)}")

    # 6. Append evaluation metrics back into the original DataFrame
    df["eval_syntax_status"] = syntax_status_list
    df["eval_requirement_score"] = requirement_scores
    df["eval_assertion_score"] = assertion_scores
    df["eval_hallucination_count"] = hallucination_scores
    df["eval_overall_score"] = overall_scores
    df["eval_final_summary"] = consensus_reasonings

    # 7. Persist results
    print(f"\n💾 [Data Persistence] Exporting evaluation report to: {output_csv_path}...")
    output_dir = os.path.dirname(output_csv_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    df.to_csv(output_csv_path, index=False, encoding="utf-8-sig")
    print("Evaluation completed successfully")

if __name__ == "__main__":
    INPUT_FILE = "data/e2edev_sample.csv"
    OUTPUT_FILE = "data/evaluation_results.csv"
    run_batch_evaluation(INPUT_FILE, OUTPUT_FILE)