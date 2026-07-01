# Agentic E2E Test Evaluator

This repository evaluates the quality of generated end-to-end test scripts. It
does not generate E2E tests as its primary task.

The evaluator combines independent static LLM judges with deterministic,
isolated execution, source branch coverage when an adapter applies, and
mutation testing. Dynamic failures return structured evidence and do not abort
the complete batch.

## Final architecture

```text
Syntax/Linter Gatekeeper
-> Static LLM Evaluators
-> Executable Validation
-> Branch Coverage Agent (when supported)
-> Mutation Planning / Generation / Execution / Analysis
-> Hybrid Evidence Aggregator
```

LangGraph remains the orchestrator. Static LLM evaluators are independent from
the deterministic dynamic evaluators. The main modules remain at the repository
root for compatibility; reusable implementation is organized under:

```text
e2e_eval/
|-- config.py          typed run configuration
|-- dynamic/           execution, branch coverage, and mutation utilities
|-- reporting/         portable CSV/JSON writers
|-- runtime/           dependency and runtime helpers
|-- schemas/           shared result/state contracts
|-- sources/           reference-source resolution
`-- static/            static evaluator API
```

All workspaces and generated reports are written below `artifacts/`. Reference
projects are copied into isolated workspaces; mutation testing applies one
mutation to one fresh copy and never changes the original project.

### Evidence ownership

- LLMs interpret requirements, propose bounded mutation targets, assess
  relevance, and explain evidence.
- Tools determine process status, timeouts, coverage counters, and mutation
  verdicts.
- LLMs cannot invent coverage values or override deterministic `KILLED`,
  `SURVIVED`, `INVALID`, `TIMEOUT`, or `EXECUTION_ERROR` verdicts.
- Without model credentials, planning/analysis is explicitly
  `ANALYSIS_UNAVAILABLE`; seeded deterministic fallback remains active.

Each graph node also contributes a standard envelope under `agent_results`:

```json
{
  "status": "PASSED",
  "score": 82.5,
  "rationale": "Summary",
  "artifacts": {"report_path": "artifacts/.../report.json"},
  "evidence": {},
  "failure_reason": ""
}
```

## Installation and activation

Python 3.11 is the configured environment:

```powershell
conda activate e2e_eval311
python -m pip install -r requirements.txt
python -c "from selenium import webdriver; d=webdriver.Chrome(); d.quit()"
```

Chrome or Chromium is required for the current Behave/Selenium adapter. Recent
Selenium versions use Selenium Manager for the driver. Set `GOOGLE_API_KEY` or
`GEMINI_API_KEY` in `.env` for Gemini-backed stages.

## Run commands

Run one case:

```powershell
conda activate e2e_eval311
$env:E2E_ENABLE_DYNAMIC = "1"
$env:E2E_REFERENCE_NETWORK_ENABLED = "0"
python scripts/reproduce_one.py `
  --input data/e2edev_sample.csv `
  --benchmark-id E2ESD_Bench_02 `
  --req-id 1 `
  --test-id 1
```

Enable executable branch coverage:

```powershell
$env:E2E_ENABLE_DYNAMIC = "1"
$env:E2E_ENABLE_COVERAGE = "1"
$env:E2E_ENABLE_BRANCH_COVERAGE_ANALYSIS = "1"
$env:E2E_COVERAGE_BRANCH_ENABLED = "1"
$env:E2E_COVERAGE_TIMEOUT_SECONDS = "120"
```

Enable mutation testing and set the campaign budget:

```powershell
$env:E2E_ENABLE_DYNAMIC = "1"
$env:E2E_ENABLE_MUTATION = "1"
$env:E2E_ENABLE_MUTATION_PLANNING = "1"
$env:E2E_ENABLE_MUTATION_ANALYSIS = "1"
$env:E2E_MAX_MUTANTS = "5"
$env:E2E_MUTATION_MAX_PER_FILE = "5"
$env:E2E_MUTATION_SEED = "1337"
```

Run the configured input as a batch (all rows when `E2E_MAX_CASES=0`):

```powershell
conda activate e2e_eval311
$env:E2E_INPUT_FILE = "data/e2edev_sample.csv"
$env:E2E_OUTPUT_FILE = "artifacts/reports/evaluation_results.csv"
$env:E2E_MAX_CASES = "0"
python main.py
```

The bundled `data/e2edev_sample.csv` is the 70-case full-evaluation input. To
run the full dynamic campaign explicitly:

```powershell
$env:E2E_ENABLE_DYNAMIC = "1"
$env:E2E_ENABLE_COVERAGE = "1"
$env:E2E_ENABLE_MUTATION = "1"
$env:E2E_INPUT_FILE = "data/e2edev_sample.csv"
$env:E2E_OUTPUT_FILE = "artifacts/reports/full_evaluation.csv"
$env:E2E_MAX_CASES = "0"
python main.py
```

Run tracked selected cases without external LLM calls:

```powershell
python scripts/reproduce_selected_cases.py `
  artifacts/reports/selected_cases_with_sources.csv `
  --output artifacts/reports/selected_cases_local_mutation.csv `
  --local-dynamic-only --mutation --max-mutants 5
```

