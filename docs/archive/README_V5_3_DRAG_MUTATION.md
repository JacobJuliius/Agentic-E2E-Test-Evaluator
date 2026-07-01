# V5.3: Behaviour-aware Drag Mutation

This patch adds two targeted improvements:

1. **Dynamic Analyst CSV summary fallback**  
   `eval_dynamic_evidence_summary` now falls back to `root_cause` or a status explanation
   when the LLM omits its optional `dynamic_evidence_summary` field.

2. **Negative drag/drop mutation operator**  
   The mutation engine can now generate:

   ```
   draggable="false"  ->  draggable="true"
   ```

   It is labeled `DRAGGABLE_FALSE_TO_TRUE`.

   When the current BDD scenario explicitly mentions a drag/drop element (for example,
   `data-testid "drop-area"`), drag behaviour mutants are prioritized before price mutants.
   The operator is scope-aware: a mutation on the same concrete `data-testid` is marked
   `RELEVANT`.

## Important measurement note

A DOM attribute mutant is useful only if the test asserts observable negative behaviour
(for example, no title/price was captured, no drag transfer occurred, or no cart state
changed). Checking the original `draggable` attribute alone will kill this mutant, but that
is still a valid regression oracle for a requirement that explicitly says the element must
not be draggable.

## Quick validation

Run the existing `Bench_01 / req1 / test4` one-row CSV with:

```powershell
$env:E2E_INPUT_FILE = "data/bench01_req1_test4.csv"
$env:E2E_OUTPUT_FILE = "data/bench01_req1_test4_v5_3_validation.csv"
$env:E2E_MAX_CASES = "1"
$env:E2E_ENABLE_DYNAMIC = "1"
$env:E2E_ENABLE_MUTATION = "1"
$env:E2E_MAX_MUTANTS = "3"
$env:E2E_ENABLE_DYNAMIC_ANALYST = "1"
$env:E2E_ENABLE_CRITIC = "1"
$env:E2E_ENABLE_REFINER = "1"
$env:E2E_ENABLE_REFINEMENT_VALIDATION = "1"
$env:E2E_ENABLE_REFINEMENT_MUTATION_VALIDATION = "1"
python main.py
```

Expected mutation report:
- at least one `DRAGGABLE_FALSE_TO_TRUE` record;
- scope relation `RELEVANT` for the `drop-area` scenario;
- a scope-aware before/after score when the test can detect the attribute/behaviour regression.
