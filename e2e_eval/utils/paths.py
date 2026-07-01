"""Portable path handling for public evaluator exports.

Runtime code keeps native absolute paths. These helpers are intended only for
CSV, JSON, and other public serialization boundaries.
"""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any


_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_QUOTED_ABSOLUTE = re.compile(
    r'(?P<quote>["\'])(?P<path>(?:[A-Za-z]:[\\/]|/)[^"\']+)(?P=quote)'
)
_WINDOWS_EMBEDDED = re.compile(r"(?<![\w])(?P<path>[A-Za-z]:[\\/][^\s,;]+)")
_POSIX_EMBEDDED = re.compile(
    r"(?<![\w])(?P<path>/(?:home|Users|tmp|var|opt)/[^\s,;]+)"
)
_PATH_KEYS = re.compile(
    r"(?:path|dir|workspace|artifact|file|source|report|command)",
    re.IGNORECASE,
)
_KNOWN_RELATIVE_ROOTS = (
    "artifacts/",
    "data/",
    "E2E_data/",
    "workspace/",
)


def project_root() -> Path:
    """Return the repository root independently of the process CWD."""
    return Path(__file__).resolve().parents[2]


def _is_windows_absolute(value: str) -> bool:
    return bool(_WINDOWS_ABSOLUTE.match(value))


def _external_name(value: str, *, windows: bool) -> str:
    path = PureWindowsPath(value) if windows else PurePosixPath(value)
    name = path.name or "path"
    return f"<external>/{name}"


def _relative_parts(
    path_parts: tuple[str, ...],
    root_parts: tuple[str, ...],
    *,
    case_insensitive: bool,
) -> tuple[str, ...] | None:
    if len(path_parts) < len(root_parts):
        return None
    if case_insensitive:
        candidate = tuple(part.casefold() for part in path_parts[:len(root_parts)])
        expected = tuple(part.casefold() for part in root_parts)
    else:
        candidate = path_parts[:len(root_parts)]
        expected = root_parts
    return path_parts[len(root_parts):] if candidate == expected else None


def to_portable_path(
    path: str | Path,
    *,
    root: str | Path | None = None,
) -> str:
    """Convert one path to a project-relative POSIX representation.

    Absolute paths outside ``root`` intentionally retain only their basename.
    """
    value = str(path)
    root_value = str(root if root is not None else project_root())

    if _is_windows_absolute(value):
        candidate = PureWindowsPath(value)
        if _is_windows_absolute(root_value):
            relative = _relative_parts(
                candidate.parts,
                PureWindowsPath(root_value).parts,
                case_insensitive=True,
            )
            if relative is not None:
                return PurePosixPath(*relative).as_posix() or "."
        return _external_name(value, windows=True)

    if value.startswith("/"):
        candidate = PurePosixPath(value)
        if root_value.startswith("/"):
            relative = _relative_parts(
                candidate.parts,
                PurePosixPath(root_value).parts,
                case_insensitive=False,
            )
            if relative is not None:
                return PurePosixPath(*relative).as_posix() or "."
        return _external_name(value, windows=False)

    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return PurePosixPath(normalized).as_posix()


def _looks_like_path(value: str, key: str | None) -> bool:
    if not value or "://" in value:
        return False
    if _is_windows_absolute(value) or value.startswith("/"):
        return True
    normalized = value.replace("\\", "/")
    if (
        "\\" in value
        and not any(character.isspace() for character in value)
    ) or normalized.startswith(("./", "../")):
        return True
    if any(normalized.startswith(prefix) for prefix in _KNOWN_RELATIVE_ROOTS):
        return True
    return bool(key and _PATH_KEYS.search(key) and "/" in normalized)


def _sanitize_string(
    value: str,
    *,
    root: str | Path | None,
    key: str | None,
) -> str:
    stripped = value.strip()
    if stripped.startswith(("{", "[")):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, (dict, list)):
            return json.dumps(
                sanitize_export_payload(decoded, root=root),
                ensure_ascii=False,
            )
    if "://" in value:
        return value
    if _looks_like_path(value, key):
        return to_portable_path(value, root=root)

    # Commands and logs can contain absolute paths inside a larger string.
    def replace_quoted(match: re.Match[str]) -> str:
        quote = match.group("quote")
        portable = to_portable_path(match.group("path"), root=root)
        return f"{quote}{portable}{quote}"

    def replace_unquoted(match: re.Match[str]) -> str:
        raw = match.group("path")
        trailing = ""
        while raw and raw[-1] in ")]}":
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        return to_portable_path(raw, root=root) + trailing

    sanitized = _QUOTED_ABSOLUTE.sub(replace_quoted, value)
    sanitized = _WINDOWS_EMBEDDED.sub(replace_unquoted, sanitized)
    sanitized = _POSIX_EMBEDDED.sub(replace_unquoted, sanitized)
    return sanitized


def sanitize_export_payload(
    payload: Any,
    *,
    root: str | Path | None = None,
    _key: str | None = None,
) -> Any:
    """Recursively sanitize paths while preserving non-path values."""
    if payload is None:
        return None
    if isinstance(payload, PurePath):
        return to_portable_path(payload, root=root)
    if isinstance(payload, dict):
        return {
            (
                _sanitize_string(key, root=root, key=None)
                if isinstance(key, str)
                else key
            ): sanitize_export_payload(
                value,
                root=root,
                _key=str(key),
            )
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [
            sanitize_export_payload(value, root=root, _key=_key)
            for value in payload
        ]
    if isinstance(payload, tuple):
        return tuple(
            sanitize_export_payload(value, root=root, _key=_key)
            for value in payload
        )
    if isinstance(payload, str):
        return _sanitize_string(payload, root=root, key=_key)
    return payload


def sanitize_dataframe_for_export(
    dataframe: Any,
    *,
    root: str | Path | None = None,
) -> Any:
    """Return a copy whose cells are safe for public serialization."""
    portable = dataframe.copy(deep=True)
    for column in portable.columns:
        portable[column] = portable[column].map(
            lambda value: sanitize_export_payload(value, root=root)
        )
    return portable
