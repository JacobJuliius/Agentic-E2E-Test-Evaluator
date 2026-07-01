# Mutation Testing Architecture

The mutation extension preserves the existing LangGraph and dynamic workspace
mechanism. Mutation testing remains opt-in through `E2E_ENABLE_MUTATION=1`.

## Final architecture

```text
Syntax/Linter Gatekeeper
-> Static LLM Evaluators
-> Executable Validation
-> Branch Coverage Agent (when supported)
-> Mutation Planning / Generation / Execution / Analysis
-> Hybrid Evidence Aggregator
```

LLMs own requirement interpretation, bounded target planning, and explanatory
relevance analysis. Tools own execution status, branch counters, and mutation
verdicts. An LLM cannot invent coverage or change a tool-derived verdict.

## Responsibility boundaries

1. `mutation_planner.py` gives an LLM requirements, generated test code, bounded
   source snippets, and source metadata. Its output is validated target metadata:
   relative file, line range, registered operator family, relevance, rationale,
   and confidence. Patch text, commands, traversal paths, and file-write
   instructions are rejected.
2. `mutation_operators.py` owns a configurable registry. Conservative,
   deterministic HTML/JavaScript operators turn a validated target into one
   exact-span patch. Operators skip locations they cannot preserve safely.
3. `mutation_runner.py` applies exactly one patch in a fresh workspace created
   by the existing dynamic workspace utility. It writes the patch, stdout,
   stderr, execution metadata, and runner report below `artifacts/`.
4. The existing generated E2E test is executed unchanged. Tool evidence alone
   assigns `KILLED`, `SURVIVED`, `INVALID`, `TIMEOUT`, or `EXECUTION_ERROR`.
5. `mutation_analysis.py` sends only survived records to an LLM. It may assess a
   likely requirement-relevant test gap, but it cannot change a verdict.

Every target has a stable `proposal_id` and an auditable lifecycle. LLM and
deterministic fallback targets record transitions through `PROPOSED`,
`ACCEPTED`, `REJECTED`, `GENERATED`, `EXECUTED`, and `SKIPPED`. A proposal that
does not produce a mutant always carries a rejection or skip reason in
`planning.proposal_lifecycle` and the CSV
`dynamic_mutation_proposal_lifecycle` column.
Concrete mutation records also identify `candidate_source` as `llm_proposal`
or `deterministic_fallback`.

When an accepted LLM target cannot be materialized, deterministic discovery
fills the remaining configured campaign budget from a broad seeded pool. It
prefers requirement-linked candidates, distinct operator families, and
functional HTML/JavaScript behavior. With the default limit this normally
produces five mutants when the source has enough safe mutation sites.

The graph order is:

```text
passing executable baseline
→ branch coverage and branch-relevance stages
→ mutation planning
→ deterministic mutation campaign
→ survivor-only interpretation
→ existing dynamic analyst / critic / consensus
```

Every failure is converted to a structured skip/unavailable/error result; a
mutation-stage failure does not terminate the complete evaluator.

## Scoring and compatibility

The main score is:

```text
mutation_score = killed / (killed + survived) * 100
```

`INVALID`, `TIMEOUT`, and `EXECUTION_ERROR` are excluded and reported
separately. Reports add `operator_stats`, `requirement_relevance_breakdown`, and
`requirement_relevant_survivors`. Historical keys including
`total_mutants_generated`, `killed_mutants`, `per_operator_breakdown`, scope
metrics, and CSV columns remain available.

Each mutation record also states `proposal_id`, `scope_relation`,
`included_in_raw_score`, `included_in_relevant_score`, and `exclusion_reason`.
The default policy rejects stylesheet and favicon links, CDN/external resource
URLs, `integrity`/`crossorigin` infrastructure attributes, metadata elements,
inline style mutations, and purely cosmetic CSS. Such mutations cannot lower a
requirement-level test adequacy score.

`RELEVANT` mutants form the requirement-level denominator. `UNCERTAIN` mutants
remain auditable without being asserted relevant, while `OUT_OF_SCOPE` mutants
remain available for broader suite analysis. These categories never alter the
deterministic execution verdict.

The seed, limits, include/exclude patterns, validation command, timeouts,
planning status, proposals, environment, raw records, and artifact paths are
saved in `mutation_report.json`.

## Framework limitations

- The bundled execution adapter targets Python Behave steps driving Selenium
  against local HTML/JavaScript projects. Playwright installation does not add
  a mutation execution adapter.
- JavaScript/TypeScript mutation uses conservative token/regex recognition,
  not a complete TypeScript compiler AST. Unsupported or ambiguous syntax is
  skipped.
- JavaScript syntax validation uses `node --check` when Node is available.
  HTML parsing can detect malformed structure only to the degree supported by
  Python's tolerant `HTMLParser`; projects should configure a build validation
  command when stronger validation is needed.
- LLM planning quality depends on the bounded source snippets. Large files are
  truncated, and no proposal is trusted until a deterministic operator matches
  its exact file/range.
- Equivalent mutants can survive without representing a test gap. The
  survivor analyst is advisory and is explicitly marked unavailable without
  credentials.
- Browser startup, dependency, and harness failures are
  `EXECUTION_ERROR`, not killed mutants. Timeouts are reported separately.
- Mutation score is meaningful only when the original generated test passes
  on the unmodified reference project.
