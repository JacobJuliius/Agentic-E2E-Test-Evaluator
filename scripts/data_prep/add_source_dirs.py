"""Attach portable local source-project paths to selected benchmark rows."""
from pathlib import Path
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from e2e_eval.utils.paths import sanitize_dataframe_for_export


INPUT = Path("artifacts/reports/selected_cases.csv")
OUTPUT = Path("artifacts/reports/selected_cases_with_sources.csv")
SOURCE_ROOT = Path("data/reference_sources/E2EDev_data")


df = pd.read_csv(INPUT)
df["source_project_dir"] = df["id"].apply(
    lambda benchmark_id: str(
        SOURCE_ROOT / str(benchmark_id) / "source_projcet"
    )
)

print(df[["id", "req_id", "test_id", "source_project_dir"]].to_string(index=False))

missing = []
for benchmark_id, source_dir in zip(df["id"], df["source_project_dir"]):
    source_path = Path(source_dir)
    index_files = (
        list(source_path.rglob("index.html")) if source_path.exists() else []
    )
    if not index_files:
        missing.append((benchmark_id, source_dir))

if missing:
    print("\nWARNING: These source folders are missing or have no index.html:")
    for benchmark_id, path in missing:
        print(f"  {benchmark_id}: {path}")

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
sanitize_dataframe_for_export(df).to_csv(
    OUTPUT, index=False, encoding="utf-8-sig"
)
print(f"\nSaved: {OUTPUT}")
