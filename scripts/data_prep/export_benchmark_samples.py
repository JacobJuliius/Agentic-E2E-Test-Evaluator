"""Export per-benchmark sample CSVs for Bench 03, 04, and 05."""
from pathlib import Path

import pandas as pd


INPUT_CSV = Path("data/e2edev_sample.csv")
OUTPUT_DIR = Path("artifacts/bench_samples")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

df = pd.read_csv(INPUT_CSV)
possible_columns = [
    "id", "benchmark", "bench", "project", "reference_answer", "repo_url"
]
benchmark_column = next(
    (column for column in possible_columns if column in df.columns),
    None,
)
if benchmark_column is None:
    raise ValueError(
        "Cannot identify a benchmark column. Available columns: "
        f"{df.columns.tolist()}"
    )

for benchmark_number in ("03", "04", "05"):
    benchmark_id = f"E2ESD_Bench_{benchmark_number}"
    subset = df[
        df[benchmark_column].astype(str).str.contains(
            benchmark_id, case=False, na=False
        )
    ].copy()
    output_path = OUTPUT_DIR / f"{benchmark_id}_sample.csv"
    subset.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"{benchmark_id}: exported {len(subset)} row(s) -> {output_path}")

print("Done.")
