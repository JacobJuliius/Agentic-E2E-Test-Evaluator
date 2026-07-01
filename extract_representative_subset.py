#!/usr/bin/env python3
"""Extract the five representative E2EDev rows into a small CSV.

This script does not re-run any evaluation. It merely creates a 5-row input file
that you can feed to your *current* static evaluator after you have fixed the
assertion extractor.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd
from e2e_eval.utils.paths import sanitize_dataframe_for_export

CASES = [
    ("E2ESD_Bench_01", 1, 4, "weak_drag_cart"),
    ("E2ESD_Bench_02", 4, 1, "notes_delete_persistence"),
    ("E2ESD_Bench_03", 4, 1, "movie_booking_positive"),
    ("E2ESD_Bench_03", 4, 4, "movie_booking_negative"),
    ("E2ESD_Bench_05", 4, 1, "dictionary_reset"),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Full evaluation_results_v3.csv")
    parser.add_argument(
        "--output",
        default="artifacts/reports/representative_5_tests.csv",
    )
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    required = {"id", "req_id", "test_id"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Input CSV misses required columns: {sorted(missing)}")

    selected = []
    for bench, req_id, test_id, label in CASES:
        rows = df[(df["id"] == bench) & (df["req_id"] == req_id) & (df["test_id"] == test_id)].copy()
        if len(rows) != 1:
            raise ValueError(
                f"Expected exactly 1 row for {bench} req={req_id} test={test_id}; got {len(rows)}"
            )
        rows.insert(0, "representative_label", label)
        selected.append(rows)

    out = pd.concat(selected, ignore_index=True)
    sanitize_dataframe_for_export(out).to_csv(
        args.output, index=False, encoding="utf-8-sig"
    )
    print(f"Wrote {len(out)} representative rows to: {Path(args.output).resolve()}")
    print(out[["representative_label", "id", "req_id", "test_id"]].to_string(index=False))


if __name__ == "__main__":
    main()
