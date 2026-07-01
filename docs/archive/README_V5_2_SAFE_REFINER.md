# V5.2 — Scope-Precise Mutation and Safe Refiner Acceptance

## What changed from V5.1

### 1. Scope precision

Mutation relevance is now inferred from the **current immutable BDD Scenario** first.
Only if the scenario has no concrete target does the evaluator inspect literals inside
`@then` assertion bodies. It never uses generic `@given` / `@when` decorators, helper
functions, or broad fine-grained requirements to decide that a product belongs to the
current test.

This prevents a reusable step definition such as `@when('... {product_id} ...')` from
incorrectly making product 1, product 2, and product 3 all relevant to one concrete
scenario.

### 2. Refiner trigger policy

A low static score **does not** trigger code rewriting. The Refiner can run only when:

- Python syntax fails;
- baseline execution is `TEST_FAILED` or `TIMEOUT`;
- Dynamic Evidence Analyst returns `should_refine=true`; or
- at least one **relevant** mutant survives and the scope-aware mutation score is below
  `E2E_RELEVANT_MUTATION_REFINE_THRESHOLD` (default 80).

### 3. Immutable BDD scope

The Refiner may change only Python Behave step code. It cannot claim to add or alter a
Feature, Scenario, Scenario Outline, Examples block, parameterization, or extra test.
Feature-level claims are detected in the report and the proposal is rejected.

### 4. Safety acceptance gate

A generated repair is only exported in `eval_fixed_code` if local validation passes.

- `ACCEPTED_VALIDATED`: candidate passed validation and becomes the accepted code.
- `REJECTED_REGRESSION`: original passed but refined code failed/timed out; original code
  is restored automatically.
- `REJECTED_VALIDATION_FAILURE`: refined candidate failed validation; original code is restored.
- `PENDING_VALIDATION`: validation is disabled; candidate is retained only in
  `eval_refiner_candidate_code`, while `eval_fixed_code` remains original code.

## Key output fields

```text
# Scope precision
dynamic_test_scope
dynamic_relevant_mutation_score

# Refiner safety
eval_refiner_status
eval_refiner_accepted
eval_refiner_candidate_code
eval_refiner_rejection_reason
eval_refiner_scope_violation
repair_validation_status
```

## Recommended Bench-01 run

```powershell
$env:E2E_MAX_CASES = "12"
$env:E2E_ENABLE_DYNAMIC = "1"
$env:E2E_ENABLE_MUTATION = "1"
$env:E2E_MAX_MUTANTS = "3"
$env:E2E_ENABLE_DYNAMIC_ANALYST = "1"
$env:E2E_ENABLE_CRITIC = "1"
$env:E2E_ENABLE_REFINER = "1"
$env:E2E_ENABLE_REFINEMENT_VALIDATION = "1"
$env:E2E_ENABLE_REFINEMENT_MUTATION_VALIDATION = "0"
$env:E2E_RELEVANT_MUTATION_REFINE_THRESHOLD = "80"

python main.py
```

Keep refined mutation validation off for the integration run. Enable it only for accepted,
validated repair candidates that still have a relevant mutation gap.

## Deterministic Python Coverage Agent

The graph also supports an opt-in coverage.py node after the existing BDD
step-execution diagnostic and before mutation testing. It executes the generated
Behave/Selenium test in an isolated copy of a local source project and requests
branch coverage by default.

This metric is deliberately separate from BDD step coverage. It measures Python
source executed in the configured source project. HTML/JavaScript-only applications
cannot be measured by coverage.py and return a structured `failed` result with a
failure reason rather than an artificial zero.

Configuration:

```powershell
$env:E2E_ENABLE_COVERAGE = "1"
$env:E2E_COVERAGE_SOURCE_DIR = "workspace/E2EDev_data/E2ESD_Bench_01/source_project"
$env:E2E_COVERAGE_TIMEOUT_SECONDS = "120"
$env:E2E_COVERAGE_BRANCH_ENABLED = "1"
$env:E2E_COVERAGE_INCLUDE_PATTERNS = "*.py"
$env:E2E_COVERAGE_EXCLUDE_PATTERNS = "*/migrations/*,*/generated/*"
$env:E2E_COVERAGE_INCLUDE_TESTS = "0"
```

The CSV export includes status, execution status, line/branch percentages,
per-file covered and missing lines/branches, measured files, the exact command,
output summaries, failure reason, and raw report/artifact paths.

## Reference Source Resolver

Coverage, baseline execution, and mutation testing share one deterministic source
resolver. Local directories are validated directly. Remote retrieval is disabled
by default; when enabled, explicit archive URLs are downloaded and safely extracted,
while other HTTP(S), Git, or SSH URLs are attempted only as Git repositories.
The resolver never scrapes an arbitrary HTML page for download links.

```powershell
$env:E2E_REFERENCE_WORKSPACE_ROOT = "artifacts"
$env:E2E_REFERENCE_NETWORK_ENABLED = "0"
$env:E2E_REFERENCE_TIMEOUT_SECONDS = "90"
$env:E2E_REFERENCE_EXPECTED_PATTERNS = "**/index.html,**/*.js"
```

Resolved repositories are cached below
`artifacts/reference_cache/<stable-project-id>/`. Existing valid basename caches
are reused for compatibility. Resolution metadata includes source URL, local path,
cache status, retrieval method, validation evidence, and failure reason.

## General Mutation Testing Agent

The active mutation node uses the modular operators documented in
`MUTATION_OPERATORS.md`. It reuses the successful original execution as its
baseline gate, creates one isolated source copy per mutant, validates each mutant,
and excludes invalid mutants from the score denominator.

```powershell
$env:E2E_ENABLE_MUTATION = "1"
$env:E2E_MAX_MUTANTS = "20"
$env:E2E_MUTATION_MAX_PER_FILE = "5"
$env:E2E_MUTATION_SEED = "1337"
$env:E2E_MUTATION_INCLUDE_PATTERNS = "**/*.py,**/*.js,**/*.html"
$env:E2E_MUTATION_EXCLUDE_PATTERNS = "**/vendor/**,**/*.min.js"
$env:E2E_MUTATION_PROJECT_VALIDATION_COMMAND = "npm test -- --runInBand"
$env:E2E_MUTATION_KEEP_WORKSPACES = "0"
```

Reports include the complete mutation schema, exact execution commands,
environment metadata, per-operator metrics, invalid-mutant reasons, and surviving
mutant details. Mutant workspaces are deleted after execution by default; raw
reports remain under `artifacts/dynamic/results/`.
