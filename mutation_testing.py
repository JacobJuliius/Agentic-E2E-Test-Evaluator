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
from dataclasses import asdict, dataclass, replace
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Protocol, Sequence

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


@dataclass(frozen=True)
class MutationCandidate:
    mutant_id: str
    operator: str
    source_file: str
    location: dict[str, int]
    original_code: str
    replacement_code: str
    mutation_description: str


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
                command = [node, "--check", str(target)]
                commands.append(subprocess.list2cmdline(command))
                completed = subprocess_runner(
                    command,
                    cwd=app_dir,
                    capture_output=True,
                    text=True,
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
    if execution_status == "PASSED":
        return "SURVIVED"
    if execution_status == "TEST_FAILED":
        return "KILLED"
    return "INVALID"


def calculate_mutation_metrics(
    records: Sequence[MutationRecord | dict[str, Any]],
) -> dict[str, Any]:
    def value(record: MutationRecord | dict[str, Any], key: str) -> Any:
        return record.get(key) if isinstance(record, dict) else getattr(record, key)

    generated = len(records)
    killed = sum(value(item, "execution_verdict") == "KILLED" for item in records)
    survived = sum(value(item, "execution_verdict") == "SURVIVED" for item in records)
    invalid = sum(value(item, "execution_verdict") == "INVALID" for item in records)
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
                "mutation_score": None,
            },
        )
        bucket["total_mutants_generated"] += 1
        if verdict == "KILLED":
            bucket["killed_mutants"] += 1
            bucket["valid_mutants"] += 1
        elif verdict == "SURVIVED":
            bucket["survived_mutants"] += 1
            bucket["valid_mutants"] += 1
        else:
            bucket["invalid_mutants"] += 1
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
        "mutation_score": score,
        "per_operator_breakdown": per_operator,
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

    candidates = discover_mutations(
        reference_dir,
        include_patterns=include_patterns,
        exclude_patterns=exclude_patterns,
        max_mutants_per_file=per_file_limit,
        max_total_mutants=total_limit,
        seed=seed,
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
            "mutation_score": None,
            "per_operator_breakdown": {},
        }
        return {
            "mutation_status": "NO_MUTANTS",
            **empty_metrics,
            "mutation_metrics": empty_metrics,
            "mutants_total": 0,
            "mutants_killed": 0,
            "mutants_survived": 0,
            "mutants_timeout": 0,
            "mutants_inconclusive": 0,
            "mutation_scope_status": "GENERAL_MUTATION_SET",
            "relevant_mutation_score": None,
            "relevant_mutants_total": 0,
            "relevant_mutants_killed": 0,
            "relevant_mutants_survived": 0,
            "surviving_mutant_report": [],
            "surviving_mutants": [],
            "killed_mutant_report": [],
            "mutation_records": [],
            "mutation_report_path": "",
            "mutation_detail": "No applicable general mutation candidates found.",
        }

    records: list[MutationRecord] = []
    for mutant in candidates:
        prepared = create_workspace(
            state,
            f"{run_namespace}_mutant_{mutant.mutant_id}",
            reference_dir=reference_dir,
        )
        try:
            apply_mutation(prepared.app_dir, mutant)
            validation = validate_mutated_project(
                prepared.app_dir,
                mutant,
                timeout_seconds=timeout,
                project_validation_command=project_validation_command,
            )
            if not validation["valid"]:
                records.append(MutationRecord(
                    **asdict(mutant),
                    execution_verdict="INVALID",
                    execution_status=validation["status"],
                    stdout_summary="",
                    stderr_summary=validation["failure_reason"],
                    command_run="; ".join(validation["commands"]),
                    environment_metadata={},
                    validation_result=validation,
                    duration_seconds=0.0,
                    artifact_dir=str(prepared.artifact_dir),
                ))
                logger.info(
                    "Mutation case=%s mutant=%s verdict=INVALID status=%s",
                    state.get("case_uid", "unknown"),
                    mutant.mutant_id,
                    validation["status"],
                )
                continue

            write_test_project(
                prepared,
                str(state["excutable_test_test_case"]),
                test_code,
            )
            execution = run_test(prepared, state, timeout)
            execution_status = str(execution.get("execution_status", "HARNESS_ERROR"))
            verdict = verdict_for_execution(execution_status)
            records.append(MutationRecord(
                **asdict(mutant),
                execution_verdict=verdict,
                execution_status=execution_status,
                stdout_summary=str(execution.get("execution_stdout_tail", "")),
                stderr_summary=str(execution.get("execution_stderr_tail", "")),
                command_run=str(execution.get("execution_command", "")),
                environment_metadata=dict(
                    execution.get("execution_environment", {})
                ),
                validation_result=validation,
                duration_seconds=float(
                    execution.get("execution_duration_seconds", 0.0)
                ),
                artifact_dir=str(prepared.artifact_dir),
            ))
        except Exception as exc:
            records.append(MutationRecord(
                **asdict(mutant),
                execution_verdict="INVALID",
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
                artifact_dir=str(getattr(prepared, "artifact_dir", "")),
            ))
        finally:
            if not keep_workspaces:
                _safe_cleanup(prepared.workspace, cleanup_root)
        logger.info(
            "Mutation case=%s mutant=%s verdict=%s status=%s",
            state.get("case_uid", "unknown"),
            records[-1].mutant_id,
            records[-1].execution_verdict,
            records[-1].execution_status,
        )

    metrics = calculate_mutation_metrics(records)
    serialized = [asdict(record) for record in records]
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
        "records": serialized,
        "surviving_mutant_report": survivors,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    timed_out = sum(
        record.execution_status == "TIMEOUT" for record in records
    )
    killed_descriptions = [
        f"{item.mutant_id} {item.operator} {item.source_file}:{item.location['line']}"
        for item in records if item.execution_verdict == "KILLED"
    ]
    survivor_descriptions = [
        f"{item.mutant_id} {item.operator} {item.source_file}:{item.location['line']}"
        for item in records if item.execution_verdict == "SURVIVED"
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
        "mutants_inconclusive": metrics["invalid_mutants"],
        "mutation_scope_status": "GENERAL_MUTATION_SET",
        "relevant_mutation_score": metrics["mutation_score"],
        "relevant_mutants_total": metrics["valid_mutants"],
        "relevant_mutants_killed": metrics["killed_mutants"],
        "relevant_mutants_survived": metrics["survived_mutants"],
        "relevant_mutants_timeout": timed_out,
        "relevant_mutants_inconclusive": metrics["invalid_mutants"],
        "out_of_scope_mutants_total": 0,
        "out_of_scope_mutants_survived": 0,
        "uncertain_mutants_total": 0,
        "uncertain_mutants_survived": 0,
        "test_scope": {"mode": "GENERAL_SOURCE_MUTATION"},
        "mutation_report_path": str(report_path),
        "surviving_mutants": survivor_descriptions,
        "killed_mutant_report": killed_descriptions,
        "scope_relevant_surviving_mutants": survivor_descriptions,
        "scope_out_of_scope_surviving_mutants": [],
        "scope_uncertain_surviving_mutants": [],
        "mutation_records": serialized,
        "surviving_mutant_report": survivors,
        "mutation_detail": (
            "General source mutation score excludes INVALID mutants. "
            f"Score={metrics['killed_mutants']}/{metrics['valid_mutants']}*100."
        ),
    }
