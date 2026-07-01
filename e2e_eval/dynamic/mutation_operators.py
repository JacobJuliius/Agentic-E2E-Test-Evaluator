"""Configurable registry of conservative HTML/JavaScript mutation operators."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class RegisteredOperator:
    family: str
    implementation: Any

    @property
    def name(self) -> str:
        return str(self.implementation.name)

    def generate(self, source: str, relative_path: str) -> Iterable[Any]:
        return self.implementation.generate(source, relative_path)


class OperatorRegistry:
    def __init__(self) -> None:
        self._operators: dict[str, list[RegisteredOperator]] = {}

    def register(self, family: str, operator: Any) -> None:
        normalized = family.strip().lower()
        if not normalized or not hasattr(operator, "generate"):
            raise ValueError("A family and generate-capable operator are required.")
        self._operators.setdefault(normalized, []).append(
            RegisteredOperator(normalized, operator)
        )

    def families(self) -> tuple[str, ...]:
        return tuple(sorted(self._operators))

    def for_families(self, families: Sequence[str] | None = None) -> tuple[Any, ...]:
        selected = (
            self.families()
            if not families
            else tuple(dict.fromkeys(str(item).lower() for item in families))
        )
        return tuple(
            entry.implementation
            for family in selected
            for entry in self._operators.get(family, ())
        )

    def family_for_operator(self, operator_name: str) -> str:
        for family, entries in self._operators.items():
            if any(entry.name == operator_name for entry in entries):
                return family
        return "unknown"


class ConditionalNegationOperator:
    """Negate a simple JS conditional expression while preserving syntax."""

    name = "CONDITIONAL_NEGATION"
    pattern = re.compile(
        r"\b(?:if|while)\s*\(\s*(?P<condition>!?\s*"
        r"[A-Za-z_$][\w$]*(?:\s*(?:===|!==|==|!=|>=|<=|>|<)\s*"
        r"(?:[A-Za-z_$][\w$]*|\d+(?:\.\d+)?|[\"'][^\"']*[\"']))?"
        r")\s*\)"
    )

    def generate(self, source: str, relative_path: str) -> Iterable[Any]:
        if Path(relative_path).suffix.lower() not in {
            ".js", ".mjs", ".cjs", ".ts", ".tsx"
        }:
            return
        from mutation_testing import _candidate

        for match in self.pattern.finditer(source):
            start, end = match.span("condition")
            condition = match.group("condition")
            stripped = condition.strip()
            replacement = (
                stripped[1:].strip()
                if stripped.startswith("!")
                else f"!({stripped})"
            )
            yield _candidate(
                operator=self.name,
                relative_path=relative_path,
                source=source,
                start=start,
                end=end,
                replacement=replacement,
                description="Negate one simple JavaScript conditional expression.",
            )


class StateUpdateOperator:
    """Neutralize a direct JS state assignment without deleting surrounding code."""

    name = "STATE_UPDATE_MUTATION"
    pattern = re.compile(
        r"(?m)^(?P<indent>\s*)(?P<target>"
        r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*=\s*"
        r"(?P<value>[^;{}\n]+)(?P<semicolon>;?)\s*$"
    )
    set_data_pattern = re.compile(
        r"\.setData\s*\(\s*(?P<quote>[\"'])"
        r"(?P<key>[^\"']+)(?P=quote)\s*,"
    )

    def generate(self, source: str, relative_path: str) -> Iterable[Any]:
        if Path(relative_path).suffix.lower() not in {
            ".js", ".mjs", ".cjs", ".ts", ".tsx"
        }:
            return
        from mutation_testing import _candidate

        for match in self.set_data_pattern.finditer(source):
            start, end = match.span("key")
            yield _candidate(
                operator=self.name,
                relative_path=relative_path,
                source=source,
                start=start,
                end=end,
                replacement="__mutated_transfer_key__",
                description="Change one data-transfer state key.",
            )
        for match in self.pattern.finditer(source):
            target = match.group("target")
            value = match.group("value").strip()
            if value == target or value.startswith(("function", "class")):
                continue
            start, end = match.span("value")
            yield _candidate(
                operator=self.name,
                relative_path=relative_path,
                source=source,
                start=start,
                end=end,
                replacement=target,
                description=f"Neutralize the state update assigned to {target!r}.",
            )


class EventPropertyHandlerOperator:
    """Neutralize JavaScript ``element.onevent =`` handler assignment."""

    name = "EVENT_HANDLER_NEUTRALIZATION"
    pattern = re.compile(
        r"\.(?P<handler>on(?:click|change|input|submit|dragstart|dragover|"
        r"drop|keydown|keyup|load|blur|focus))\s*=",
        re.IGNORECASE,
    )

    def generate(self, source: str, relative_path: str) -> Iterable[Any]:
        if Path(relative_path).suffix.lower() not in {
            ".js", ".mjs", ".cjs", ".ts", ".tsx"
        }:
            return
        from mutation_testing import _candidate

        for match in self.pattern.finditer(source):
            start, end = match.span("handler")
            handler = match.group("handler")
            yield _candidate(
                operator=self.name,
                relative_path=relative_path,
                source=source,
                start=start,
                end=end,
                replacement=f"on__mutated_{handler[2:]}",
                description=f"Neutralize JavaScript {handler} handler assignment.",
            )


class EmbeddedScriptOperator:
    """Apply one JavaScript operator only inside HTML ``script`` blocks."""

    script_re = re.compile(
        r"<script\b[^>]*>(?P<body>.*?)</script\s*>",
        re.IGNORECASE | re.DOTALL,
    )

    def __init__(self, implementation: Any):
        self.implementation = implementation
        self.name = str(implementation.name)

    def generate(self, source: str, relative_path: str) -> Iterable[Any]:
        if Path(relative_path).suffix.lower() not in {".html", ".htm"}:
            return
        for script in self.script_re.finditer(source):
            body = script.group("body")
            body_start = script.start("body")
            for candidate in self.implementation.generate(
                body, f"{relative_path}.js"
            ):
                start = body_start + int(candidate.location["start_offset"])
                end = body_start + int(candidate.location["end_offset"])
                line_start = source.rfind("\n", 0, start) + 1
                location = {
                    **candidate.location,
                    "line": source.count("\n", 0, start) + 1,
                    "column": start - line_start,
                    "start_offset": start,
                    "end_offset": end,
                }
                yield replace(
                    candidate,
                    source_file=relative_path,
                    location=location,
                )


def default_operator_registry() -> OperatorRegistry:
    """Build lazily to preserve root-module compatibility imports."""
    from mutation_testing import (
        BooleanNegationOperator,
        ConditionalBoundaryOperator,
        DomAttributeValueOperator,
        EventHandlerOperator,
        NumericLiteralOperator,
        RelationalOperator,
        StringLiteralOperator,
        UiTextOperator,
    )

    registry = OperatorRegistry()
    comparison = [ConditionalBoundaryOperator(), RelationalOperator()]
    boolean = [BooleanNegationOperator(), ConditionalNegationOperator()]
    events = [EventHandlerOperator(), EventPropertyHandlerOperator()]
    arithmetic = [NumericLiteralOperator()]
    displayed = [StringLiteralOperator()]
    state_updates = [StateUpdateOperator()]
    for operator in comparison:
        registry.register("comparison_boundary", operator)
    for operator in boolean:
        registry.register("boolean_conditional_negation", operator)
    for operator in events:
        registry.register("event_handler", operator)
    for operator in arithmetic:
        registry.register("arithmetic_constant", operator)
    for operator in displayed:
        registry.register("displayed_text_value", operator)
    registry.register("displayed_text_value", UiTextOperator())
    registry.register("dom_attribute", DomAttributeValueOperator())
    for operator in state_updates:
        registry.register("state_update", operator)
    for family, operators in (
        ("comparison_boundary", comparison),
        ("boolean_conditional_negation", boolean),
        ("event_handler", events),
        ("arithmetic_constant", arithmetic),
        ("displayed_text_value", displayed),
        ("state_update", state_updates),
    ):
        for operator in operators:
            registry.register(family, EmbeddedScriptOperator(operator))
    return registry


def select_proposed_candidates(
    source_dir: Path,
    proposals: Sequence[dict[str, Any]],
    *,
    max_total_mutants: int,
) -> list[Any]:
    """Backward-compatible candidate-only proposal selection."""
    candidates, _ = select_proposed_candidates_with_lifecycle(
        source_dir,
        proposals,
        max_total_mutants=max_total_mutants,
    )
    return candidates


def select_proposed_candidates_with_lifecycle(
    source_dir: Path,
    proposals: Sequence[dict[str, Any]],
    *,
    max_total_mutants: int,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Convert advisory targets and explain every proposal disposition."""
    from mutation_testing import nonfunctional_exclusion_reason

    registry = default_operator_registry()
    selected: list[Any] = []
    lifecycle: list[dict[str, Any]] = []
    used: set[tuple[str, int, int, str]] = set()
    ordered = sorted(
        proposals,
        key=lambda item: (
            -float(item.get("confidence", 0.0)),
            str(item.get("source_file", "")),
            int(item.get("line_start", 1)),
            str(item.get("operator_family", "")),
        ),
    )
    for proposal_index, proposal in enumerate(ordered, start=1):
        proposal_id = str(
            proposal.get("proposal_id") or f"P{proposal_index:04d}"
        )
        family = str(proposal.get("operator_family", "")).lower()
        relative = str(proposal.get("source_file", "")).replace("\\", "/")
        event = {
            "proposal_id": proposal_id,
            "origin": "llm",
            "mutation_target": str(proposal.get("mutation_target", "")),
            "source_file": relative,
            "operator_family": family,
            "status": "PROPOSED",
            "status_history": ["PROPOSED"],
            "rejection_reason": "",
            "mutant_id": "",
        }
        if len(selected) >= max(0, max_total_mutants):
            event.update({
                "status": "SKIPPED",
                "status_history": ["PROPOSED", "SKIPPED"],
                "rejection_reason": "Mutation campaign limit reached.",
            })
            lifecycle.append(event)
            continue
        path = source_dir / relative
        if not path.is_file():
            event.update({
                "status": "REJECTED",
                "status_history": ["PROPOSED", "REJECTED"],
                "rejection_reason": "Proposed source file does not exist.",
            })
            lifecycle.append(event)
            continue
        operators = registry.for_families([family])
        if not operators:
            event.update({
                "status": "REJECTED",
                "status_history": ["PROPOSED", "REJECTED"],
                "rejection_reason": "No registered deterministic operator family.",
            })
            lifecycle.append(event)
            continue
        event["status"] = "ACCEPTED"
        event["status_history"].append("ACCEPTED")
        source = path.read_text(encoding="utf-8", errors="replace")
        start_line = int(proposal.get("line_start", 1))
        end_line = int(proposal.get("line_end", start_line))
        candidates = sorted(
            (
                candidate
                for operator in operators
                for candidate in operator.generate(source, relative)
                if start_line <= int(candidate.location["line"]) <= end_line
            ),
            key=lambda item: (
                int(item.location["start_offset"]),
                item.operator,
                item.replacement_code,
            ),
        )
        if not candidates:
            event.update({
                "status": "REJECTED",
                "status_history": [
                    "PROPOSED", "ACCEPTED", "REJECTED"
                ],
                "rejection_reason": (
                    "No syntax-preserving deterministic patch matched the "
                    "proposed file, line range, and operator family."
                ),
            })
            lifecycle.append(event)
            continue
        functional = [
            candidate for candidate in candidates
            if not nonfunctional_exclusion_reason(candidate, source)
        ]
        if not functional:
            event.update({
                "status": "REJECTED",
                "status_history": [
                    "PROPOSED", "ACCEPTED", "REJECTED"
                ],
                "rejection_reason": (
                    nonfunctional_exclusion_reason(candidates[0], source)
                    or "All matching patches were excluded by policy."
                ),
            })
            lifecycle.append(event)
            continue
        candidate = functional[0]
        key = (
            candidate.source_file,
            candidate.location["start_offset"],
            candidate.location["end_offset"],
            candidate.operator,
        )
        if key in used:
            event.update({
                "status": "REJECTED",
                "status_history": [
                    "PROPOSED", "ACCEPTED", "REJECTED"
                ],
                "rejection_reason": (
                    "Proposal resolved to a duplicate concrete mutation."
                ),
            })
            lifecycle.append(event)
            continue
        used.add(key)
        mutant_id = f"M{len(selected) + 1:04d}"
        selected.append(replace(
            candidate,
            mutant_id=mutant_id,
            proposal_id=proposal_id,
            candidate_source="llm_proposal",
        ))
        event.update({
            "status": "GENERATED",
            "status_history": [
                "PROPOSED", "ACCEPTED", "GENERATED"
            ],
            "mutant_id": mutant_id,
        })
        lifecycle.append(event)
    return selected, lifecycle
