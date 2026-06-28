"""Compatibility exports for the validated reference resolver."""

from reference_resolver import (
    derive_project_identifier,
    resolve_project_source,
    resolve_reference_source,
    validate_source_directory,
)

__all__ = [
    "derive_project_identifier",
    "resolve_project_source",
    "resolve_reference_source",
    "validate_source_directory",
]
