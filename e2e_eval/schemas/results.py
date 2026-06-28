"""Standard result envelopes layered over backward-compatible agent outputs."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from functools import wraps
from typing import Any, Callable


@dataclass
class AgentResult:
    status: str
    score: float | None = None
    rationale: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    failure_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _status(result: dict[str, Any], key: str | None) -> str:
    if key and key in result:
        value = result[key]
        if isinstance(value, bool):
            return "success" if value else "failed"
        return str(value)
    for name, value in result.items():
        if name.endswith("_status"):
            return str(value)
    return "completed"


def _rationale(result: dict[str, Any], key: str | None) -> str:
    candidates = [
        key, "final_reasoning", "maintainability_rationale",
        "missing_assertions_rationale", "dynamic_coverage_detail",
        "mutation_detail", "execution_report_summary",
        "repair_comparison_summary", "error_message",
    ]
    for candidate in candidates:
        if candidate and result.get(candidate):
            return str(result[candidate])
    return ""


def standardized_agent(
    name: str,
    function: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    status_key: str | None = None,
    score_key: str | None = None,
    rationale_key: str | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Wrap an existing node without changing its historical output fields."""
    @wraps(function)
    def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        previous = dict(state.get("agent_results", {}))
        try:
            result = function(state)
        except Exception as exc:
            envelope = AgentResult(
                status="failed",
                rationale=f"{name} raised an exception.",
                failure_reason=repr(exc),
            )
            previous[name] = envelope.to_dict()
            return {
                "agent_results": previous,
                f"{name}_failure_reason": repr(exc),
            }

        status = _status(result, status_key)
        score_value = result.get(score_key) if score_key else None
        try:
            score = float(score_value) if score_value is not None else None
        except (TypeError, ValueError):
            score = None
        artifacts = {
            key: str(value) for key, value in result.items()
            if value and (
                key.endswith("_path") or key.endswith("_dir")
                or key.endswith("_artifact")
            )
        }
        omitted = {
            "fixed_code", "refiner_candidate_code",
            "execution_stdout_tail", "execution_stderr_tail",
            "coverage_stdout_summary", "coverage_stderr_summary",
        }
        evidence = {
            key: value for key, value in result.items()
            if key not in omitted and key not in artifacts
        }
        rationale = _rationale(result, rationale_key)
        if not rationale:
            rationale = f"{name} completed with status {status}."
        failure_reason = str(
            result.get("failure_reason")
            or result.get("coverage_failure_reason")
            or result.get("error_message")
            or ""
        )
        if (
            not failure_reason
            and any(token in status.lower() for token in ("fail", "error"))
        ):
            failure_reason = rationale
        previous[name] = AgentResult(
            status=status,
            score=score,
            rationale=rationale,
            artifacts=artifacts,
            evidence=evidence,
            failure_reason=failure_reason,
        ).to_dict()
        return {**result, "agent_results": previous}

    return wrapped
