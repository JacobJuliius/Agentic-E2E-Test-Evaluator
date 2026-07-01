"""Tests for centralized portable-path export sanitization."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from e2e_eval.reporting import write_evaluation_reports
from e2e_eval.utils.paths import (
    sanitize_export_payload,
    to_portable_path,
)


def test_windows_absolute_path_inside_project_root():
    result = to_portable_path(
        r"G:\repo\artifacts\dynamic\workspaces\case-1",
        root=r"G:\repo",
    )
    assert result == "artifacts/dynamic/workspaces/case-1"


def test_posix_absolute_path_inside_project_root():
    result = to_portable_path(
        "/opt/evaluator/E2E_data/E2ESD_Bench_02/index.html",
        root="/opt/evaluator",
    )
    assert result == "E2E_data/E2ESD_Bench_02/index.html"


def test_absolute_path_outside_project_root_is_redacted():
    assert to_portable_path(
        r"C:\Users\person\secret\coverage.json",
        root=r"G:\repo",
    ) == "<external>/coverage.json"
    assert to_portable_path(
        "/home/person/private/report.json",
        root="/opt/evaluator",
    ) == "<external>/report.json"


def test_nested_payload_sanitizes_dicts_lists_and_path_keys():
    payload = {
        "artifact_paths": {
            "coverage": r"G:\repo\artifacts\coverage\coverage.json",
        },
        "source_files_measured": [
            r"G:\repo\E2E_data\E2ESD_Bench_02\script.js",
            r"C:\Users\person\outside.py",
        ],
        r"G:\repo\artifacts\dynamic\report.json": {
            "workspace_dir": r"G:\repo\artifacts\dynamic\workspaces\case",
        },
    }

    result = sanitize_export_payload(payload, root=r"G:\repo")

    assert result["artifact_paths"]["coverage"] == (
        "artifacts/coverage/coverage.json"
    )
    assert result["source_files_measured"] == [
        "E2E_data/E2ESD_Bench_02/script.js",
        "<external>/outside.py",
    ]
    assert "artifacts/dynamic/report.json" in result


def test_none_non_path_and_existing_relative_paths():
    payload = {
        "none": None,
        "rationale": "No branch evidence was available.",
        "relative_path": r"artifacts\reports\result.json",
        "url": "https://example.test/source",
    }

    result = sanitize_export_payload(payload, root=r"G:\repo")

    assert result["none"] is None
    assert result["rationale"] == "No branch evidence was available."
    assert result["relative_path"] == "artifacts/reports/result.json"
    assert result["url"] == "https://example.test/source"


def test_commands_and_logs_redact_embedded_absolute_paths():
    payload = {
        "command": (
            r'"G:\repo\.venv\Scripts\python.exe" --report '
            r'"G:\repo\artifacts\coverage\coverage.json"'
        ),
        "stderr": (
            r'File "C:\Users\person\private\runner.py", line 4'
        ),
    }

    result = sanitize_export_payload(payload, root=r"G:\repo")

    assert "G:\\" not in result["command"]
    assert "C:\\Users\\" not in result["stderr"]
    assert "artifacts/coverage/coverage.json" in result["command"]
    assert "<external>/runner.py" in result["stderr"]


def test_csv_and_json_writer_sanitize_nested_and_json_string_cells(
    tmp_path: Path,
):
    root = Path(__file__).resolve().parents[1]
    internal = root / "artifacts" / "dynamic" / "workspaces" / "case"
    outside = tmp_path / "external" / "report.json"
    nested = {
        "workspace": str(internal),
        "report_path": str(outside),
    }
    frame = pd.DataFrame([{
        "workspace_dir": str(internal),
        "nested": nested,
        "json_cell": json.dumps(nested),
        "message": "ordinary text",
    }])

    paths = write_evaluation_reports(frame, tmp_path / "result.csv")
    csv_text = Path(paths["csv"]).read_text(encoding="utf-8-sig")
    json_payload = json.loads(
        Path(paths["json"]).read_text(encoding="utf-8")
    )[0]

    assert str(root) not in csv_text
    assert str(tmp_path) not in csv_text
    assert json_payload["workspace_dir"] == (
        "artifacts/dynamic/workspaces/case"
    )
    assert json_payload["nested"]["report_path"] == "<external>/report.json"
    assert str(root) not in json_payload["json_cell"]
    assert json_payload["message"] == "ordinary text"
