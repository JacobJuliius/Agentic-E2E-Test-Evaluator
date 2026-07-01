"""Select a small benchmark subset for deterministic smoke evaluation."""
from pathlib import Path
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from e2e_eval.utils.paths import sanitize_dataframe_for_export


INPUT = Path("data/e2edev_sample.csv")
OUTPUT = Path("artifacts/reports/selected_cases.csv")
TARGETS = {
    ("E2ESD_Bench_01", 1, 4),
    ("E2ESD_Bench_02", 1, 1),
    ("E2ESD_Bench_03", 2, 2),
    ("E2ESD_Bench_05", 3, 1),
}

df = pd.read_csv(INPUT)
df["id"] = df["id"].astype(str).str.strip()
df["req_id"] = pd.to_numeric(df["req_id"], errors="coerce")
df["test_id"] = pd.to_numeric(df["test_id"], errors="coerce")

selected = df[
    df.apply(
        lambda row: (
            row["id"], int(row["req_id"]), int(row["test_id"])
        ) in TARGETS
        if pd.notna(row["req_id"]) and pd.notna(row["test_id"])
        else False,
        axis=1,
    )
].copy()
selected = selected.sort_values(["id", "req_id", "test_id"])

found = {
    (row.id, int(row.req_id), int(row.test_id))
    for row in selected.itertuples()
}
missing = TARGETS - found

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
sanitize_dataframe_for_export(selected).to_csv(
    OUTPUT, index=False, encoding="utf-8-sig"
)

print(f"Selected {len(selected)} case(s):")
print(selected[["id", "req_id", "test_id"]].to_string(index=False))
if missing:
    print("\nWARNING: Not found:")
    for item in sorted(missing):
        print(" ", item)
print(f"\nSaved to: {OUTPUT}")
