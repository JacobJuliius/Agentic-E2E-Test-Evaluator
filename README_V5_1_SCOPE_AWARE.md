> **Superseded in this package.** Read `README_V5_2_SAFE_REFINER.md` for the active V5.2 behavior.

# V5.1: Scope-Aware, Budget-Aware Hybrid E2E Test Evaluator

V5.1 completes the evaluation-and-repair loop while correcting four evaluation risks:

1. **Test scope vs requirement-suite scope:** raw mutation score is retained for suite aggregation, but a single test is judged only against mutants deterministically tied to its own target.
2. **Refiner scope:** the Refiner may change only Python Behave step-definition code. The BDD `.feature` scenario is immutable and feature-file output is rejected.
3. **BDD coverage semantics:** BDD step-execution coverage is diagnostic runtime evidence. It is not JavaScript branch coverage, product-space coverage, or a direct score component.
4. **API-budget routing:** Dynamic Analyst, Critic, and Refiner are invoked only when evidence requires them. LLM nodes remain strictly serial.

## Pipeline

```text
Syntax + AST
  → Requirement Alignment (LLM)
  → Assertion Quality (LLM)
  → Hallucination/Smell (LLM)
  → Maintainability (LLM)
  → Execution Tool
  → BDD Step-Execution Diagnostic Tool
  → Mutation Tool
  → [conditional Dynamic Evidence Analyst (LLM)]
  → [conditional Critic (LLM)]
  → Deterministic Consensus
  → [conditional Refiner (LLM; step code only)]
  → Refined-code Execution Tool
  → Refined-code BDD Diagnostic Tool
  → Refined-code Mutation Tool (optional)
  → Deterministic Before/After Comparison
```

## Metric semantics

### Execution success
A Selenium/Behave test runs on the original reference application. `PASSED`, `TEST_FAILED`, `TIMEOUT`, and `HARNESS_*` remain distinct.

### BDD step-execution diagnostic
The Behave JSON report measures whether the scenario's Given/When/Then steps actually ran. It is useful to localize an execution failure, but it **does not** claim business completeness or source-code branch coverage.

### Mutation testing: two valid levels

- `dynamic_mutation_score`: **raw score** over every selected mutant. Use it for requirement-suite aggregation, where complementary tests can collectively kill all mutants.
- `dynamic_relevant_mutation_score`: **scope-aware score** over only mutants tied to the current test's explicit data-testid/product target or asserted literal. Consensus uses this score for individual-test quality.

Each mutant record now has:

```text
scope_relation = RELEVANT | OUT_OF_SCOPE | UNCERTAIN
source_scope = nearest product/UI context
```

An `OUT_OF_SCOPE` mutant may be valuable for another test in the same requirement suite, but does not reduce the current test's score or trigger a repair.

## API-limit design

All model calls are serial and have a five-second interval after successful calls, yielding roughly 12 RPM under a 15-RPM allowance.

Every syntax-valid case uses the four static specialist calls. The other calls are conditional:

- **Dynamic Analyst:** execution `TEST_FAILED/TIMEOUT`, or a `RELEVANT/UNCERTAIN` mutant survives.
- **Critic:** only when a material static/dynamic contradiction is plausible.
- **Refiner:** only when the Analyst recommends repair, execution fails, or static score falls below `E2E_STATIC_REFINE_THRESHOLD`.

`E2E_LLM_RPD_SOFT_LIMIT=470` is a same-process safety guard. It leaves margin below a 500-RPD free-tier limit; it cannot count calls made by previous Python processes earlier the same day.

## Installation

```powershell
pip install -U behave selenium pandas tqdm python-dotenv langgraph langchain-google-genai pytest
```

Install Chrome. Recent Selenium versions use Selenium Manager to obtain a matching driver.

Place reference applications manually under:

```text
artifacts/reference_cache/E2ESD_Bench_01/
artifacts/reference_cache/E2ESD_Bench_02/
...
```

Each benchmark folder needs an `index.html` somewhere below it.

## Safe first run

```powershell
$env:E2E_MAX_CASES = "3"
$env:E2E_ENABLE_DYNAMIC = "1"
$env:E2E_ENABLE_MUTATION = "1"
$env:E2E_MAX_MUTANTS = "3"
$env:E2E_ENABLE_DYNAMIC_ANALYST = "1"
$env:E2E_ENABLE_CRITIC = "1"
$env:E2E_ENABLE_REFINER = "1"
$env:E2E_ENABLE_REFINEMENT_VALIDATION = "0"
$env:E2E_ENABLE_REFINEMENT_MUTATION_VALIDATION = "0"

python main.py
```

## Refiner validation run

Enable local refined-code execution only after baseline dynamic tools work:

```powershell
$env:E2E_MAX_CASES = "1"
$env:E2E_ENABLE_REFINEMENT_VALIDATION = "1"
$env:E2E_ENABLE_REFINEMENT_MUTATION_VALIDATION = "0"
python main.py
```

Then, for a small selected batch, validate fault-detection improvement:

```powershell
$env:E2E_ENABLE_REFINEMENT_MUTATION_VALIDATION = "1"
python main.py
```

## Important output columns

### Scope-aware mutation

- `dynamic_mutation_score`: raw score for suite aggregation.
- `dynamic_relevant_mutation_score`: individual-test fault-detection score.
- `dynamic_mutation_scope_status`: `SCOPE_AWARE`, `UNCERTAIN_SCOPE`, or `NO_RELEVANT_MUTANTS`.
- `dynamic_scope_relevant_survivors`
- `dynamic_scope_out_of_scope_survivors`
- `dynamic_scope_uncertain_survivors`
- `dynamic_test_scope`

### Conditional LLM traceability

- `eval_dynamic_analysis_status`
- `eval_critic_status`
- `eval_refiner_status`
- `eval_refiner_scope` (always `STEP_CODE_ONLY`)

### Repair validation

- `validation_relevant_mutation_score`
- `repair_delta_mutation` (scope-aware when V5.1 fields exist)
- `repair_validation_status`

## Scoring policy

1. Syntax failure → `overall_score = 0`.
2. Genuine baseline `TEST_FAILED` / `TIMEOUT` → score capped at 40.
3. `HARNESS_*` / unavailable tooling → static score is preserved; no artificial dynamic zero.
4. Passing baseline score components:
   - static quality: 60%
   - execution success: 15%
   - scope-aware mutation score: 25%, only when relevant mutant evidence exists
5. BDD step-execution coverage is reported and explained, but is **not directly weighted**.
6. If mutation is unavailable or has no relevant mutants, its weight is omitted and remaining components are normalized.

## Tests

```powershell
pytest -q test_v5_local_tools.py test_v5_1_scope_logic.py
python -m py_compile agents.py dynamic_agents.py graph.py main.py
```
