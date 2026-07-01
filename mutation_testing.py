"""General, deterministic mutation testing for generated E2E test evaluation.

Operators are language-aware and span-based: no mutation uses global string
replacement.  The campaign engine is runner-agnostic; the existing dynamic
Behave/Selenium harness is supplied through callbacks by ``dynamic_agents.py``.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import platform
import random
import re
import shlex
import shutil
import subprocess
import sys
import time
import tokenize
from dataclasses import asdict, dataclass, field, replace
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Protocol, Sequence

from e2e_eval.utils.paths import sanitize_export_payload

logger = logging.getLogger(__name__)


SOURCE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".html", ".htm"}
SKIPPED_DIRECTORY_NAMES = {
    ".git", ".hg", ".svn", ".pytest_cache", "__pycache__", "artifacts",
    "build", "coverage", "dist", "generated", "node_modules", "venv", ".venv",
}
DEFAULT_EXCLUDE_PATTERNS = (
    "**/*.min.js",
    "**/*.min.css",
    "**/vendor/**",
    "**/third_party/**",
    "**/test_*",
    "**/*_test.py",
    "**/*.spec.js",
    "**/*.test.js",
    "**/tests/**",
)
MAX_SOURCE_BYTES = 1_000_000
INFRASTRUCTURE_ATTRIBUTES = {"integrity", "crossorigin", "referrerpolicy"}
EXTERNAL_RESOURCE_RE = re.compile(
    r"^(?:https?:)?//|(?:^|[./_-])(?:cdn|cdnjs|unpkg|jsdelivr)(?:[./_-]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MutationCandidate:
    mutant_id: str
    operator: str
    source_file: str
    location: dict[str, int]
    original_code: str
    replacement_code: str
    mutation_description: str
    proposal_id: str = ""
    candidate_source: str = "deterministic_fallback"


@dataclass
class MutationRecord:
    mutant_id: str
    operator: str
    source_file: str
    location: dict[str, int]
    original_code: str
    replacement_code: str
    mutation_description: str
    execution_verdict: str
    execution_status: str
    stdout_summary: str
    stderr_summary: str
    command_run: str
    environment_metadata: dict[str, Any]
    validation_result: dict[str, Any]
    duration_seconds: float
    artifact_dir: str
    scope_relation: str = "UNCERTAIN"
    scope_reason: str = ""
    source_scope: dict[str, Any] = field(default_factory=dict)
    exit_code: int | None = None
    timed_out: bool = False
    stdout_path: str = ""
    stderr_path: str = ""
    execution_result_path: str = ""
    patch_path: str = ""
    proposal_id: str = ""
    candidate_source: str = "deterministic_fallback"
    included_in_raw_score: bool = True
    included_in_relevant_score: bool = False
    exclusion_reason: str = ""


class MutationOperator(Protocol):
    name: str

    def generate(
        self, source: str, relative_path: str
    ) -> Iterable[MutationCandidate]:
        ...


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    start: int
    end: int


def _line_starts(source: str) -> list[int]:
    starts = [0]
    starts.extend(match.end() for match in re.finditer(r"\n", source))
    return starts


def _line_column(source: str, offset: int) -> tuple[int, int]:
    starts = _line_starts(source)
    line_index = 0
    for index, start in enumerate(starts):
        if start > offset:
            break
        line_index = index
    return line_index + 1, offset - starts[line_index]


def _candidate(
    *,
    operator: str,
    relative_path: str,
    source: str,
    start: int,
    end: int,
    replacement: str,
    description: str,
) -> MutationCandidate:
    line, column = _line_column(source, start)
    return MutationCandidate(
        mutant_id="",
        operator=operator,
        source_file=relative_path,
        location={
            "line": line,
            "column": column,
            "start_offset": start,
            "end_offset": end,
        },
        original_code=source[start:end],
        replacement_code=replacement,
        mutation_description=description,
    )


def _python_char_offset(source: str, line: int, byte_column: int) -> int:
    """Translate AST UTF-8 byte columns into Python string offsets."""
    lines = source.splitlines(keepends=True)
    prefix = "".join(lines[: line - 1])
    current = lines[line - 1] if line - 1 < len(lines) else ""
    character_column = len(
        current.encode("utf-8")[:byte_column].decode("utf-8", errors="ignore")
    )
    return len(prefix) + character_column


def _python_constant_nodes(source: str) -> tuple[ast.AST, set[int]]:
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for owner in ast.walk(tree):
        body = getattr(owner, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(getattr(body[0], "value", None), ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))
    return tree, docstrings


def _python_node_span(source: str, node: ast.AST) -> tuple[int, int] | None:
    if not all(
        hasattr(node, attribute)
        for attribute in ("lineno", "col_offset", "end_lineno", "end_col_offset")
    ):
        return None
    return (
        _python_char_offset(source, node.lineno, node.col_offset),
        _python_char_offset(source, node.end_lineno, node.end_col_offset),
    )


def _scan_js_tokens(source: str) -> list[_Token]:
    """Conservative JS/TS tokenizer that excludes comments from mutation."""
    tokens: list[_Token] = []
    index = 0
    length = len(source)
    multi_ops = ("!==", "===", ">=", "<=", "==", "!=", "&&", "||", "=>")
    while index < length:
        char = source[index]
        if char.isspace():
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index + 2)
            index = length if newline < 0 else newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            end = index + 1
            while end < length:
                if source[end] == "\\":
                    end += 2
                    continue
                if source[end] == quote:
                    end += 1
                    break
                end += 1
            if end <= length and source[end - 1:end] == quote:
                tokens.append(_Token("string", source[index:end], index, end))
            index = max(end, index + 1)
            continue
        number = re.match(r"(?:\d+\.\d+|\d+)", source[index:])
        if number:
            end = index + len(number.group(0))
            tokens.append(_Token("number", source[index:end], index, end))
            index = end
            continue
        identifier = re.match(r"[A-Za-z_$][A-Za-z0-9_$]*", source[index:])
        if identifier:
            end = index + len(identifier.group(0))
            tokens.append(_Token("identifier", source[index:end], index, end))
            index = end
            continue
        operator = next(
            (value for value in multi_ops if source.startswith(value, index)),
            None,
        )
        if operator:
            tokens.append(_Token("operator", operator, index, index + len(operator)))
            index += len(operator)
            continue
        tokens.append(_Token("punctuation", char, index, index + 1))
        index += 1
    return tokens


def _python_compare_tokens(source: str) -> list[_Token]:
    tree = ast.parse(source)
    compare_spans = [
        span for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        for span in [_python_node_span(source, node)]
        if span is not None
    ]
    starts = _line_starts(source)
    result: list[_Token] = []
    reader = StringIO(source).readline
    for token_info in tokenize.generate_tokens(reader):
        if token_info.type != tokenize.OP:
            continue
        start = starts[token_info.start[0] - 1] + token_info.start[1]
        end = starts[token_info.end[0] - 1] + token_info.end[1]
        if any(left <= start and end <= right for left, right in compare_spans):
            result.append(_Token("operator", token_info.string, start, end))
    return result


class NumericLiteralOperator:
    name = "NUMERIC_LITERAL_PERTURBATION"

    def generate(self, source: str, relative_path: str) -> Iterable[MutationCandidate]:
        suffix = Path(relative_path).suffix.lower()
        if suffix == ".py":
            try:
                tree, _ = _python_constant_nodes(source)
            except SyntaxError:
                return
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, (int, float))
                    and not isinstance(node.value, bool)
                ):
                    span = _python_node_span(source, node)
                    if not span:
                        continue
                    replacement = repr(node.value + 1)
                    yield _candidate(
                        operator=self.name, relative_path=relative_path,
                        source=source, start=span[0], end=span[1],
                        replacement=replacement,
                        description="Perturb a numeric literal by +1.",
                    )
        elif suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx"}:
            for token in _scan_js_tokens(source):
                if token.kind != "number":
                    continue
                value = float(token.value) if "." in token.value else int(token.value)
                replacement = str(value + 1)
                yield _candidate(
                    operator=self.name, relative_path=relative_path,
                    source=source, start=token.start, end=token.end,
                    replacement=replacement,
                    description="Perturb a numeric literal by +1.",
                )


class StringLiteralOperator:
    name = "STRING_LITERAL_PERTURBATION"

    def generate(self, source: str, relative_path: str) -> Iterable[MutationCandidate]:
        suffix = Path(relative_path).suffix.lower()
        if suffix == ".py":
            try:
                tree, docstrings = _python_constant_nodes(source)
            except SyntaxError:
                return
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value
                    and id(node) not in docstrings
                ):
                    span = _python_node_span(source, node)
                    if not span:
                        continue
                    yield _candidate(
                        operator=self.name, relative_path=relative_path,
                        source=source, start=span[0], end=span[1],
                        replacement=repr(node.value + "__MUTATED__"),
                        description="Perturb a non-docstring string literal.",
                    )
        elif suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx"}:
            for token in _scan_js_tokens(source):
                if token.kind != "string" or len(token.value) < 2:
                    continue
                replacement = (
                    token.value[:-1] + "__MUTATED__" + token.value[-1]
                )
                yield _candidate(
                    operator=self.name, relative_path=relative_path,
                    source=source, start=token.start, end=token.end,
                    replacement=replacement,
                    description="Perturb a JavaScript/TypeScript string literal.",
                )


class BooleanNegationOperator:
    name = "BOOLEAN_NEGATION"

    def generate(self, source: str, relative_path: str) -> Iterable[MutationCandidate]:
        suffix = Path(relative_path).suffix.lower()
        if suffix == ".py":
            try:
                tree, _ = _python_constant_nodes(source)
            except SyntaxError:
                return
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, bool):
                    span = _python_node_span(source, node)
                    if span:
                        yield _candidate(
                            operator=self.name, relative_path=relative_path,
                            source=source, start=span[0], end=span[1],
                            replacement="False" if node.value else "True",
                            description="Negate a boolean literal.",
                        )
        elif suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx"}:
            for token in _scan_js_tokens(source):
                if token.kind == "identifier" and token.value in {"true", "false"}:
                    yield _candidate(
                        operator=self.name, relative_path=relative_path,
                        source=source, start=token.start, end=token.end,
                        replacement="false" if token.value == "true" else "true",
                        description="Negate a boolean literal.",
                    )


class ConditionalBoundaryOperator:
    name = "CONDITIONAL_BOUNDARY_REPLACEMENT"
    replacements = {">=": ">", "<=": "<", ">": ">=", "<": "<="}

    def generate(self, source: str, relative_path: str) -> Iterable[MutationCandidate]:
        suffix = Path(relative_path).suffix.lower()
        try:
            tokens = (
                _python_compare_tokens(source)
                if suffix == ".py"
                else _scan_js_tokens(source)
            )
        except (SyntaxError, tokenize.TokenError):
            return
        if suffix not in {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx"}:
            return
        for token in tokens:
            if token.value in self.replacements:
                yield _candidate(
                    operator=self.name, relative_path=relative_path,
                    source=source, start=token.start, end=token.end,
                    replacement=self.replacements[token.value],
                    description=(
                        f"Replace conditional boundary {token.value!r} with "
                        f"{self.replacements[token.value]!r}."
                    ),
                )


class RelationalOperator:
    name = "RELATIONAL_OPERATOR_REPLACEMENT"
    replacements = {
        "==": "!=", "!=": "==", "===": "!==", "!==": "===",
        "is": "is not", "is not": "is", "in": "not in", "not in": "in",
    }

    def generate(self, source: str, relative_path: str) -> Iterable[MutationCandidate]:
        suffix = Path(relative_path).suffix.lower()
        if suffix == ".py":
            try:
                tokens = _python_compare_tokens(source)
            except (SyntaxError, tokenize.TokenError):
                return
            # Tokenize exposes ``is not`` and ``not in`` as separate NAME tokens,
            # so the first defensible Python set is equality/inequality.
            replacements = {"==": "!=", "!=": "=="}
        elif suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx"}:
            tokens = _scan_js_tokens(source)
            replacements = {
                key: value for key, value in self.replacements.items()
                if key in {"==", "!=", "===", "!=="}
            }
        else:
            return
        for token in tokens:
            if token.value in replacements:
                yield _candidate(
                    operator=self.name, relative_path=relative_path,
                    source=source, start=token.start, end=token.end,
                    replacement=replacements[token.value],
                    description=(
                        f"Replace relational operator {token.value!r} with "
                        f"{replacements[token.value]!r}."
                    ),
                )


class EventHandlerOperator:
    name = "EVENT_HANDLER_REPLACEMENT"
    html_event_re = re.compile(
        r"\b(?P<name>on[A-Za-z][\w:-]*)\s*=\s*"
        r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
        re.DOTALL,
    )

    def generate(self, source: str, relative_path: str) -> Iterable[MutationCandidate]:
        suffix = Path(relative_path).suffix.lower()
        if suffix in {".js", ".mjs", ".cjs", ".ts", ".tsx"}:
            tokens = _scan_js_tokens(source)
            for index, token in enumerate(tokens):
                if token.value != "addEventListener":
                    continue
                following = tokens[index + 1:index + 4]
                event_token = next(
                    (item for item in following if item.kind == "string"),
                    None,
                )
                if event_token:
                    quote = event_token.value[0]
                    yield _candidate(
                        operator=self.name, relative_path=relative_path,
                        source=source, start=event_token.start, end=event_token.end,
                        replacement=f"{quote}__mutated_event__{quote}",
                        description=(
                            "Replace a statically detected addEventListener event "
                            "name so the handler no longer receives the event."
                        ),
                    )
        elif suffix in {".html", ".htm"}:
            for match in self.html_event_re.finditer(source):
                start, end = match.span("value")
                if start == end:
                    continue
                yield _candidate(
                    operator=self.name, relative_path=relative_path,
                    source=source, start=start, end=end,
                    replacement="",
                    description=f"Remove inline {match.group('name')} handler code.",
                )


class DomAttributeValueOperator:
    name = "DOM_ATTRIBUTE_VALUE_MUTATION"
    tag_re = re.compile(r"<[A-Za-z][^<>]*>", re.DOTALL)
    attribute_re = re.compile(
        r"(?P<name>[A-Za-z_:][\w:.-]*)\s*=\s*"
        r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
        re.DOTALL,
    )

    def generate(self, source: str, relative_path: str) -> Iterable[MutationCandidate]:
        if Path(relative_path).suffix.lower() not in {".html", ".htm"}:
            return
        for tag in self.tag_re.finditer(source):
            tag_text = tag.group(0)
            for attribute in self.attribute_re.finditer(tag_text):
                name = attribute.group("name").lower()
                value = attribute.group("value")
                if not value or name.startswith("on"):
                    continue
                if value.lower() == "true":
                    replacement = "false"
                elif value.lower() == "false":
                    replacement = "true"
                elif re.fullmatch(r"\d+(?:\.\d+)?", value):
                    number = float(value) if "." in value else int(value)
                    replacement = str(number + 1)
                else:
                    replacement = value + "__MUTATED__"
                local_start, local_end = attribute.span("value")
                start = tag.start() + local_start
                end = tag.start() + local_end
                yield _candidate(
                    operator=self.name, relative_path=relative_path,
                    source=source, start=start, end=end,
                    replacement=replacement,
                    description=f"Mutate DOM attribute {name!r} value.",
                )


class UiTextOperator:
    name = "UI_TEXT_MUTATION"
    text_re = re.compile(r">(?P<text>[^<>]+)<", re.DOTALL)
    excluded_block_re = re.compile(
        r"<(?:script|style|template)\b[^>]*>.*?</(?:script|style|template)\s*>"
        r"|<!--.*?-->",
        re.IGNORECASE | re.DOTALL,
    )

    def generate(self, source: str, relative_path: str) -> Iterable[MutationCandidate]:
        if Path(relative_path).suffix.lower() not in {".html", ".htm"}:
            return
        excluded = [match.span() for match in self.excluded_block_re.finditer(source)]
        for match in self.text_re.finditer(source):
            start, end = match.span("text")
            if any(left <= start < right for left, right in excluded):
                continue
            text = match.group("text")
            if not re.search(r"[A-Za-z0-9]", text):
                continue
            leading = text[: len(text) - len(text.lstrip())]
            trailing = text[len(text.rstrip()):]
            replacement = leading + "__MUTATED_TEXT__" + trailing
            yield _candidate(
                operator=self.name, relative_path=relative_path,
                source=source, start=start, end=end,
                replacement=replacement,
                description="Mutate a visible HTML text node.",
            )


DEFAULT_OPERATORS: tuple[MutationOperator, ...] = (
    NumericLiteralOperator(),
    StringLiteralOperator(),
    BooleanNegationOperator(),
    ConditionalBoundaryOperator(),
    RelationalOperator(),
    EventHandlerOperator(),
    DomAttributeValueOperator(),
    UiTextOperator(),
)


def _matches_patterns(relative_path: str, patterns: Sequence[str]) -> bool:
    path = PurePosixPath(relative_path)
    return any(
        path.match(pattern)
        or (pattern.startswith("**/") and path.match(pattern[3:]))
        for pattern in patterns
    )


def _iter_source_files(
    source_dir: Path,
    include_patterns: Sequence[str],
    exclude_patterns: Sequence[str],
) -> Iterable[Path]:
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_dir)
        relative_text = relative.as_posix()
        if any(part in SKIPPED_DIRECTORY_NAMES for part in relative.parts):
            continue
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if ".min." in path.name.lower() or path.stat().st_size > MAX_SOURCE_BYTES:
            continue
        if include_patterns and not _matches_patterns(relative_text, include_patterns):
            continue
        if exclude_patterns and _matches_patterns(relative_text, exclude_patterns):
            continue
        yield path


def nonfunctional_exclusion_reason(
    candidate: MutationCandidate | dict[str, Any],
    source: str,
) -> str:
    """Return the default policy reason for non-functional infrastructure."""
    if isinstance(candidate, dict):
        location = candidate.get("location", {})
        original = str(candidate.get("original_code", ""))
        source_file = str(candidate.get("source_file", ""))
    else:
        location = candidate.location
        original = candidate.original_code
        source_file = candidate.source_file
    suffix = Path(source_file).suffix.lower()
    if suffix == ".css":
        return "Purely cosmetic CSS mutations are excluded by default."
    if suffix not in {".html", ".htm"}:
        return ""
    start = int(location.get("start_offset", 0))
    tag_start = source.rfind("<", 0, start + 1)
    tag_end = source.find(">", max(0, start))
    if tag_start < 0 or tag_end < 0:
        return ""
    tag = source[tag_start:tag_end + 1]
    tag_name_match = re.match(r"<\s*([A-Za-z][\w:-]*)", tag)
    tag_name = (
        tag_name_match.group(1).lower() if tag_name_match else ""
    )
    lower_tag = tag.lower()
    if tag_name == "meta":
        return "Metadata elements are non-functional infrastructure."
    if tag_name == "link" and re.search(
        r"\brel\s*=\s*[\"'][^\"']*(?:stylesheet|icon|favicon)[^\"']*[\"']",
        lower_tag,
    ):
        return "Stylesheet, favicon, and icon links are excluded infrastructure."
    for attribute in INFRASTRUCTURE_ATTRIBUTES:
        match = re.search(
            rf"\b{attribute}\s*=\s*([\"'])(?P<value>.*?)(?:\1)",
            tag,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            value_start = tag_start + match.start("value")
            value_end = tag_start + match.end("value")
            if value_start <= start <= value_end:
                return f"DOM attribute {attribute!r} is infrastructure metadata."
    style_match = re.search(
        r"\bstyle\s*=\s*([\"'])(?P<value>.*?)(?:\1)",
        tag,
        re.IGNORECASE | re.DOTALL,
    )
    if style_match:
        value_start = tag_start + style_match.start("value")
        value_end = tag_start + style_match.end("value")
        if value_start <= start <= value_end:
            return "Inline style mutations are purely cosmetic by default."
    if tag_name in {"link", "script", "img", "source"}:
        url_match = re.search(
            r"\b(?:href|src)\s*=\s*([\"'])(?P<value>.*?)(?:\1)",
            tag,
            re.IGNORECASE | re.DOTALL,
        )
        if url_match and EXTERNAL_RESOURCE_RE.search(
            url_match.group("value").strip()
        ):
            return "Unrelated external resource URLs are excluded infrastructure."
    if EXTERNAL_RESOURCE_RE.search(original.strip("\"' ")):
        return "Unrelated external resource URLs are excluded infrastructure."
    return ""


def discover_mutations(
    source_dir: Path,
    *,
    operators: Sequence[MutationOperator] = DEFAULT_OPERATORS,
    include_patterns: Sequence[str] = (),
    exclude_patterns: Sequence[str] = DEFAULT_EXCLUDE_PATTERNS,
    max_mutants_per_file: int = 5,
    max_total_mutants: int = 20,
    seed: int = 1337,
) -> list[MutationCandidate]:
    """Discover, deduplicate, and deterministically select mutation candidates."""
    selected: list[MutationCandidate] = []
    rng = random.Random(seed)
    for path in _iter_source_files(
        source_dir, include_patterns, exclude_patterns
    ):
        relative = path.relative_to(source_dir).as_posix()
        source = path.read_text(encoding="utf-8", errors="replace")
        candidates: list[MutationCandidate] = []
        seen: set[tuple[Any, ...]] = set()
        for operator in operators:
            for candidate in operator.generate(source, relative):
                key = (
                    candidate.source_file,
                    candidate.location["start_offset"],
                    candidate.location["end_offset"],
                    candidate.replacement_code,
                    candidate.operator,
                )
                if (
                    key in seen
                    or candidate.original_code == candidate.replacement_code
                    or nonfunctional_exclusion_reason(candidate, source)
                ):
                    continue
                seen.add(key)
                candidates.append(candidate)
        candidates.sort(
            key=lambda item: (
                item.location["start_offset"], item.operator,
                item.replacement_code,
            )
        )
        rng.shuffle(candidates)
        selected.extend(candidates[: max(0, max_mutants_per_file)])

    selected.sort(
        key=lambda item: (
            item.source_file, item.location["start_offset"], item.operator
        )
    )
    rng.shuffle(selected)
    selected = selected[: max(0, max_total_mutants)]
    return [
        replace(candidate, mutant_id=f"M{index:04d}")
        for index, candidate in enumerate(selected, start=1)
    ]


def discover_business_mutations(
    source_dir: Path,
    *,
    test_scope: dict[str, Any],
    include_patterns: Sequence[str],
    exclude_patterns: Sequence[str],
    max_mutants_per_file: int,
    max_total_mutants: int,
    seed: int,
    excluded_keys: set[tuple[str, int, int, str]] | None = None,
) -> list[MutationCandidate]:
    """Prefer distinct requirement-linked candidates from a broad stable pool."""
    from e2e_eval.dynamic.mutation_operators import default_operator_registry

    if max_total_mutants <= 0:
        return []
    pool = discover_mutations(
        source_dir,
        operators=default_operator_registry().for_families(),
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        max_mutants_per_file=max(
            max_mutants_per_file, max_total_mutants * 8
        ),
        max_total_mutants=max_total_mutants * 20,
        seed=seed,
    )
    excluded = excluded_keys or set()
    source_cache: dict[str, str] = {}
    ranked: list[tuple[int, float, MutationCandidate]] = []
    rng = random.Random(seed)
    for candidate in pool:
        key = (
            candidate.source_file,
            candidate.location["start_offset"],
            candidate.location["end_offset"],
            candidate.operator,
        )
        if key in excluded:
            continue
        if candidate.source_file not in source_cache:
            source_cache[candidate.source_file] = (
                source_dir / candidate.source_file
            ).read_text(encoding="utf-8", errors="replace")
        relevance = classify_mutant_relevance(
            candidate,
            source_cache[candidate.source_file],
            test_scope,
        )
        priority = {
            "RELEVANT": 0,
            "UNCERTAIN": 1,
            "OUT_OF_SCOPE": 2,
        }.get(relevance["scope_relation"], 3)
        ranked.append((priority, rng.random(), candidate))
    ranked.sort(key=lambda item: (
        item[0],
        item[2].source_file,
        item[2].location["line"],
        item[1],
    ))

    selected: list[MutationCandidate] = []
    seen_operators: set[str] = set()
    for prefer_distinct in (True, False):
        for _, _, candidate in ranked:
            if candidate in selected:
                continue
            if prefer_distinct and candidate.operator in seen_operators:
                continue
            selected.append(candidate)
            seen_operators.add(candidate.operator)
            if len(selected) >= max_total_mutants:
                break
        if len(selected) >= max_total_mutants:
            break
    return [
        replace(candidate, mutant_id=f"M{index:04d}")
        for index, candidate in enumerate(selected, start=1)
    ]


def apply_mutation(app_dir: Path, mutant: MutationCandidate) -> None:
    target = app_dir / mutant.source_file
    source = target.read_text(encoding="utf-8", errors="replace")
    start = mutant.location["start_offset"]
    end = mutant.location["end_offset"]
    if source[start:end] != mutant.original_code:
        raise ValueError(
            f"{mutant.mutant_id} source span no longer matches "
            f"{mutant.source_file}:{mutant.location['line']}."
        )
    target.write_text(
        source[:start] + mutant.replacement_code + source[end:],
        encoding="utf-8",
    )


def validate_mutated_project(
    app_dir: Path,
    mutant: MutationCandidate,
    *,
    timeout_seconds: int,
    project_validation_command: Sequence[str] = (),
    subprocess_runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Reject syntactically invalid mutants before mutation-score execution."""
    target = app_dir / mutant.source_file
    suffix = target.suffix.lower()
    commands: list[str] = []
    try:
        if suffix == ".py":
            ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
        elif suffix in {".js", ".mjs", ".cjs"}:
            node = shutil.which("node")
            if node:
                command = [node, "--check", str(target.resolve())]
                commands.append(subprocess.list2cmdline(command))
                completed = subprocess_runner(
                    command,
                    cwd=app_dir,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                )
                if completed.returncode != 0:
                    return {
                        "valid": False,
                        "status": "SYNTAX_INVALID",
                        "commands": commands,
                        "failure_reason": (completed.stderr or completed.stdout)[-2000:],
                    }
        elif suffix in {".html", ".htm"}:
            parser = HTMLParser()
            parser.feed(target.read_text(encoding="utf-8", errors="replace"))

        if project_validation_command:
            command = [str(item) for item in project_validation_command]
            commands.append(subprocess.list2cmdline(command))
            completed = subprocess_runner(
                command,
                cwd=app_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            if completed.returncode != 0:
                return {
                    "valid": False,
                    "status": "PROJECT_VALIDATION_FAILED",
                    "commands": commands,
                    "failure_reason": (completed.stderr or completed.stdout)[-2000:],
                }
        return {
            "valid": True,
            "status": "VALID",
            "commands": commands,
            "failure_reason": "",
        }
    except SyntaxError as exc:
        return {
            "valid": False,
            "status": "SYNTAX_INVALID",
            "commands": commands,
            "failure_reason": str(exc),
        }
    except subprocess.TimeoutExpired:
        return {
            "valid": False,
            "status": "VALIDATION_TIMEOUT",
            "commands": commands,
            "failure_reason": (
                f"Mutant validation timed out after {timeout_seconds}s."
            ),
        }
    except Exception as exc:
        return {
            "valid": False,
            "status": "VALIDATION_ERROR",
            "commands": commands,
            "failure_reason": repr(exc),
        }


def verdict_for_execution(execution_status: str) -> str:
    from e2e_eval.dynamic.mutation_runner import verdict_for_status

    return verdict_for_status(execution_status)


def calculate_mutation_metrics(
    records: Sequence[MutationRecord | dict[str, Any]],
) -> dict[str, Any]:
    def value(
        record: MutationRecord | dict[str, Any],
        key: str,
        default: Any = None,
    ) -> Any:
        return (
            record.get(key, default)
            if isinstance(record, dict)
            else getattr(record, key, default)
        )

    generated = len(records)
    included = [
        item for item in records
        if bool(value(item, "included_in_raw_score", True))
    ]
    killed = sum(value(item, "execution_verdict") == "KILLED" for item in included)
    survived = sum(value(item, "execution_verdict") == "SURVIVED" for item in included)
    invalid = sum(value(item, "execution_verdict") == "INVALID" for item in records)
    timeout = sum(value(item, "execution_verdict") == "TIMEOUT" for item in records)
    execution_error = sum(
        value(item, "execution_verdict") == "EXECUTION_ERROR"
        for item in records
    )
    valid = killed + survived
    score = round(100.0 * killed / valid, 2) if valid else None
    per_operator: dict[str, dict[str, Any]] = {}
    for record in records:
        operator = str(value(record, "operator"))
        verdict = str(value(record, "execution_verdict"))
        bucket = per_operator.setdefault(
            operator,
            {
                "total_mutants_generated": 0,
                "valid_mutants": 0,
                "killed_mutants": 0,
                "survived_mutants": 0,
                "invalid_mutants": 0,
                "timeout_mutants": 0,
                "execution_error_mutants": 0,
                "excluded_from_score": 0,
                "mutation_score": None,
            },
        )
        bucket["total_mutants_generated"] += 1
        score_included = bool(value(record, "included_in_raw_score", True))
        if verdict in {"KILLED", "SURVIVED"} and not score_included:
            bucket["excluded_from_score"] += 1
        elif verdict == "KILLED":
            bucket["killed_mutants"] += 1
            bucket["valid_mutants"] += 1
        elif verdict == "SURVIVED":
            bucket["survived_mutants"] += 1
            bucket["valid_mutants"] += 1
        elif verdict == "INVALID":
            bucket["invalid_mutants"] += 1
        elif verdict == "TIMEOUT":
            bucket["timeout_mutants"] += 1
        else:
            bucket["execution_error_mutants"] += 1
    for bucket in per_operator.values():
        denominator = bucket["valid_mutants"]
        bucket["mutation_score"] = (
            round(100.0 * bucket["killed_mutants"] / denominator, 2)
            if denominator else None
        )
    return {
        "total_mutants_generated": generated,
        "valid_mutants": valid,
        "killed_mutants": killed,
        "survived_mutants": survived,
        "invalid_mutants": invalid,
        "timeout_mutants": timeout,
        "execution_error_mutants": execution_error,
        "mutation_score": score,
        "per_operator_breakdown": per_operator,
        # Additive agentic-pipeline schema; historical names above remain stable.
        "total_mutants": generated,
        "killed": killed,
        "survived": survived,
        "invalid": invalid,
        "timeout": timeout,
        "execution_error": execution_error,
        "operator_stats": per_operator,
    }


SCOPE_TESTID_RE = re.compile(
    r"(?:data-testid|data-test)\s*(?:=)?\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
SCOPE_QUOTED_RE = re.compile(r"[\"']([^\"'\n]{2,120})[\"']")
SCOPE_INDEXED_ID_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_.-]*?-\d+)\b"
)
SCOPE_NUMBER_RE = re.compile(r"(?<![\w])(?:[$€£]\s*)?\d+(?:\.\d+)?")


