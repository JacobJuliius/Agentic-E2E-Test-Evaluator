"""Deterministic branch-coverage collection and parsing utilities.

This module contains no LLM calls. It executes the already validated generated
Behave test under coverage.py and converts its JSON report into stable evidence.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

from dynamic_agents import (
    ARTIFACT_ROOT,
    PreparedWorkspace,
    _new_workspace,
    _run_behave,
    _write_behave_project,
)
from e2e_eval.runtime.utils import (
    as_bool,
    normalize_patterns,
    safe_name,
    tail,
)
from e2e_eval.utils.paths import project_root, sanitize_export_payload


DEFAULT_COVERAGE_TIMEOUT = int(
    os.getenv("E2E_COVERAGE_TIMEOUT_SECONDS", "120")
)
DEFAULT_EXCLUDE_PATTERNS = (
    "*/venv/*",
    "*/.venv/*",
    "*/node_modules/*",
    "*/source_project/artifacts/*",
    "*/__pycache__/*",
    "*/.pytest_cache/*",
    "*/test_*.py",
    "*/tests/*",
)


def coverage_available() -> bool:
    """Return whether coverage.py can be invoked by the active interpreter."""
    return importlib.util.find_spec("coverage") is not None


def empty_branch_coverage_result(
    status: str,
    execution_status: str,
    rationale: str,
    *,
    command: str = "",
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    timed_out: bool = False,
    artifact_dir: str = "",
    workspace: str = "",
    report_path: str = "",
    data_path: str = "",
    coverage_adapter: str = "",
    source_language: str = "unknown",
    instrumentation_status: str = "NOT_ATTEMPTED",
) -> dict[str, Any]:
    """Build the stable public result for unavailable and failed collection."""
    return {
        "coverage_status": status,
        "execution_status": execution_status,
        "coverage_adapter": coverage_adapter,
        "source_language": source_language,
        "instrumentation_status": instrumentation_status,
        "branch_coverage_percent": None,
        "covered_branches": 0,
        "total_branches": 0,
        "uncovered_branches": [],
        "requirement_relevant_uncovered_branches": [],
        "branch_coverage_score": None,
        "rationale": rationale,
        "logs": {
            "command": command,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timed_out": timed_out,
        },
        "artifact_paths": {
            "workspace": workspace,
            "artifact_dir": artifact_dir,
            "coverage_data": data_path,
            "coverage_report": report_path,
        },
        "line_coverage_percent": None,
        "covered_lines": {},
        "missing_lines": {},
        "covered_branch_locations": {},
        "source_files_measured": [],
        "failure_reason": rationale,
    }


def _source_project_path(state: dict[str, Any]) -> Path | None:
    workspace = Path(str(state.get("workspace_dir") or ""))
    workspace_source = workspace / "source_project"
    if state.get("workspace_dir") and workspace_source.is_dir():
        return workspace_source.resolve()
    for field in ("resolved_source_project_dir", "source_project_dir"):
        value = str(state.get(field) or "").strip()
        if value and Path(value).is_dir():
            return Path(value).resolve()
    return None


def detect_source_language(state: dict[str, Any]) -> str:
    """Select a coverage adapter from executable source files."""
    source = _source_project_path(state)
    if source is None:
        return "unknown"
    has_python = False
    has_javascript = False
    has_html = False
    ignored = {
        ".git", ".venv", "artifacts", "node_modules", "tests", "venv"
    }
    for path in source.rglob("*"):
        relative_parts = path.relative_to(source).parts
        if not path.is_file() or any(
            part in ignored for part in relative_parts
        ):
            continue
        suffix = path.suffix.lower()
        has_python = has_python or suffix == ".py"
        has_javascript = has_javascript or suffix in {".js", ".mjs", ".cjs"}
        has_html = has_html or suffix in {".html", ".htm"}
    if has_python:
        return "python"
    if has_javascript or has_html:
        return "javascript"
    return "unknown"


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if parsed < 0:
        raise ValueError(f"{name} cannot be negative.")
    return parsed


def _line_list(value: Any, name: str) -> list[int]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list.")
    return [_integer(item, name) for item in value]


def _branch_list(value: Any, name: str) -> list[list[int]]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list.")
    branches: list[list[int]] = []
    for branch in value:
        if (
            not isinstance(branch, (list, tuple))
            or len(branch) != 2
            or isinstance(branch[0], bool)
            or isinstance(branch[1], bool)
        ):
            raise ValueError(f"{name} entries must be two-integer locations.")
        try:
            branches.append([int(branch[0]), int(branch[1])])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{name} entries must be two-integer locations."
            ) from exc
    return branches


def _source_line(
    workspace: Path | None, filename: str, line_number: int
) -> str:
    if workspace is None or line_number <= 0:
        return ""
    candidate = Path(filename)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(workspace.resolve())
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[line_number - 1].strip() if line_number <= len(lines) else ""
    except (OSError, ValueError):
        return ""


def parse_coverage_payload(
    report: dict[str, Any],
    *,
    branch_enabled: bool = True,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Parse coverage.py JSON into counts and source-preserving branch records.

    Raises ``ValueError`` for malformed content. The file-level wrapper below
    converts those errors into a stable result so callers never need to catch.
    """
    if not isinstance(report, dict):
        raise ValueError("Coverage report root must be an object.")
    files = report.get("files")
    totals = report.get("totals")
    if not isinstance(files, dict) or not files:
        raise ValueError("Coverage report contains no measured source files.")
    if not isinstance(totals, dict):
        raise ValueError("Coverage report totals are missing or malformed.")

    statements = _integer(totals.get("num_statements"), "num_statements")
    covered_line_count = _integer(
        totals.get("covered_lines"), "covered_lines"
    )
    total_branches = (
        _integer(totals.get("num_branches"), "num_branches")
        if branch_enabled
        else 0
    )
    covered_branch_count = (
        _integer(totals.get("covered_branches"), "covered_branches")
        if branch_enabled
        else 0
    )
    if covered_line_count > statements:
        raise ValueError("covered_lines exceeds num_statements.")
    if covered_branch_count > total_branches:
        raise ValueError("covered_branches exceeds num_branches.")

    covered_lines: dict[str, list[int]] = {}
    missing_lines: dict[str, list[int]] = {}
    covered_locations: dict[str, list[list[int]]] = {}
    uncovered: list[dict[str, Any]] = []

    for raw_filename, payload in sorted(files.items(), key=lambda item: str(item[0])):
        filename = str(raw_filename)
        if not isinstance(payload, dict):
            raise ValueError(f"Coverage entry for {filename} is malformed.")
        covered_lines[filename] = _line_list(
            payload.get("executed_lines", []),
            f"{filename}.executed_lines",
        )
        missing_lines[filename] = _line_list(
            payload.get("missing_lines", []),
            f"{filename}.missing_lines",
        )
        if not branch_enabled:
            continue
        covered_locations[filename] = _branch_list(
            payload.get("executed_branches", []),
            f"{filename}.executed_branches",
        )
        for source_line, destination_line in _branch_list(
            payload.get("missing_branches", []),
            f"{filename}.missing_branches",
        ):
            branch_id = f"{filename}:{source_line}->{destination_line}"
            uncovered.append({
                "branch_id": branch_id,
                "file": filename,
                "from_line": source_line,
                "to_line": destination_line,
                "from_source": _source_line(
                    workspace, filename, source_line
                ),
                "to_source": _source_line(
                    workspace, filename, destination_line
                ),
            })

    reported_missing = total_branches - covered_branch_count
    if branch_enabled and len(uncovered) != reported_missing:
        raise ValueError(
            "Branch totals do not match file-level missing branch evidence: "
            f"totals report {reported_missing}, files report {len(uncovered)}."
        )

    percentage = (
        round(100.0 * covered_branch_count / total_branches, 2)
        if branch_enabled and total_branches
        else None
    )
    line_percentage = (
        round(100.0 * covered_line_count / statements, 2)
        if statements
        else None
    )
    return {
        "branch_coverage_percent": percentage,
        "covered_branches": covered_branch_count,
        "total_branches": total_branches,
        "uncovered_branches": uncovered,
        "line_coverage_percent": line_percentage,
        "covered_lines": covered_lines,
        "missing_lines": missing_lines,
        "covered_branch_locations": covered_locations,
        "source_files_measured": sorted(covered_lines),
    }


