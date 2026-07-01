# V5: Complete Hybrid E2E Test Evaluator

This version implements the system through **Step 5**:

1. Static rule-based gate and AST evidence.
2. Four serial static LLM specialists.
3. Local runtime evidence: Behave/Selenium execution, BDD step coverage, mutation testing.
4. A Dynamic Evidence Analyst LLM reads tool reports and diagnoses test-quality implications.
5. Critic + deterministic Consensus combine static and dynamic evidence.
6. Refiner generates replacement Behave/Selenium step code.
7. Optional, fully local validation re-runs the refined code and reports before/after change.

It deliberately does **not** run the final benchmark-wide analysis/presentation phase.

## Pipeline

```text
Syntax + AST
  → Requirement Alignment (LLM)
  → Assertion Quality (LLM)
  → Hallucination/Smell (LLM)
  → Maintainability (LLM)
  → Execution Tool
  → BDD Dynamic Coverage Tool
  → Mutation Tool
  → Dynamic Evidence Analyst (LLM)
  → Critic (LLM)
  → Deterministic Consensus
  → Refiner (LLM)
  → Refined-code Execution Tool
  → Refined-code BDD Coverage Tool
  → Refined-code Mutation Tool (optional)
  → Deterministic Before/After Comparison
```

## Why tool nodes do not call an LLM

`Execution`, `Dynamic Coverage`, `Mutation`, and `Refinement Validation` are local tool agents.
They generate reproducible evidence. The Dynamic Evidence Analyst, Critic, and Refiner read this
evidence. The LLM never invents a mutation score or decides whether a browser test passed.

## Installation

```powershell
pip install -U behave selenium pandas tqdm python-dotenv langgraph langchain-google-genai pytest
```

Install Chrome. Recent Selenium versions use Selenium Manager to resolve the matching driver.
Place each reference application manually under:

```text
artifacts/reference_cache/E2ESD_Bench_01/
artifacts/reference_cache/E2ESD_Bench_02/
...
```

Each folder must contain `index.html` somewhere below it.

## API-limit design

All LLM nodes are serial and `invoke_with_retry()` sleeps five seconds after successful calls.
That yields about 12 RPM, below a 15 RPM limit.

For a dynamically evaluated valid case, the maximum is:

```text
Requirement + Assertion + Hallucination + Maintainability
+ Dynamic Evidence Analyst + Critic + Refiner = 7 LLM calls
```

For 70 valid cases this is at most **490 successful calls**, leaving ten calls below a 500-RPD
quota. The local browser/mutation/validation nodes make no API calls. `E2E_LLM_RPD_SOFT_LIMIT`
defaults to 490 for a same-process safety stop. It cannot know API calls made in earlier processes
on the same calendar day.

## First safe run: three cases, dynamic tools, no refinement re-run

```powershell
$env:E2E_MAX_CASES = "3"
$env:E2E_ENABLE_DYNAMIC = "1"
$env:E2E_ENABLE_MUTATION = "1"
$env:E2E_MAX_MUTANTS = "3"
$env:E2E_ENABLE_DYNAMIC_ANALYST = "1"
$env:E2E_ENABLE_REFINER = "1"
$env:E2E_ENABLE_REFINEMENT_VALIDATION = "0"
$env:E2E_ENABLE_REFINEMENT_MUTATION_VALIDATION = "0"

python main.py
```

## Step-5 validation run: one to three selected cases

Refiner validation executes the `fixed_code` against the original app. It has no additional LLM
cost, but can substantially increase Chrome runtime. Start with mutation validation disabled:

```powershell
$env:E2E_MAX_CASES = "1"
$env:E2E_ENABLE_DYNAMIC = "1"
$env:E2E_ENABLE_MUTATION = "1"
$env:E2E_MAX_MUTANTS = "3"
$env:E2E_ENABLE_REFINEMENT_VALIDATION = "1"
$env:E2E_ENABLE_REFINEMENT_MUTATION_VALIDATION = "0"

python main.py
```

Only after refined baseline execution works should you enable refined mutation testing:

```powershell
$env:E2E_ENABLE_REFINEMENT_MUTATION_VALIDATION = "1"
python main.py
```

## Key outputs

### Baseline dynamic tools

- `dynamic_execution_status`: `PASSED`, `TEST_FAILED`, `TIMEOUT`, `HARNESS_*`, etc.
- `dynamic_coverage_score`: BDD successful-step coverage, **not JavaScript branch coverage**.
- `dynamic_mutation_score`: killed / (killed + survived).
- `dynamic_mutation_records`: structured mutant evidence for the LLM analyst.

### Dynamic LLM interpretation

- `eval_dynamic_quality_label`
- `eval_dynamic_failure_category`
- `eval_dynamic_root_cause`
- `eval_dynamic_fault_detection_gaps`
- `eval_dynamic_prioritized_repairs`

### Refiner validation

- `validation_execution_status`
- `validation_dynamic_coverage_score`
- `validation_dynamic_mutation_score`
- `repair_validation_status`
- `repair_delta_coverage`
- `repair_delta_mutation`

## Scoring policy

1. Syntax failure => `overall_score = 0`.
2. Genuine `TEST_FAILED` or `TIMEOUT` baseline => score capped at 40.
3. `HARNESS_*` / missing tool evidence => static score remains; no artificial zero.
4. Passing baseline with all evidence available:
   - static quality: 45%
   - execution success: 15%
   - BDD dynamic coverage: 15%
   - mutation score: 25%
5. If a dynamic metric is disabled/unavailable, its weight is omitted and remaining weights are
   normalized. This avoids penalizing a test for unavailable tooling.

## Tests

```powershell
pytest -q test_v5_local_tools.py
python -m py_compile agents.py dynamic_agents.py graph.py main.py
```
