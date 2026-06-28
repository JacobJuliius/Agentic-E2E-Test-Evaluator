"""
main_sub.py
Description: Subset evaluation runner for pipeline smoke-testing.
Selects the first 10 rows from the dataset and runs them through the V3
Hybrid Evaluation Pipeline (with enhanced context) to verify correctness and inspect output quality.

Usage:
    python main_sub.py
"""

import os
import pandas as pd
from tqdm import tqdm
from graph import app
from dotenv import load_dotenv

load_dotenv()

SUBSET_SIZE = 10
INPUT_FILE  = "data/e2edev_sample.csv" 
OUTPUT_FILE = "data/evaluation_results_v3_sub10.csv"

# ==========================================
# Pretty-print helper for console inspection
# ==========================================
def print_case_summary(index: int, case_id, final_state: dict):
    """Print a concise per-case report to the console for quick inspection."""
    syntax_ok   = final_state.get("syntax_passed", False)
    req_cov     = final_state.get("requirement_coverage", 0.0)
    assert_sc   = final_state.get("assertion_score", 0)
    halluc      = final_state.get("hallucination_count", 0)
    maintain_sc = final_state.get("maintainability_score", 0)
    overall     = final_state.get("overall_score", 0.0)
    has_conflict= final_state.get("has_conflict", False)

    print(f"\n[Case {index+1}/{SUBSET_SIZE}] ID: {case_id}")
    if not syntax_ok:
        print(f"  ❌ Syntax FAILED | Error: {final_state.get('error_message', '')[:100]}...")
    else:
        print(f"  ✅ Syntax Passed | Conflict Triggered: {has_conflict}")
        print(f"  📊 Scores -> Req: {req_cov}% | Assert: {assert_sc} | Halluc: {halluc} | Maint: {maintain_sc} || OVERALL: {overall}")


def run_subset():
    print(f"🚀 [Initialization] Loading subset ({SUBSET_SIZE} cases) from: {INPUT_FILE}...")
    
    if not os.path.exists(INPUT_FILE):
        print(f"❌ Error: {INPUT_FILE} not found! Please check the path.")
        return

    # 只读取前 10 条数据用于快速调试
    df = pd.read_csv(INPUT_FILE).head(SUBSET_SIZE)
    
    # 用于最终统计的列表
    syntax_status_list = []
    overall_scores = []
    requirement_scores = []
    assertion_scores = []
    maintainability_scores = []
    critic_conflict_flags = []
    final_rationales = []
    improvement_reports = []  
    fixed_codes = []          
    
    print("🕵️ [AI Jury] Invoking the pipeline (This may take a while)...\n")
    
    for index, row in tqdm(df.iterrows(), total=len(df), desc="Eval Progress"):
        case_id = row.get("id", f"UNK_{index}")
        
        # --- NEW: Extract Base & Enhanced Context from CSV ---
        code = str(row.get("excutable_test_step_code", ""))
        reqs = str(row.get("fine_grained_reqs", ""))
        test_case_bdd = str(row.get("excutable_test_test_case", ""))  # BDD 意图
        req_summary = str(row.get("requirement_summary", ""))         # 高层摘要
        prompt_text = str(row.get("prompt", ""))                      # 原始生成指令

        # 组装丢给 LangGraph 的初始状态
        initial_state = {
            "executable_test_code": code,
            "fine_grained_reqs": reqs,
            "excutable_test_test_case": test_case_bdd,
            "requirement_summary": req_summary,
            "prompt": prompt_text,
            "revision_count": 0
        }

        try:
            # 跑图
            final_state = app.invoke(initial_state)
            
            # 在控制台打印该条 case 的微型总结
            print_case_summary(index, case_id, final_state)
            
            # 收集数据
            syntax_passed = final_state.get("syntax_passed", False)
            syntax_status_list.append("PASSED" if syntax_passed else "FAILED_SYNTAX")
            overall_scores.append(final_state.get("overall_score", 0.0))
            requirement_scores.append(final_state.get("requirement_coverage", 0.0))
            assertion_scores.append(final_state.get("assertion_score", 0))
            maintainability_scores.append(final_state.get("maintainability_score", 0))
            critic_conflict_flags.append(1 if final_state.get("has_conflict", False) else 0)
            final_rationales.append(final_state.get("final_reasoning", "No reasoning generated."))
            improvement_reports.append(final_state.get("improvement_report", "No report generated."))
            fixed_codes.append(final_state.get("fixed_code", code))


        except Exception as e:
            print(f"❌ Pipeline Error on case {case_id}: {str(e)}")
            syntax_status_list.append("PIPELINE_ERROR")
            overall_scores.append(0.0)
            requirement_scores.append(0.0)
            assertion_scores.append(0)
            maintainability_scores.append(0)
            critic_conflict_flags.append(0)
            final_rationales.append(f"Error: {str(e)}")

    # 把几个核心指标写回 DataFrame，准备导出
    df["eval_syntax_status"] = syntax_status_list
    df["eval_overall_score"] = overall_scores
    df["eval_final_reasoning"] = final_rationales
    df["eval_improvement_report"] = improvement_reports  
    df["eval_fixed_code"] = fixed_codes
    
    # 导出到 CSV
    output_dir = os.path.dirname(OUTPUT_FILE)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
    df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    print(f"\n💾 Subset data saved to {OUTPUT_FILE}")

    # ==========================================
    # Final aggregate summary (终端漂亮打印)
    # ==========================================
    passed     = syntax_status_list.count("PASSED")
    errors     = syntax_status_list.count("PIPELINE_ERROR")
    avg_score  = round(sum(overall_scores) / max(len(overall_scores), 1), 2)
    avg_req    = round(sum(requirement_scores) / max(len(requirement_scores), 1), 2)
    avg_maint  = round(sum(maintainability_scores) / max(len(maintainability_scores), 1), 2)
    conflicts  = sum(critic_conflict_flags)

    print("\n" + "="*65)
    print(f"  📋  SUBSET EVALUATION SUMMARY  ({len(df)} cases)")
    print("="*65)
    print(f"  Syntax Pass Rate      : {passed}/{len(df)} ({round(passed/max(len(df),1)*100)}%)")
    print(f"  Pipeline Errors       : {errors}")
    print(f"  Critic Conflicts      : {conflicts}")
    print(f"  Avg Requirement Cov.  : {avg_req}%")
    print(f"  Avg Assertion Score   : {round(sum(assertion_scores)/max(len(assertion_scores),1), 2)}/100")
    print(f"  Avg Maintainability   : {avg_maint}/100")
    print(f"  Avg OVERALL SCORE     : {avg_score}/100")
    print("="*65)

if __name__ == "__main__":
    run_subset()