def parse_coverage_report(
    report_path: str | Path,
    *,
    branch_enabled: bool = True,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Read and parse a report, returning structured malformed/unavailable data."""
    path = Path(report_path)
    if not path.is_file():
        return empty_branch_coverage_result(
            "UNAVAILABLE",
            "REPORT_UNAVAILABLE",
            f"Coverage report does not exist: {path}",
            report_path=str(path),
            workspace=str(workspace or ""),
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            "coverage_status": "SUCCESS",
            **parse_coverage_payload(
                payload,
                branch_enabled=branch_enabled,
                workspace=workspace,
            ),
        }
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return empty_branch_coverage_result(
            "MALFORMED",
            "REPORT_MALFORMED",
            f"Malformed coverage report: {exc}",
            report_path=str(path),
            workspace=str(workspace or ""),
        )


def _command_text(command: Iterable[str]) -> str:
    return subprocess.list2cmdline(list(command))


def _prepare_or_reuse_workspace(
    state: dict[str, Any],
) -> tuple[PreparedWorkspace, bool]:
    """Reuse the baseline isolated workspace, falling back to a fresh copy."""
    case_uid = safe_name(str(state.get("case_uid") or "coverage_case"))
    baseline = Path(str(state.get("workspace_dir") or ""))
    baseline_app = baseline / "source_project"
    baseline_index = baseline_app / "index.html"
    generated_feature = baseline / "features" / "generated.feature"
    generated_steps = baseline / "features" / "steps" / "generated_steps.py"
    artifact_dir = ARTIFACT_ROOT / "results" / case_uid / "branch_coverage"

    if (
        state.get("execution_status") == "PASSED"
        and baseline.is_dir()
        and baseline_app.is_dir()
        and baseline_index.is_file()
        and generated_feature.is_file()
        and generated_steps.is_file()
    ):
        shutil.rmtree(artifact_dir, ignore_errors=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        return PreparedWorkspace(
            baseline,
            baseline_app,
            baseline_index,
            artifact_dir,
            dict(state.get("reference_resolution") or {}),
        ), True

    prepared = _new_workspace(state, "branch_coverage")
    _write_behave_project(
        prepared,
        str(state["excutable_test_test_case"]),
        str(state["executable_test_code"]),
    )
    return prepared, False


def _javascript_line(app_dir: Path, filename: str, line: int) -> str:
    if line <= 0:
        return ""
    try:
        candidate = (app_dir / filename).resolve()
        candidate.relative_to(app_dir.resolve())
        lines = candidate.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
        return lines[line - 1].strip() if line <= len(lines) else ""
    except (OSError, ValueError):
        return ""


def parse_istanbul_coverage_payload(
    report: dict[str, Any],
    *,
    app_dir: Path,
) -> dict[str, Any]:
    """Parse executable Istanbul branch counters without estimating branches."""
    if not isinstance(report, dict) or not report:
        raise ValueError("Istanbul coverage report is empty or malformed.")

    total_branches = 0
    covered_branches = 0
    total_statements = 0
    covered_statements = 0
    uncovered: list[dict[str, Any]] = []
    covered_locations: dict[str, list[list[int]]] = {}
    source_files: list[str] = []

    for raw_filename, file_data in sorted(report.items()):
        if not isinstance(file_data, dict):
            raise ValueError(f"Istanbul entry for {raw_filename} is malformed.")
        filename = str(raw_filename).replace("\\", "/")
        branch_map = file_data.get("branchMap", {})
        branch_counts = file_data.get("b", {})
        statement_counts = file_data.get("s", {})
        if not isinstance(branch_map, dict) or not isinstance(branch_counts, dict):
            raise ValueError(f"Istanbul branch data for {filename} is malformed.")
        if not isinstance(statement_counts, dict):
            statement_counts = {}
        source_files.append(filename)
        covered_locations[filename] = []
        total_statements += len(statement_counts)
        covered_statements += sum(
            1 for count in statement_counts.values() if int(count or 0) > 0
        )

        for branch_id, metadata in sorted(branch_map.items()):
            if not isinstance(metadata, dict):
                raise ValueError(
                    f"Istanbul branch metadata {filename}:{branch_id} is malformed."
                )
            locations = metadata.get("locations", [])
            counts = branch_counts.get(str(branch_id))
            if not isinstance(locations, list) or not isinstance(counts, list):
                raise ValueError(
                    f"Istanbul counters {filename}:{branch_id} are malformed."
                )
            if len(locations) != len(counts):
                raise ValueError(
                    f"Istanbul branch locations/counters differ for "
                    f"{filename}:{branch_id}."
                )
            for path_index, (location, count) in enumerate(
                zip(locations, counts)
            ):
                if not isinstance(location, dict):
                    raise ValueError(
                        f"Istanbul location {filename}:{branch_id} is malformed."
                    )
                start = location.get("start") or {}
                end = location.get("end") or {}
                start_line = int(start.get("line", 0) or 0)
                end_line = int(end.get("line", start_line) or start_line)
                execution_count = int(count or 0)
                total_branches += 1
                if execution_count > 0:
                    covered_branches += 1
                    covered_locations[filename].append(
                        [start_line, end_line]
                    )
                    continue
                record_id = f"{filename}:{branch_id}:{path_index}"
                uncovered.append({
                    "branch_id": record_id,
                    "file": filename,
                    "branch_kind": str(metadata.get("type", "unknown")),
                    "branch_path_index": path_index,
                    "from_line": start_line,
                    "to_line": end_line,
                    "from_source": _javascript_line(
                        app_dir, filename, start_line
                    ),
                    "to_source": _javascript_line(
                        app_dir, filename, end_line
                    ),
                    "execution_count": execution_count,
                })

    if not source_files:
        raise ValueError("Istanbul report contains no measured source files.")
    percentage = (
        round(100.0 * covered_branches / total_branches, 2)
        if total_branches
        else None
    )
    return {
        "branch_coverage_percent": percentage,
        "covered_branches": covered_branches,
        "total_branches": total_branches,
        "uncovered_branches": uncovered,
        "line_coverage_percent": (
            round(100.0 * covered_statements / total_statements, 2)
            if total_statements else None
        ),
        "covered_lines": {},
        "missing_lines": {},
        "covered_branch_locations": covered_locations,
        "source_files_measured": source_files,
    }


def _collect_javascript_branch_coverage(
    state: dict[str, Any],
) -> dict[str, Any]:
    """Instrument isolated JavaScript and collect browser-side Istanbul data."""
    baseline_status = str(state.get("execution_status", "NOT_RUN"))
    adapter = "selenium_istanbul"
    if baseline_status != "PASSED":
        return empty_branch_coverage_result(
            "UNAVAILABLE",
            baseline_status,
            "JavaScript branch coverage requires a passing executable baseline.",
            coverage_adapter=adapter,
            source_language="javascript",
        )
    if not as_bool(state.get("enable_coverage"), False):
        return empty_branch_coverage_result(
            "SKIPPED_DISABLED",
            baseline_status,
            "Branch coverage evaluation is disabled.",
            coverage_adapter=adapter,
            source_language="javascript",
        )
    if not as_bool(state.get("coverage_branch_enabled"), True):
        return empty_branch_coverage_result(
            "SKIPPED_DISABLED",
            baseline_status,
            "Branch measurement is disabled by configuration.",
            coverage_adapter=adapter,
            source_language="javascript",
        )

    node = shutil.which("node")
    instrument_script = (
        project_root() / "scripts" / "instrument_javascript_coverage.js"
    )
    if not node or not instrument_script.is_file():
        return empty_branch_coverage_result(
            "UNAVAILABLE",
            "INSTRUMENTATION_DEPENDENCY_MISSING",
            "JavaScript coverage requires Node.js and the Istanbul "
            "instrumentation helper.",
            coverage_adapter=adapter,
            source_language="javascript",
            instrumentation_status="DEPENDENCY_MISSING",
        )

    timeout = max(
        1, int(state.get("coverage_timeout_seconds", DEFAULT_COVERAGE_TIMEOUT))
    )
    try:
        prepared, reused = _prepare_or_reuse_workspace(state)
        artifact_dir = prepared.artifact_dir.resolve()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        raw_path = artifact_dir / "istanbul_coverage.json"
        manifest_path = artifact_dir / "instrumentation_manifest.json"
        command = [
            node,
            str(instrument_script),
            str(prepared.app_dir.resolve()),
            str(manifest_path),
        ]
        try:
            instrumentation = subprocess.run(
                command,
                cwd=project_root(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return empty_branch_coverage_result(
                "UNAVAILABLE",
                "INSTRUMENTATION_TIMEOUT",
                f"JavaScript instrumentation timed out after {timeout}s.",
                command=_command_text(command),
                stdout=tail(exc.stdout),
                stderr=tail(exc.stderr),
                timed_out=True,
                artifact_dir=str(artifact_dir),
                workspace=str(prepared.workspace.resolve()),
                report_path=str(raw_path),
                data_path=str(manifest_path),
                coverage_adapter=adapter,
                source_language="javascript",
                instrumentation_status="TIMEOUT",
            )
        if instrumentation.returncode != 0:
            return empty_branch_coverage_result(
                "UNAVAILABLE",
                "INSTRUMENTATION_FAILED",
                "Istanbul could not instrument the isolated JavaScript source.",
                command=_command_text(command),
                stdout=tail(instrumentation.stdout),
                stderr=tail(instrumentation.stderr),
                exit_code=int(instrumentation.returncode),
                artifact_dir=str(artifact_dir),
                workspace=str(prepared.workspace.resolve()),
                report_path=str(raw_path),
                data_path=str(manifest_path),
                coverage_adapter=adapter,
                source_language="javascript",
                instrumentation_status="FAILED",
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_path.write_text(
            json.dumps(
                sanitize_export_payload(manifest),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if not manifest.get("files"):
            return empty_branch_coverage_result(
                "UNAVAILABLE",
                "NO_INSTRUMENTABLE_JAVASCRIPT",
                "No external JavaScript files were available for instrumentation.",
                command=_command_text(command),
                artifact_dir=str(artifact_dir),
                workspace=str(prepared.workspace.resolve()),
                report_path=str(raw_path),
                data_path=str(manifest_path),
                coverage_adapter=adapter,
                source_language="javascript",
                instrumentation_status="NO_FILES",
            )

        run_state = {
            **state,
            "js_coverage_output_path": str(raw_path),
        }
        run = _run_behave(prepared, run_state, timeout=timeout)
        if run["execution_status"] != "PASSED":
            return empty_branch_coverage_result(
                "UNAVAILABLE",
                str(run["execution_status"]),
                "Instrumented Selenium execution did not pass; no defensible "
                "JavaScript branch percentage is reported.",
                command=str(run.get("execution_command", "")),
                stdout=str(run.get("execution_stdout_tail", "")),
                stderr=str(run.get("execution_stderr_tail", "")),
                exit_code=int(run.get("execution_return_code", -1)),
                timed_out=run["execution_status"] == "TIMEOUT",
                artifact_dir=str(artifact_dir),
                workspace=str(prepared.workspace.resolve()),
                report_path=str(raw_path),
                data_path=str(manifest_path),
                coverage_adapter=adapter,
                source_language="javascript",
                instrumentation_status="EXECUTION_FAILED",
            )
        if not raw_path.is_file():
            return empty_branch_coverage_result(
                "UNAVAILABLE",
                "BROWSER_INSTRUMENTATION_UNAVAILABLE",
                "The Selenium browser session produced no window.__coverage__ "
                "payload. The driver may not be Chrome-compatible, or the "
                "scenario may not have loaded instrumented JavaScript.",
                command=str(run.get("execution_command", "")),
                stdout=str(run.get("execution_stdout_tail", "")),
                stderr=str(run.get("execution_stderr_tail", "")),
                exit_code=int(run.get("execution_return_code", 0)),
                artifact_dir=str(artifact_dir),
                workspace=str(prepared.workspace.resolve()),
                report_path=str(raw_path),
                data_path=str(manifest_path),
                coverage_adapter=adapter,
                source_language="javascript",
                instrumentation_status="NO_BROWSER_DATA",
            )

        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        parsed = parse_istanbul_coverage_payload(
            payload, app_dir=(artifact_dir / "original_sources").resolve()
        )
        raw_path.write_text(
            json.dumps(
                sanitize_export_payload(payload),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        parsed.update({
            "coverage_status": (
                "SUCCESS"
                if parsed["branch_coverage_percent"] is not None
                else "UNAVAILABLE"
            ),
            "execution_status": "PASSED",
            "coverage_adapter": adapter,
            "source_language": "javascript",
            "instrumentation_status": "COLLECTED",
            "requirement_relevant_uncovered_branches": [],
            "branch_coverage_score": parsed["branch_coverage_percent"],
            "rationale": (
                "Executable browser-side branch counters were collected from "
                f"Istanbul-instrumented JavaScript in "
                f"{'the reused baseline' if reused else 'a fresh isolated'} "
                "workspace."
            ),
            "logs": {
                "command": str(run.get("execution_command", "")),
                "instrumentation_command": _command_text(command),
                "stdout": str(run.get("execution_stdout_tail", "")),
                "stderr": str(run.get("execution_stderr_tail", "")),
                "exit_code": int(run.get("execution_return_code", 0)),
                "timed_out": False,
            },
            "artifact_paths": {
                "workspace": str(prepared.workspace.resolve()),
                "artifact_dir": str(artifact_dir),
                "coverage_data": str(raw_path),
                "coverage_report": str(raw_path),
                "raw_coverage": str(raw_path),
                "instrumentation_manifest": str(manifest_path),
                "original_sources": str(
                    (artifact_dir / "original_sources").resolve()
                ),
                "behave_report": str(run.get("behave_report_path", "")),
            },
            "failure_reason": (
                ""
                if parsed["branch_coverage_percent"] is not None
                else "Istanbul reported no branch counters."
            ),
        })
        return parsed
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return empty_branch_coverage_result(
            "UNAVAILABLE",
            "INSTRUMENTATION_REPORT_ERROR",
            f"JavaScript coverage artifact could not be parsed: {exc}",
            coverage_adapter=adapter,
            source_language="javascript",
            instrumentation_status="REPORT_ERROR",
        )
    except Exception as exc:
        return empty_branch_coverage_result(
            "FAILED",
            "HARNESS_ERROR",
            f"JavaScript coverage harness error: {exc!r}",
            coverage_adapter=adapter,
            source_language="javascript",
            instrumentation_status="HARNESS_ERROR",
        )


def _collect_python_branch_coverage(state: dict[str, Any]) -> dict[str, Any]:
    """Run a validated E2E test under coverage.py and return raw evidence."""
    baseline_status = str(state.get("execution_status", "NOT_RUN"))
    if baseline_status != "PASSED":
        return empty_branch_coverage_result(
            "UNAVAILABLE",
            baseline_status,
            "Branch coverage is unavailable because executable validation "
            f"did not pass (status={baseline_status}).",
        )
    if not as_bool(state.get("enable_coverage"), False):
        return empty_branch_coverage_result(
            "SKIPPED_DISABLED",
            baseline_status,
            "Branch coverage evaluation is disabled.",
        )
    if not coverage_available():
        return empty_branch_coverage_result(
            "UNAVAILABLE",
            "DEPENDENCY_MISSING",
            "coverage.py is not installed in the active Python environment.",
        )

    branch_enabled = as_bool(state.get("coverage_branch_enabled"), True)
    if not branch_enabled:
        return empty_branch_coverage_result(
            "SKIPPED_DISABLED",
            baseline_status,
            "Branch measurement is disabled by configuration.",
        )

    timeout = max(
        1, int(state.get("coverage_timeout_seconds", DEFAULT_COVERAGE_TIMEOUT))
    )
    include_tests = as_bool(state.get("coverage_include_tests"), False)
    include_patterns = normalize_patterns(
        state.get("coverage_include_patterns")
    )
    exclude_defaults = () if include_tests else DEFAULT_EXCLUDE_PATTERNS
    exclude_patterns = normalize_patterns(
        state.get("coverage_exclude_patterns"), exclude_defaults
    )

    try:
        prepared, reused = _prepare_or_reuse_workspace(state)
        artifact_dir = prepared.artifact_dir.resolve()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        data_path = artifact_dir / ".coverage"
        report_path = artifact_dir / "coverage.json"
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
            "--branch",
        ]
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
                "true" if as_bool(state.get("headless"), True) else "false"
            ),
        }
        started = time.monotonic()
        try:
            run = subprocess.run(
                run_command,
                cwd=prepared.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return empty_branch_coverage_result(
                "TIMEOUT",
                "TIMEOUT",
                f"Coverage execution timed out after {timeout}s.",
                command=_command_text(run_command),
                stdout=tail(exc.stdout),
                stderr=tail(exc.stderr),
                exit_code=-1,
                timed_out=True,
                artifact_dir=str(artifact_dir),
                workspace=str(prepared.workspace.resolve()),
                report_path=str(report_path),
                data_path=str(data_path),
            )

        if run.returncode != 0:
            return empty_branch_coverage_result(
                "FAILED",
                "COVERAGE_EXECUTION_FAILED",
                "The test passed executable validation but failed while "
                f"instrumented by coverage.py (exit code {run.returncode}).",
                command=_command_text(run_command),
                stdout=tail(run.stdout),
                stderr=tail(run.stderr),
                exit_code=int(run.returncode),
                artifact_dir=str(artifact_dir),
                workspace=str(prepared.workspace.resolve()),
                report_path=str(report_path),
                data_path=str(data_path),
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
                encoding="utf-8",
                errors="replace",
                timeout=remaining,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            return empty_branch_coverage_result(
                "TIMEOUT",
                "REPORT_TIMEOUT",
                "Coverage report generation timed out.",
                command=_command_text(run_command),
                stdout=tail(run.stdout),
                stderr=tail(run.stderr) + "\n" + tail(exc.stderr),
                exit_code=-1,
                timed_out=True,
                artifact_dir=str(artifact_dir),
                workspace=str(prepared.workspace.resolve()),
                report_path=str(report_path),
                data_path=str(data_path),
            )

        combined_stdout = tail(run.stdout + "\n" + report_run.stdout)
        combined_stderr = tail(run.stderr + "\n" + report_run.stderr)
        if report_run.returncode != 0:
            return empty_branch_coverage_result(
                "UNAVAILABLE",
                "REPORT_UNAVAILABLE",
                "coverage.py could not produce a report; the target may "
                "contain no Python source executed in the test process.",
                command=_command_text(run_command),
                stdout=combined_stdout,
                stderr=combined_stderr,
                exit_code=int(report_run.returncode),
                artifact_dir=str(artifact_dir),
                workspace=str(prepared.workspace.resolve()),
                report_path=str(report_path),
                data_path=str(data_path),
            )

        parsed = parse_coverage_report(
            report_path,
            branch_enabled=True,
            workspace=prepared.workspace.resolve(),
        )
        try:
            raw_report = json.loads(report_path.read_text(encoding="utf-8"))
            report_path.write_text(
                json.dumps(
                    sanitize_export_payload(raw_report),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except (OSError, json.JSONDecodeError):
            # Parsing already produced the authoritative structured status.
            pass
        parsed_ok = parsed.get("coverage_status") == "SUCCESS"
        parser_rationale = str(parsed.get("rationale", ""))
        parsed.update({
            "execution_status": baseline_status,
            "requirement_relevant_uncovered_branches": [],
            "branch_coverage_score": parsed.get(
                "branch_coverage_percent"
            ),
            "rationale": (
                "Deterministic coverage.py branch evidence collected from "
                f"{'the reused baseline' if reused else 'a fresh isolated'} "
                "workspace; business relevance has not yet been assessed."
                if parsed_ok else parser_rationale
            ),
            "logs": {
                "command": _command_text(run_command),
                "stdout": combined_stdout,
                "stderr": combined_stderr,
                "exit_code": int(run.returncode),
                "timed_out": False,
            },
            "artifact_paths": {
                "workspace": str(prepared.workspace.resolve()),
                "artifact_dir": str(artifact_dir),
                "coverage_data": str(data_path),
                "coverage_report": str(report_path),
            },
            "failure_reason": (
                parsed.get("failure_reason", "")
                if not parsed_ok
                else ""
            ),
        })
        return parsed
    except Exception as exc:
        # Dynamic evidence must never terminate the graph or batch.
        return empty_branch_coverage_result(
            "FAILED",
            "HARNESS_ERROR",
            f"Branch coverage harness error: {exc!r}",
        )


def collect_branch_coverage(state: dict[str, Any]) -> dict[str, Any]:
    """Select a deterministic executable coverage adapter by source type."""
    language = detect_source_language(state)
    if language == "javascript":
        return _collect_javascript_branch_coverage(state)
    if language == "python":
        result = _collect_python_branch_coverage(state)
        result.update({
            "coverage_adapter": "python_coverage_py",
            "source_language": "python",
            "instrumentation_status": (
                "COLLECTED"
                if result.get("coverage_status") == "SUCCESS"
                else str(result.get("execution_status", "UNAVAILABLE"))
            ),
        })
        return result
    return empty_branch_coverage_result(
        "UNAVAILABLE",
        "SOURCE_LANGUAGE_UNSUPPORTED",
        "No Python or JavaScript application source was detected.",
        coverage_adapter="none",
        source_language=language,
        instrumentation_status="NOT_SUPPORTED",
    )
