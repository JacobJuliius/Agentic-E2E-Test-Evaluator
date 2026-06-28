"""Small shared deterministic utilities used by local evaluator tools."""
from __future__ import annotations

import re
from typing import Any, Iterable


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "case"


def tail(text: str | bytes | None, limit: int = 6000) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    return text[-limit:] if len(text) > limit else text


def normalize_patterns(
    value: Any, defaults: Iterable[str] = ()
) -> list[str]:
    if value is None or value == "":
        return list(defaults)
    values = value.split(",") if isinstance(value, str) else value
    normalized = [
        str(item).strip() for item in values if str(item).strip()
    ]
    return normalized or list(defaults)

