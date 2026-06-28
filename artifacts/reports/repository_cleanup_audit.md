# Repository cleanup audit

Date: 2026-06-28
Branch: `feature/agentic-workflow-v2`

## Audit baseline

The working tree was clean before cleanup. The repository index contained
generated execution data and a complete virtual environment:

| Area | Local files | Local size | Tracked files before cleanup | Classification |
|---|---:|---:|---:|---|
| `artifacts/runtime/` | 14,039 | 280.30 MiB | 7,954 | `IGNORE_FROM_GIT` |
| `artifacts/dynamic/workspaces/` | 2,301 | 18.39 MiB | 2,005 | `IGNORE_FROM_GIT` |
| `artifacts/dynamic/results/` | 240 | 1.56 MiB | 189 | `IGNORE_FROM_GIT` |
| `workspace/` | 167 | 1.33 MiB | 156 | `LOCAL_ARCHIVE_ONLY` |
| `__pycache__/` | 38 | 0.84 MiB | ignored | `IGNORE_FROM_GIT` |
| `working_smoke_test.patch` | 1 | 234.55 MiB | 1 | `LOCAL_ARCHIVE_ONLY` |

No local file is deleted by this cleanup. Tracked generated files are removed
from the Git index with `git rm --cached`; their working-tree copies remain
available and are protected by `.gitignore`.

## KEEP_IN_GIT

- Core evaluator modules: `main.py`, `graph.py`, `agents.py`,
  `dynamic_agents.py`, `coverage_agent.py`, `mutation_testing.py`,
  `reference_resolver.py`, and `e2e_eval/`.
- Unit and integration tests: `tests/` and root `test_*.py`.
- Reproducibility and reporting scripts under `scripts/`, plus the existing
  source-level helper scripts.
- Lightweight reference fixtures under `data/reference_sources/`. The retained
  selected-case CSV uses repository-relative paths to these fixtures, replacing
  machine-specific absolute paths into the ignored legacy workspace.
- Documentation: `README.md`, `MUTATION_OPERATORS.md`, historical methodology
  notes, and the consolidation/cleanup audits.
- Dependency metadata: `requirements.txt`, `requirements_dynamic.txt`, and
  `pyproject.toml`.
- Final reports:
  - `final_full_pipeline_baseline.csv`
  - `selected_cases_local_mutation_10_final.csv`
  - `dynamic_analyst_bench05.csv`
  - `final_scope_aware_mutation.csv`
  - `final_results_summary.csv`
  - `final_results_summary.md`
  - `final_results_summary_for_slides.csv`
  - `final_e2e_test_evaluator_presentation.pptx`
  - `figures/`
  - `selected_cases_with_sources.csv`
- Configuration snapshots matching the retained final experiments.

## IGNORE_FROM_GIT

- `.env`, `*.env`, caches, bytecode, coverage output, logs, virtual
  environments, and `node_modules/`.
- `artifacts/runtime/`: reproducible installed dependencies, not source.
- `artifacts/dynamic/workspaces/`: isolated per-run source/test copies.
- `artifacts/dynamic/results/`: raw screenshots, page sources, Behave reports,
  and per-mutant execution evidence.
- Non-allowlisted files under `artifacts/reports/`, especially duplicated JSON
  exports and smoke/intermediate runs. The retained CSV/config summaries provide
  the presentation evidence without duplicating bulky raw payloads.

## LOCAL_ARCHIVE_ONLY

- `workspace/`: legacy benchmark copies, runner prototypes, and generated
  reports remain local for historical inspection. The small source fixtures
  needed by the final selected run are retained separately under
  `data/reference_sources/`.
- `working_smoke_test.patch`: a 234.55 MiB accidental patch artifact. It remains
  on disk but is untracked and explicitly ignored. Curated patches in `patches/`
  remain tracked.

## REVIEW_REQUIRED

- None for this cleanup. The legacy `workspace/` and large patch are preserved
  locally, so a later decision to archive them externally or delete them can be
  made without affecting this Git update.

## Index removals

The following tracked generated areas are removed only from the Git index:

- `artifacts/runtime/`
- `artifacts/dynamic/workspaces/`
- `artifacts/dynamic/results/`
- `workspace/`
- `working_smoke_test.patch`
- non-allowlisted intermediate files in `artifacts/reports/`

These removals reduce clone size and prevent future experiment runs from
polluting commits while leaving all local evidence intact.
