# V4: Pure-Python Hybrid Static–Dynamic Evaluator

## What this version changes

The dynamic layer uses only Python, Behave, Selenium, Git and Chrome. It does **not** require Node.js, npm, Istanbul or Stryker.

```text
Syntax + AST
→ Requirement (LLM)
→ Assertion (LLM)
→ Hallucination (LLM)
→ Maintainability (LLM)
→ Execution Agent (local)
→ Dynamic Coverage Agent (local)
→ Mutation Agent (local)
→ Critic (LLM)
→ Consensus
→ Refiner (LLM)
```

The original six LLM calls remain serial. Thus the 15 RPM strategy is unchanged. The three dynamic nodes are local Python tools and make no Gemini calls.

## Honest coverage terminology

`dynamic_coverage_score` is **not JavaScript branch coverage**. It is **Requirement-aligned Dynamic BDD Coverage** obtained from Behave's JSON report:

- successful `Given` / setup steps;
- successful `When` / action steps;
- successful `Then` / verification-oracle steps.

Use that exact name in slides and reports. It is a valid dynamic E2E execution metric, but it must not be described as source-code branch coverage.

`mutation_score` is a true mutation metric:

```text
mutation score = killed mutants / (killed mutants + survived mutants) × 100
```

The agent mutates the **reference application's** HTML/JavaScript in isolated workspaces, reruns the original generated test and records every outcome. Browser timeouts and harness errors are excluded from the denominator.

## Installation

Activate your `e2e_agent` environment and install:

```powershell
pip install -r requirements_dynamic.txt
```

Install Google Chrome. Selenium 4.11+ normally uses Selenium Manager to obtain a matching driver automatically. Also make sure Git is available:

```powershell
git --version
```

## First run: only three cases, no mutation yet

Copy `agents.py`, `graph.py`, `main.py` and `dynamic_agents.py` into the same directory as your existing project files. Then run:

```powershell
$env:E2E_INPUT_FILE = "data/e2edev_sample.csv"
$env:E2E_OUTPUT_FILE = "data/evaluation_results_v4_pure_python.csv"
$env:E2E_MAX_CASES = "3"
$env:E2E_ENABLE_DYNAMIC = "1"
$env:E2E_ENABLE_MUTATION = "0"

python main.py
```

It exports `dynamic_execution_*` and `dynamic_coverage_*` columns. Browser screenshots, page source, Behave JSON reports and mutation reports appear in `artifacts/dynamic/`.

## Enable a small mutation trial

Only after at least one baseline execution result is `PASSED`:

```powershell
$env:E2E_ENABLE_MUTATION = "1"
$env:E2E_MAX_MUTANTS = "3"
$env:E2E_MUTANT_TIMEOUT_SECONDS = "45"

python main.py
```

Start with three mutants. Each mutant is a separate browser run, so full-scale mutation testing is intentionally expensive.

## Hybrid score policy

- Baseline `PASSED`: static score 50%, execution success 20%, Dynamic BDD Coverage 15%, mutation score 15%. Missing dynamic metrics are removed and the available weights are normalized.
- Baseline `TEST_FAILED` or `TIMEOUT`: final score is capped at 40.
- `HARNESS_*`, `SKIPPED_DISABLED` or unavailable tooling: dynamic evidence is inconclusive; the score stays static-only rather than becoming 0.

## Regression tests

```powershell
pytest -q test_static_metrics_v32.py test_dynamic_agents.py
```