def _normalized_scope_values(values: Iterable[str]) -> list[str]:
    return sorted({
        re.sub(r"\s+", " ", str(value)).strip().lower()
        for value in values
        if str(value).strip()
    })


def extract_test_scope(
    scenario_text: str, test_code: str
) -> dict[str, Any]:
    """Extract concrete scenario/test literals without benchmark assumptions."""
    scenario = str(scenario_text or "")
    code = str(test_code or "")
    combined = scenario + "\n" + code
    test_ids = _normalized_scope_values(
        SCOPE_TESTID_RE.findall(combined)
    )
    quoted = _normalized_scope_values(SCOPE_QUOTED_RE.findall(scenario))
    indexed = _normalized_scope_values(
        SCOPE_INDEXED_ID_RE.findall(combined)
    )
    numbers = _normalized_scope_values(SCOPE_NUMBER_RE.findall(scenario))
    return {
        "data_testids": test_ids,
        "scenario_literals": quoted,
        "indexed_identifiers": indexed,
        "numeric_literals": numbers,
        "has_concrete_scope": bool(test_ids or quoted or indexed or numbers),
    }


def _identifier_family(value: str) -> str:
    return re.sub(r"\d+$", "#", value.lower())


def classify_mutant_relevance(
    mutant: MutationCandidate | MutationRecord | dict[str, Any],
    source: str,
    test_scope: dict[str, Any],
) -> dict[str, Any]:
    """Classify one mutant using concrete scenario and nearby source evidence."""
    if isinstance(mutant, dict):
        location = mutant.get("location", {})
        original = str(mutant.get("original_code", ""))
        source_file = str(mutant.get("source_file", ""))
    else:
        location = mutant.location
        original = mutant.original_code
        source_file = mutant.source_file
    infrastructure_reason = nonfunctional_exclusion_reason(mutant, source)
    if infrastructure_reason:
        return {
            "scope_relation": "OUT_OF_SCOPE",
            "scope_reason": infrastructure_reason,
            "source_scope": {
                "data_testids": [],
                "indexed_identifiers": [],
                "nearby_literals": [],
                "excerpt": "",
            },
            "exclusion_reason": infrastructure_reason,
        }
    start = int(location.get("start_offset", 0))
    end = int(location.get("end_offset", start))
    context = source[max(0, start - 240): min(len(source), end + 240)]
    if Path(source_file).suffix.lower() in {".html", ".htm"}:
        tag_start = source.rfind("<", 0, start + 1)
        tag_end = source.find(">", max(tag_start, 0))
        if tag_start >= 0 and tag_end >= 0:
            if start <= tag_end:
                context = source[tag_start:tag_end + 1]
            else:
                next_tag = source.find("<", start)
                context = source[
                    tag_start: next_tag if next_tag >= 0 else min(len(source), end + 160)
                ]
    source_testids = _normalized_scope_values(
        SCOPE_TESTID_RE.findall(context)
    )
    source_indexed = _normalized_scope_values(
        SCOPE_INDEXED_ID_RE.findall(context)
    )
    source_literals = _normalized_scope_values(
        SCOPE_QUOTED_RE.findall(context)
    )
    source_scope = {
        "data_testids": source_testids,
        "indexed_identifiers": source_indexed,
        "nearby_literals": source_literals[:20],
        "excerpt": re.sub(r"\s+", " ", context).strip()[:500],
    }
    if not test_scope.get("has_concrete_scope"):
        return {
            "scope_relation": "UNCERTAIN",
            "scope_reason": "The scenario exposes no concrete target or literal.",
            "source_scope": source_scope,
            "exclusion_reason": (
                "Requirement relevance could not be established deterministically."
            ),
        }

    target_ids = set(test_scope.get("data_testids", []))
    target_indexed = set(test_scope.get("indexed_identifiers", []))
    source_ids = set(source_testids)
    source_index_set = set(source_indexed)
    if target_ids & source_ids or target_indexed & source_index_set:
        return {
            "scope_relation": "RELEVANT",
            "scope_reason": "Nearby source identifiers match the active scenario target.",
            "source_scope": source_scope,
            "exclusion_reason": "",
        }

    target_families = {
        _identifier_family(item): item
        for item in target_ids | target_indexed
    }
    source_families = {
        _identifier_family(item): item
        for item in source_ids | source_index_set
    }
    shared_families = set(target_families) & set(source_families)
    if shared_families:
        return {
            "scope_relation": "OUT_OF_SCOPE",
            "scope_reason": (
                "The mutant targets a different indexed member of a "
                "scenario-targeted identifier family."
            ),
            "source_scope": source_scope,
            "exclusion_reason": (
                "Different indexed member from the active scenario target."
            ),
        }

    normalized_original = re.sub(
        r"\s+", " ", original.strip("\"'` \t\r\n")
    ).lower()
    target_literals = set(test_scope.get("scenario_literals", []))
    target_numbers = set(test_scope.get("numeric_literals", []))
    if (
        normalized_original
        and (
            normalized_original in target_literals
            or normalized_original in target_numbers
        )
    ):
        return {
            "scope_relation": "RELEVANT",
            "scope_reason": "The mutated literal is explicitly used by the scenario.",
            "source_scope": source_scope,
            "exclusion_reason": "",
        }

    if (target_ids or target_indexed) and (source_ids or source_index_set):
        return {
            "scope_relation": "OUT_OF_SCOPE",
            "scope_reason": "Nearby source identifiers do not match the scenario target.",
            "source_scope": source_scope,
            "exclusion_reason": (
                "Nearby source identifiers do not match the active scenario."
            ),
        }
    return {
        "scope_relation": "UNCERTAIN",
        "scope_reason": "No reliable target-level link or exclusion was found.",
        "source_scope": source_scope,
        "exclusion_reason": (
            "Requirement relevance could not be established deterministically."
        ),
    }


