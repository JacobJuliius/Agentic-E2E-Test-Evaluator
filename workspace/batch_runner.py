from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from runner import run_one_test


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-run E2EDev generated tests using runner.py"
    )
    parser.add_argument("--csv", required=True)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--benchmark-id", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--keep-workspace", action="store_true")
    return parser.parse_args()


def load_rows(csv_path: Path, benchmark_id: str) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    selected = [
        row for row in rows
        if row.get("id", "").strip() == benchmark_id
    ]

    selected.sort(
        key=lambda row: (
            int(row.get("req_id", "0")),
            int(row.get("test_id", "0"))
        )
    )
    return selected


def main() -> int:
    args = parse_args()

    csv_path = Path(args.csv).resolve()
    rows = load_rows(csv_path, args.benchmark_id)

    if not rows:
        print(f"No rows found for benchmark: {args.benchmark_id}")
        return 1

    rows = rows[:args.limit]

    print(f"\nRunning {len(rows)} tests from {args.benchmark_id}...\n")

    results = []

    for index, row in enumerate(rows, start=1):
        req_id = row["req_id"]
        test_id = row["test_id"]

        print(
            f"[{index}/{len(rows)}] "
            f"req_id={req_id}, test_id={test_id}"
        )

        one_test_args = SimpleNamespace(
            csv=args.csv,
            dataset_root=args.dataset_root,
            benchmark_id=args.benchmark_id,
            req_id=req_id,
            test_id=test_id,
            output_dir=args.output_dir,
            timeout=args.timeout,
            keep_workspace=args.keep_workspace,
        )

        try:
            result = run_one_test(one_test_args)
            result_dict = result.__dict__
        except Exception as exc:
            result_dict = {
                "benchmark_id": args.benchmark_id,
                "req_id": req_id,
                "test_id": test_id,
                "execution_status": "runner_error",
                "return_code": None,
                "duration_seconds": None,
                "error_message": str(exc),
            }

        results.append(result_dict)

        print(
            f"    -> {result_dict['execution_status']} "
            f"({result_dict.get('duration_seconds')} sec)"
        )

    output_dir = Path(args.output_dir).resolve()
    summary_dir = output_dir / f"{args.benchmark_id}_batch_summary"
    summary_dir.mkdir(parents=True, exist_ok=True)

    summary_json = summary_dir / "batch_results.json"
    summary_csv = summary_dir / "batch_results.csv"
    summary_json.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fieldnames = sorted({key for result in results for key in result.keys()})
    with summary_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    counts = Counter(result["execution_status"] for result in results)
    total = len(results)
    passed = counts["pass"]

    print("\n========== Batch Summary ==========")
    print(f"Total tests: {total}")
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")

    print(f"\nExecution Pass Rate: {passed}/{total} = {passed / total:.1%}")
    print(f"\nSaved JSON: {summary_json}")
    print(f"Saved CSV:  {summary_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())