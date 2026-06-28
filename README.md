# Agentic E2E Test Evaluator

This project evaluates generated end-to-end test scripts. It does not generate
tests as its primary task. The framework combines deterministic static analysis,
independent LLM evaluators, isolated browser execution, Python source coverage,
and general mutation testing.

The main evaluation protocol is a hybrid evaluator: independent static LLM
judges are followed by isolated Selenium/Behave execution, a BDD
step-execution diagnostic, and deterministic scope-aware mutation testing.
The optional dynamic analyst explains surviving-mutant evidence but is not a
primary scoring component.

## Architecture

```text
e2e_eval/
├── config.py                 central typed configuration
├── orchestration/            LangGraph public API
├── static/                   static evaluator public API
├── dynamic/                  execution, coverage, mutation APIs
├── sources/                  validated reference-source resolution
├── runtime/                  shared utilities and dependency preflight
├── schemas/                  standardized agent result envelopes
└── reporting/                CSV, JSON, and run-manifest writers
```

The root modules (`main.py`, `graph.py`, `agents.py`, `dynamic_agents.py`,
`coverage_agent.py`, `mutation_testing.py`, and `reference_resolver.py`) remain
available for backward compatibility. New integrations should prefer the
`e2e_eval` package.

Every graph node retains its historical state fields and also contributes a
standard envelope under `agent_results`:

```json
{
  "status": "PASSED",
  "score": 82.5,
  "rationale": "Summary of the result",
  "artifacts": {"report_path": "artifacts/.../report.json"},
  "evidence": {},
  "failure_reason": ""
}
```

## Pipeline order

```text
Syntax/AST gate
→ Requirement alignment (LLM)
→ Assertion quality (LLM)
→ Hallucination and smell analysis (LLM)
→ Maintainability (LLM)
→ Isolated Behave/Selenium execution
→ BDD step-execution diagnostic
→ Python coverage.py analysis
→ General source mutation testing
→ Conditional dynamic analyst and critic
→ Deterministic consensus
→ Conditional step-code refiner
→ Optional refined-code execution/coverage/mutation validation
→ Repair safety gate and comparison
```

Static LLM evaluators remain independent from deterministic dynamic tools.
Dynamic failures return structured evidence and do not terminate the batch.

## Installation

Python 3.11 is recommended.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Set `GOOGLE_API_KEY` in `.env` for the Gemini-backed static agents.

### Browser setup

The current E2EDev harness executes Behave/Selenium tests with Chrome:

```powershell
.\.venv\Scripts\python.exe -c "from selenium import webdriver; d=webdriver.Chrome(); d.quit()"
```

Recent Selenium releases use Selenium Manager to obtain a compatible driver.
Install Chrome or Chromium before dynamic evaluation.

Playwright is optional for repositories containing Playwright-generated tests:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[playwright]"
.\.venv\Scripts\python.exe -m playwright install chromium
```

The bundled E2EDev runner currently targets Behave/Selenium; adding a Playwright
runtime adapter is separate from installing the browser.

## Run the evaluator

The historical entry point remains valid:

```powershell
python main.py
```

Defaults:

- input: `data/e2edev_sample.csv`
- CSV report: `artifacts/reports/evaluation_results.csv`
- JSON report: `artifacts/reports/evaluation_results.json`
- configuration manifest: `artifacts/reports/evaluation_results.config.json`

Run a single benchmark row reproducibly:

```powershell
python scripts/reproduce_one.py `
  --input data/e2edev_sample.csv `
  --benchmark-id E2ESD_Bench_01 `
  --req-id 1 `
  --test-id 1