The primary outputs for `E2E_OUTPUT_FILE=artifacts/reports/evaluation_results.csv`
are:

- `artifacts/reports/evaluation_results.csv`
- `artifacts/reports/evaluation_results.json`
- `artifacts/reports/evaluation_results.config.json`
- per-case evidence under `artifacts/dynamic/results/`
- isolated workspaces under `artifacts/dynamic/workspaces/`

CSV and JSON serialization converts repository paths to project-relative POSIX
paths. External absolute paths are redacted as `<external>/filename`.

Locate the primary and per-case artifacts:

```powershell
Get-Item artifacts/reports/evaluation_results.csv
Get-Item artifacts/reports/evaluation_results.json
Get-Item artifacts/reports/evaluation_results.config.json
Get-ChildItem artifacts/dynamic/results -Recurse -File
```

## Environment variables

Boolean values accept `1`, `true`, `yes`, or `on` (case-insensitive). Pattern
lists are comma-separated.

| Variable | Default | Purpose |
|---|---:|---|
| `GOOGLE_API_KEY` | unset | Gemini credential used by LLM stages. |
| `GEMINI_API_KEY` | unset | Alternative Gemini credential. |
| `GOOGLE_APPLICATION_CREDENTIALS` | unset | Google application credential file. |
| `E2E_LLM_RPD_SOFT_LIMIT` | `470` | Soft daily LLM request guard. |
| `E2E_INPUT_FILE` | `data/e2edev_sample.csv` | Batch input CSV. |
| `E2E_OUTPUT_FILE` | `artifacts/reports/evaluation_results.csv` | Primary report path. |
| `E2E_MAX_CASES` | `0` | Maximum rows; `0` means all. |
| `E2E_CASE_SLEEP_SECONDS` | `5` | Delay between batch rows. |
| `E2E_ENABLE_DYNAMIC` | `0` | Enable executable validation. |
| `E2E_ENABLE_COVERAGE` | `0` | Enable source coverage collection. |
| `E2E_ENABLE_BRANCH_COVERAGE_ANALYSIS` | `1` | Enable semantic analysis of tool-supplied uncovered branches. |
| `E2E_ENABLE_MUTATION` | `0` | Enable mutation testing. |
| `E2E_ENABLE_MUTATION_PLANNING` | `1` | Enable LLM mutation planning when credentials exist. |
| `E2E_ENABLE_MUTATION_ANALYSIS` | `1` | Enable survivor interpretation when credentials exist. |
| `E2E_ENABLE_DYNAMIC_ANALYST` | value of `E2E_ENABLE_DYNAMIC` | Enable the explanatory dynamic analyst. |
| `E2E_ENABLE_CRITIC` | `1` | Enable critic stage. |
| `E2E_ENABLE_REFINER` | `1` | Enable conditional step-code refinement. |
| `E2E_ENABLE_REFINEMENT_VALIDATION` | `0` | Execute refined code before acceptance. |
| `E2E_ENABLE_REFINEMENT_MUTATION_VALIDATION` | `0` | Mutation-check refined code. |
| `E2E_DYNAMIC_ARTIFACT_ROOT` | `artifacts/dynamic` | Dynamic workspace/result root. |
| `E2E_REFERENCE_CACHE` | `artifacts/reference_cache` | Validated source cache. |
| `E2E_REFERENCE_WORKSPACE_ROOT` | parent of reference cache | Source-resolution workspace. |
| `E2E_REFERENCE_NETWORK_ENABLED` | `0` | Permit bounded archive/Git retrieval. |
| `E2E_REFERENCE_TIMEOUT_SECONDS` | `90` | Source retrieval timeout. |
| `E2E_REFERENCE_EXPECTED_PATTERNS` | empty | Required files/globs for source validation. |
| `E2E_COVERAGE_SOURCE_DIR` | empty | Explicit source project fallback. |
| `E2E_COVERAGE_TIMEOUT_SECONDS` | `120` | Instrumentation/execution timeout. |
| `E2E_COVERAGE_INCLUDE_PATTERNS` | empty | Coverage file include patterns. |
| `E2E_COVERAGE_EXCLUDE_PATTERNS` | empty | Coverage file exclude patterns. |
| `E2E_COVERAGE_BRANCH_ENABLED` | `1` | Collect branch counters. |
| `E2E_COVERAGE_INCLUDE_TESTS` | `0` | Include test files in Python coverage. |
| `E2E_MAX_MUTANTS` | `5` | Total mutation budget per case. |
| `E2E_MUTATION_MAX_PER_FILE` | `5` | Per-file mutation budget. |
| `E2E_MUTATION_SEED` | `1337` | Stable candidate-selection seed. |
| `E2E_MUTATION_INCLUDE_PATTERNS` | empty | Mutation include patterns. |
| `E2E_MUTATION_EXCLUDE_PATTERNS` | built-in exclusions | Mutation exclude patterns. |
| `E2E_MUTATION_PROJECT_VALIDATION_COMMAND` | empty | Optional build/syntax validation command. |
| `E2E_MUTATION_KEEP_WORKSPACES` | `0` | Retain per-mutant workspaces. |
| `E2E_EXECUTION_TIMEOUT_SECONDS` | `90` | Baseline subprocess timeout. |
| `E2E_MUTANT_TIMEOUT_SECONDS` | `45` | Per-mutant subprocess timeout. |
| `E2E_RELEVANT_MUTATION_REFINE_THRESHOLD` | `80` | Relevant-score threshold considered by refinement. |
| `E2E_HEADLESS` | `1` | Run Chrome headlessly. |

