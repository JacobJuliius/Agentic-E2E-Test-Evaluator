import pandas as pd
from pathlib import Path

INPUT = Path("artifacts/reports/selected_cases.csv")
OUTPUT = Path("artifacts/reports/selected_cases_with_sources.csv")

# 改成实际目录：其下应有 E2ESD_Bench_01、E2ESD_Bench_02 等文件夹
SOURCE_ROOT = Path(
    r"G:\学\语言资料\语言学\NLP_CL\LMU_cis\E2E\Agentic_E2Etest_Evaluator_5"
    r"\workspace\E2EDev_data"
)

df = pd.read_csv(INPUT)

df["source_project_dir"] = df["id"].apply(
    lambda bench_id: str((SOURCE_ROOT / str(bench_id) / "source_projcet").resolve())
)

print(df[["id", "req_id", "test_id", "source_project_dir"]].to_string(index=False))

missing = []
for bench_id, source_dir in zip(df["id"], df["source_project_dir"]):
    source_path = Path(source_dir)
    index_files = list(source_path.rglob("index.html")) if source_path.exists() else []
    if not index_files:
        missing.append((bench_id, source_dir))

if missing:
    print("\nWARNING: These source folders are missing or have no index.html:")
    for bench_id, path in missing:
        print(f"  {bench_id}: {path}")

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(OUTPUT, index=False, encoding="utf-8-sig")
print(f"\nSaved: {OUTPUT}")