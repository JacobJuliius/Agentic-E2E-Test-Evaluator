"""Pure-Python dynamic evaluation agents for Selenium/Behave E2E tests.

This module intentionally does not require Node.js, npm, Istanbul, or Stryker.

It provides three local (non-LLM) LangGraph nodes:
1. execution_agent: runs the generated Behave/Selenium test against the reference app.
2. dynamic_coverage_agent: computes *BDD dynamic coverage* from Behave's JSON report.
   It measures successful execution of setup/action/oracle BDD steps; it is NOT JS branch coverage.
3. mutation_agent: applies a bounded set of deterministic mutants to the reference app's
   HTML/JavaScript source and re-runs the generated test.

All executions use an isolated workspace. The evaluator injects only a browser harness
(headless Chrome and `file://index.html` path resolution), not changes to the test steps.
"""
from __future__ import annotations

import ast
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from e2e_eval.runtime.utils import (
    as_bool as _as_bool,
    safe_name as _safe_name,
    tail as _tail,
)
from reference_resolver import resolve_reference_source

DEFAULT_EXECUTION_TIMEOUT = int(os.getenv("E2E_EXECUTION_TIMEOUT_SECONDS", "90"))
DEFAULT_MUTANT_TIMEOUT = int(os.getenv("E2E_MUTANT_TIMEOUT_SECONDS", "45"))
DEFAULT_MAX_MUTANTS = int(os.getenv("E2E_MAX_MUTANTS", "5"))
ARTIFACT_ROOT = Path(os.getenv("E2E_DYNAMIC_ARTIFACT_ROOT", "artifacts/dynamic"))
REFERENCE_CACHE = Path(os.getenv("E2E_REFERENCE_CACHE", "artifacts/reference_cache"))

# Longest token first prevents >= from being split into >.
OPERATOR_RE = re.compile(r"!==|===|!=|==|>=|<=|&&|\|\||\btrue\b|\bfalse\b|>|<")
OPERATOR_REPLACEMENTS = {
    "===": "!==", "!==": "===", "==": "!=", "!=": "==",
    ">=": "<", "<=": ">", ">": "<=", "<": ">=",
    "&&": "||", "||": "&&", "true": "false", "false": "true",
}
PRICE_RE = re.compile(r"\$([0-9]+(?:\.[0-9]{1,2})?)")
# Requirement-aware DOM mutation: a non-draggable element accidentally becomes draggable.
# We mutate only the boolean literal, preserving the element and all other attributes.
DRAGGABLE_FALSE_RE = re.compile(
    r'\bdraggable\s*=\s*(?P<quote>["\'])(?P<value>false)(?P=quote)',
    re.IGNORECASE,
)
# Generic DOM opening-tag scanner used by boolean-attribute insertion mutations.
# It intentionally considers only explicitly identified UI elements (data-testid/data-test/id),
# not every HTML node in the application.
OPEN_TAG_RE = re.compile(
    r'<(?P<tag>[A-Za-z][A-Za-z0-9:_-]*)(?P<attrs>[^<>]*)>',
    re.IGNORECASE | re.DOTALL,
)
INLINE_SCRIPT_RE = re.compile(
    r"(?P<open><script\b(?P<attrs>[^>]*)>)(?P<body>.*?)(?P<close></script\s*>)",
    re.IGNORECASE | re.DOTALL,
)

