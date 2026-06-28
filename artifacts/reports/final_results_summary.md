# Final evaluation results

Main protocol: local source overrides, reference network disabled, isolated dynamic execution, and at most 10 sampled mutants per case.

| Case | Static | Hybrid | Execution | Raw mutation | Relevant mutation | Relevant killed | Interpretation |
|---|---:|---:|---|---:|---:|---:|---|
| Bench 01 — Drag/drop negative case | 9.95 | 27.96 | PASSED | 0% | N/A | 0/0 | Executable, but static quality is very low; raw 0% is not used to penalize this scenario because no sampled mutant was case-relevant. |
| Bench 02 — Notes add-note | 55.15 | 64.12 | PASSED | 20% | 100% | 2/2 | The add-note scenario has meaningful fault-detection ability (2/2 relevant mutants killed), while case-level requirement alignment still flags the omitted rapid-click behavior. |
| Bench 03 — Seat selection | 50.10 | 60.08 | PASSED | 40% | 50% | 2/4 | The test catches some seat-selection faults (2/4 relevant mutants) but misses persistence and reload-related behavior. |
| Bench 05 — Dictionary search | 80.75 | 84.60 | PASSED | 0% | 0% | 0/1 | Execution passes and scores look strong, yet sampled mutation evidence exposes a concrete assertion gap around result detail and word-example binding. |

## Interpretation guardrails

- BDD step-execution diagnostic is not source branch coverage.
- Python branch coverage is disabled because these benchmark applications are primarily HTML/JavaScript.
- Results are case-level requirement alignment and sampled mutation evidence, not full application-level coverage.
- Relevant mutation score is meaningful only when `relevant_mutants_total > 0`.
- Optional critic/refiner extensions are excluded from the main protocol; the dynamic analyst is explanatory, not the primary scorer.