```

Preflight source resolution and dependencies without invoking LangGraph or an LLM:

```powershell
python scripts/reproduce_one.py --row-index 0 --check-only
```

Use `--install-missing` to run the bounded `pip install -r requirements.txt`
step. Network source retrieval is still controlled separately.

### Input CSV schema

The evaluator accepts:

```text
id, req_id, test_id, reference_answer, fine_grained_reqs,
excutable_test_test_case, excutable_test_step_code,
source_project_dir (optional)
```

When `source_project_dir` is present, it takes priority over cache/network
resolution. The directory may have any name—including the dataset's literal
`source_projcet` spelling. The evaluator recursively locates `index.html`, uses
its parent as the project root, and copies it into each isolated workspace as
`source_project/`.

## Central configuration

`e2e_eval.config.EvaluationConfig` is the canonical configuration object.
Environment variables are read once by `EvaluationConfig.from_env()` and saved
with every report.

Important options:

```powershell
$env:E2E_MAX_CASES = "1"
$env:E2E_ENABLE_DYNAMIC = "1"
$env:E2E_HEADLESS = "1"
$env:E2E_EXECUTION_TIMEOUT_SECONDS = "90"

$env:E2E_REFERENCE_WORKSPACE_ROOT = "artifacts"
$env:E2E_REFERENCE_NETWORK_ENABLED = "0"
$env:E2E_REFERENCE_TIMEOUT_SECONDS = "90"
```

Remote source retrieval is disabled by default. The resolver accepts validated
local directories, explicit ZIP/TAR endpoints, and Git repositories. It never
scrapes arbitrary HTML pages.

Source provenance is exported as `LOCAL_SOURCE_OVERRIDE`, `REFERENCE_CACHE`, or
`NETWORK_REFERENCE`, together with the input path, resolved project path, and
resolved entrypoint.

Run a selected-case CSV entirely offline:

```powershell
python scripts/reproduce_selected_cases.py `
  artifacts/reports/selected_cases_with_sources.csv `
  --output artifacts/reports/selected_cases_local_baseline_final.csv `
  --local-dynamic-only
```

Add a bounded mutation smoke test:

```powershell
python scripts/reproduce_selected_cases.py `
  artifacts/reports/selected_cases_with_sources.csv `
  --output artifacts/reports/selected_cases_local_mutation_final.csv `
  --local-dynamic-only --mutation --max-mutants 3
```

`--local-dynamic-only` runs the real syntax, Selenium/Behave, BDD diagnostic,
and mutation tools but deliberately skips external LLM calls.

## Reproduce the final local-source experiments

The final experiments use explicit `source_project_dir` values and disable
reference-network access. This avoids benchmark-host availability affecting
results. `artifacts/reports/selected_cases_with_sources.csv` uses
repository-relative paths into the tracked lightweight fixtures under
`data/reference_sources/`, so the commands do not depend on the ignored legacy
`workspace/` directory.

### 1. Baseline evaluation

```powershell
$env:E2E_REFERENCE_NETWORK_ENABLED = "0"
$env:E2E_ENABLE_DYNAMIC = "1"
$env:E2E_ENABLE_MUTATION = "0"
$env:E2E_ENABLE_COVERAGE = "0"
$env:E2E_HEADLESS = "1"

python scripts/reproduce_selected_cases.py `
  artifacts/reports/selected_cases_with_sources.csv `
  --output artifacts/reports/selected_cases_local_baseline_final.csv `
  --local-dynamic-only
```

### 2. Scope-aware mutation evaluation

```powershell
$env:E2E_REFERENCE_NETWORK_ENABLED = "0"
$env:E2E_ENABLE_DYNAMIC = "1"
$env:E2E_ENABLE_MUTATION = "1"
$env:E2E_ENABLE_COVERAGE = "0"
$env:E2E_HEADLESS = "1"

python scripts/reproduce_selected_cases.py `
  artifacts/reports/selected_cases_with_sources.csv `
  --output artifacts/reports/selected_cases_local_mutation_10_final.csv `
  --local-dynamic-only --mutation --max-mutants 10
