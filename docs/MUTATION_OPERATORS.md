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
| `CONDITIONAL_NEGATION` | JavaScript, TypeScript | Negates one conservatively parsed simple `if`/`while` condition. |
| `STATE_UPDATE_MUTATION` | JavaScript, TypeScript | Neutralizes one direct assignment while preserving statement syntax. |

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

JavaScript operators also inspect inline `<script>` blocks in HTML while
retaining exact offsets in the original HTML file. When LLM proposals do not
match a safe operator span, seeded fallback discovery fills the remaining
campaign budget and records why each original proposal was rejected.

The default non-functional exclusion policy removes stylesheet/favicon/meta
changes, CDN and unrelated external-resource URLs, integrity/crossorigin
metadata, inline styles, and cosmetic CSS from scoring.

## Validity and scoring

Every mutant is applied to a fresh isolated project copy. Python is parsed with
`ast`; JavaScript is checked with `node --check` when Node is available; HTML is
parsed after mutation. A configurable project validation command can add a build
or compile check.

- `KILLED`: the baseline passed and the generated test fails on the valid mutant.
- `SURVIVED`: the generated test still passes on the valid mutant.
- `INVALID`: the patch cannot be safely applied or validated.
- `TIMEOUT`: mutant execution exceeds the configured subprocess timeout.
- `EXECUTION_ERROR`: the runner/harness cannot produce a test pass/fail result.

Only killed and survived mutants are valid denominator members:

```text
mutation_score = killed / (killed + survived) * 100
```

Invalid, timeout, and execution-error mutants are reported separately with raw
evidence and never counted in the denominator.
