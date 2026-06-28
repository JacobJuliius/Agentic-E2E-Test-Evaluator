"""Run a selected-case CSV with local-source support and compact reporting."""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from e2e_eval.config import EvaluationConfig
from e2e_eval.reporting import write_evaluation_reports


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv")
    parser.add_argument(
        "--output",
        default="artifacts/reports/selected_cases_reproduced.csv",
    )
    parser.add_argument("--mutation", action="store_true")
    parser.add_argument("--coverage", action="store_true")
    parser.add_argument("--max-mutants", type=int, default=3)
    parser.add_argument("--network", action="store_true")
    parser.add_argument(
        "--local-dynamic-only",
        action="store_true",
        help="Run real local deterministic agents without external LLM calls.",
    )
    return parser.parse_args()


def run_local_dynamic_only(config: EvaluationConfig) -> None:
    import pandas as pd

    from agents import syntax_linter_agent
    from dynamic_agents import (
        dynamic_coverage_agent,
        execution_agent,
        mutation_agent,
    )

    source = pd.read_csv(config.input_file)
    output_rows = []
    for index, row in source.iterrows():
        state = config.build_state(row, int(index))
        result: dict = {}
        syntax = syntax_linter_agent(state)
        state.update(syntax)
        result.update(syntax)
        execution = execution_agent(state)
        state.update(execution)
        result.update(execution)
        coverage = dynamic_coverage_agent(state)
        state.update(coverage)
        result.update(coverage)
        mutation = mutation_agent(state)
        state.update(mutation)
        result.update(mutation)
        output_rows.append({
            "source_origin": result.get("source_origin", ""),
            "input_source_project_dir": result.get(
                "input_source_project_dir", state.get("source_project_dir", "")
            ),
            "resolved_source_project_dir": result.get(
                "resolved_source_project_dir", ""
            ),
            "resolved_entrypoint": result.get("resolved_entrypoint", ""),
            "dynamic_execution_status": result.get(
                "execution_status", "NOT_RUN"
            ),
            "dynamic_execution_return_code": result.get(
                "execution_return_code", -1
            ),
            "dynamic_execution_stderr_tail": result.get(
                "execution_stderr_tail", ""
            ),
            "dynamic_coverage_status": result.get(
                "dynamic_coverage_status", "NOT_RUN"
            ),
            "dynamic_coverage_detail": result.get(
                "dynamic_coverage_detail", ""
            ),
            "dynamic_mutation_status": result.get(
                "mutation_status", "NOT_RUN"
            ),
            "dynamic_mutation_score": result.get("mutation_score"),
            "dynamic_relevant_mutation_score": result.get(
                "relevant_mutation_score"
            ),
            "dynamic_mutation_scope_status": result.get(
                "mutation_scope_status"
            ),
            "dynamic_total_mutants_generated": result.get(
                "total_mutants_generated", 0
            ),
            "dynamic_valid_mutants": result.get("valid_mutants", 0),
            "dynamic_killed_mutants_count": result.get("killed_mutants", 0),
            "dynamic_survived_mutants_count": result.get(
                "survived_mutants", 0
            ),
            "dynamic_invalid_mutants": result.get("invalid_mutants", 0),
            "dynamic_mutation_report_path": result.get(
                "mutation_report_path", ""
            ),
        })
    combined = pd.concat(
        [source.reset_index(drop=True), pd.DataFrame(output_rows)], axis=1
    )
    write_evaluation_reports(combined, config.output_file, config=config)


def main() -> int:
    args = parse_args()
    base = EvaluationConfig.from_env()
    config = replace(
        base,
        input_file=str(Path(args.input_csv).resolve()),
        output_file=str(Path(args.output).resolve()),
        max_cases=0,
        case_sleep_seconds=0.0,
        enable_dynamic=True,
        enable_coverage=args.coverage,
        enable_mutation=args.mutation,
        max_mutants=args.max_mutants,
        reference_network_enabled=args.network,
    )
    if args.local_dynamic_only:
        run_local_dynamic_only(config)
    else:
        from main import run_batch_evaluation

        run_batch_evaluation(
            config.input_file, config.output_file, config=config
        )

    import pandas as pd

    frame = pd.read_csv(config.output_file)
    columns = [
        column for column in (
            "id", "req_id", "test_id", "source_origin",
            "dynamic_execution_status", "dynamic_mutation_score",
            "dynamic_relevant_mutation_score",
        )
        if column in frame.columns
    ]
    print(frame[columns].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