def calculate_relevant_mutation_metrics(
    records: Sequence[MutationRecord | dict[str, Any]],
) -> dict[str, Any]:
    def value(
        record: MutationRecord | dict[str, Any],
        key: str,
        default: Any = None,
    ) -> Any:
        return (
            record.get(key, default)
            if isinstance(record, dict)
            else getattr(record, key, default)
        )

    relevant = [
        record for record in records
        if bool(
            value(
                record,
                "included_in_relevant_score",
                value(record, "scope_relation") == "RELEVANT",
            )
        )
    ]
    killed = sum(value(item, "execution_verdict") == "KILLED" for item in relevant)
    survived = sum(value(item, "execution_verdict") == "SURVIVED" for item in relevant)
    invalid = sum(value(item, "execution_verdict") == "INVALID" for item in relevant)
    valid = killed + survived
    relevance_stats: dict[str, dict[str, int]] = {}
    for relation in ("RELEVANT", "OUT_OF_SCOPE", "UNCERTAIN"):
        matching = [
            item for item in records
            if value(item, "scope_relation") == relation
        ]
        relevance_stats[relation] = {
            verdict.lower(): sum(
                value(item, "execution_verdict") == verdict
                for item in matching
            )
            for verdict in (
                "KILLED", "SURVIVED", "INVALID", "TIMEOUT", "EXECUTION_ERROR"
            )
        }
        relevance_stats[relation]["total"] = len(matching)
        relevance_stats[relation]["included_in_raw_score"] = sum(
            bool(value(item, "included_in_raw_score", True))
            for item in matching
        )
        relevance_stats[relation]["included_in_relevant_score"] = sum(
            bool(
                value(
                    item,
                    "included_in_relevant_score",
                    value(item, "scope_relation") == "RELEVANT",
                )
            )
            for item in matching
        )
    return {
        "mutation_scope_status": (
            "SCOPE_AWARE" if valid else "NO_RELEVANT_MUTANTS"
        ),
        "relevant_mutation_score": (
            round(100.0 * killed / valid, 2) if valid else None
        ),
        "relevant_mutants_total": valid,
        "relevant_mutants_killed": killed,
        "relevant_mutants_survived": survived,
        "relevant_mutants_inconclusive": invalid,
        "out_of_scope_mutants_total": sum(
            value(item, "scope_relation") == "OUT_OF_SCOPE"
            for item in records
        ),
        "out_of_scope_mutants_survived": sum(
            value(item, "scope_relation") == "OUT_OF_SCOPE"
            and value(item, "execution_verdict") == "SURVIVED"
            for item in records
        ),
        "uncertain_mutants_total": sum(
            value(item, "scope_relation") == "UNCERTAIN"
            for item in records
        ),
        "uncertain_mutants_survived": sum(
            value(item, "scope_relation") == "UNCERTAIN"
            and value(item, "execution_verdict") == "SURVIVED"
            for item in records
        ),
        "requirement_relevance_breakdown": relevance_stats,
    }


