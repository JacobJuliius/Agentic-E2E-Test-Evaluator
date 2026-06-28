\# Project Instructions



\## Project goal

Evaluate the quality of generated end-to-end test scripts.

Do not generate E2E tests as the primary task.



\## Evaluation dimensions

\- Syntax / execution validity

\- Requirement coverage

\- Assertion quality

\- Maintainability

\- Branch coverage

\- Mutation score



\## Architecture rules

\- Preserve the existing LangGraph orchestration.

\- Static LLM evaluators must remain independent from deterministic dynamic evaluators.

\- Dynamic evaluation must never crash the complete pipeline.

\- All subprocess execution must use timeouts.

\- All generated artifacts must be written under artifacts/.

\- Original reference source projects must never be mutated in place.

\- Mutation testing must use isolated copies.



\## Research validity rules

\- Avoid benchmark-specific hardcoding.

\- Mutation operators must be documented and generalizable.

\- Invalid mutants do not count in mutation score denominator.

\- Coverage and mutation results must include raw evidence and failure reasons.

\- Keep all outputs reproducible with stable seeds and saved configuration.



\## Coding rules

\- Use Python type hints.

\- Add tests for new functionality.

\- Prefer modular utilities over logic embedded in graph.py.

\- Do not remove existing functionality unless explicitly instructed.

