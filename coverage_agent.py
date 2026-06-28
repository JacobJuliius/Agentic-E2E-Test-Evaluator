"""Deterministic Python branch-coverage agent for generated Behave E2E tests.

The agent runs the generated test in an isolated copy of the supplied source
project.  It deliberately remains separate from the existing BDD step diagnostic:
coverage.py measures executed Python source, while the BDD diagnostic measures
successful Given/When/Then execution.

Many E2EDev reference applications contain only HTML/JavaScript.  coverage.py
cannot measure JavaScript; those cases return a structured failure rather than a
fabricated zero or an exception that aborts the LangGraph pipeline.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from dynamic_agents import (
    ARTIFACT_ROOT,
    REFERENCE_CACHE,
    PreparedWorkspace,
    _find_primary_index,
    _write_behave_project,
)
from e2e_eval.runtime.utils import (
    as_bool as _as_bool,
    normalize_patterns as _patterns,
    safe_name as _safe_name,
    tail as _tail,
)
from reference_resolver import resolve_project_source

logger = logging.getLogger(__name__)


DEFAULT_COVERAGE_TIMEOUT = int(os.getenv("E2E_COVERAGE_TIMEOUT_SECONDS", "120"))
DEFAULT_EXCLUDE_PATTERNS = (
    "*/venv/*",
    "*/.venv/*",
    "*/node_modules/*",
    "*/app/artifacts/*",
    "*/__pycache__/*",
    "*/.pytest_cache/*",
    "*/test_*.py",
    "*/tests/*",
)


def _empty_result(
    coverage_status: str,
    execution_status: str,
    failure_reason: str,
    *,
    command_run: str = "",
    stdout_summary: str = "",
    stderr_summary: str = "",
) -> dict[str, Any]:
    """Return the complete stable output schema for every skip/failure path."""
    return {
        "coverage_status": coverage_status,
        "coverage_execution_status": execution_status,
        "total_line_coverage": None,
        "total_branch_coverage": None,
        "covered_lines": {},
        "missing_lines": {},
        "covered_branches": {},
        "missing_branches": {},
        "source_files_measured": [],
        "coverage_command_run": command_run,
        "coverage_stdout_summary": stdout_summary,
        "coverage_stderr_summary": stderr_summary,
        "coverage_failure_reason": failure_reason,
        "coverage_report_path": "",
        "coverage_artifact_dir": "",
        "reference_resolution": {},
    }


def _coverage_available() -> bool:
    return importlib.util.find_spec("coverage") is not None


def _prepare_workspace(state: dict[str, Any], source_dir: Path) -> PreparedWorkspace:
    """Copy the source project into an artifact-scoped, isolated workspace."""
    case_uid = _safe_name(str(state.get("case_uid") or "coverage_case"))
    workspace = ARTIFACT_ROOT / "workspaces" / case_uid / "python_coverage"
    artifact_dir = ARTIFACT_ROOT / "results" / case_uid / "python_coverage"

    shutil.rmtree(workspace, ignore_errors=True)
    shutil.rmtree(artifact_dir, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    app_dir = workspace / "source_project"
    shutil.copytree(
        source_dir,
        app_dir,
        ignore=shutil.ignore_patterns(
            ".git",
            "venv",
            ".venv",
            "node_modules",
            "artifacts",
            "__pycache__",
            ".pytest_cache",
        ),
    )
    app_index = _find_primary_index(app_dir)
    return PreparedWorkspace(workspace, app_dir, app_index, artifact_dir)


def _command_text(command: list[str]) -> str:
    """Render the exact argv without invoking a shell."""
    return subprocess.list2cmdline(command)


def _classify_execution(return_code: int, stdout: str, stderr: str) -> str:
    """Separate test failures from missing harness and browser dependencies."""
    if return_code == 0:
        return "test_passed"
    combined = f"{stdout}\n{stderr}".lower()
    if "modulenotfounderror" in combined or "no module named" in combined:
        return "dependency_unavailable"
    if (
        "sessionnotcreatedexception" in combined
        or "chromedriver" in combined
        or "cannot find chrome binary" in combined
        or "selenium manager" in combined
    ):
        return "browser_unavailable"
    return "test_failed"


def _percentage(covered: int, total: int) -> float | None:
    return round(100.0 * covered / total, 2) if total else None


def _parse_coverage_report(
    report: dict[str, Any],
    *,
    branch_enabled: bool,
) -> dict[str, Any]:
    """Convert coverage.py JSON into source-preserving structured evidence."""
    files = report.get("files", {})
    if not isinstance(files, dict) or not files:
        raise ValueError("coverage.py produced no measured source files.")

    covered_lines: dict[str, list[int]] = {}
    missing_lines: dict[str, list[int]] = {}
    covered_branches: dict[str, list[list[int]]] = {}
    missing_branches: dict[str, list[list[int]]] = {}

    for filename, payload in sorted(files.items()):
        if not isinstance(payload, dict):
            continue
        covered_lines[str(filename)] = [
            int(line) for line in payload.get("executed_lines", [])
        ]
        missing_lines[str(filename)] = [
            int(line) for line in payload.get("missing_lines", [])
        ]
        if branch_enabled:
            covered_branches[str(filename)] = [
                [int(point) for point in branch]
                for branch in payload.get("executed_branches", [])
            ]
            missing_branches[str(filename)] = [
                [int(point) for point in branch]
                for branch in payload.get("missing_branches", [])
            ]

    totals = report.get("totals", {})
    statements = int(totals.get("num_statements", 0) or 0)
    covered_statement_count = int(totals.get("covered_lines", 0) or 0)
    branch_count = int(totals.get("num_branches", 0) or 0)
    covered_branch_count = int(totals.get("covered_branches", 0) or 0)

    return {
        "total_line_coverage": _percentage(covered_statement_count, statements),
        "total_branch_coverage": (
            _percentage(covered_branch_count, branch_count)
            if branch_enabled else None
        ),
        "covered_lines": covered_lines,
        "missing_lines": missing_lines,
        "covered_branches": covered_branches if branch_enabled else {},
        "missing_branches": missing_branches if branch_enabled else {},
        "source_files_measured": sorted(covered_lines),
    }


def _run_coverage_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Execute generated Behave code under coverage.py without raising outward."""
    if not state.get("syntax_passed", False):
        return _empty_result(
            "skipped", "skipped_syntax_failed", "Syntax/linter gate did not pass."
        )
    if not _as_bool(state.get("enable_coverage"), False):
        return _empty_result(
            "skipped", "skipped_disabled", "Python coverage evaluation is disabled."
        )
    if not _coverage_available():
        return _empty_result(
            "skipped",
            "dependency_missing",
            "coverage.py is not installed in the active Python environment.",
        )

    source_value = str(
        state.get("source_project_dir")
        or state.get("coverage_source_dir")
    ).strip()
    reference_value = str(state.get("reference_answer") or "").strip()
    if not source_value and not reference_value:
        return _empty_result(
            "skipped",
            "source_missing",
            "No coverage_source_dir or reference_answer was configured.",
        )
    resolution = resolve_project_source(
        source_project_dir=source_value,
        reference_url=reference_value,
        workspace_root=state.get("reference_workspace_root") or REFERENCE_CACHE.parent,
        allow_network=_as_bool(
            state.get("reference_network_enabled"), False
        ),
        timeout_seconds=int(state.get("reference_timeout_seconds", 90)),
        expected_patterns=state.get("reference_expected_patterns"),
    )
    state["_coverage_reference_resolution"] = resolution
    if resolution["resolution_status"] != "success":
        result = _empty_result(
            "skipped",
            "source_missing",
            "Reference source could not be resolved: "
            + resolution["failure_reason"],
        )
        result["reference_resolution"] = resolution
        return result
    source_dir = Path(resolution["resolved_source_project_dir"])

    feature_text = str(state.get("excutable_test_test_case") or "")
    test_code = str(state.get("executable_test_code") or "")
    if not feature_text.strip() or not test_code.strip():
        return _empty_result(
            "skipped",
            "input_missing",
            "Generated Behave feature text or step code is missing.",
        )

    branch_enabled = _as_bool(state.get("coverage_branch_enabled"), True)
    include_tests = _as_bool(state.get("coverage_include_tests"), False)
    include_patterns = _patterns(state.get("coverage_include_patterns"))
    exclude_defaults = () if include_tests else DEFAULT_EXCLUDE_PATTERNS
    exclude_patterns = _patterns(
        state.get("coverage_exclude_patterns"), exclude_defaults
    )
    timeout = max(
        1, int(state.get("coverage_timeout_seconds", DEFAULT_COVERAGE_TIMEOUT))
    )

    try:
        prepared = _prepare_workspace(state, source_dir.resolve())
        _write_behave_project(prepared, feature_text, test_code)
        data_path = (prepared.artifact_dir / ".coverage").resolve()
        report_path = (prepared.artifact_dir / "coverage.json").resolve()

        measured_sources = [str(prepared.app_dir.resolve())]
        if include_tests:
            measured_sources.append(
                str((prepared.workspace / "features" / "steps").resolve())
            )

        run_command = [
            sys.executable,
            "-m",
            "coverage",
            "run",
            f"--data-file={data_path}",
            f"--source={','.join(measured_sources)}",
        ]
        if branch_enabled:
            run_command.append("--branch")
        if include_patterns:
            run_command.append(f"--include={','.join(include_patterns)}")
        if exclude_patterns:
            run_command.append(f"--omit={','.join(exclude_patterns)}")
        run_command.extend([
            "-m",
            "behave",
            "features/generated.feature",
            "--no-capture",
        ])

        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "E2E_APP_INDEX_URI": prepared.app_index.resolve().as_uri(),
            "E2E_HEADLESS": (
                "true" if _as_bool(state.get("headless"), True) else "false"
            ),
        }
        started = time.monotonic()
        try:
            run = subprocess.run(
                run_command,
                cwd=prepared.workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                **_empty_result(
                    "failed",
                    "timeout",
                    f"Coverage test execution timed out after {timeout}s.",
                    command_run=_command_text(run_command),
                    stdout_summary=_tail(exc.stdout),
                    stderr_summary=_tail(exc.stderr),
                ),
                "coverage_artifact_dir": str(prepared.artifact_dir),
            }

        execution_status = _classify_execution(
            run.returncode, run.stdout, run.stderr
        )
        report_command = [
            sys.executable,
            "-m",
            "coverage",
            "json",
            f"--data-file={data_path}",
            "-o",
            str(report_path),
            "--pretty-print",
        ]
        if include_patterns:
            report_command.append(f"--include={','.join(include_patterns)}")
        if exclude_patterns:
            report_command.append(f"--omit={','.join(exclude_patterns)}")

        remaining = max(1, timeout - int(time.monotonic() - started))
        try:
            report_run = subprocess.run(
                report_command,
                cwd=prepared.workspace,
                capture_output=True,
                text=True,
                timeout=remaining,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                **_empty_result(
                    "failed",
                    execution_status,
                    "Coverage report generation timed out.",
                    command_run=_command_text(run_command),
                    stdout_summary=_tail(run.stdout),
                    stderr_summary=_tail(run.stderr) + "\n" + _tail(exc.stderr),
                ),
                "coverage_artifact_dir": str(prepared.artifact_dir),
            }

        stdout = _tail(run.stdout + "\n" + report_run.stdout)
        stderr = _tail(run.stderr + "\n" + report_run.stderr)
        if report_run.returncode != 0 or not report_path.exists():
            reason = (
                "coverage.py could not produce a report. The configured source may "
                "contain no Python code executed by the E2E test."
            )
            return {
                **_empty_result(
                    "failed",
                    execution_status,
                    reason,
                    command_run=_command_text(run_command),
                    stdout_summary=stdout,
                    stderr_summary=stderr,
                ),
                "coverage_artifact_dir": str(prepared.artifact_dir),
            }

        parsed = _parse_coverage_report(
            json.loads(report_path.read_text(encoding="utf-8")),
            branch_enabled=branch_enabled,
        )
        return {
            "coverage_status": "success",
            "coverage_execution_status": execution_status,
            **parsed,
            "coverage_command_run": _command_text(run_command),
            "coverage_stdout_summary": stdout,
            "coverage_stderr_summary": stderr,
            "coverage_failure_reason": (
                "" if run.returncode == 0
                else f"Generated E2E test exited with status {run.returncode}; "
                     "partial coverage evidence was retained."
            ),
            "coverage_report_path": str(report_path),
            "coverage_artifact_dir": str(prepared.artifact_dir),
        }
    except Exception as exc:
        # Dynamic evaluation must never terminate the complete batch or graph.
        return _empty_result(
            "failed",
            "harness_error",
            f"Coverage harness error: {exc!r}",
        )


