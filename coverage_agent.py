"""Backward-compatible graph adapter for deterministic branch coverage."""
from __future__ import annotations

import logging
from typing import Any

from e2e_eval.dynamic.branch_coverage import (
    collect_branch_coverage,
    coverage_available,
    empty_branch_coverage_result,
    parse_coverage_payload,
    parse_coverage_report,
)

logger = logging.getLogger(__name__)

# Compatibility aliases retained for callers of the earlier prototype.
_coverage_available = coverage_available
_parse_coverage_report = parse_coverage_payload
_empty_result = empty_branch_coverage_result


def coverage_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Collect tool-derived evidence and retain historical state fields."""
    branch_result = collect_branch_coverage(state)
    artifacts = branch_result.get("artifact_paths", {})
    logs = branch_result.get("logs", {})
    uncovered = branch_result.get("uncovered_branches", [])
    missing_by_file: dict[str, list[list[int]]] = {}
    for branch in uncovered:
        filename = str(branch.get("file", ""))
        missing_by_file.setdefault(filename, []).append([
            int(branch.get("from_line", 0)),
            int(branch.get("to_line", 0)),
        ])

    status_map = {
        "SUCCESS": "success",
        "SKIPPED_DISABLED": "skipped",
        "UNAVAILABLE": "unavailable",
        "MALFORMED": "failed",
        "TIMEOUT": "failed",
        "FAILED": "failed",
    }
    result = {
        "coverage_status": status_map.get(
            str(branch_result.get("coverage_status")), "failed"
        ),
        "coverage_adapter": branch_result.get("coverage_adapter", ""),
        "coverage_source_language": branch_result.get(
            "source_language", "unknown"
        ),
        "coverage_instrumentation_status": branch_result.get(
            "instrumentation_status", "NOT_ATTEMPTED"
        ),
        "coverage_execution_status": branch_result.get(
            "execution_status", "NOT_RUN"
        ),
        "total_line_coverage": branch_result.get("line_coverage_percent"),
        "total_branch_coverage": branch_result.get(
            "branch_coverage_percent"
        ),
        "covered_lines": branch_result.get("covered_lines", {}),
        "missing_lines": branch_result.get("missing_lines", {}),
        # Historical fields remain location maps. Exact count fields live in
        # branch_coverage_result, avoiding a collision with graph consumers.
        "covered_branches": branch_result.get(
            "covered_branch_locations", {}
        ),
        "missing_branches": missing_by_file,
        "source_files_measured": branch_result.get(
            "source_files_measured", []
        ),
        "coverage_command_run": logs.get("command", ""),
        "coverage_stdout_summary": logs.get("stdout", ""),
        "coverage_stderr_summary": logs.get("stderr", ""),
        "coverage_return_code": logs.get("exit_code"),
        "coverage_timed_out": bool(logs.get("timed_out", False)),
        "coverage_failure_reason": branch_result.get("failure_reason", ""),
        "coverage_report_path": artifacts.get("coverage_report", ""),
        "coverage_data_path": artifacts.get("coverage_data", ""),
        "coverage_artifact_dir": artifacts.get("artifact_dir", ""),
        "coverage_workspace_dir": artifacts.get("workspace", ""),
        "branch_coverage_result": branch_result,
        # Preserve the former public container for compatibility.
        "coverage_result": {
            "coverage_status": branch_result.get("coverage_status"),
            "coverage_adapter": branch_result.get("coverage_adapter", ""),
            "source_language": branch_result.get(
                "source_language", "unknown"
            ),
            "instrumentation_status": branch_result.get(
                "instrumentation_status", "NOT_ATTEMPTED"
            ),
            "execution_status": branch_result.get("execution_status"),
            "total_line_coverage": branch_result.get(
                "line_coverage_percent"
            ),
            "total_branch_coverage": branch_result.get(
                "branch_coverage_percent"
            ),
            "covered_lines": branch_result.get("covered_lines", {}),
            "missing_lines": branch_result.get("missing_lines", {}),
            "covered_branches": branch_result.get(
                "covered_branch_locations", {}
            ),
            "missing_branches": missing_by_file,
            "source_files_measured": branch_result.get(
                "source_files_measured", []
            ),
            "command_run": logs.get("command", ""),
            "stdout_summary": logs.get("stdout", ""),
            "stderr_summary": logs.get("stderr", ""),
            "failure_reason": branch_result.get("failure_reason", ""),
        },
    }
    logger.info(
        "Branch coverage case=%s status=%s branches=%s/%s reason=%s",
        state.get("case_uid", "unknown"),
        branch_result.get("coverage_status"),
        branch_result.get("covered_branches"),
        branch_result.get("total_branches"),
        branch_result.get("failure_reason") or "none",
    )
    return result


__all__ = [
    "coverage_agent",
    "collect_branch_coverage",
    "parse_coverage_payload",
    "parse_coverage_report",
]
