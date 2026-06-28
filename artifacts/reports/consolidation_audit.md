# Consolidation audit

- `EvaluationConfig.build_state()` reads CSV `source_project_dir` but stores it only
  as `coverage_source_dir`; the shared graph state has no `source_project_dir`.
- Baseline/refined execution and mutation call `_clone_reference(reference_answer)`
  through `_new_workspace()` / `_run_general_mutation_evaluation()`, so they ignore
  the supplied local directory. This explains offline failures for uncached benches.
- Coverage uses `coverage_source_dir`, but it has separate resolution/copy logic and
  copies the project as `app/`; dynamic execution also uses `app/`.
- The active mutation agent uses `mutation_testing.py`. Older scope/discovery helpers
  remain in `dynamic_agents.py` for compatibility tests but are not the active engine.
- `e2e_eval/` is primarily shared configuration/schema/reporting plus compatibility
  exports; it does not contain a competing evaluator implementation.
- No root-level `coverage.py` exists, so the PyPI `coverage` package is not shadowed.
- Mandatory fixes: retain `source_project_dir`, centralize local-first source
  selection/provenance, use canonical `source_project/` workspaces, restore
  scenario-aware mutation relevance, and include Gherkin context in hallucination
  evaluation.
