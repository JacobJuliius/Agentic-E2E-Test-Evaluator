import json
from pathlib import Path
import pandas as pd


INPUT_CSV = "data/evaluation_results_v4_pure_python.csv"
OUTPUT_CSV = "data/requirement_suite_mutation_summary.csv"


def parse_mutation_report(report_path: str):
    """Read one mutation_report.json and return its records."""
    if not report_path or pd.isna(report_path):
        return []

    path = Path(str(report_path))
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("records", [])
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Warning: cannot read {path}: {exc}")
        return []


def build_suite_mutation_summary(df: pd.DataFrame) -> pd.DataFrame:
    required_columns = {
        "id",
        "req_id",
        "test_id",
        "dynamic_mutation_report_path",
    }
    missing = required_columns - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {sorted(missing)}")

    # 用 id 作为 benchmark 标识；如果你的 CSV 里已有 benchmark_id，也可替换成 benchmark_id
    group_columns = ["id", "req_id"]

    suite_rows = []

    for group_key, group_df in df.groupby(group_columns, dropna=False):
        benchmark_id, req_id = group_key

        mutant_results = {}
        included_tests = []

        for _, row in group_df.iterrows():
            test_id = row["test_id"]
            records = parse_mutation_report(row["dynamic_mutation_report_path"])

            if not records:
                continue

            included_tests.append(test_id)

            for record in records:
                mutant_key = (
                    record.get("source_file"),
                    record.get("line"),
                    record.get("original"),
                    record.get("replacement"),
                    record.get("operator"),
                )

                if mutant_key not in mutant_results:
                    mutant_results[mutant_key] = {
                        "mutant_id": record.get("mutant_id"),
                        "source_file": record.get("source_file"),
                        "line": record.get("line"),
                        "original": record.get("original"),
                        "replacement": record.get("replacement"),
                        "operator": record.get("operator"),
                        "killed_by_tests": [],
                        "survived_by_tests": [],
                        "inconclusive_by_tests": [],
                    }

                verdict = record.get("verdict", "INCONCLUSIVE")

                if verdict == "KILLED":
                    mutant_results[mutant_key]["killed_by_tests"].append(test_id)
                elif verdict == "SURVIVED":
                    mutant_results[mutant_key]["survived_by_tests"].append(test_id)
                else:
                    mutant_results[mutant_key]["inconclusive_by_tests"].append(test_id)

        mutants_total = len(mutant_results)
        mutants_killed = 0
        mutants_survived = 0
        mutants_inconclusive = 0

        mutant_details = []

        for mutant in mutant_results.values():
            # 核心 suite 规则：任意一个 test kill 即为 suite-level killed
            if mutant["killed_by_tests"]:
                suite_verdict = "KILLED"
                mutants_killed += 1
            elif mutant["survived_by_tests"]:
                suite_verdict = "SURVIVED"
                mutants_survived += 1
            else:
                suite_verdict = "INCONCLUSIVE"
                mutants_inconclusive += 1

            mutant_details.append({
                **mutant,
                "suite_verdict": suite_verdict,
            })

        valid_mutants = mutants_killed + mutants_survived
        suite_score = (
            round(100 * mutants_killed / valid_mutants, 2)
            if valid_mutants > 0
            else None
        )

        suite_rows.append({
            "benchmark_id": benchmark_id,
            "req_id": req_id,
            "tests_in_suite": len(included_tests),
            "included_test_ids": "; ".join(map(str, included_tests)),
            "suite_mutants_total": mutants_total,
            "suite_mutants_killed": mutants_killed,
            "suite_mutants_survived": mutants_survived,
            "suite_mutants_inconclusive": mutants_inconclusive,
            "suite_mutation_score": suite_score,
            "suite_mutant_details": json.dumps(
                mutant_details,
                ensure_ascii=False
            ),
        })

    return pd.DataFrame(suite_rows)


if __name__ == "__main__":
    df = pd.read_csv(INPUT_CSV)

    summary_df = build_suite_mutation_summary(df)

    Path(OUTPUT_CSV).parent.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print("\nRequirement-suite mutation summary:")
    print(
        summary_df[
            [
                "benchmark_id",
                "req_id",
                "tests_in_suite",
                "suite_mutants_total",
                "suite_mutants_killed",
                "suite_mutants_survived",
                "suite_mutation_score",
            ]
        ].to_string(index=False)
    )

    print(f"\nSaved to: {OUTPUT_CSV}")