def _safe_cleanup(workspace: Path, cleanup_root: Path) -> None:
    workspace = workspace.resolve()
    cleanup_root = cleanup_root.resolve()
    try:
        inside = os.path.commonpath([str(workspace), str(cleanup_root)]) == str(cleanup_root)
    except ValueError:
        inside = False
    if inside and workspace != cleanup_root:
        shutil.rmtree(workspace, ignore_errors=True)


def run_mutation_campaign(
    state: dict[str, Any],
    test_code: str,
    reference_dir: Path,
    *,
    create_workspace: Callable[..., Any],
    write_test_project: Callable[..., None],
    run_test: Callable[..., dict[str, Any]],
    report_filename: str,
    run_namespace: str,
    cleanup_root: Path,
) -> dict[str, Any]:
    """Execute one isolated generated-test run per selected mutant."""
    total_limit = max(0, int(state.get("max_mutants", 20)))
    per_file_limit = max(0, int(state.get("mutation_max_mutants_per_file", 5)))
    seed = int(state.get("mutation_seed", 1337))
    include_patterns = tuple(state.get("mutation_include_patterns") or ())
    exclude_patterns = tuple(
        state.get("mutation_exclude_patterns") or DEFAULT_EXCLUDE_PATTERNS
    )
    timeout = max(1, int(state.get("mutant_timeout_seconds", 45)))
    raw_validation_command = state.get(
        "mutation_project_validation_command"
    ) or ()
    project_validation_command = tuple(
        shlex.split(raw_validation_command, posix=os.name != "nt")
        if isinstance(raw_validation_command, str)
        else raw_validation_command
    )
    keep_workspaces = bool(state.get("mutation_keep_workspaces", False))

    proposals = state.get("mutation_proposals") or []
    planning_status = str(
        state.get("mutation_planning_status", "NOT_RUN")
    )
    test_scope = extract_test_scope(
        str(state.get("excutable_test_test_case", "")), test_code
    )
    proposal_lifecycle: list[dict[str, Any]] = []
    candidates: list[MutationCandidate] = []
    if planning_status == "PLANNED" and proposals:
        from e2e_eval.dynamic.mutation_operators import (
            select_proposed_candidates_with_lifecycle,
        )

        candidates, proposal_lifecycle = (
            select_proposed_candidates_with_lifecycle(
            reference_dir,
            proposals,
            max_total_mutants=total_limit,
        ))
    selected_keys = {
        (
            candidate.source_file,
            candidate.location["start_offset"],
            candidate.location["end_offset"],
            candidate.operator,
        )
        for candidate in candidates
    }
    fallback = discover_business_mutations(
        reference_dir,
        test_scope=test_scope,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        max_mutants_per_file=per_file_limit,
        max_total_mutants=max(0, total_limit - len(candidates)),
        seed=seed,
        excluded_keys=selected_keys,
    )
    from e2e_eval.dynamic.mutation_operators import default_operator_registry

    fallback_registry = default_operator_registry()
    for index, candidate in enumerate(fallback, start=1):
        proposal_id = f"F{index:04d}"
        candidates.append(replace(
            candidate,
            proposal_id=proposal_id,
            candidate_source="deterministic_fallback",
        ))
        proposal_lifecycle.append({
            "proposal_id": proposal_id,
            "origin": "deterministic_fallback",
            "mutation_target": candidate.mutation_description,
            "source_file": candidate.source_file,
            "operator_family": fallback_registry.family_for_operator(
                candidate.operator
            ),
            "status": "GENERATED",
            "status_history": ["PROPOSED", "ACCEPTED", "GENERATED"],
            "rejection_reason": "",
            "mutant_id": "",
        })
    candidates = [
        replace(candidate, mutant_id=f"M{index:04d}")
        for index, candidate in enumerate(candidates, start=1)
    ]
    id_by_proposal = {
        candidate.proposal_id: candidate.mutant_id
        for candidate in candidates
    }
    for lifecycle in proposal_lifecycle:
        if lifecycle["status"] == "GENERATED":
            lifecycle["mutant_id"] = id_by_proposal.get(
                lifecycle["proposal_id"], ""
            )
    logger.info(
        "Mutation campaign case=%s candidates=%d seed=%d max_total=%d",
        state.get("case_uid", "unknown"),
        len(candidates),
        seed,
        total_limit,
    )
    if not candidates:
        empty_metrics = {
            "total_mutants_generated": 0,
            "valid_mutants": 0,
            "killed_mutants": 0,
            "survived_mutants": 0,
            "invalid_mutants": 0,
            "timeout_mutants": 0,
            "execution_error_mutants": 0,
            "mutation_score": None,
            "per_operator_breakdown": {},
            "total_mutants": 0,
            "killed": 0,
            "survived": 0,
            "invalid": 0,
            "timeout": 0,
            "execution_error": 0,
            "operator_stats": {},
        }
        empty_relevance = {
            relation: {
                "killed": 0,
                "survived": 0,
                "invalid": 0,
                "timeout": 0,
                "execution_error": 0,
                "total": 0,
                "included_in_raw_score": 0,
                "included_in_relevant_score": 0,
            }
            for relation in ("RELEVANT", "OUT_OF_SCOPE", "UNCERTAIN")
        }
        case_uid = re.sub(
            r"[^A-Za-z0-9_.-]+", "_",
            str(state.get("case_uid", "case")),
        )
        report_dir = Path(
            state.get("mutation_report_root")
            or cleanup_root.parent / "results"
        ) / case_uid
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / report_filename
        report_path.write_text(
            json.dumps(
                sanitize_export_payload({
                    **empty_metrics,
                    "requirement_relevance_breakdown": empty_relevance,
                    "planning": {
                        "status": planning_status,
                        "proposals": proposals,
                        "proposal_lifecycle": proposal_lifecycle,
                        "candidate_sources": {},
                        "detail": state.get(
                            "mutation_planning_detail", ""
                        ),
                    },
                    "baseline": {
                        "execution_status": state.get("execution_status"),
                        "command_run": state.get(
                            "execution_command", ""
                        ),
                        "artifact_dir": state.get(
                            "execution_artifact_dir", ""
                        ),
                    },
                    "records": [],
                }),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "mutation_status": "NO_MUTANTS",
            **empty_metrics,
            "mutation_metrics": empty_metrics,
            "mutants_total": 0,
            "mutants_killed": 0,
            "mutants_survived": 0,
            "mutants_timeout": 0,
            "mutants_inconclusive": 0,
            "mutation_scope_status": "NO_RELEVANT_MUTANTS",
            "relevant_mutation_score": None,
            "relevant_mutants_total": 0,
            "relevant_mutants_killed": 0,
            "relevant_mutants_survived": 0,
            "surviving_mutant_report": [],
            "surviving_mutants": [],
            "killed_mutant_report": [],
            "mutation_records": [],
            "mutation_proposal_lifecycle": proposal_lifecycle,
            "mutation_candidate_sources": {},
            "requirement_relevance_breakdown": empty_relevance,
            "mutation_report_path": str(report_path),
            "mutation_detail": "No applicable general mutation candidates found.",
        }

    from e2e_eval.dynamic.mutation_runner import run_single_mutant

    records: list[MutationRecord] = []
    for mutant in candidates:
        prepared = None
        try:
            prepared = create_workspace(
                state,
                f"{run_namespace}_mutant_{mutant.mutant_id}",
                reference_dir=reference_dir,
            )
            record = run_single_mutant(
                state=state,
                patch=mutant,
                prepared=prepared,
                test_code=test_code,
                timeout_seconds=timeout,
                project_validation_command=project_validation_command,
                apply_patch=apply_mutation,
                validate_project=validate_mutated_project,
                write_test_project=write_test_project,
                run_test=run_test,
            )
            records.append(MutationRecord(**record))
        except Exception as exc:
            records.append(MutationRecord(
                **asdict(mutant),
                execution_verdict="EXECUTION_ERROR",
                execution_status="MUTATION_HARNESS_ERROR",
                stdout_summary="",
                stderr_summary=repr(exc),
                command_run="",
                environment_metadata={},
                validation_result={
                    "valid": False,
                    "status": "MUTATION_HARNESS_ERROR",
                    "failure_reason": repr(exc),
                },
                duration_seconds=0.0,
                artifact_dir=str(
                    getattr(prepared, "artifact_dir", "")
                ),
            ))
        finally:
            if not keep_workspaces and prepared is not None:
                _safe_cleanup(prepared.workspace, cleanup_root)
        logger.info(
            "Mutation case=%s mutant=%s verdict=%s status=%s",
            state.get("case_uid", "unknown"),
            records[-1].mutant_id,
            records[-1].execution_verdict,
            records[-1].execution_status,
        )
        lifecycle = next(
            (
                item for item in proposal_lifecycle
                if item.get("proposal_id") == records[-1].proposal_id
            ),
            None,
        )
        if lifecycle is not None:
            if records[-1].execution_verdict == "INVALID":
                lifecycle["status"] = "SKIPPED"
                lifecycle["status_history"].append("SKIPPED")
                lifecycle["rejection_reason"] = (
                    records[-1].validation_result.get("failure_reason")
                    or records[-1].execution_status
                )
            else:
                lifecycle["status"] = "EXECUTED"
                lifecycle["status_history"].append("EXECUTED")

    test_scope = extract_test_scope(
        str(state.get("excutable_test_test_case", "")), test_code
    )
    source_cache: dict[str, str] = {}
    for record in records:
        if record.source_file not in source_cache:
            source_cache[record.source_file] = (
                reference_dir / record.source_file
            ).read_text(encoding="utf-8", errors="replace")
        relevance = classify_mutant_relevance(
            record, source_cache[record.source_file], test_scope
        )
        record.scope_relation = relevance["scope_relation"]
        record.scope_reason = relevance["scope_reason"]
        record.source_scope = relevance["source_scope"]
        policy_exclusion = nonfunctional_exclusion_reason(
            record, source_cache[record.source_file]
        )
        scoreable_verdict = record.execution_verdict in {
            "KILLED", "SURVIVED"
        }
        record.included_in_raw_score = (
            scoreable_verdict and not policy_exclusion
        )
        record.included_in_relevant_score = (
            record.included_in_raw_score
            and record.scope_relation == "RELEVANT"
        )
        if policy_exclusion:
            record.exclusion_reason = policy_exclusion
        elif not scoreable_verdict:
            record.exclusion_reason = (
                f"Verdict {record.execution_verdict} is excluded from scoring."
            )
        elif not record.included_in_relevant_score:
            record.exclusion_reason = (
                relevance.get("exclusion_reason")
                or record.scope_reason
            )
        else:
            record.exclusion_reason = ""

    metrics = calculate_mutation_metrics(records)
    scope_metrics = calculate_relevant_mutation_metrics(records)
    serialized = [asdict(record) for record in records]
    candidate_sources: dict[str, int] = {}
    for record in records:
        candidate_sources[record.candidate_source] = (
            candidate_sources.get(record.candidate_source, 0) + 1
        )
    survivors = [
        record for record in serialized
        if record["execution_verdict"] == "SURVIVED"
    ]
    case_uid = re.sub(
        r"[^A-Za-z0-9_.-]+", "_", str(state.get("case_uid", "case"))
    )
    report_dir = Path(state.get("mutation_report_root") or cleanup_root.parent / "results") / case_uid
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / report_filename
    report = {
        "configuration": {
            "seed": seed,
            "max_mutants_per_file": per_file_limit,
            "max_total_mutants": total_limit,
            "include_patterns": include_patterns,
            "exclude_patterns": exclude_patterns,
            "mutant_timeout_seconds": timeout,
            "project_validation_command": project_validation_command,
            "planning_status": planning_status,
        },
        "planning": {
            "status": planning_status,
            "proposals": proposals,
            "proposal_lifecycle": proposal_lifecycle,
            "candidate_sources": candidate_sources,
            "detail": state.get("mutation_planning_detail", ""),
        },
        "environment_metadata": {
            "python_version": sys.version,
            "platform": platform.platform(),
            "executable": sys.executable,
        },
        "baseline": {
            "execution_status": state.get("execution_status"),
            "command_run": state.get("execution_command", ""),
            "artifact_dir": state.get("execution_artifact_dir", ""),
        },
        **metrics,
        **scope_metrics,
        "test_scope": test_scope,
        "records": serialized,
        "surviving_mutant_report": survivors,
        "requirement_relevant_survivors": [],
    }
    report_path.write_text(
        json.dumps(
            sanitize_export_payload(report),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    timed_out = metrics["timeout_mutants"]
    killed_descriptions = [
        f"{item.mutant_id} {item.operator} {item.source_file}:{item.location['line']}"
        for item in records if item.execution_verdict == "KILLED"
    ]
    survivor_descriptions = [
        f"{item.mutant_id} {item.operator} {item.source_file}:{item.location['line']}"
        for item in records if item.execution_verdict == "SURVIVED"
    ]
    relevant_survivor_descriptions = [
        f"{item.mutant_id} {item.operator} {item.source_file}:{item.location['line']}"
        for item in records
        if item.execution_verdict == "SURVIVED"
        and item.scope_relation == "RELEVANT"
    ]
    out_of_scope_survivor_descriptions = [
        f"{item.mutant_id} {item.operator} {item.source_file}:{item.location['line']}"
        for item in records
        if item.execution_verdict == "SURVIVED"
        and item.scope_relation == "OUT_OF_SCOPE"
    ]
    uncertain_survivor_descriptions = [
        f"{item.mutant_id} {item.operator} {item.source_file}:{item.location['line']}"
        for item in records
        if item.execution_verdict == "SURVIVED"
        and item.scope_relation == "UNCERTAIN"
    ]
    return {
        "mutation_status": (
            "PASSED" if metrics["valid_mutants"] else "NO_VALID_MUTANTS"
        ),
        **metrics,
        "mutation_metrics": metrics,
        # Backward-compatible graph/export fields.
        "mutants_total": metrics["total_mutants_generated"],
        "mutants_killed": metrics["killed_mutants"],
        "mutants_survived": metrics["survived_mutants"],
        "mutants_timeout": timed_out,
        "mutants_inconclusive": (
            metrics["invalid_mutants"]
            + metrics["timeout_mutants"]
            + metrics["execution_error_mutants"]
        ),
        **scope_metrics,
        "relevant_mutants_timeout": timed_out,
        "test_scope": test_scope,
        "mutation_report_path": str(report_path),
        "surviving_mutants": survivor_descriptions,
        "killed_mutant_report": killed_descriptions,
        "scope_relevant_surviving_mutants": relevant_survivor_descriptions,
        "scope_out_of_scope_surviving_mutants": out_of_scope_survivor_descriptions,
        "scope_uncertain_surviving_mutants": uncertain_survivor_descriptions,
        "mutation_records": serialized,
        "mutation_proposal_lifecycle": proposal_lifecycle,
        "mutation_candidate_sources": candidate_sources,
        "surviving_mutant_report": survivors,
        "requirement_relevant_survivors": [],
        "mutation_detail": (
            "General source mutation score excludes INVALID, TIMEOUT, and "
            "EXECUTION_ERROR mutants. "
            f"Raw score={metrics['killed_mutants']}/{metrics['valid_mutants']}*100; "
            f"scenario-relevant score={scope_metrics['relevant_mutation_score']}."
        ),
    }