def coverage_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Return collision-safe graph fields plus the requested standalone schema.

    The graph already uses ``execution_status`` for its baseline execution node.
    Therefore the coverage-specific result is exposed both as prefixed top-level
    fields and as ``coverage_result`` with the exact public field names.
    """
    working_state = dict(state)
    result = _run_coverage_agent(working_state)
    result["reference_resolution"] = working_state.get(
        "_coverage_reference_resolution",
        state.get(
            "reference_resolution",
            result.get("reference_resolution", {}),
        ),
    )
    metadata = result["reference_resolution"]
    result.update({
        "source_origin": metadata.get("source_origin", state.get("source_origin", "")),
        "input_source_project_dir": metadata.get(
            "input_source_project_dir", state.get("source_project_dir", "")
        ),
        "resolved_source_project_dir": metadata.get(
            "resolved_source_project_dir",
            state.get("resolved_source_project_dir", ""),
        ),
        "resolved_entrypoint": metadata.get(
            "resolved_entrypoint", state.get("resolved_entrypoint", "")
        ),
        "local_override_diagnostic": metadata.get(
            "local_override_diagnostic",
            state.get("local_override_diagnostic", ""),
        ),
    })
    result["coverage_result"] = {
        "coverage_status": result["coverage_status"],
        "execution_status": result["coverage_execution_status"],
        "total_line_coverage": result["total_line_coverage"],
        "total_branch_coverage": result["total_branch_coverage"],
        "covered_lines": result["covered_lines"],
        "missing_lines": result["missing_lines"],
        "covered_branches": result["covered_branches"],
        "missing_branches": result["missing_branches"],
        "source_files_measured": result["source_files_measured"],
        "command_run": result["coverage_command_run"],
        "stdout_summary": result["coverage_stdout_summary"],
        "stderr_summary": result["coverage_stderr_summary"],
        "failure_reason": result["coverage_failure_reason"],
        "reference_resolution": result["reference_resolution"],
    }
    logger.info(
        "Python coverage case=%s status=%s execution=%s files=%d reason=%s",
        state.get("case_uid", "unknown"),
        result["coverage_status"],
        result["coverage_execution_status"],
        len(result["source_files_measured"]),
        result["coverage_failure_reason"] or "none",
    )
    return result
