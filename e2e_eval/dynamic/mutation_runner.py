"""Deterministic execution of exactly one patch in one isolated workspace."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Sequence


def verdict_for_status(execution_status: str) -> str:
    if execution_status == "PASSED":
        return "SURVIVED"
    if execution_status == "TEST_FAILED":
        return "KILLED"
    if execution_status == "TIMEOUT":
        return "TIMEOUT"
    return "EXECUTION_ERROR"


def _write_evidence(
    artifact_dir: Path,
    *,
    patch: Any,
    execution: dict[str, Any],
    verdict: str,
) -> dict[str, str]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = artifact_dir / "stdout.log"
    stderr_path = artifact_dir / "stderr.log"
    result_path = artifact_dir / "execution.json"
    patch_path = artifact_dir / "mutation_patch.json"
    stdout = str(
        execution.get("execution_stdout", execution.get("execution_stdout_tail", ""))
    )
    stderr = str(
        execution.get("execution_stderr", execution.get("execution_stderr_tail", ""))
    )
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    patch_path.write_text(
        json.dumps(asdict(patch), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result_path.write_text(
        json.dumps({
            "verdict": verdict,
            "execution_status": execution.get("execution_status"),
            "exit_code": execution.get("execution_return_code"),
            "timed_out": verdict == "TIMEOUT",
            "command": execution.get("execution_command", ""),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "execution_result_path": str(result_path),
        "patch_path": str(patch_path),
    }


def run_single_mutant(
    *,
    state: dict[str, Any],
    patch: Any,
    prepared: Any,
    test_code: str,
    timeout_seconds: int,
    project_validation_command: Sequence[str],
    apply_patch: Callable[[Path, Any], None],
    validate_project: Callable[..., dict[str, Any]],
    write_test_project: Callable[..., None],
    run_test: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Apply, validate, and execute one patch; never infer verdicts with an LLM."""
    empty_execution: dict[str, Any] = {
        "execution_status": "NOT_RUN",
        "execution_return_code": None,
        "execution_duration_seconds": 0.0,
        "execution_stdout": "",
        "execution_stderr": "",
        "execution_command": "",
        "execution_environment": {},
    }
    try:
        apply_patch(prepared.app_dir, patch)
    except Exception as exc:
        validation = {
            "valid": False,
            "status": "PATCH_INVALID",
            "commands": [],
            "failure_reason": repr(exc),
        }
        execution = {
            **empty_execution,
            "execution_status": "PATCH_INVALID",
            "execution_stderr": repr(exc),
        }
        verdict = "INVALID"
    else:
        try:
            validation = validate_project(
                prepared.app_dir,
                patch,
                timeout_seconds=timeout_seconds,
                project_validation_command=project_validation_command,
            )
        except Exception as exc:
            validation = {
                "valid": False,
                "status": "VALIDATION_ERROR",
                "commands": [],
                "failure_reason": repr(exc),
            }
        if not validation["valid"]:
            execution = {
                **empty_execution,
                "execution_status": validation["status"],
                "execution_stderr": validation.get("failure_reason", ""),
                "execution_command": "; ".join(validation.get("commands", [])),
            }
            verdict = "INVALID"
        else:
            try:
                write_test_project(
                    prepared,
                    str(state["excutable_test_test_case"]),
                    test_code,
                )
                execution = run_test(prepared, state, timeout_seconds)
                verdict = verdict_for_status(
                    str(execution.get("execution_status", "HARNESS_ERROR"))
                )
            except Exception as exc:
                execution = {
                    **empty_execution,
                    "execution_status": "MUTATION_HARNESS_ERROR",
                    "execution_stderr": repr(exc),
                }
                verdict = "EXECUTION_ERROR"

    artifact_paths = _write_evidence(
        Path(prepared.artifact_dir),
        patch=patch,
        execution=execution,
        verdict=verdict,
    )
    return {
        **asdict(patch),
        "execution_verdict": verdict,
        "execution_status": str(execution.get("execution_status", "NOT_RUN")),
        "stdout_summary": str(
            execution.get("execution_stdout_tail", execution.get("execution_stdout", ""))
        ),
        "stderr_summary": str(
            execution.get("execution_stderr_tail", execution.get("execution_stderr", ""))
        ),
        "command_run": str(execution.get("execution_command", "")),
        "environment_metadata": dict(execution.get("execution_environment", {})),
        "validation_result": validation,
        "duration_seconds": float(
            execution.get("execution_duration_seconds", 0.0)
        ),
        "artifact_dir": str(prepared.artifact_dir),
        "exit_code": execution.get("execution_return_code"),
        "timed_out": verdict == "TIMEOUT",
        **artifact_paths,
    }