`E2E_APP_INDEX_URI` and `E2E_JS_COVERAGE_PATH` are internal variables injected
into isolated subprocesses by the harness; users should not set them.

## Branch coverage

The coverage agent chooses an adapter from the resolved source project:

- `selenium_istanbul` instruments supported external JavaScript and reads
  browser-side Istanbul counters.
- `python_coverage_py` measures supported in-process Python applications.
- unsupported layouts return explicit unavailable evidence, never a fabricated
  zero.

JavaScript coverage is adapter- and project-structure-dependent. In the latest
70-case benchmark it succeeded for Bench 02 and Bench 03 and was unavailable
for Bench 01, 04, and 05. That status describes adapter applicability, not
missing test coverage; this project does not claim universal JavaScript
coverage support.

Reports export `coverage_adapter`, `coverage_source_language`,
`coverage_instrumentation_status`, `coverage_failure_reason`, and
`branch_coverage_included_in_scoring`. Branch coverage enters hybrid scoring
only when the baseline passes and the tool produces a numeric branch result.

```text
branch coverage = covered branches / measured branches * 100
```

BDD step diagnostics are separate: they report which Given/When/Then steps ran,
not source-code branch coverage.

## Mutation testing and metrics

The baseline must pass before mutation begins. Planning proposals contain only
validated target metadata; deterministic operators create patches. Each report
records the candidate source (`llm_proposal` or `deterministic_fallback`), full
proposal lifecycle, execution verdict, scope relation, score inclusion flags,
and exclusion reason.

```text
raw mutation score = killed / (killed + survived) * 100
```

`INVALID`, `TIMEOUT`, and `EXECUTION_ERROR` are reported separately and are not
in the denominator. Valid unfavorable survivors remain visible in raw records
and statistics.

The requirement-relevant mutation score uses only valid mutants classified
`RELEVANT`. Infrastructure/cosmetic mutants are excluded from requirement-level
assessment. `UNCERTAIN` mutants remain visible but do not assert relevance;
`OUT_OF_SCOPE` mutants remain useful for suite-level evidence but do not lower
one scenario's requirement score. A missing relevant denominator is exported as
unavailable, not zero.

See [MUTATION_ARCHITECTURE.md](MUTATION_ARCHITECTURE.md) and
[MUTATION_OPERATORS.md](MUTATION_OPERATORS.md).

## Latest full-run summary

The latest full evaluation contained 70 cases:

- 67 executable baselines passed;
- 2 returned `HARNESS_DEPENDENCY_ERROR`;
- 1 returned `TEST_FAILED`;
- 335 mutants were generated: 319 valid, 85 killed, 234 survived, 16 invalid;
- 0 mutation timeouts and 0 mutation execution errors;
- raw mutation score: approximately 26.6%;
- requirement-relevant mutation score: approximately 30.2%.

## Known limitations

- Executable validation is focused on Behave/Selenium.
- JavaScript coverage applicability varies with source-project structure.
- JavaScript/TypeScript mutation parsing is deliberately conservative.
- Equivalent mutants can survive without representing a real test gap.
- LLM planning and explanation require credentials; deterministic fallback is
  explicit and reproducible.
- Mutation campaigns are bounded samples, not exhaustive application mutation.

## Validation

```powershell
conda activate e2e_eval311
python -m pytest -p no:cacheprovider -q
python scripts/lint.py
python -m compileall -q -x "artifacts|data|workspace|patches" .
git diff --check
```

## Presentation update outline

- **Architecture:** show the six-stage final architecture above and label the
  LangGraph control flow, isolated workspaces, and hybrid aggregator.
- **Branch coverage:** distinguish executable source-branch evidence from BDD
  step diagnostics; report Bench 02/03 support and explicit unavailability for
  Bench 01/04/05.
- **Mutation testing:** show deterministic verdict ownership, seeded bounded
  operators, fresh workspaces, and separate raw versus requirement-relevant
  scores.
- **Final results:** present 70 cases, 67 passing baselines, 335 generated
  mutants, 319 valid, 85 killed, 234 survived, 16 invalid, 26.6% raw score,
  and 30.2% requirement-relevant score.
- **Limitations/future work:** broaden execution and coverage adapters, improve
  conservative JS/TS parsing and equivalent-mutant handling, and retain the
  credential-free deterministic fallback.