# Scope-aware mutation metadata. The raw mutation score is retained for suite-level
# aggregation, while these hints determine whether a mutant is relevant to one test.
TESTID_RE = re.compile(
    r"(?:data-testid|data-testid|data-test)\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
PRODUCT_INDEX_RE = re.compile(r"product-(?:item|title|price)-([0-9]+)", re.IGNORECASE)
# Scenario lines often use natural-language syntax such as:
# ``data-testid "product-item-1"`` rather than HTML attributes.
BDD_TESTID_RE = re.compile(
    r"(?:data-testid|data-test)\s*(?:=)?\s*[\"']?([A-Za-z0-9_.{}-]+)[\"']?",
    re.IGNORECASE,
)
PRODUCT_TESTID_RE = re.compile(r"product-(?:item|title|price)-[0-9]+", re.IGNORECASE)
GENERIC_PLACEHOLDER_RE = re.compile(r"[{}<>]")


@dataclass(frozen=True)
class PreparedWorkspace:
    workspace: Path
    app_dir: Path
    app_index: Path
    artifact_dir: Path


@dataclass(frozen=True)
class MutationSpec:
    mutant_id: str
    source_file: str
    start: int
    end: int
    original: str
    replacement: str
    operator: str
    line: int


# ---------------------------------------------------------------------------
# General utilities
# ---------------------------------------------------------------------------

def _repo_cache_dir(reference_answer: str) -> Path:
    stem = reference_answer.rstrip("/").split("/")[-1]
    return REFERENCE_CACHE / _safe_name(stem)


def _clone_reference(
    reference_answer: str,
    *,
    workspace_root: str | Path | None = None,
    allow_network: bool | None = None,
    timeout_seconds: int | None = None,
    expected_patterns: Iterable[str] | None = None,
) -> Path:
    """Compatibility adapter over the shared validated reference resolver."""
    if allow_network is None:
        allow_network = _as_bool(
            os.getenv("E2E_REFERENCE_NETWORK_ENABLED"), False
        )
    resolution = resolve_reference_source(
        reference_answer,
        workspace_root or REFERENCE_CACHE.parent,
        allow_network=allow_network,
        timeout_seconds=(
            timeout_seconds
            if timeout_seconds is not None
            else int(os.getenv("E2E_REFERENCE_TIMEOUT_SECONDS", "90"))
        ),
        expected_patterns=expected_patterns,
    )
    if resolution["resolution_status"] != "success":
        raise RuntimeError(
            "Reference resolution "
            f"{resolution['resolution_status']}: {resolution['failure_reason']}"
        )
    return Path(resolution["local_path"])


def _find_primary_index(app_dir: Path) -> Path:
    direct = app_dir / "index.html"
    if direct.exists():
        return direct
    candidates = sorted(app_dir.rglob("index.html"))
    if not candidates:
        candidates = sorted(app_dir.rglob("*.html"))
    if not candidates:
        raise RuntimeError("Reference application contains no HTML entry point.")
    return candidates[0]


def _copy_reference_into_workspace(reference_dir: Path, workspace: Path, benchmark_id: str) -> tuple[Path, Path]:
    app_dir = workspace / "app"
    shutil.copytree(reference_dir, app_dir, ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__"))
    app_index = _find_primary_index(app_dir)

    # Some generated tests assume a directory named after the benchmark.
    alias = workspace / benchmark_id
    try:
        alias.symlink_to(app_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        shutil.copytree(app_dir, alias, dirs_exist_ok=True)

    # Convenient for manual debugging. The Chrome shim uses the absolute URI below.
    root_index = workspace / "index.html"
    if not root_index.exists():
        try:
            root_index.symlink_to(app_index)
        except (OSError, NotImplementedError):
            shutil.copy2(app_index, root_index)
    return app_dir, app_index


def _new_workspace(state: dict[str, Any], run_label: str, reference_dir: Path | None = None) -> PreparedWorkspace:
    reference_answer = str(state["reference_answer"])
    case_uid = _safe_name(str(state["case_uid"]))
    label = _safe_name(run_label)
    workspace = ARTIFACT_ROOT / "workspaces" / case_uid / label
    artifact_dir = ARTIFACT_ROOT / "results" / case_uid / label
    benchmark_id = str(state.get("benchmark_id") or reference_answer.rstrip("/").split("/")[-1])

    shutil.rmtree(workspace, ignore_errors=True)
    shutil.rmtree(artifact_dir, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    source = reference_dir or _clone_reference(
        reference_answer,
        workspace_root=state.get("reference_workspace_root"),
        allow_network=_as_bool(state.get("reference_network_enabled"), False),
        timeout_seconds=int(state.get("reference_timeout_seconds", 90)),
        expected_patterns=state.get("reference_expected_patterns"),
    )
    app_dir, app_index = _copy_reference_into_workspace(source, workspace, benchmark_id)
    return PreparedWorkspace(workspace, app_dir, app_index, artifact_dir)


# ---------------------------------------------------------------------------
# Behave + Selenium execution harness
# ---------------------------------------------------------------------------

def _environment_py() -> str:
    return r'''from pathlib import Path


def _artifact_dir(context):
    out = Path(context.config.userdata.get("artifact_dir", "artifacts"))
    out.mkdir(parents=True, exist_ok=True)
    return out


def after_step(context, step):
    if step.status == "failed" and hasattr(context, "driver"):
        try:
            out = _artifact_dir(context)
            context.driver.save_screenshot(str(out / "failure.png"))
            (out / "page_source.html").write_text(context.driver.page_source, encoding="utf-8")
        except Exception:
            pass


def after_scenario(context, scenario):
    if hasattr(context, "driver"):
        try:
            context.driver.quit()
        except Exception:
            pass
'''


def _chrome_harness_shim() -> str:
    """Minimal harness that preserves test actions/assertions while making them runnable."""
    return r'''# --- E2E evaluator harness shim: browser setup only ---
import os as _e2e_os
from selenium import webdriver as _e2e_webdriver
from selenium.webdriver.chrome.options import Options as _E2EChromeOptions

_E2E_ORIGINAL_CHROME = _e2e_webdriver.Chrome

def _e2e_managed_chrome(*args, **kwargs):
    if "options" not in kwargs:
        _opts = _E2EChromeOptions()
        if _e2e_os.getenv("E2E_HEADLESS", "true").lower() in {"1", "true", "yes", "on"}:
            _opts.add_argument("--headless=new")
        _opts.add_argument("--window-size=1400,1000")
        _opts.add_argument("--disable-gpu")
        _opts.add_argument("--allow-file-access-from-files")
        kwargs["options"] = _opts

    _driver = _E2E_ORIGINAL_CHROME(*args, **kwargs)
    _original_get = _driver.get

    def _e2e_get(url, *get_args, **get_kwargs):
        _normalized = str(url).replace("\\", "/").strip()
        if _normalized in {"file://index.html", "file:///index.html", "index.html"}:
            return _original_get(_e2e_os.environ["E2E_APP_INDEX_URI"], *get_args, **get_kwargs)
        return _original_get(url, *get_args, **get_kwargs)

    _driver.get = _e2e_get
    return _driver

_e2e_webdriver.Chrome = _e2e_managed_chrome
# --- End evaluator harness shim ---
'''


def _write_behave_project(prepared: PreparedWorkspace, feature_text: str, step_code: str) -> None:
    features_dir = prepared.workspace / "features"
    steps_dir = features_dir / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)
    (features_dir / "generated.feature").write_text(feature_text, encoding="utf-8")
    generated = _chrome_harness_shim() + "\n\n# --- Generated test code ---\n" + step_code
    (steps_dir / "generated_steps.py").write_text(generated, encoding="utf-8")
    (features_dir / "environment.py").write_text(_environment_py(), encoding="utf-8")


def _classify_result(return_code: int, stdout: str, stderr: str) -> str:
    combined = (stdout + "\n" + stderr).lower()
    if return_code == 0:
        return "PASSED"
    if "modulenotfounderror" in combined or "no module named" in combined:
        return "HARNESS_DEPENDENCY_ERROR"
    if (
        "sessionnotcreatedexception" in combined
        or "chromedriver" in combined
        or "cannot find chrome binary" in combined
        or "selenium manager" in combined
    ):
        return "HARNESS_BROWSER_ERROR"
    if "undefined step" in combined or "parsererror" in combined or "no feature files" in combined:
        return "HARNESS_FORMAT_ERROR"
    return "TEST_FAILED"


def _extract_behave_failure_evidence(report_path: Path) -> dict[str, Any]:
    """Extract compact failure facts from Behave JSON without using an LLM."""
    empty = {"execution_failed_steps": [], "execution_report_summary": ""}
    if not report_path.exists():
        return empty
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return empty

    failed: list[dict[str, str]] = []
    for step in _iter_behave_steps(report):
        result = step.get("result") or {}
        status = str(result.get("status", "")).lower()
        if status not in {"failed", "error", "undefined", "pending"}:
            continue
        error = result.get("error_message", "")
        if isinstance(error, list):
            error = "\n".join(str(x) for x in error)
        failed.append({
            "keyword": str(step.get("keyword", "")).strip(),
            "name": str(step.get("name", "")).strip(),
            "status": status or "unknown",
            "error": _tail(str(error), 700),
        })

    if not failed:
        return empty
    summary = "; ".join(
        f"{item['keyword']} {item['name']} [{item['status']}]"
        for item in failed[:4]
    )
    return {"execution_failed_steps": failed[:8], "execution_report_summary": summary}


def _run_behave(prepared: PreparedWorkspace, state: dict[str, Any], timeout: int) -> dict[str, Any]:
    """Run Behave and persist a JSON report at an absolute artifact path."""
    report_path = (prepared.artifact_dir / "behave_report.json").resolve()
    artifact_dir = prepared.artifact_dir.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "behave",
        "features/generated.feature",
        "--no-capture",
        "--format=json.pretty",
        "--outfile",
        str(report_path),
        "-D",
        f"artifact_dir={artifact_dir}",
    ]
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "E2E_APP_INDEX_URI": prepared.app_index.resolve().as_uri(),
        "E2E_HEADLESS": "true" if _as_bool(state.get("headless"), True) else "false",
    }
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=prepared.workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        duration = round(time.monotonic() - started, 3)
        evidence = _extract_behave_failure_evidence(report_path)
        return {
            "execution_status": _classify_result(proc.returncode, proc.stdout, proc.stderr),
            "execution_return_code": int(proc.returncode),
            "execution_duration_seconds": duration,
            "execution_stdout_tail": _tail(proc.stdout),
            "execution_stderr_tail": _tail(proc.stderr),
            "behave_report_path": str(report_path) if report_path.exists() else "",
            "execution_command": subprocess.list2cmdline(command),
            "execution_environment": {
                "python_version": sys.version,
                "platform": platform.platform(),
                "python_executable": sys.executable,
                "headless": env["E2E_HEADLESS"],
            },
            **evidence,
        }
    except subprocess.TimeoutExpired as exc:
        evidence = _extract_behave_failure_evidence(report_path)
        return {
            "execution_status": "TIMEOUT",
            "execution_return_code": -1,
            "execution_duration_seconds": round(time.monotonic() - started, 3),
            "execution_stdout_tail": _tail(exc.stdout),
            "execution_stderr_tail": _tail(exc.stderr) + f"\nTimed out after {timeout}s.",
            "behave_report_path": str(report_path) if report_path.exists() else "",
            "execution_command": subprocess.list2cmdline(command),
            "execution_environment": {
                "python_version": sys.version,
                "platform": platform.platform(),
                "python_executable": sys.executable,
                "headless": env["E2E_HEADLESS"],
            },
            **evidence,
        }


def _execution_skip(status: str, detail: str) -> dict[str, Any]:
    return {
        "execution_status": status,
        "execution_success": False,
        "execution_return_code": -1,
        "execution_duration_seconds": 0.0,
        "execution_stdout_tail": "",
        "execution_stderr_tail": detail,
        "execution_artifact_dir": "",
        "workspace_dir": "",
        "behave_report_path": "",
        "execution_failed_steps": [],
        "execution_report_summary": "",
    }


def execution_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Baseline execution: can the generated E2E test pass on the original reference app?"""
    if not state.get("syntax_passed", False):
        return _execution_skip("SKIPPED_SYNTAX_FAILED", "Static syntax gate failed.")
    if not _as_bool(state.get("enable_dynamic"), False):
        return _execution_skip("SKIPPED_DISABLED", "Dynamic evaluation disabled by configuration.")

    required = ["reference_answer", "case_uid", "excutable_test_test_case", "executable_test_code"]
    missing = [field for field in required if not state.get(field)]
    if missing:
        return _execution_skip("HARNESS_INPUT_ERROR", f"Missing dynamic inputs: {', '.join(missing)}")

    try:
        prepared = _new_workspace(state, "baseline")
        _write_behave_project(
            prepared,
            str(state["excutable_test_test_case"]),
            str(state["executable_test_code"]),
        )
        result = _run_behave(
            prepared,
            state,
            timeout=int(state.get("execution_timeout_seconds", DEFAULT_EXECUTION_TIMEOUT)),
        )
        result.update({
            "execution_success": result["execution_status"] == "PASSED",
            "execution_artifact_dir": str(prepared.artifact_dir),
            "workspace_dir": str(prepared.workspace),
        })
        return result
    except Exception as exc:
        return {
            **_execution_skip("HARNESS_ERROR", repr(exc)),
            "execution_artifact_dir": "",
            "workspace_dir": "",
        }


# ---------------------------------------------------------------------------
# Dynamic coverage: Behave report / BDD phase evidence
# ---------------------------------------------------------------------------

def _iter_behave_steps(report_data: Any) -> Iterable[dict[str, Any]]:
    """Yield normalized steps from Behave's JSON/JSON.pretty formatter output."""
    features = report_data if isinstance(report_data, list) else [report_data]
    for feature in features:
        if not isinstance(feature, dict):
            continue
        for element in feature.get("elements", []) or []:
            if not isinstance(element, dict):
                continue
            for step in element.get("steps", []) or []:
                if isinstance(step, dict):
                    yield step


def _phase_from_keyword(keyword: str, current_phase: str | None) -> str:
    normalized = keyword.strip().lower()
    if normalized == "given":
        return "setup"
    if normalized == "when":
        return "action"
    if normalized == "then":
        return "oracle"
    if normalized in {"and", "but"}:
        return current_phase or "other"
    return "other"


def _summarize_behave_report(report_data: Any) -> dict[str, Any]:
    """Calculate successful BDD step coverage from an execution report.

    *Expected* means all feature-file steps. *Successful* requires Behave to report `passed`.
    A failed first action therefore prevents later assertions from being credited.
    """
    counts = {
        "steps_total": 0,
        "steps_executed": 0,
        "steps_passed": 0,
        "steps_failed": 0,
        "steps_skipped": 0,
        "actions_total": 0,
        "actions_passed": 0,
        "oracles_total": 0,
        "oracles_passed": 0,
        "setup_total": 0,
        "setup_passed": 0,
    }
    phase: str | None = None
    executed_statuses = {"passed", "failed", "error", "undefined", "pending"}

    for step in _iter_behave_steps(report_data):
        phase = _phase_from_keyword(str(step.get("keyword", "")), phase)
        result = step.get("result") or {}
        status = str(result.get("status", "unknown")).lower()
        counts["steps_total"] += 1
        if status in executed_statuses:
            counts["steps_executed"] += 1
        if status == "passed":
            counts["steps_passed"] += 1
        elif status == "failed":
            counts["steps_failed"] += 1
        else:
            counts["steps_skipped"] += 1

        if phase == "action":
            counts["actions_total"] += 1
            if status == "passed":
                counts["actions_passed"] += 1
        elif phase == "oracle":
            counts["oracles_total"] += 1
            if status == "passed":
                counts["oracles_passed"] += 1
        elif phase == "setup":
            counts["setup_total"] += 1
            if status == "passed":
                counts["setup_passed"] += 1

    def pct(numerator: int, denominator: int) -> float | None:
        return round(100.0 * numerator / denominator, 2) if denominator else None

    step_success = pct(counts["steps_passed"], counts["steps_total"])
    action_success = pct(counts["actions_passed"], counts["actions_total"])
    oracle_success = pct(counts["oracles_passed"], counts["oracles_total"])

    # Weight the whole scenario, action phase, and oracle phase. Missing phases are omitted
    # and the remaining weights are normalized rather than treated as zero.
    components: list[tuple[float, float]] = []
    if step_success is not None:
        components.append((step_success, 0.40))
    if action_success is not None:
        components.append((action_success, 0.30))
    if oracle_success is not None:
        components.append((oracle_success, 0.30))

    weight_sum = sum(weight for _, weight in components)
    score = round(sum(value * weight for value, weight in components) / weight_sum, 2) if weight_sum else None
    return {
        **counts,
        "step_success_coverage": step_success,
        "action_step_coverage": action_success,
        "oracle_step_coverage": oracle_success,
        "dynamic_coverage_score": score,
    }


def _coverage_skip(status: str, detail: str) -> dict[str, Any]:
    return {
        "dynamic_coverage_status": status,
        "dynamic_coverage_score": None,
        "step_success_coverage": None,
        "action_step_coverage": None,
        "oracle_step_coverage": None,
        "dynamic_steps_total": 0,
        "dynamic_steps_executed": 0,
        "dynamic_steps_passed": 0,
        "dynamic_steps_failed": 0,
        "dynamic_steps_skipped": 0,
        "dynamic_actions_total": 0,
        "dynamic_actions_passed": 0,
        "dynamic_oracles_total": 0,
        "dynamic_oracles_passed": 0,
        "dynamic_coverage_detail": detail,
    }


def dynamic_coverage_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Compute requirement-aligned dynamic coverage after baseline execution.

    This is deliberately named `dynamic_coverage`, not `branch_coverage`: it measures
    BDD behavior steps that successfully executed in a real browser run.
    """
    execution_status = state.get("execution_status")
    if execution_status not in {"PASSED", "TEST_FAILED"}:
        return _coverage_skip(
            "SKIPPED_EXECUTION_NOT_ANALYZABLE",
            "No Behave step evidence is available for this execution status.",
        )

    report_path_text = str(state.get("behave_report_path", ""))
    report_path = Path(report_path_text) if report_path_text else None
    if not report_path or not report_path.exists():
        return _coverage_skip("INCONCLUSIVE_REPORT_MISSING", "Behave JSON report was not created.")

    try:
        report_data = json.loads(report_path.read_text(encoding="utf-8"))
        summary = _summarize_behave_report(report_data)
        if summary["steps_total"] == 0:
            return _coverage_skip("INCONCLUSIVE_EMPTY_REPORT", "Behave report contains no scenario steps.")
        return {
            "dynamic_coverage_status": "PASSED" if execution_status == "PASSED" else "PARTIAL_BASELINE_FAILURE",
            "dynamic_coverage_score": summary["dynamic_coverage_score"],
            "step_success_coverage": summary["step_success_coverage"],
            "action_step_coverage": summary["action_step_coverage"],
            "oracle_step_coverage": summary["oracle_step_coverage"],
            "dynamic_steps_total": summary["steps_total"],
            "dynamic_steps_executed": summary["steps_executed"],
            "dynamic_steps_passed": summary["steps_passed"],
            "dynamic_steps_failed": summary["steps_failed"],
            "dynamic_steps_skipped": summary["steps_skipped"],
            "dynamic_actions_total": summary["actions_total"],
            "dynamic_actions_passed": summary["actions_passed"],
            "dynamic_oracles_total": summary["oracles_total"],
            "dynamic_oracles_passed": summary["oracles_passed"],
            "dynamic_coverage_detail": (
                "Diagnostic BDD step-execution evidence; it does not measure requirement completeness or source branch coverage."
                if execution_status == "PASSED"
                else "Partial diagnostic BDD step-execution evidence from a failed baseline run."
            ),
        }
    except Exception as exc:
        return _coverage_skip("COVERAGE_HARNESS_ERROR", repr(exc))



# ---------------------------------------------------------------------------
# Scope-aware mutation interpretation
# ---------------------------------------------------------------------------

def _compact_excerpt(text: str, limit: int = 360) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _extract_source_scope(source: str, start: int, end: int, radius: int = 240) -> dict[str, Any]:
    """Return local element/product hints for a mutated source location.

    For attribute mutations such as ``draggable="false"``, the nearest enclosing HTML
    start tag is the authoritative scope. For price mutations in product lists, prefer
    the enclosing ``<li>...</li>`` card. This keeps adjacent products out of the context.
    """
    tag_left = source.rfind("<", 0, start)
    tag_right = source.find(">", end)
    tag_snippet = ""
    if tag_left >= 0 and tag_right >= 0 and tag_left < start < tag_right:
        tag_snippet = source[tag_left:tag_right + 1]

    search_left = max(0, start - 3000)
    li_start = source.rfind("<li", search_left, start)
    li_end = source.find("</li>", end, min(len(source), end + 3000))
    if li_start >= 0 and li_end >= 0:
        snippet = source[li_start:li_end + len("</li>")]
    elif tag_snippet:
        snippet = tag_snippet
    else:
        left = max(0, start - radius)
        right = min(len(source), end + radius)
        snippet = source[left:right]

    # data-testid is the primary app/test contract. Also accept plain id attributes when
    # a benchmark uses them as the only stable element identifier.
    element_ids = sorted({
        *{item.strip() for item in TESTID_RE.findall(snippet) if item.strip()},
        *{item.strip() for item in re.findall(r'\bid\s*=\s*["\']([^"\']+)["\']', snippet, re.I) if item.strip()},
    })
    product_indices = sorted({item for item in PRODUCT_INDEX_RE.findall(snippet)})
    return {
        "element_ids": element_ids,
        "product_indices": product_indices,
        "context_excerpt": _compact_excerpt(snippet),
    }

def _normalise_targets(values: Iterable[str]) -> list[str]:
    """Return concrete, stable values only (never Behave placeholders such as ``{id}``)."""
    cleaned = {
        value.strip()
        for value in values
        if isinstance(value, str)
        and value.strip()
        and not GENERIC_PLACEHOLDER_RE.search(value)
    }
    return sorted(cleaned, key=str.lower)


def _scenario_step_text(bdd: str) -> str:
    """Extract only the active Scenario's Given/When/Then/And/But lines.

    Feature descriptions and other prose may mention additional products. They must not
    broaden the target scope of the current generated test.
    """
    lines: list[str] = []
    in_scenario = False
    for raw in str(bdd or '').splitlines():
        stripped = raw.strip()
        if re.match(r'(?i)^scenario(?:\s+outline)?\s*:', stripped):
            in_scenario = True
            continue
        if in_scenario and re.match(r'(?i)^(given|when|then|and|but)\b', stripped):
            lines.append(stripped)
    return '\n'.join(lines) if lines else str(bdd or '')


def _scope_from_text(text: str) -> dict[str, list[str]]:
    product_ids = _normalise_targets(PRODUCT_TESTID_RE.findall(text or ''))
    test_ids = _normalise_targets(BDD_TESTID_RE.findall(text or ''))
    # Include direct product references even when no ``data-testid`` phrase appears.
    element_ids = _normalise_targets([*test_ids, *product_ids])
    product_indices = sorted({item for item in PRODUCT_INDEX_RE.findall(' '.join(element_ids) + '\n' + (text or ''))})
    prices = sorted({f'${value}' for value in PRICE_RE.findall(text or '')})
    return {
        'element_ids': element_ids,
        'product_indices': product_indices,
        'prices': prices,
    }


def _decorator_name(decorator: ast.AST) -> str:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return target.id.lower()
    if isinstance(target, ast.Attribute):
        return target.attr.lower()
    return ''


def _assertion_scope_from_code(test_code: str) -> dict[str, list[str]]:
    """Extract literals only from ``@then`` bodies, never from generic decorators/helpers.

    Generic step definitions can contain placeholders or reusable product examples. Their
    literals describe a *step grammar*, not the specific BDD scenario being evaluated.
    """
    try:
        tree = ast.parse(str(test_code or ''))
    except SyntaxError:
        return {'element_ids': [], 'product_indices': [], 'prices': []}

    fragments: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(_decorator_name(decorator) == 'then' for decorator in node.decorator_list):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                fragments.append(child.value)
    return _scope_from_text('\n'.join(fragments))


def _extract_test_scope(state: dict[str, Any], test_code: str) -> dict[str, Any]:
    """Infer the current test's target with strict evidence precedence.

    Priority:
    1. Concrete literals in the immutable BDD Scenario;
    2. Concrete literals in ``@then`` assertion bodies;
    3. No broad fallback to requirements, decorators, or generic helpers.

    This deliberately avoids treating reusable Behave decorators such as
    ``@when('... {product_id} ...')`` as proof that the current scenario targets every
    product handled by that reusable step implementation.
    """
    scenario_text = _scenario_step_text(str(state.get('excutable_test_test_case', '') or ''))
    bdd_scope = _scope_from_text(scenario_text)
    assertion_scope = _assertion_scope_from_code(str(test_code or ''))

    has_bdd_target = bool(bdd_scope['element_ids'] or bdd_scope['product_indices'] or bdd_scope['prices'])
    has_assertion_target = bool(assertion_scope['element_ids'] or assertion_scope['product_indices'] or assertion_scope['prices'])

    if has_bdd_target:
        # BDD determines product identity. Assertion-only prices can supplement a scenario
        # only when the scenario omitted a price literal.
        final_scope = {
            'element_ids': bdd_scope['element_ids'],
            'product_indices': bdd_scope['product_indices'],
            'prices': bdd_scope['prices'] or assertion_scope['prices'],
            'primary_source': 'BDD_SCENARIO',
            'confidence': 'HIGH',
        }
    elif has_assertion_target:
        final_scope = {
            'element_ids': assertion_scope['element_ids'],
            'product_indices': assertion_scope['product_indices'],
            'prices': assertion_scope['prices'],
            'primary_source': 'THEN_ASSERTION_BODY',
            'confidence': 'MEDIUM',
        }
    else:
        final_scope = {
            'element_ids': [],
            'product_indices': [],
            'prices': [],
            'primary_source': 'NO_EXPLICIT_TARGET',
            'confidence': 'LOW',
        }

    return {
        **final_scope,
        'bdd_scope': bdd_scope,
        'assertion_scope': assertion_scope,
        'scenario_step_text': _compact_excerpt(scenario_text, 800),
    }

def _classify_mutant_scope(
    mutant: MutationSpec,
    source_text: str,
    test_scope: dict[str, Any],
) -> dict[str, Any]:
    """Classify a mutant against the *specific* BDD/assertion target.

    Product identity is stronger evidence than a price literal. If the scenario explicitly
    targets product 3, a price mutation inside product 1 is OUT_OF_SCOPE even when generic
    reusable step code mentions product 1 elsewhere.
    """
    source_scope = _extract_source_scope(source_text, mutant.start, mutant.end)
    source_products = set(source_scope['product_indices'])
    target_products = {str(item) for item in test_scope.get('product_indices', [])}
    target_prices = {str(item) for item in test_scope.get('prices', [])}
    source_ids = {str(item).lower() for item in source_scope['element_ids']}
    # For non-product scenarios (e.g. drop-area), an explicit BDD data-testid is
    # sufficient evidence that an attribute mutation on that same element is relevant.
    target_ids = {str(item).lower() for item in test_scope.get('element_ids', [])}

    # Explicit product indices are the strongest and preferred decision rule.
    if source_products and target_products:
        if source_products & target_products:
            return {
                'scope_relation': 'RELEVANT',
                'scope_reason': 'The mutated source card matches the concrete product index named by the current BDD/assertion target.',
                'source_scope': source_scope,
            }
        return {
            'scope_relation': 'OUT_OF_SCOPE',
            'scope_reason': 'The mutant belongs to a different concrete product card than the current BDD/assertion target.',
            'source_scope': source_scope,
        }

    # Fall back to concrete product test IDs only when product indices are unavailable.
    if source_ids and target_ids:
        if source_ids & target_ids:
            return {
                'scope_relation': 'RELEVANT',
                'scope_reason': 'The mutant shares an explicit product data-testid with the current target.',
                'source_scope': source_scope,
            }
        return {
            'scope_relation': 'OUT_OF_SCOPE',
            'scope_reason': 'The mutant targets a different explicit product data-testid.',
            'source_scope': source_scope,
        }

    # Explicit non-product element identity covers DOM-behaviour scenarios such as
    # data-testid="drop-area" with draggable="false" -> "true".
    if source_ids and target_ids:
        if source_ids & target_ids:
            return {
                'scope_relation': 'RELEVANT',
                'scope_reason': 'The mutated DOM element shares an explicit data-testid/id with the current BDD/assertion target.',
                'source_scope': source_scope,
            }
        return {
            'scope_relation': 'OUT_OF_SCOPE',
            'scope_reason': 'The mutated DOM element has a different explicit data-testid/id from the current target.',
            'source_scope': source_scope,
        }

    # Literal price matching is weaker evidence and is used only when no product identity
    # can be established from the source or test target.
    if mutant.original in target_prices and not source_products:
        return {
            'scope_relation': 'RELEVANT',
            'scope_reason': 'No product identity was available; the mutated literal is explicitly specified by the current scenario/assertion.',
            'source_scope': source_scope,
        }

    return {
        'scope_relation': 'UNCERTAIN',
        'scope_reason': 'Insufficient concrete scenario/assertion and source-product evidence to determine this mutant\'s scope.',
        'source_scope': source_scope,
    }

def _scope_aware_mutation_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarise mutation outcomes separately for relevant, out-of-scope and uncertain mutants."""
    buckets: dict[str, list[dict[str, Any]]] = {"RELEVANT": [], "OUT_OF_SCOPE": [], "UNCERTAIN": []}
    for record in records:
        relation = str(record.get("scope_relation", "UNCERTAIN")).upper()
        buckets.setdefault(relation if relation in buckets else "UNCERTAIN", []).append(record)

    def count(bucket: str, verdict: str) -> int:
        return sum(1 for item in buckets[bucket] if str(item.get("verdict", "")).upper() == verdict)

    relevant_killed = count("RELEVANT", "KILLED")
    relevant_survived = count("RELEVANT", "SURVIVED")
    relevant_timeout = count("RELEVANT", "TIMEOUT")
    relevant_inconclusive = len(buckets["RELEVANT"]) - relevant_killed - relevant_survived - relevant_timeout
    relevant_valid = relevant_killed + relevant_survived
    relevant_score = round(100.0 * relevant_killed / relevant_valid, 2) if relevant_valid else None

    uncertain_survived = count("UNCERTAIN", "SURVIVED")
    out_scope_survived = count("OUT_OF_SCOPE", "SURVIVED")
    if relevant_valid:
        scope_status = "SCOPE_AWARE"
    elif buckets["UNCERTAIN"]:
        scope_status = "UNCERTAIN_SCOPE"
    else:
        scope_status = "NO_RELEVANT_MUTANTS"

    return {
        "mutation_scope_status": scope_status,
        "relevant_mutants_total": len(buckets["RELEVANT"]),
        "relevant_mutants_killed": relevant_killed,
        "relevant_mutants_survived": relevant_survived,
        "relevant_mutants_timeout": relevant_timeout,
        "relevant_mutants_inconclusive": relevant_inconclusive,
        "relevant_mutation_score": relevant_score,
        "out_of_scope_mutants_total": len(buckets["OUT_OF_SCOPE"]),
        "out_of_scope_mutants_survived": out_scope_survived,
        "uncertain_mutants_total": len(buckets["UNCERTAIN"]),
        "uncertain_mutants_survived": uncertain_survived,
    }


# ---------------------------------------------------------------------------
# Mutation testing: deterministic HTML / JavaScript source mutants
# ---------------------------------------------------------------------------

def _is_escaped(source: str, position: int) -> bool:
    backslashes = 0
    index = position - 1
    while index >= 0 and source[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _iter_js_operator_matches(source: str) -> Iterable[re.Match[str]]:
    """Yield operators outside strings and comments.

    It is intentionally lightweight, but it avoids the most harmful false mutations:
    changing text inside quotes or comments. It is adequate for the small, vanilla-JS
    benchmark apps and does not claim to be a complete JavaScript parser.
    """
    index = 0
    length = len(source)
    state = "code"
    quote = ""

    while index < length:
        char = source[index]
        nxt = source[index + 1] if index + 1 < length else ""

        if state == "code":
            if char in {"'", '"', "`"}:
                state = "string"
                quote = char
                index += 1
                continue
            if char == "/" and nxt == "/":
                state = "line_comment"
                index += 2
                continue
            if char == "/" and nxt == "*":
                state = "block_comment"
                index += 2
                continue
            match = OPERATOR_RE.match(source, index)
            if match:
                yield match
                index = match.end()
                continue
            index += 1
            continue

        if state == "string":
            if char == quote and not _is_escaped(source, index):
                state = "code"
            index += 1
            continue

        if state == "line_comment":
            if char == "\n":
                state = "code"
            index += 1
            continue

        # block comment
        if char == "*" and nxt == "/":
            state = "code"
            index += 2
        else:
            index += 1


def _mutation_specs_from_javascript(source: str, relative_file: str, base_offset: int = 0) -> list[MutationSpec]:
    specs: list[MutationSpec] = []
    for match in _iter_js_operator_matches(source):
        original = match.group(0)
        replacement = OPERATOR_REPLACEMENTS[original]
        specs.append(MutationSpec(
            mutant_id="",
            source_file=relative_file,
            start=base_offset + match.start(),
            end=base_offset + match.end(),
            original=original,
            replacement=replacement,
            operator=f"{original}_TO_{replacement}",
            line=source.count("\n", 0, match.start()) + 1,
        ))
    return specs


def _mutation_specs_from_price_literals(source: str, relative_file: str) -> list[MutationSpec]:
    """Create targeted data mutants such as `$40` -> `$41` in HTML or JavaScript."""
    specs: list[MutationSpec] = []
    for match in PRICE_RE.finditer(source):
        original = match.group(0)
        numeric = match.group(1)
        try:
            value = float(numeric)
        except ValueError:
            continue
        replacement_value = value + 1.0
        replacement_number = str(int(replacement_value)) if replacement_value.is_integer() else f"{replacement_value:.2f}"
        replacement = f"${replacement_number}"
        specs.append(MutationSpec(
            mutant_id="",
            source_file=relative_file,
            start=match.start(),
            end=match.end(),
            original=original,
            replacement=replacement,
            operator="PRICE_PLUS_ONE",
            line=source.count("\n", 0, match.start()) + 1,
        ))
    return specs


def _mutation_specs_from_draggable_false(source: str, relative_file: str) -> list[MutationSpec]:
    """Flip an explicit ``draggable="false"`` value to ``true``.

    This is one member of a reusable *boolean DOM attribute mutation* family. It applies
    when an element explicitly declares a disabled drag state.
    """
    specs: list[MutationSpec] = []
    for match in DRAGGABLE_FALSE_RE.finditer(source):
        start, end = match.span("value")
        specs.append(MutationSpec(
            mutant_id="",
            source_file=relative_file,
            start=start,
            end=end,
            original=source[start:end],
            replacement="true",
            operator="DRAGGABLE_FALSE_TO_TRUE",
            line=source.count("\n", 0, start) + 1,
        ))
    return specs


def _element_identifiers_from_open_tag(tag_text: str) -> set[str]:
    """Extract explicit, stable identity attributes from one opening HTML tag."""
    test_ids = {
        value.strip().lower()
        for value in TESTID_RE.findall(tag_text)
        if value and value.strip()
    }
    plain_ids = {
        value.strip().lower()
        for value in re.findall(r'\bid\s*=\s*["\']([^"\']+)["\']', tag_text, re.IGNORECASE)
        if value and value.strip()
    }
    return test_ids | plain_ids


def _mutation_specs_add_missing_draggable(
    source: str,
    relative_file: str,
    target_element_ids: set[str] | None = None,
) -> list[MutationSpec]:
    """Insert ``draggable="true"`` on an identified element that lacks the attribute.

    This is not a drop-area-specific rule. It is a generic boolean DOM-property mutation:
    a UI element that should be non-draggable can regress because the property is enabled
    even though it was previously absent rather than explicitly set to ``false``.

    When ``target_element_ids`` is supplied, only matching target elements are mutated. This
    makes a small mutation budget scenario-relevant rather than filling it with unrelated DOM
    controls.
    """
    target_ids = {item.lower() for item in (target_element_ids or set()) if item}
    specs: list[MutationSpec] = []

    for match in OPEN_TAG_RE.finditer(source):
        tag_text = match.group(0)
        attrs = match.group("attrs")
        # Do not duplicate or override an explicit attribute; the flip operator handles false.
        if re.search(r'\bdraggable\s*=', attrs, re.IGNORECASE):
            continue

        element_ids = _element_identifiers_from_open_tag(tag_text)
        if not element_ids:
            continue
        if target_ids and not (element_ids & target_ids):
            continue

        # Insert after the tag name, before existing attributes. Empty original supports an
        # insertion mutation and is handled by _apply_mutant.
        insertion = match.start("attrs")
        specs.append(MutationSpec(
            mutant_id="",
            source_file=relative_file,
            start=insertion,
            end=insertion,
            original="",
            replacement=' draggable="true"',
            operator="DRAGGABLE_MISSING_TO_TRUE",
            line=source.count("\n", 0, insertion) + 1,
        ))
    return specs


def _iter_app_source_files(reference_dir: Path) -> Iterable[Path]:
    ignored_parts = {".git", "node_modules", "__pycache__", ".pytest_cache"}
    for path in sorted(reference_dir.rglob("*")):
        if not path.is_file() or any(part in ignored_parts for part in path.parts):
            continue
        if path.suffix.lower() in {".js", ".html", ".htm"}:
            yield path


def _discover_mutants(
    reference_dir: Path,
    max_mutants: int,
    *,
    test_scope: dict[str, Any] | None = None,
    prefer_drag_behaviour: bool = False,
) -> list[MutationSpec]:
    """Discover a bounded, reproducible and scope-targeted mutant portfolio.

    General selection policy:
    1. Generate from reusable operator families (data, boolean DOM state, logic).
    2. When the current scenario has an explicit target and behaviour cue, select applicable
       target-local mutants first; do not spend a small budget on unrelated app-wide mutants.
    3. When no target-local mutant exists, fall back to a stable global portfolio so suite-level
       mutation analysis remains possible.

    This is a reusable *operator applicability + target selection* policy, not a benchmark-
    specific "if drop-area then mutate X" special case.
    """
    test_scope = test_scope or {}
    target_ids = {
        str(item).lower()
        for item in test_scope.get("element_ids", [])
        if isinstance(item, str) and item
    }
    candidates: list[MutationSpec] = []
    source_cache: dict[str, str] = {}

    for path in _iter_app_source_files(reference_dir):
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = str(path.relative_to(reference_dir))
        source_cache[relative] = text

        candidates.extend(_mutation_specs_from_price_literals(text, relative))
        if path.suffix.lower() in {".html", ".htm"}:
            candidates.extend(_mutation_specs_from_draggable_false(text, relative))
            # Missing-attribute mutation is restricted to an explicit scenario target.
            if target_ids:
                candidates.extend(_mutation_specs_add_missing_draggable(text, relative, target_ids))

        if path.suffix.lower() == ".js":
            candidates.extend(_mutation_specs_from_javascript(text, relative))
        else:
            for script_match in INLINE_SCRIPT_RE.finditer(text):
                attrs = script_match.group("attrs").lower()
                body = script_match.group("body")
                if "src=" in attrs or "application/json" in attrs or not body.strip():
                    continue
                candidates.extend(
                    _mutation_specs_from_javascript(
                        body, relative, base_offset=script_match.start("body")
                    )
                )

    drag_operators = {"DRAGGABLE_FALSE_TO_TRUE", "DRAGGABLE_MISSING_TO_TRUE"}

    def source_scope_for(item: MutationSpec) -> dict[str, Any]:
        return _extract_source_scope(source_cache[item.source_file], item.start, item.end)

    def is_target_local(item: MutationSpec) -> bool:
        if not target_ids:
            return False
        source_ids = {
            str(value).lower()
            for value in source_scope_for(item).get("element_ids", [])
        }
        return bool(source_ids & target_ids)

    target_local = [item for item in candidates if is_target_local(item)]

    def global_priority(item: MutationSpec) -> tuple[int, str, int, str]:
        # Backward-compatible fallback: if a caller explicitly requests a drag-oriented
        # portfolio but provides no concrete test scope, prefer generic drag-state mutants.
        if prefer_drag_behaviour:
            family_priority = {
                "DRAGGABLE_FALSE_TO_TRUE": 0,
                "DRAGGABLE_MISSING_TO_TRUE": 0,
                "PRICE_PLUS_ONE": 1,
            }.get(item.operator, 2)
        else:
            family_priority = {
                "PRICE_PLUS_ONE": 0,
                "DRAGGABLE_FALSE_TO_TRUE": 1,
                "DRAGGABLE_MISSING_TO_TRUE": 1,
            }.get(item.operator, 2)
        return (family_priority, item.source_file, item.start, item.operator)

    def targeted_priority(item: MutationSpec) -> tuple[int, str, int, str]:
        if prefer_drag_behaviour:
            family_priority = 0 if item.operator in drag_operators else 1
        else:
            # For a product/data scenario, price/data mutations remain first locally.
            family_priority = 0 if item.operator == "PRICE_PLUS_ONE" else 1
        return (family_priority, item.source_file, item.start, item.operator)

    # Key fix: when scenario-local candidates exist, do not pad a low budget with unrelated
    # global price mutants. This makes target behaviour measurable with max_mutants=1..3.
    pool = target_local if (prefer_drag_behaviour and target_local) else candidates
    pool.sort(key=targeted_priority if pool is target_local else global_priority)

    selected: list[MutationSpec] = []
    for index, candidate in enumerate(pool[:max_mutants], start=1):
        selected.append(MutationSpec(
            mutant_id=f"M{index:03d}",
            source_file=candidate.source_file,
            start=candidate.start,
            end=candidate.end,
            original=candidate.original,
            replacement=candidate.replacement,
            operator=candidate.operator,
            line=candidate.line,
        ))
    return selected

def _apply_mutant(app_dir: Path, mutant: MutationSpec) -> None:
    target = app_dir / mutant.source_file
    source = target.read_text(encoding="utf-8", errors="replace")
    if source[mutant.start:mutant.end] != mutant.original:
        raise RuntimeError(
            f"Mutant {mutant.mutant_id} no longer matches {mutant.source_file} at "
            f"{mutant.start}:{mutant.end}."
        )
    target.write_text(
        source[:mutant.start] + mutant.replacement + source[mutant.end:],
        encoding="utf-8",
    )


def _mutation_skip(status: str, detail: str) -> dict[str, Any]:
    return {
        "mutation_status": status,
        "mutation_score": None,  # raw score across the generated mutant set
        "total_mutants_generated": 0,
        "valid_mutants": 0,
        "killed_mutants": 0,
        "survived_mutants": 0,
        "invalid_mutants": 0,
        "per_operator_breakdown": {},
        "surviving_mutant_report": [],
        "mutants_total": 0,
        "mutants_killed": 0,
        "mutants_survived": 0,
        "mutants_timeout": 0,
        "mutants_inconclusive": 0,
        "mutation_scope_status": "NO_DYNAMIC_SCOPE_EVIDENCE",
        "relevant_mutation_score": None,
        "relevant_mutants_total": 0,
        "relevant_mutants_killed": 0,
        "relevant_mutants_survived": 0,
        "relevant_mutants_timeout": 0,
        "relevant_mutants_inconclusive": 0,
        "out_of_scope_mutants_total": 0,
        "out_of_scope_mutants_survived": 0,
        "uncertain_mutants_total": 0,
        "uncertain_mutants_survived": 0,
        "scope_relevant_surviving_mutants": [],
        "scope_out_of_scope_surviving_mutants": [],
        "scope_uncertain_surviving_mutants": [],
        "test_scope": {},
        "mutation_report_path": "",
        "surviving_mutants": [],
        "killed_mutant_report": [],
        "mutation_metrics": {
            "total_mutants_generated": 0,
            "valid_mutants": 0,
            "killed_mutants": 0,
            "survived_mutants": 0,
            "invalid_mutants": 0,
            "mutation_score": None,
            "per_operator_breakdown": {},
        },
        "mutation_records": [],
        "mutation_detail": detail,
    }


def _run_mutation_evaluation(
    state: dict[str, Any],
    test_code: str,
    run_namespace: str,
    report_filename: str,
) -> dict[str, Any]:
    """Run a deterministic mutant set and annotate test-level scope relevance.

    ``mutation_score`` remains the raw score over all selected mutants and is used for
    requirement-suite aggregation. ``relevant_mutation_score`` only uses mutants that
    can be tied to the current test target; Consensus uses this scope-aware value.
    """
    max_mutants = max(0, int(state.get("max_mutants", DEFAULT_MAX_MUTANTS)))
    if max_mutants == 0:
        return _mutation_skip("SKIPPED_DISABLED", "max_mutants is 0.")

    reference_dir = _clone_reference(
        str(state["reference_answer"]),
        workspace_root=state.get("reference_workspace_root"),
        allow_network=_as_bool(state.get("reference_network_enabled"), False),
        timeout_seconds=int(state.get("reference_timeout_seconds", 90)),
        expected_patterns=state.get("reference_expected_patterns"),
    )
    test_scope = _extract_test_scope(state, test_code)
    scenario_text = str(test_scope.get("scenario_step_text", "")).lower()
    target_ids = [str(item).lower() for item in test_scope.get("element_ids", [])]
    prefer_drag_behaviour = (
        any("drag" in item or "drop" in item for item in target_ids)
        or "drag" in scenario_text
        or "drop" in scenario_text
        or "draggable" in scenario_text
    )
    mutants = _discover_mutants(
        reference_dir,
        max_mutants,
        test_scope=test_scope,
        prefer_drag_behaviour=prefer_drag_behaviour,
    )
    if not mutants:
        return _mutation_skip("NO_MUTANTS", "No supported mutation location found in the reference app.")
    source_cache: dict[str, str] = {}

    def source_for(mutant: MutationSpec) -> str:
        if mutant.source_file not in source_cache:
            source_cache[mutant.source_file] = (reference_dir / mutant.source_file).read_text(
                encoding="utf-8", errors="replace"
            )
        return source_cache[mutant.source_file]

    case_uid = _safe_name(str(state["case_uid"]))
    report_dir = ARTIFACT_ROOT / "results" / case_uid
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / report_filename
    timeout = int(state.get("mutant_timeout_seconds", DEFAULT_MUTANT_TIMEOUT))
    records: list[dict[str, Any]] = []
    killed = survived = timed_out = inconclusive = 0

    for mutant in mutants:
        prepared = _new_workspace(state, f"{run_namespace}_mutant_{mutant.mutant_id}", reference_dir=reference_dir)
        scope = _classify_mutant_scope(mutant, source_for(mutant), test_scope)
        try:
            _apply_mutant(prepared.app_dir, mutant)
            _write_behave_project(
                prepared,
                str(state["excutable_test_test_case"]),
                test_code,
            )
            run = _run_behave(prepared, state, timeout)
            execution_status = run["execution_status"]
            if execution_status == "PASSED":
                verdict = "SURVIVED"
                survived += 1
            elif execution_status == "TEST_FAILED":
                verdict = "KILLED"
                killed += 1
            elif execution_status == "TIMEOUT":
                verdict = "TIMEOUT"
                timed_out += 1
            else:
                verdict = "INCONCLUSIVE"
                inconclusive += 1
            records.append({
                **asdict(mutant),
                **scope,
                "verdict": verdict,
                "execution_status": execution_status,
                "duration_seconds": run["execution_duration_seconds"],
                "artifact_dir": str(prepared.artifact_dir),
                "failed_steps": run.get("execution_failed_steps", []),
                "stderr_tail": run["execution_stderr_tail"],
            })
        except Exception as exc:
            inconclusive += 1
            records.append({
                **asdict(mutant),
                **scope,
                "verdict": "INCONCLUSIVE",
                "error": repr(exc),
            })

    valid = killed + survived
    raw_score = round(100.0 * killed / valid, 2) if valid else None
    scope_summary = _scope_aware_mutation_summary(records)
    report = {
        "baseline_execution_status": state.get("execution_status"),
        "test_scope": test_scope,
        "mutation_score": raw_score,
        "scope_aware_summary": scope_summary,
        "mutants_total": len(mutants),
        "mutants_killed": killed,
        "mutants_survived": survived,
        "mutants_timeout": timed_out,
        "mutants_inconclusive": inconclusive,
        "records": records,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def render(record: dict[str, Any]) -> str:
        return (
            f"{record.get('mutant_id')} {record.get('source_file')}:{record.get('line')} "
            f"{record.get('original')}→{record.get('replacement')} "
            f"[{record.get('operator')}; scope={record.get('scope_relation')}]"
        )

    compact_records = [
        {
            "mutant_id": record.get("mutant_id"),
            "source_file": record.get("source_file"),
            "line": record.get("line"),
            "original": record.get("original"),
            "replacement": record.get("replacement"),
            "operator": record.get("operator"),
            "verdict": record.get("verdict"),
            "scope_relation": record.get("scope_relation"),
            "scope_reason": record.get("scope_reason"),
            "source_scope": record.get("source_scope", {}),
            "failed_steps": record.get("failed_steps", [])[:2],
        }
        for record in records
    ]
    survivors = [render(record) for record in records if record.get("verdict") == "SURVIVED"]
    killed_mutants = [render(record) for record in records if record.get("verdict") == "KILLED"]
    scope_relevant_survivors = [
        render(record) for record in records
        if record.get("verdict") == "SURVIVED" and record.get("scope_relation") == "RELEVANT"
    ]
    scope_out_of_scope_survivors = [
        render(record) for record in records
        if record.get("verdict") == "SURVIVED" and record.get("scope_relation") == "OUT_OF_SCOPE"
    ]
    scope_uncertain_survivors = [
        render(record) for record in records
        if record.get("verdict") == "SURVIVED" and record.get("scope_relation") == "UNCERTAIN"
    ]
    return {
        "mutation_status": "PASSED" if valid else "NO_VALID_MUTANTS",
        "mutation_score": raw_score,
        "mutants_total": len(mutants),
        "mutants_killed": killed,
        "mutants_survived": survived,
        "mutants_timeout": timed_out,
        "mutants_inconclusive": inconclusive,
        "test_scope": test_scope,
        **scope_summary,
        "mutation_report_path": str(report_path),
        "surviving_mutants": survivors,
        "killed_mutants": killed_mutants,
        "scope_relevant_surviving_mutants": scope_relevant_survivors,
        "scope_out_of_scope_surviving_mutants": scope_out_of_scope_survivors,
        "scope_uncertain_surviving_mutants": scope_uncertain_survivors,
        "mutation_records": compact_records,
        "mutation_detail": (
            "Raw mutation score covers all selected mutants for suite aggregation. "
            "Scope-aware mutation score covers only mutants tied to this test target; "
            "out-of-scope mutants do not lower the single-test quality score."
        ),
    }


def _run_general_mutation_evaluation(
    state: dict[str, Any],
    test_code: str,
    run_namespace: str,
    report_filename: str,
) -> dict[str, Any]:
    """Run the active modular mutation engine on isolated reference copies."""
    from mutation_testing import run_mutation_campaign

    reference_dir = _clone_reference(
        str(state["reference_answer"]),
        workspace_root=state.get("reference_workspace_root"),
        allow_network=_as_bool(state.get("reference_network_enabled"), False),
        timeout_seconds=int(state.get("reference_timeout_seconds", 90)),
        expected_patterns=state.get("reference_expected_patterns"),
    )
    campaign_state = dict(state)
    if run_namespace == "refined":
        campaign_state["execution_status"] = state.get(
            "validation_execution_status"
        )
        campaign_state["execution_command"] = state.get(
            "validation_execution_command", ""
        )
        campaign_state["execution_artifact_dir"] = state.get(
            "validation_execution_artifact_dir", ""
        )
    return run_mutation_campaign(
        campaign_state,
        test_code,
        reference_dir,
        create_workspace=_new_workspace,
        write_test_project=_write_behave_project,
        run_test=_run_behave,
        report_filename=report_filename,
        run_namespace=run_namespace,
        cleanup_root=ARTIFACT_ROOT / "workspaces",
    )


def mutation_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Run bounded app-source mutation testing only after a passing baseline test."""
    if not _as_bool(state.get("enable_mutation"), False):
        return _mutation_skip("SKIPPED_DISABLED", "Mutation testing is disabled by configuration.")
    if state.get("execution_status") != "PASSED":
        return _mutation_skip("SKIPPED_BASELINE_NOT_PASS", "Baseline execution did not pass.")
    try:
        return _run_general_mutation_evaluation(
            state,
            str(state["executable_test_code"]),
            "baseline",
            "mutation_report.json",
        )
    except Exception as exc:
        return _mutation_skip("MUTATION_HARNESS_ERROR", repr(exc))


# ---------------------------------------------------------------------------
# Refinement validation: same local tools, separate state fields
# ---------------------------------------------------------------------------

def _normalise_fixed_code(code: Any) -> str:
    text = str(code or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _validation_execution_skip(status: str, detail: str) -> dict[str, Any]:
    return {
        "refined_syntax_passed": False,
        "validation_execution_status": status,
        "validation_execution_success": False,
        "validation_execution_return_code": -1,
        "validation_execution_duration_seconds": 0.0,
        "validation_execution_stdout_tail": "",
        "validation_execution_stderr_tail": detail,
        "validation_execution_failed_steps": [],
        "validation_execution_command": "",
        "validation_execution_environment": {},
        "validation_execution_artifact_dir": "",
        "validation_workspace_dir": "",
        "validation_behave_report_path": "",
    }


def validation_execution_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Execute Refiner output on the unmodified reference app without another LLM call."""
    if not _as_bool(state.get("enable_refinement_validation"), False):
        return _validation_execution_skip("SKIPPED_DISABLED", "Refinement validation disabled by configuration.")
    if not state.get("fixed_code"):
        return _validation_execution_skip("SKIPPED_NO_FIXED_CODE", "Refiner returned no code.")

    fixed_code = _normalise_fixed_code(state.get("fixed_code"))
    original_code = _normalise_fixed_code(state.get("executable_test_code"))
    if not fixed_code or fixed_code == original_code:
        return _validation_execution_skip("SKIPPED_UNCHANGED", "Refiner did not produce a changed implementation.")
    try:
        ast.parse(fixed_code)
    except SyntaxError as exc:
        return _validation_execution_skip("FIXED_CODE_SYNTAX_FAILED", f"Refined code does not parse: {exc}")

    try:
        prepared = _new_workspace(state, "refined_baseline")
        _write_behave_project(prepared, str(state["excutable_test_test_case"]), fixed_code)
        run = _run_behave(
            prepared,
            state,
            timeout=int(state.get("execution_timeout_seconds", DEFAULT_EXECUTION_TIMEOUT)),
        )
        return {
            "refined_syntax_passed": True,
            "validation_execution_status": run["execution_status"],
            "validation_execution_success": run["execution_status"] == "PASSED",
            "validation_execution_return_code": run["execution_return_code"],
            "validation_execution_duration_seconds": run["execution_duration_seconds"],
            "validation_execution_stdout_tail": run["execution_stdout_tail"],
            "validation_execution_stderr_tail": run["execution_stderr_tail"],
            "validation_execution_failed_steps": run.get("execution_failed_steps", []),
            "validation_execution_command": run.get("execution_command", ""),
            "validation_execution_environment": run.get(
                "execution_environment", {}
            ),
            "validation_execution_artifact_dir": str(prepared.artifact_dir),
            "validation_workspace_dir": str(prepared.workspace),
            "validation_behave_report_path": run.get("behave_report_path", ""),
        }
    except Exception as exc:
        return _validation_execution_skip("HARNESS_ERROR", repr(exc))


def _validation_coverage_skip(status: str, detail: str) -> dict[str, Any]:
    return {
        "validation_dynamic_coverage_status": status,
        "validation_dynamic_coverage_score": None,
        "validation_step_success_coverage": None,
        "validation_action_step_coverage": None,
        "validation_oracle_step_coverage": None,
        "validation_steps_total": 0,
        "validation_steps_passed": 0,
        "validation_steps_failed": 0,
        "validation_coverage_detail": detail,
    }


def validation_coverage_agent(state: dict[str, Any]) -> dict[str, Any]:
    status = state.get("validation_execution_status")
    if status not in {"PASSED", "TEST_FAILED"}:
        return _validation_coverage_skip("SKIPPED_EXECUTION_NOT_ANALYZABLE", "No refined Behave step evidence is available.")
    path_text = str(state.get("validation_behave_report_path", ""))
    path = Path(path_text) if path_text else None
    if not path or not path.exists():
        return _validation_coverage_skip("INCONCLUSIVE_REPORT_MISSING", "Refined Behave JSON report was not created.")
    try:
        summary = _summarize_behave_report(json.loads(path.read_text(encoding="utf-8")))
        if summary["steps_total"] == 0:
            return _validation_coverage_skip("INCONCLUSIVE_EMPTY_REPORT", "Refined report contains no scenario steps.")
        return {
            "validation_dynamic_coverage_status": "PASSED" if status == "PASSED" else "PARTIAL_REFINED_FAILURE",
            "validation_dynamic_coverage_score": summary["dynamic_coverage_score"],
            "validation_step_success_coverage": summary["step_success_coverage"],
            "validation_action_step_coverage": summary["action_step_coverage"],
            "validation_oracle_step_coverage": summary["oracle_step_coverage"],
            "validation_steps_total": summary["steps_total"],
            "validation_steps_passed": summary["steps_passed"],
            "validation_steps_failed": summary["steps_failed"],
            "validation_coverage_detail": "BDD successful-step coverage of Refiner output.",
        }
    except Exception as exc:
        return _validation_coverage_skip("COVERAGE_HARNESS_ERROR", repr(exc))


def _validation_mutation_skip(status: str, detail: str) -> dict[str, Any]:
    return {
        "validation_mutation_status": status,
        "validation_mutation_score": None,
        "validation_relevant_mutation_score": None,
        "validation_mutation_scope_status": "NO_DYNAMIC_SCOPE_EVIDENCE",
        "validation_mutants_total": 0,
        "validation_mutants_killed": 0,
        "validation_mutants_survived": 0,
        "validation_relevant_mutants_total": 0,
        "validation_relevant_mutants_killed": 0,
        "validation_relevant_mutants_survived": 0,
        "validation_mutants_timeout": 0,
        "validation_mutants_inconclusive": 0,
        "validation_mutation_report_path": "",
        "validation_surviving_mutants": [],
        "validation_mutation_detail": detail,
    }


def validation_mutation_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Run scope-aware mutation validation of Refiner output only when enabled."""
    if not _as_bool(state.get("enable_refinement_mutation_validation"), False):
        return _validation_mutation_skip("SKIPPED_DISABLED", "Refined-code mutation validation disabled by configuration.")
    if state.get("validation_execution_status") != "PASSED":
        return _validation_mutation_skip("SKIPPED_REFINED_BASELINE_NOT_PASS", "Refined baseline did not pass.")
    try:
        result = _run_general_mutation_evaluation(
            state,
            _normalise_fixed_code(state.get("fixed_code")),
            "refined",
            "refined_mutation_report.json",
        )
        return {
            "validation_mutation_status": result["mutation_status"],
            "validation_mutation_score": result["mutation_score"],
            "validation_relevant_mutation_score": result.get("relevant_mutation_score"),
            "validation_mutation_scope_status": result.get("mutation_scope_status"),
            "validation_mutants_total": result["mutants_total"],
            "validation_mutants_killed": result["mutants_killed"],
            "validation_mutants_survived": result["mutants_survived"],
            "validation_relevant_mutants_total": result.get("relevant_mutants_total", 0),
            "validation_relevant_mutants_killed": result.get("relevant_mutants_killed", 0),
            "validation_relevant_mutants_survived": result.get("relevant_mutants_survived", 0),
            "validation_mutants_timeout": result["mutants_timeout"],
            "validation_mutants_inconclusive": result["mutants_inconclusive"],
            "validation_mutation_report_path": result["mutation_report_path"],
            "validation_surviving_mutants": result["surviving_mutants"],
            "validation_mutation_detail": result["mutation_detail"],
        }
    except Exception as exc:
        return _validation_mutation_skip("MUTATION_HARNESS_ERROR", repr(exc))



def repair_safety_gate_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Accept a refiner proposal only after successful local validation.

    The original executable code remains the authoritative output whenever a proposal is
    unvalidated or regresses execution. The rejected candidate is retained separately for
    audit/debugging, never exported as the accepted fixed code.
    """
    original = _normalise_fixed_code(state.get('executable_test_code'))
    candidate = _normalise_fixed_code(state.get('fixed_code'))
    refiner_status = str(state.get('refiner_status', 'NOT_RUN'))
    validation_enabled = _as_bool(state.get('enable_refinement_validation'), False)
    baseline = str(state.get('execution_status', 'NOT_RUN'))
    validation = str(state.get('validation_execution_status', 'NOT_RUN'))

    base = {
        'refiner_candidate_code': candidate if candidate and candidate != original else '',
        'refiner_accepted': False,
        'refiner_rejection_reason': '',
    }
    if refiner_status != 'GENERATED' or not candidate or candidate == original:
        return {
            **base,
            'fixed_code': original,
            'refiner_status': refiner_status,
            'refiner_rejection_reason': 'No changed refiner proposal was available.',
        }

    if not validation_enabled:
        return {
            **base,
            'fixed_code': original,
            'refiner_status': 'PENDING_VALIDATION',
            'refiner_rejection_reason': 'Candidate retained for audit only because refinement validation is disabled.',
        }

    if validation == 'PASSED':
        return {
            **base,
            'fixed_code': candidate,
            'refiner_status': 'ACCEPTED_VALIDATED',
            'refiner_accepted': True,
            'refiner_rejection_reason': '',
        }

    if baseline == 'PASSED' and validation in {'TEST_FAILED', 'TIMEOUT'}:
        reason = f'Refined code regressed baseline executability: original={baseline}, refined={validation}.'
        status = 'REJECTED_REGRESSION'
    else:
        reason = f'Refined code failed validation and is not accepted: refined={validation}.'
        status = 'REJECTED_VALIDATION_FAILURE'

    return {
        **base,
        'fixed_code': original,
        'refiner_status': status,
        'refiner_rejection_reason': reason,
    }

def repair_comparison_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Summarise validated repair evidence without accepting unsafe candidates."""
    # Legacy direct callers (older unit tests / scripts) did not pass the safety-gate
    # fields. Preserve their deterministic comparison behavior without weakening the
    # V5.2 graph, which always invokes the safety gate before this node.
    legacy_direct_comparison = 'refiner_accepted' not in state and 'refiner_status' not in state
    acceptance = bool(state.get('refiner_accepted', legacy_direct_comparison))
    refiner_status = str(state.get('refiner_status', 'LEGACY_DIRECT' if legacy_direct_comparison else 'NOT_RUN'))
    before_execution = str(state.get('execution_status', 'NOT_RUN'))
    after_execution = str(state.get('validation_execution_status', 'NOT_RUN'))

    if refiner_status == 'PENDING_VALIDATION':
        return {
            'repair_validation_status': 'PENDING_VALIDATION',
            'repair_delta_coverage': None,
            'repair_delta_mutation': None,
            'repair_comparison_summary': 'A refiner candidate was generated but is not accepted because local validation was disabled.',
        }
    if refiner_status in {'REJECTED_REGRESSION', 'REJECTED_VALIDATION_FAILURE'}:
        return {
            'repair_validation_status': refiner_status,
            'repair_delta_coverage': None,
            'repair_delta_mutation': None,
            'repair_comparison_summary': state.get('refiner_rejection_reason', 'Refined candidate was rejected.'),
        }
    if not acceptance:
        return {
            'repair_validation_status': 'NOT_VALIDATED',
            'repair_delta_coverage': None,
            'repair_delta_mutation': None,
            'repair_comparison_summary': 'No accepted refined code is available for before/after comparison.',
        }

    before_coverage = state.get('dynamic_coverage_score')
    after_coverage = state.get('validation_dynamic_coverage_score')
    before_mutation = state.get('relevant_mutation_score')
    after_mutation = state.get('validation_relevant_mutation_score')
    if before_mutation is None and after_mutation is None:
        before_mutation = state.get('mutation_score')
        after_mutation = state.get('validation_mutation_score')

    def delta(after: Any, before: Any) -> float | None:
        if isinstance(after, (int, float)) and isinstance(before, (int, float)):
            return round(float(after) - float(before), 2)
        return None

    coverage_delta = delta(after_coverage, before_coverage)
    mutation_delta = delta(after_mutation, before_mutation)
    if before_execution == 'PASSED' and after_execution != 'PASSED':
        # This branch is reachable only for legacy direct callers. In V5.2 graph
        # execution, the safety gate rejects and restores regressions before comparison.
        status = 'REGRESSED_EXECUTABILITY'
    elif before_execution != 'PASSED' and after_execution == 'PASSED':
        status = 'IMPROVED_EXECUTABILITY'
    elif mutation_delta is not None and mutation_delta > 0:
        status = 'IMPROVED_FAULT_DETECTION'
    else:
        status = 'ACCEPTED_NO_MEASURED_IMPROVEMENT'

    return {
        'repair_validation_status': status,
        'repair_delta_coverage': coverage_delta,
        'repair_delta_mutation': mutation_delta,
        'repair_comparison_summary': (
            f'Accepted validated repair. Before execution={before_execution}; after execution={after_execution}; '
            f'diagnostic BDD step-coverage delta={coverage_delta}; '
            f'scope-aware mutation delta={mutation_delta}.'
        ),
    }
