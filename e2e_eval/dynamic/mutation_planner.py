"""Validated LLM planning for business-relevant source mutation targets.

The planner is advisory. It can identify source locations and operator families,
but it cannot provide replacement text, commands, or write source files.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

ModelInvoker = Callable[[str, str], str]

ALLOWED_OPERATOR_FAMILIES = {
    "comparison_boundary",
    "boolean_conditional_negation",
    "event_handler",
    "arithmetic_constant",
    "displayed_text_value",
    "dom_attribute",
    "state_update",
}
SOURCE_SUFFIXES = {".html", ".htm", ".js", ".mjs", ".cjs", ".ts", ".tsx"}
MAX_SNIPPET_CHARS = 12_000
MAX_CONTEXT_CHARS = 48_000


@dataclass(frozen=True)
class MutationProposal:
    proposal_id: str
    mutation_target: str
    source_file: str
    line_start: int
    line_end: int
    operator_family: str
    requirement_relevance: str
    rationale: str
    confidence: float


def collect_source_context(source_dir: Path) -> dict[str, Any]:
    """Collect bounded source metadata/snippets without modifying the project."""
    root = source_dir.resolve()
    files: list[dict[str, Any]] = []
    remaining = MAX_CONTEXT_CHARS
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or path.suffix.lower() not in SOURCE_SUFFIXES
            or any(part in {".git", "node_modules", "artifacts"} for part in path.parts)
        ):
            continue
        if remaining <= 0:
            break
        text = path.read_text(encoding="utf-8", errors="replace")
        snippet = text[:min(MAX_SNIPPET_CHARS, remaining)]
        remaining -= len(snippet)
        files.append({
            "source_file": path.relative_to(root).as_posix(),
            "line_count": len(text.splitlines()),
            "snippet": snippet,
            "snippet_truncated": len(text) > len(snippet),
        })
    return {"source_root_name": root.name, "files": files}


def _safe_source_file(source_dir: Path, value: Any) -> str | None:
    candidate = str(value or "").replace("\\", "/").strip()
    if not candidate or candidate.startswith("/") or ":" in candidate:
        return None
    root = source_dir.resolve()
    target = (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    if not target.is_file() or target.suffix.lower() not in SOURCE_SUFFIXES:
        return None
    return target.relative_to(root).as_posix()


def validate_proposals(
    payload: Any,
    source_dir: Path,
    *,
    max_proposals: int,
) -> list[MutationProposal]:
    """Accept only bounded target metadata from model output."""
    raw_items = payload.get("proposals", []) if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        return []
    validated: list[MutationProposal] = []
    seen: set[tuple[str, int, int, str]] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        # File content, patches, replacement text, and commands are never accepted.
        forbidden = {
            "patch", "replacement", "replacement_code", "command", "commands",
            "shell", "script", "file_content", "write",
        }
        if forbidden & {str(key).lower() for key in item}:
            continue
        source_file = _safe_source_file(source_dir, item.get("source_file"))
        family = str(item.get("operator_family", "")).strip().lower()
        if source_file is None or family not in ALLOWED_OPERATOR_FAMILIES:
            continue
        raw_range = item.get("line_range", item.get("line"))
        if isinstance(raw_range, dict):
            start = raw_range.get("start", raw_range.get("line_start", 0))
            end = raw_range.get("end", raw_range.get("line_end", start))
        elif isinstance(raw_range, (list, tuple)) and raw_range:
            start = raw_range[0]
            end = raw_range[-1]
        else:
            start = item.get("line_start", raw_range)
            end = item.get("line_end", start)
        try:
            line_start = max(1, int(start))
            line_end = max(line_start, int(end))
            confidence = min(1.0, max(0.0, float(item.get("confidence", 0.0))))
        except (TypeError, ValueError):
            continue
        actual_lines = len(
            (source_dir / source_file).read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        )
        if line_start > max(1, actual_lines):
            continue
        line_end = min(line_end, max(1, actual_lines))
        target = str(item.get("mutation_target", "")).strip()[:300]
        relevance = str(item.get("requirement_relevance", "")).strip()[:500]
        rationale = str(item.get("rationale", "")).strip()[:800]
        if not target or not relevance or not rationale:
            continue
        key = (source_file, line_start, line_end, family)
        if key in seen:
            continue
        seen.add(key)
        validated.append(MutationProposal(
            proposal_id=f"P{len(validated) + 1:04d}",
            mutation_target=target,
            source_file=source_file,
            line_start=line_start,
            line_end=line_end,
            operator_family=family,
            requirement_relevance=relevance,
            rationale=rationale,
            confidence=confidence,
        ))
        if len(validated) >= max(0, max_proposals):
            break
    return validated


def plan_mutations(
    *,
    requirements: str,
    generated_test: str,
    source_dir: Path,
    source_metadata: dict[str, Any] | None,
    invoke_model: ModelInvoker | None,
    max_proposals: int,
) -> dict[str, Any]:
    """Ask an LLM for target metadata, then strictly validate its response."""
    if invoke_model is None:
        return {
            "mutation_planning_status": "ANALYSIS_UNAVAILABLE",
            "mutation_proposals": [],
            "mutation_planning_detail": (
                "No LLM credentials/invoker configured; deterministic seeded "
                "mutation discovery remains available."
            ),
        }
    evidence = {
        "requirements": requirements,
        "generated_e2e_test": generated_test,
        "source_metadata": source_metadata or {},
        "source_context": collect_source_context(source_dir),
        "allowed_operator_families": sorted(ALLOWED_OPERATOR_FAMILIES),
        "maximum_proposals": max_proposals,
    }
    system_prompt = """
You are a mutation-target planner for an E2E test evaluator. Propose business-
relevant fault locations using only the supplied source files and requirements.
You have no permission to edit files. Never return source replacement text,
patches, shell commands, scripts, or file-write instructions.

Return ONLY JSON:
{"proposals":[{
  "mutation_target":"brief semantic target",
  "source_file":"exact supplied relative path",
  "line_range":{"start":1,"end":1},
  "operator_family":"one exact allowed family",
  "requirement_relevance":"business behavior tied to the requirement",
  "rationale":"why this fault could expose a test gap",
  "confidence":0.0
}]}
"""
    try:
        raw = invoke_model(
            system_prompt,
            json.dumps(evidence, ensure_ascii=False, indent=2),
        )
        proposals = validate_proposals(
            json.loads(raw), source_dir, max_proposals=max_proposals
        )
        return {
            "mutation_planning_status": (
                "PLANNED" if proposals else "NO_VALID_PROPOSALS"
            ),
            "mutation_proposals": [asdict(item) for item in proposals],
            "mutation_planning_detail": (
                f"Validated {len(proposals)} bounded mutation target proposal(s)."
            ),
        }
    except Exception as exc:
        return {
            "mutation_planning_status": "ANALYSIS_UNAVAILABLE",
            "mutation_proposals": [],
            "mutation_planning_detail": (
                "Mutation planning failed; deterministic seeded mutation "
                f"discovery remains available. {exc}"
            ),
        }


def proposal_lines(proposal: dict[str, Any]) -> Iterable[int]:
    start = int(proposal.get("line_start", 1))
    end = int(proposal.get("line_end", start))
    return range(start, end + 1)