```

### 3. Test suite

```powershell
python -m pytest -p no:cacheprovider -q
```

## Interpretation limitations

- The BDD step-execution diagnostic is not source branch coverage.
- Current `coverage.py` instrumentation measures Python code; it does not
  measure HTML/JavaScript branches in the E2EDev applications.
- Relevant mutation score is meaningful only when
  `relevant_mutants_total > 0`; otherwise it is reported as unavailable.
- Mutation results are sampled evidence, bounded by the configured mutant
  limit, not full application-level coverage.
- The dynamic analyst is an optional explanatory layer and does not replace
  deterministic mutation scoring or the independent static judges.

## Final results and presentation

- [Final results summary](artifacts/reports/final_results_summary.md)
- [Final results CSV](artifacts/reports/final_results_summary.csv)
- [Baseline evaluation](artifacts/reports/final_full_pipeline_baseline.csv)
- [10-mutant selected evaluation](artifacts/reports/selected_cases_local_mutation_10_final.csv)
- [Dynamic analyst evidence](artifacts/reports/dynamic_analyst_bench05.csv)
- [Presentation](artifacts/reports/final_e2e_test_evaluator_presentation.pptx)
- [Figures](artifacts/reports/figures/)

## Coverage

Enable Python coverage:

```powershell
$env:E2E_ENABLE_COVERAGE = "1"
$env:E2E_COVERAGE_BRANCH_ENABLED = "1"
$env:E2E_COVERAGE_TIMEOUT_SECONDS = "120"
$env:E2E_COVERAGE_INCLUDE_PATTERNS = "**/*.py"
```

`total_line_coverage` and `total_branch_coverage` come from `coverage.py`.
Branch coverage is:

```text
covered Python branches / measured Python branches × 100
```

This is not JavaScript branch coverage. HTML/JavaScript-only reference projects
return structured unavailable/no-data evidence rather than a fabricated zero.
The BDD step diagnostic is also separate: it reports which Given/When/Then steps
ran successfully, not source-code coverage.

## Mutation testing

Enable mutation testing:

```powershell
$env:E2E_ENABLE_MUTATION = "1"
$env:E2E_MAX_MUTANTS = "20"
$env:E2E_MUTATION_MAX_PER_FILE = "5"
$env:E2E_MUTATION_SEED = "1337"
$env:E2E_MUTATION_INCLUDE_PATTERNS = "**/*.py,**/*.js,**/*.html"
```

The original test must pass before mutants run. Every mutant is applied to a
separate source copy. Verdicts:

- `KILLED`: the generated test fails on a valid mutant.
- `SURVIVED`: the generated test still passes.
- `INVALID`: the mutant cannot be validated or executed defensibly.

```text
mutation score = killed / (killed + survived) × 100
```

Invalid mutants are always excluded from the denominator. See
`MUTATION_OPERATORS.md` for the documented, general operator taxonomy.

The report retains two distinct mutation views:

- raw mutation score over every valid selected mutant;
- relevant mutation score over valid mutants deterministically linked to the
  active scenario target or literal.

If no relevant valid mutant exists, the relevant score is null and
`mutation_scope_status=NO_RELEVANT_MUTANTS`. Out-of-scope mutants remain in the
raw suite report but are not evidence that one scenario is weak.

## Optional reference acquisition

Offline local overrides and validated caches are preferred. When
`E2E_REFERENCE_NETWORK_ENABLED=1`, explicit ZIP/TAR URLs may be safely extracted
and actual Git URLs may be shallow-cloned with a timeout. Directory-style
`anonymous.4open.science` URLs are not scraped to guess download links; provide a
local source directory or pre-populated cache when no stable archive endpoint is
available. Automated refresh/fetch-mode policies remain optional future work.

## Validation

```powershell
python scripts/lint.py
python -m pytest -p no:cacheprovider -q `
  test_mutation_testing.py test_reference_resolver.py test_coverage_agent.py `
  tests/test_framework_integration.py
```

All generated workspaces, evidence, reports, and reproducibility inputs are
written below `artifacts/`. Reference projects are copied before mutation.
