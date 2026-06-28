#!/usr/bin/env python3
"""Create one compact per-test mutation-score summary from mutation_runner CSV output."""
from __future__ import annotations
import argparse
from pathlib import Path
import pandas as pd


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="mutation_details_*.csv")
    p.add_argument("--output", default="mutation_summary.csv")
    args = p.parse_args()

    df = pd.read_csv(args.input)
    baselines = df[df.phase.eq("baseline")][["case_id", "case_label", "status"]].rename(columns={"status": "baseline_status"})
    muts = df[df.phase.eq("mutant")].copy()
    valid = muts[muts.status.isin(["KILLED", "SURVIVED"])]
    agg = valid.groupby(["case_id", "case_label"], as_index=False).agg(
        killed=("status", lambda x: int((x == "KILLED").sum())),
        survived=("status", lambda x: int((x == "SURVIVED").sum()),),
    )
    operational = muts.groupby(["case_id", "case_label"], as_index=False).agg(
        timeout_or_error=("status", lambda x: int(x.isin(["TIMEOUT", "ERROR"]).sum())),
        total_planned_mutants=("mutant_id", "count"),
    )
    out = baselines.merge(agg, on=["case_id", "case_label"], how="left").merge(operational, on=["case_id", "case_label"], how="left")
    for c in ["killed", "survived", "timeout_or_error", "total_planned_mutants"]:
        out[c] = out[c].fillna(0).astype(int)
    denom = out.killed + out.survived
    out["mutation_score"] = (out.killed / denom.where(denom > 0)).round(4)
    out["mutation_score_pct"] = (100 * out["mutation_score"]).round(1)
    out.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(out.to_string(index=False))
    print(f"\nWrote summary: {Path(args.output).resolve()}")

if __name__ == "__main__":
    main()
