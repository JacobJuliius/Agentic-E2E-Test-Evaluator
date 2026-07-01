"""
Aggregate per-test mutation reports into requirement-level mutation suites.

Suite definition:
    same reference application + same requirement ID
    => all generated tests for that requirement form one requirement-level suite.

The script:
1. Loads the evaluator CSV.
2. Groups tests by (reference_answer, req_id) when reference_answer exists.
   Otherwise it falls back to (id, req_id).
3. Reads each test's mutation_report.json.
4. Aggregates verdicts per unique mutant:
      - KILLED if any test in the suite kills it
      - SURVIVED if no test kills it but at least one survives it
      - INCONCLUSIVE otherwise
5. Writes:
      - requirement_suite_mutation_summary.csv
      - requirement_suite_mutant_details.csv
      - requirement_suite_membership.csv

Important:
- Run this AFTER mutation has been enabled for all rows you want to analyze.
- A mutation score is only comparable within a suite when its tests use the
  same reference app and have overlapping / compatible mutant universes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from e2e_eval.utils.paths import sanitize_dataframe_for_export


DEFAULT_INPUT = "data/evaluation_results_v4_pure_python.csv"
DEFAULT_OUTPUT_DIR = "data/suite_analysis"


def clean_text(value: Any) -> str:
    """Normalize missing values and values used as grouping keys."""
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def read_report(report_path_value: Any) -> tuple[list[dict[str, Any]], str]:
    """Read one mutation report and return (records, status_message)."""
    report_path_text = clean_text(report_path_value)
    if not report_path_text:
        return [], "MISSING_REPORT_PATH"

    path = Path(report_path_text)
    if not path.exists():
        return [], f"REPORT_NOT_FOUND: {path}"

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [], f"REPORT_READ_ERROR: {exc}"

    records = payload.get("records", [])
    if not isinstance(records, list):
        return [], "INVALID_REPORT_RECORDS"

    return [r for r in records if isinstance(r, dict)], "OK"


def mutant_key(record: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Stable identity of a mutant across separately generated reports.

    Do NOT use mutant_id alone, because each test report starts again at M001.
    """
    return (
        clean_text(record.get("source_file")),
        clean_text(record.get("original")),
        clean_text(record.get("replacement")),
        clean_text(record.get("operator")),
        str(record.get("line", "")),
    )


def choose_group_columns(df: pd.DataFrame) -> list[str]:
    """Select the safest suite grouping key available in the CSV."""
    if "reference_answer" in df.columns and df["reference_answer"].notna().any():
        return ["reference_answer", "req_id"]

    # In the supplied dataset, id commonly identifies a benchmark/application.
    # This fallback avoids accidentally merging req_id=1 from different apps.
    if "id" in df.columns:
        return ["id", "req_id"]

    raise ValueError(
        "Cannot identify a suite. CSV needs either "
        "['reference_answer', 'req_id'] or ['id', 'req_id']."
    )


def suite_label(group_values: tuple[Any, ...], group_columns: list[str]) -> str:
    pairs = [f"{column}={clean_text(value)}" for column, value in zip(group_columns, group_values)]
    return " | ".join(pairs)


