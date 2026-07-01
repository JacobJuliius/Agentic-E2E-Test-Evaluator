# V5.3.1 — Targeted Mutation Portfolio

## What was fixed

### Root cause
`DRAGGABLE_FALSE_TO_TRUE` was not selected for `req1-test4` because the reference app's `drop-area` does not explicitly contain `draggable="false"`. It has no `draggable` attribute. The old operator therefore produced no candidate and the global price mutants filled the `max_mutants=3` budget.

### New generic boolean-DOM attribute family
The evaluator now supports two reusable DOM-state operators:

1. `DRAGGABLE_FALSE_TO_TRUE`
   - flips an explicit `draggable="false"` value to `true`.
2. `DRAGGABLE_MISSING_TO_TRUE`
   - inserts `draggable="true"` into an explicitly identified UI element that lacks the attribute.

The second operator is **not specific to `drop-area`**. It is a generic "missing boolean UI state becomes enabled" mutation pattern, limited to stable identified elements (`data-testid`, `data-test`, or `id`).

## Targeted mutant selection policy

Mutation discovery now uses a general two-stage policy:

1. **Operator applicability**: generate mutants from reusable families (data value, boolean DOM state, source logic).
2. **Target selection**: when the BDD scenario gives a concrete target and behaviour cue, choose target-local applicable mutants before unrelated app-wide mutants.

For a drag/drop scenario targeting `data-testid="drop-area"`, a small budget selects:

```text
DRAGGABLE_MISSING_TO_TRUE on drop-area
```

instead of unrelated `$40`, `$120`, `$35` price mutations.

For ordinary product scenarios, price mutants keep their original priority.

## Validation

```powershell
pytest -q test_v5_3_1_targeted_mutation.py test_v5_3_drag_mutation.py `
  test_v5_2_safe_refiner.py test_v5_1_scope_logic.py test_v5_local_tools.py
```

Expected: `23 passed`.

## Re-run req1/test4

```powershell
$env:E2E_INPUT_FILE = "data/bench01_req1_test4.csv"
$env:E2E_OUTPUT_FILE = "data/bench01_req1_test4_v5_3_1_validation.csv"
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

Check `mutation_report.json` for `DRAGGABLE_MISSING_TO_TRUE` or `DRAGGABLE_FALSE_TO_TRUE`.
