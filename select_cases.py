import pandas as pd
from pathlib import Path

INPUT = Path("data/e2edev_sample.csv")
OUTPUT = Path("artifacts/reports/selected_cases.csv")

# 格式：(Bench ID, req_id, test_id)
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
            row["id"],
            int(row["req_id"]),
            int(row["test_id"]),
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
selected.to_csv(OUTPUT, index=False, encoding="utf-8-sig")

print(f"Selected {len(selected)} case(s):")
print(selected[["id", "req_id", "test_id"]].to_string(index=False))

if missing:
    print("\nWARNING: Not found:")
    for item in sorted(missing):
        print(" ", item)

print(f"\nSaved to: {OUTPUT}")