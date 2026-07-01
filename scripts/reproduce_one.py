"""Reproduce one benchmark-row evaluation with saved preflight evidence."""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from e2e_eval.config import EvaluationConfig
from e2e_eval.runtime.dependencies import check_dependencies
from e2e_eval.runtime.utils import safe_name
from e2e_eval.utils.paths import sanitize_export_payload
from reference_resolver import resolve_reference_source


def _select_row(
    input_path: Path,
    *,
    row_index: int,
    benchmark_id: str | None,
    req_id: str | None,
    test_id: str | None,
) -> tuple[int, dict[str, str], list[str]]:
    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if benchmark_id:
        matches = [
            (index, row) for index, row in enumerate(rows)
            if row.get("id") == benchmark_id
            and (req_id is None or row.get("req_id") == req_id)
            and (test_id is None or row.get("test_id") == test_id)
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one matching row, found {len(matches)}."
            )
        index, row = matches[0]
        return index, row, fieldnames
    if row_index < 0 or row_index >= len(rows):
        raise IndexError(f"row-index {row_index} outside 0..{len(rows)-1}")
    return row_index, rows[row_index], fieldnames


def _write_single_row(
    path: Path, row: dict[str, str], fieldnames: list[str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(sanitize_export_payload(row))


def _install_dependencies(requirements: Path) -> dict[str, Any]:
    command = [
        sys.executable, "-m", "pip", "install", "-r", str(requirements)
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600,
        )
        return {
            "command": subprocess.list2cmdline(command),
            "return_code": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": subprocess.list2cmdline(command),
            "return_code": -1,
            "stdout": (exc.stdout or "")[-4000:],
            "stderr": (exc.stderr or "")[-4000:],
            "failure_reason": "Dependency installation timed out after 600s.",
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve and evaluate one benchmark row reproducibly."
    )
    parser.add_argument("--input", default="data/e2edev_sample.csv")
    parser.add_argument("--row-index", type=int, default=0)
    parser.add_argument("--benchmark-id")
    parser.add_argument("--req-id")
    parser.add_argument("--test-id")
    parser.add_argument("--output-root", default="artifacts/reproducibility")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--install-missing", action="store_true")
    parser.add_argument("--playwright", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_config = EvaluationConfig.from_env()
    input_path = Path(args.input).resolve()
    index, row, fieldnames = _select_row(
        input_path,
        row_index=args.row_index,
        benchmark_id=args.benchmark_id,
        req_id=args.req_id,
        test_id=args.test_id,
    )
    case_name = safe_name(
        f"{row.get('id', 'case')}_req{row.get('req_id', 'NA')}_"
        f"test{row.get('test_id', 'NA')}"
    )
    output_dir = Path(args.output_root).resolve() / case_name
    output_dir.mkdir(parents=True, exist_ok=True)

    dependency_report = check_dependencies(
        coverage_enabled=base_config.enable_coverage,
        playwright_enabled=args.playwright,
    )
    install_report = None
    if dependency_report["missing_required"] and args.install_missing:
        install_report = _install_dependencies(Path("requirements.txt").resolve())
        dependency_report = check_dependencies(
            coverage_enabled=base_config.enable_coverage,
            playwright_enabled=args.playwright,
        )

    resolution = resolve_reference_source(
        row.get("source_project_dir") or row.get("reference_answer", ""),
        base_config.reference_workspace_root,
        allow_network=base_config.reference_network_enabled,
        timeout_seconds=base_config.reference_timeout_seconds,
        expected_patterns=base_config.reference_expected_patterns,
    )
    preflight = {
        "selected_row_index": index,
        "selected_case": {
            key: row.get(key) for key in ("id", "req_id", "test_id")
        },
        "dependencies": dependency_report,
        "dependency_installation": install_report,
        "reference_resolution": resolution,
        "configuration": base_config.as_dict(),
    }
    preflight_path = output_dir / "preflight.json"
    preflight_path.write_text(
        json.dumps(
            sanitize_export_payload(preflight),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(
        sanitize_export_payload(preflight),
        ensure_ascii=False,
        indent=2,
    ))

    if args.check_only:
        return 0 if resolution["resolution_status"] == "success" else 2
    if not dependency_report["ready"]:
        print(
            "Missing required dependencies; rerun with --install-missing "
            "or install requirements.txt.",
            file=sys.stderr,
        )
        return 3
    if resolution["resolution_status"] != "success":
        print("Reference source resolution did not succeed.", file=sys.stderr)
        return 4

    selected_csv = output_dir / "selected_row.csv"
    _write_single_row(selected_csv, row, fieldnames)
    run_config = replace(
        base_config,
        input_file=str(selected_csv),
        output_file=str(output_dir / "evaluation.csv"),
        max_cases=1,
        case_sleep_seconds=0.0,
        coverage_source_dir=resolution["local_path"],
    )
    run_config.write_manifest(output_dir / "requested_config.json")

    # Import only after the dependency preflight succeeds.
    from main import run_batch_evaluation

    run_batch_evaluation(
        run_config.input_file,
        run_config.output_file,
        config=run_config,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