def aggregate_suites(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    required = {"req_id", "test_id", "dynamic_mutation_report_path"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Input CSV is missing required columns: {sorted(missing)}")

    group_columns = choose_group_columns(df)
    suite_rows: list[dict[str, Any]] = []
    mutant_rows: list[dict[str, Any]] = []
    membership_rows: list[dict[str, Any]] = []

    grouped = df.groupby(group_columns, dropna=False, sort=True)

    for group_values, group_df in grouped:
        if not isinstance(group_values, tuple):
            group_values = (group_values,)

        label = suite_label(group_values, group_columns)
        test_entries: list[dict[str, Any]] = []
        mutants: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}

        for _, row in group_df.iterrows():
            test_id = clean_text(row.get("test_id"))
            row_index = int(row.name)
            report_path = clean_text(row.get("dynamic_mutation_report_path"))
            records, report_status = read_report(report_path)

            execution_status = clean_text(row.get("dynamic_execution_status"))
            mutation_status = clean_text(row.get("dynamic_mutation_status"))

            test_entry = {
                "suite_key": label,
                "csv_row_index": row_index,
                "test_id": test_id,
                "execution_status": execution_status,
                "mutation_status": mutation_status,
                "mutation_report_path": report_path,
                "report_status": report_status,
                "mutants_seen_in_test": len(records),
            }
            test_entries.append(test_entry)
            membership_rows.append({
                **{column: clean_text(value) for column, value in zip(group_columns, group_values)},
                **test_entry,
            })

            for record in records:
                key = mutant_key(record)
                if key not in mutants:
                    mutants[key] = {
                        "source_file": clean_text(record.get("source_file")),
                        "line": record.get("line"),
                        "original": clean_text(record.get("original")),
                        "replacement": clean_text(record.get("replacement")),
                        "operator": clean_text(record.get("operator")),
                        "seen_by_tests": [],
                        "killed_by_tests": [],
                        "survived_by_tests": [],
                        "timeout_by_tests": [],
                        "inconclusive_by_tests": [],
                    }

                item = mutants[key]
                item["seen_by_tests"].append(test_id)
                verdict = clean_text(record.get("verdict")).upper()

                if verdict == "KILLED":
                    item["killed_by_tests"].append(test_id)
                elif verdict == "SURVIVED":
                    item["survived_by_tests"].append(test_id)
                elif verdict == "TIMEOUT":
                    item["timeout_by_tests"].append(test_id)
                else:
                    item["inconclusive_by_tests"].append(test_id)

        # Detect whether each test used the same mutant universe.
        unique_mutant_sets = {
            tuple(sorted(
                mutant_key(record)
                for record in read_report(entry["mutation_report_path"])[0]
            ))
            for entry in test_entries
            if entry["report_status"] == "OK"
        }

        if not test_entries:
            comparability = "NO_TESTS"
        elif not unique_mutant_sets:
            comparability = "NO_VALID_MUTATION_REPORTS"
        elif len(unique_mutant_sets) == 1:
            comparability = "IDENTICAL_MUTANT_UNIVERSE"
        else:
            comparability = "PARTIAL_OR_DIFFERENT_MUTANT_UNIVERSES"

        killed = survived = timeout = inconclusive = 0

        for item in mutants.values():
            # Requirement-suite rule: a mutant is killed once ANY member test kills it.
            if item["killed_by_tests"]:
                suite_verdict = "KILLED"
                killed += 1
            elif item["survived_by_tests"]:
                suite_verdict = "SURVIVED"
                survived += 1
            elif item["timeout_by_tests"]:
                suite_verdict = "TIMEOUT"
                timeout += 1
            else:
                suite_verdict = "INCONCLUSIVE"
                inconclusive += 1

            mutant_rows.append({
                **{column: clean_text(value) for column, value in zip(group_columns, group_values)},
                "suite_key": label,
                "suite_verdict": suite_verdict,
                "source_file": item["source_file"],
                "line": item["line"],
                "original": item["original"],
                "replacement": item["replacement"],
                "operator": item["operator"],
                "seen_by_tests": "; ".join(item["seen_by_tests"]),
                "killed_by_tests": "; ".join(item["killed_by_tests"]),
                "survived_by_tests": "; ".join(item["survived_by_tests"]),
                "timeout_by_tests": "; ".join(item["timeout_by_tests"]),
                "inconclusive_by_tests": "; ".join(item["inconclusive_by_tests"]),
            })

        valid = killed + survived
        score = round(100 * killed / valid, 2) if valid else None

        tests_with_reports = sum(1 for x in test_entries if x["report_status"] == "OK")
        tests_baseline_passed = sum(1 for x in test_entries if x["execution_status"] == "PASSED")

        suite_rows.append({
            **{column: clean_text(value) for column, value in zip(group_columns, group_values)},
            "suite_key": label,
            "tests_total_in_requirement": len(group_df),
            "tests_baseline_passed": tests_baseline_passed,
            "tests_with_mutation_reports": tests_with_reports,
            "mutation_universe_comparability": comparability,
            "suite_mutants_total": len(mutants),
            "suite_mutants_killed": killed,
            "suite_mutants_survived": survived,
            "suite_mutants_timeout": timeout,
            "suite_mutants_inconclusive": inconclusive,
            "suite_mutation_score": score,
            "included_test_ids": "; ".join(x["test_id"] for x in test_entries),
            "report_issues": "; ".join(
                f"{x['test_id']}={x['report_status']}"
                for x in test_entries
                if x["report_status"] != "OK"
            ) or "None",
        })

    return (
        pd.DataFrame(suite_rows),
        pd.DataFrame(mutant_rows),
        pd.DataFrame(membership_rows),
    )


def main() -> None:
    input_csv = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(DEFAULT_INPUT)
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(DEFAULT_OUTPUT_DIR)

    if not input_csv.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_csv}")

    output_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(input_csv)

    suite_df, mutant_df, membership_df = aggregate_suites(df)

    suite_path = output_dir / "requirement_suite_mutation_summary.csv"
    mutant_path = output_dir / "requirement_suite_mutant_details.csv"
    membership_path = output_dir / "requirement_suite_membership.csv"

    sanitize_dataframe_for_export(suite_df).to_csv(
        suite_path, index=False, encoding="utf-8-sig"
    )
    sanitize_dataframe_for_export(mutant_df).to_csv(
        mutant_path, index=False, encoding="utf-8-sig"
    )
    sanitize_dataframe_for_export(membership_df).to_csv(
        membership_path, index=False, encoding="utf-8-sig"
    )

    display_columns = [
        col for col in [
            "reference_answer", "id", "req_id",
            "tests_total_in_requirement",
            "tests_baseline_passed",
            "tests_with_mutation_reports",
            "mutation_universe_comparability",
            "suite_mutants_total",
            "suite_mutants_killed",
            "suite_mutants_survived",
            "suite_mutation_score",
        ]
        if col in suite_df.columns
    ]

    print("\nRequirement-level suite mutation summary:")
    if suite_df.empty:
        print("No suites found.")
    else:
        print(suite_df[display_columns].to_string(index=False))

    print("\nSaved:")
    print(f"  {suite_path}")
    print(f"  {mutant_path}")
    print(f"  {membership_path}")


if __name__ == "__main__":
    main()
