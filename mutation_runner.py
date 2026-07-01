#!/usr/bin/env python3
"""Small-sample mutation-testing runner for E2E test evaluation.

Design:
- Reads a JSON plan containing selected cases and 3 mutations per case.
- Runs the baseline once per case.
- Copies the source project into a temporary per-mutant workspace.
- Applies a safe literal or regex replacement to the copied source file.
- Runs the exact command provided in the plan.
- Writes detailed JSON and flat CSV reports.

The script intentionally refuses ambiguous mutations: each replacement must match
exactly `expected_matches` times. Use --dry-run first.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from e2e_eval.utils.paths import sanitize_export_payload
from e2e_eval.dynamic.mutation_runner import (
    run_single_mutant,
    verdict_for_status,
)

__all__ = ["run_single_mutant", "verdict_for_status"]


@dataclass
class RunResult:
    case_id: str
    case_label: str
    phase: str
    mutant_id: str
    operator: str
    source_file: str
    status: str
    return_code: int | None
    duration_seconds: float
    stdout_tail: str
    stderr_tail: str
    mutation_detail: str


def tail(text: str, lines: int = 30) -> str:
    return "\n".join(text.splitlines()[-lines:])


def run_command(command: str, cwd: Path, timeout: int, env_extra: dict[str, str] | None) -> tuple[int | None, float, str, str, str]:
    env = os.environ.copy()
    if env_extra:
        env.update({str(k): str(v) for k, v in env_extra.items()})
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        duration = time.perf_counter() - started
        status = "PASS" if proc.returncode == 0 else "FAIL"
        return proc.returncode, duration, proc.stdout, proc.stderr, status
    except subprocess.TimeoutExpired as exc:
        duration = time.perf_counter() - started
        return None, duration, exc.stdout or "", exc.stderr or "", "TIMEOUT"
    except Exception as exc:  # operational error, not a test result
        duration = time.perf_counter() - started
        return None, duration, "", repr(exc), "ERROR"


def apply_mutation(target: Path, mutation: dict[str, Any]) -> str:
    if not target.exists():
        raise FileNotFoundError(f"Mutation target does not exist: {target}")
    text = target.read_text(encoding=mutation.get("encoding", "utf-8"))
    kind = mutation.get("kind", "literal_replace")
    pattern = mutation["pattern"]
    replacement = mutation["replacement"]
    expected = int(mutation.get("expected_matches", 1))

    if kind == "literal_replace":
        found = text.count(pattern)
        if found != expected:
            raise ValueError(
                f"literal_replace expected {expected} occurrence(s), found {found}. "
                f"Target={target}; pattern={pattern!r}"
            )
        mutated = text.replace(pattern, replacement)
    elif kind == "regex_replace":
        flags = re.MULTILINE
        if mutation.get("dotall", False):
            flags |= re.DOTALL
        mutated, found = re.subn(pattern, replacement, text, count=int(mutation.get("count", 0)), flags=flags)
        if found != expected:
            raise ValueError(
                f"regex_replace expected {expected} occurrence(s), found {found}. "
                f"Target={target}; pattern={pattern!r}"
            )
    else:
        raise ValueError(f"Unsupported mutation kind: {kind}")

    target.write_text(mutated, encoding=mutation.get("encoding", "utf-8"))
    return f"{kind}: {pattern!r} -> {replacement!r}"


def validate_plan(plan: dict[str, Any]) -> None:
    if "cases" not in plan or not isinstance(plan["cases"], list):
        raise ValueError("Plan must contain a list field: cases")
    for case in plan["cases"]:
        for key in ("case_id", "label", "project_dir", "baseline_command", "mutations"):
            if key not in case:
                raise ValueError(f"Case missing {key}: {case}")
        if len(case["mutations"]) != 3:
            raise ValueError(f"{case['case_id']} must contain exactly 3 mutations for this small experiment.")
        for m in case["mutations"]:
            for key in ("mutant_id", "operator", "source_file", "pattern", "replacement"):
                if key not in m:
                    raise ValueError(f"Mutation missing {key}: {m}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, help="JSON mutation plan")
    parser.add_argument(
        "--output-dir", default="artifacts/mutation_cli/results"
    )
    parser.add_argument(
        "--workspace-dir", default="artifacts/mutation_cli/workspaces"
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate copy + mutation matching, but do not execute tests")
    parser.add_argument("--keep-workspaces", action="store_true")
    parser.add_argument("--case", action="append", help="Only run a case_id; repeatable")
    args = parser.parse_args()

    plan_path = Path(args.plan).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    validate_plan(plan)

    base_dir = plan_path.parent
    out_dir = Path(args.output_dir).resolve()
    ws_root = Path(args.workspace_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ws_root.mkdir(parents=True, exist_ok=True)

    results: list[RunResult] = []
    selected_ids = set(args.case or [])

    for case in plan["cases"]:
        if selected_ids and case["case_id"] not in selected_ids:
            continue
        project_dir = Path(case["project_dir"])
        if not project_dir.is_absolute():
            project_dir = (base_dir / project_dir).resolve()
        if not project_dir.exists():
            raise FileNotFoundError(f"Project directory for {case['case_id']} not found: {project_dir}")

        timeout = int(case.get("timeout_seconds", 180))
        env_extra = case.get("env")

        print(f"\n=== {case['case_id']}: {case['label']} ===")
        print("Baseline:", case["baseline_command"])
        rc, dur, out, err, status = run_command(case["baseline_command"], project_dir, timeout, env_extra)
        baseline_status = status
        results.append(RunResult(case["case_id"], case["label"], "baseline", "BASELINE", "BASELINE", "", status, rc, dur, tail(out), tail(err), ""))
        print(f"  baseline -> {status} ({dur:.1f}s)")

        # A failing baseline invalidates mutation-score interpretation.
        if baseline_status != "PASS":
            print("  Skipping mutants because the baseline did not pass.")
            continue

        for mutation in case["mutations"]:
            mutant_id = mutation["mutant_id"]
            workspace = ws_root / case["case_id"] / mutant_id
            if workspace.exists():
                shutil.rmtree(workspace)
            shutil.copytree(project_dir, workspace, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "node_modules"))
            target = workspace / mutation["source_file"]

            try:
                detail = apply_mutation(target, mutation)
                if args.dry_run:
                    status, rc, dur, out, err = "DRY_RUN", None, 0.0, "", ""
                else:
                    rc, dur, out, err, raw_status = run_command(case["baseline_command"], workspace, timeout, env_extra)
                    # Mutant test failure means the test killed the injected fault.
                    if raw_status == "FAIL":
                        status = "KILLED"
                    elif raw_status == "PASS":
                        status = "SURVIVED"
                    else:
                        status = (
                            "TIMEOUT" if raw_status == "TIMEOUT"
                            else "EXECUTION_ERROR"
                        )
                print(f"  {mutant_id:<6} {mutation['operator']:<28} -> {status} ({dur:.1f}s)")
                results.append(RunResult(case["case_id"], case["label"], "mutant", mutant_id, mutation["operator"], mutation["source_file"], status, rc, dur, tail(out), tail(err), detail))
            except Exception as exc:
                print(f"  {mutant_id:<6} {mutation['operator']:<28} -> ERROR ({exc})")
                results.append(RunResult(case["case_id"], case["label"], "mutant", mutant_id, mutation["operator"], mutation["source_file"], "INVALID", None, 0.0, "", repr(exc), ""))
            finally:
                if not args.keep_workspaces and workspace.exists():
                    shutil.rmtree(workspace, ignore_errors=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"mutation_details_{stamp}.json"
    csv_path = out_dir / f"mutation_details_{stamp}.csv"
    portable_results = sanitize_export_payload(
        [asdict(result) for result in results]
    )
    json_path.write_text(
        json.dumps(portable_results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(results[0]).keys()) if results else list(RunResult.__dataclass_fields__.keys()))
        writer.writeheader()
        writer.writerows(portable_results)

    print(f"\nDetailed CSV: {csv_path}")
    print(f"Detailed JSON: {json_path}")


if __name__ == "__main__":
    main()
