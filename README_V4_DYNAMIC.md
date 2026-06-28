# V4 dynamic evaluation: execution, branch coverage, mutation score

## What changed

The LangGraph path is now:

```text
Syntax + AST
→ Requirement
→ Assertion
→ Hallucination
→ Maintainability
→ Execution Agent
→ Branch Coverage Agent
→ Mutation Agent
→ Critic
→ Consensus
→ Refiner
```

The LLM nodes are still serial. `Execution`, `Branch Coverage`, and `Mutation` are local tools,
so they add no Gemini API calls and do not change the 15 RPM design.

## Installation

In the same Python environment as the project:

```powershell
pip install -U selenium behave
```

Install Chrome. Selenium Manager will normally obtain a matching driver automatically.

For true JavaScript branch coverage, install Node.js and then run in the project directory:

```powershell
npm install --save-dev istanbul-lib-instrument
```

## Recommended first run: three cases, no mutation

```powershell
$env:E2E_MAX_CASES = "3"
$env:E2E_ENABLE_DYNAMIC = "1"
$env:E2E_ENABLE_BRANCH_COVERAGE = "1"
$env:E2E_ENABLE_MUTATION = "0"
python main.py
```

This produces `data/evaluation_results_v4.csv` and artifacts under `artifacts/dynamic/`.

## Mutation pilot

Mutation is deliberately opt-in because every mutant creates an isolated browser run.

```powershell
$env:E2E_MAX_CASES = "3"
$env:E2E_ENABLE_MUTATION = "1"
$env:E2E_MAX_MUTANTS = "5"
$env:E2E_MUTANT_TIMEOUT_SECONDS = "45"
python main.py
```

Only run all 70 cases after the three-case pilot is stable.

## Output columns

- `dynamic_execution_status`, `dynamic_execution_success`, logs, duration and artifact directory
- `dynamic_branch_coverage_status`, `dynamic_branch_coverage`, `dynamic_branches_covered`, `dynamic_branches_total`
- `dynamic_mutation_status`, `dynamic_mutation_score`, killed/survived/timeout/inconclusive counts
- `eval_static_overall_score`, `eval_overall_score`, `eval_score_mode`

## Hybrid score policy

- `Execution PASSED`: 50% static score, 20% execution success, 15% branch coverage, 15% mutation score.
- If branch coverage or mutation is unavailable because a tool is missing, available evidence is re-normalized rather than scored as zero.
- `TEST_FAILED` or `TIMEOUT`: final score is capped at 40.
- `HARNESS_*` failures are inconclusive; the final score remains static-only.
