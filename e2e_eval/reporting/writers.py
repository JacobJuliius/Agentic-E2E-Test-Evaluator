"""CSV/JSON report writers with a saved reproducibility manifest."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from e2e_eval.utils.paths import (
    sanitize_dataframe_for_export,
    sanitize_export_payload,
)


def join_cell(value: Any) -> str:
    value = sanitize_export_payload(value)
    if value is None or value == "":
        return "None"
    if isinstance(value, list):
        return "; ".join(str(item) for item in value) or "None"
    return str(value)


def json_cell(value: Any) -> str:
    value = sanitize_export_payload(value)
    if value is None:
        return "[]"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def summarize_proposal_lifecycle(lifecycle: Any) -> dict[str, int]:
    """Count proposal outcomes for compact report columns."""
    summary: dict[str, int] = {}
    for item in lifecycle or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "UNKNOWN")
        summary[status] = summary.get(status, 0) + 1
    return summary


def write_evaluation_reports(
    dataframe: Any,
    output_csv: str | Path,
    *,
    config: Any | None = None,
) -> dict[str, str]:
    csv_path = Path(output_csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path = csv_path.with_suffix(".json")
    portable = sanitize_dataframe_for_export(dataframe)
    portable.to_csv(csv_path, index=False, encoding="utf-8-sig")
    portable.to_json(
        json_path,
        orient="records",
        force_ascii=False,
        indent=2,
    )
    paths = {"csv": str(csv_path), "json": str(json_path)}
    if config is not None:
        manifest = csv_path.with_name(csv_path.stem + ".config.json")
        config.write_manifest(manifest)
        paths["config"] = str(manifest)
    return paths

