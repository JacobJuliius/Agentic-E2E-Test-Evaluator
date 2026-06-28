# General Mutation Operators

The active mutation engine evaluates whether a generated E2E test detects
observable faults in a reference implementation. Operators are domain-neutral,
language-aware, and applied by exact source span in an isolated project copy.

## Operator families

| Operator | Languages | Transformation |
|---|---|---|
| `NUMERIC_LITERAL_PERTURBATION` | Python, JavaScript, TypeScript | Adds one to one numeric literal. |
| `STRING_LITERAL_PERTURBATION` | Python, JavaScript, TypeScript | Appends a mutation marker to a non-docstring literal. |
| `BOOLEAN_NEGATION` | Python, JavaScript, TypeScript | Exchanges true and false literals. |
| `CONDITIONAL_BOUNDARY_REPLACEMENT` | Python, JavaScript, TypeScript | Exchanges strict and inclusive boundaries such as `>=` and `>`. |
| `RELATIONAL_OPERATOR_REPLACEMENT` | Python, JavaScript, TypeScript | Exchanges equality and inequality operators. |
| `EVENT_HANDLER_REPLACEMENT` | JavaScript, TypeScript, HTML | Redirects a statically detected listener to a non-firing event or removes an inline handler body. |
| `DOM_ATTRIBUTE_VALUE_MUTATION` | HTML | Mutates one parsed attribute value while preserving markup structure. |
| `UI_TEXT_MUTATION` | HTML | Replaces one visible text node outside script, style, template, and comment blocks. |

Python candidates use AST source positions. JavaScript and TypeScript candidates
come from a conservative tokenizer that excludes comments and preserves exact
token spans. HTML mutations use bounded opening-tag, attribute, and text-node
matches. No operator performs uncontrolled global replacement.

## Selection and reproducibility

Candidates are deduplicated by file, operator, source span, and replacement.
Selection is deterministic for a saved seed. Limits apply both per source file
and across the complete campaign. Virtual environments, dependencies, caches,
generated/build directories, minified files, oversized files, tests, and paths
outside configured scopes are excluded by default.

## Validity and scoring

Every mutant is applied to a fresh isolated project copy. Python is parsed with
`ast`; JavaScript is checked with `node --check` when Node is available; HTML is
parsed after mutation. A configurable project validation command can add a build
or compile check.

- `KILLED`: the baseline passed and the generated test fails on the valid mutant.
- `SURVIVED`: the generated test still passes on the valid mutant.
- `INVALID`: syntax, build, harness, dependency, or timeout evidence prevents a
  defensible killed/survived classification.

Only killed and survived mutants are valid denominator members:

```text
mutation_score = killed / (killed + survived) * 100
```

Invalid mutants are reported with evidence but never counted in the denominator.
