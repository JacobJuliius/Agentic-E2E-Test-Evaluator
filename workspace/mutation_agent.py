"""Mutation agent for E2EDev projects with inline JavaScript in index.html.

This version includes both generic JavaScript operators and domain-oriented
drag-and-drop mutations, because many E2EDev demo apps use simple imperative
code without comparison operators such as === or true/false literals.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from runner import classify_result, normalize_generated_code, read_csv_row, write_behave_harness


@dataclass
class Mutant:
    mutant_id: str
    operator: str
    line_number: int
    original: str
    replacement: str
    mutated_source: str


class QuietHTTPRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_static_server(app_dir: Path) -> tuple[ThreadingHTTPServer, str]:
    port = free_port()

    def handler(*args: Any, **kwargs: Any) -> QuietHTTPRequestHandler:
        return QuietHTTPRequestHandler(*args, directory=str(app_dir), **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def replace_once(text: str, start: int, end: int, replacement: str) -> str:
    return text[:start] + replacement + text[end:]


def make_mutant(
    source: str,
    match: re.Match[str],
    replacement: str,
    operator: str,
    serial: int,
) -> Mutant:
    return Mutant(
        mutant_id=f"M{serial:03d}",
        operator=operator,
        line_number=line_of(source, match.start()),
        original=match.group(0),
        replacement=replacement,
        mutated_source=replace_once(source, match.start(), match.end(), replacement),
    )


def generate_mutants(source: str, max_per_operator: int = 2) -> list[Mutant]:
    """Generate syntax-preserving mutants.

    The first rules are targeted at common HTML5 drag-and-drop implementations.
    The final rules are generic JavaScript mutations. A test that correctly checks
    drag payloads should kill several of the targeted mutants.
    """
    rules: list[tuple[str, re.Pattern[str], str]] = [
        # Domain-specific mutations for the observed E2ESD_Bench_01 app.
        ("drag_title_key_corruption", re.compile(r"""setData\(\s*['"]title['"]"""), "setData('title_mutant'"),
        ("drag_money_key_corruption", re.compile(r"""setData\(\s*['"]money['"]"""), "setData('money_mutant'"),
        ("drag_title_value_corruption", re.compile(r"""aP\[0\]\.innerHTML"""), "'MUTANT_TITLE'"),
        ("drag_money_value_corruption", re.compile(r"""aP\[1\]\.innerHTML"""), "'MUTANT_PRICE'"),
        ("drop_title_key_corruption", re.compile(r"""getData\(\s*['"]title['"]"""), "getData('title_mutant')"),
        ("drop_money_key_corruption", re.compile(r"""getData\(\s*['"]money['"]"""), "getData('money_mutant')"),
        ("disable_drag_handler", re.compile(r"""ondragstart\s*=\s*function\s*\([^)]*\)\s*\{"""), "ondragstart = function (ev) { return false; //"),
        ("disable_drop_handler", re.compile(r"""ondrop\s*=\s*function\s*\([^)]*\)\s*\{"""), "ondrop = function (ev) { return false; //"),

        # Generic JavaScript mutations.
        ("strict_equal_to_not_equal", re.compile(r"==="), "!=="),
        ("strict_not_equal_to_equal", re.compile(r"!=="), "==="),
        ("true_to_false", re.compile(r"\btrue\b"), "false"),
        ("false_to_true", re.compile(r"\bfalse\b"), "true"),
        ("greater_to_greater_equal", re.compile(r"(?<![<>=])>(?!=)"), ">="),
        ("less_to_less_equal", re.compile(r"(?<![<>=])<(?!=)"), "<="),
    ]

    mutants: list[Mutant] = []
    serial = 1
    seen: set[str] = set()

    for operator, pattern, replacement in rules:
        for match in list(pattern.finditer(source))[:max_per_operator]:
            mutant = make_mutant(source, match, replacement, operator, serial)
            # Avoid exact duplicate mutated sources.
            if mutant.mutated_source in seen:
                continue
            seen.add(mutant.mutated_source)
            mutants.append(mutant)
            serial += 1

    return mutants


def execute_against_app(
    app_dir: Path,
    feature_text: str,
    step_code: str,
    timeout: int,
    keep_workspace: bool,
    workspace_prefix: str,
) -> dict[str, Any]:
    workspace = Path(tempfile.mkdtemp(prefix=workspace_prefix))
    harness_app = workspace / "app"
    shutil.copytree(app_dir, harness_app)

    server: ThreadingHTTPServer | None = None
    started = time.perf_counter()
    stdout = ""
    stderr = ""
    return_code: int | None = None
    timed_out = False
    normalized = False

    try:
        server, base_url = start_static_server(harness_app)
        normalized_code, normalized = normalize_generated_code(step_code, base_url)
        write_behave_harness(workspace, feature_text, normalized_code, base_url)

        completed = subprocess.run(
            [sys.executable, "-m", "behave", "--no-capture", "--no-capture-stderr"],
            cwd=workspace,
            text=True,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        return_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
    except Exception as exc:
        stderr = f"{type(exc).__name__}: {exc}"
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()

    status, error_type, error_message = classify_result(return_code, timed_out, stderr)
    if return_code is None and not timed_out:
        status = "error"
        error_type = "MutationRunnerError"
        error_message = stderr[-1500:] or "Execution stopped before Behave started."

    result = {
        "execution_status": status,
        "return_code": return_code,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "normalized_url": normalized,
        "error_type": error_type,
        "error_message": error_message,
        "stdout": stdout,
        "stderr": stderr,
        "workspace": str(workspace),
    }

    if not keep_workspace:
        shutil.rmtree(workspace, ignore_errors=True)
        result["workspace"] = "deleted_after_execution"

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--benchmark-id", required=True)
    parser.add_argument("--req-id", required=True)
    parser.add_argument("--test-id", required=True)
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-mutants", type=int, default=8)
    parser.add_argument("--max-per-operator", type=int, default=2)
    parser.add_argument("--keep-workspace", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv).resolve()
    dataset_root = Path(args.dataset_root).resolve()
    source_project = dataset_root / args.benchmark_id / "source_project"
    html_path = source_project / "index.html"
    js_path = source_project / "script.js"

    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    if not source_project.is_dir():
        raise FileNotFoundError(f"Source project not found: {source_project}")

    if js_path.is_file():
        mutation_target = js_path
        target_kind = "external_js"
        target_text = js_path.read_text(encoding="utf-8")
        source = target_text
        script_span = None
    elif html_path.is_file():
        mutation_target = html_path
        target_kind = "inline_html_script"
        target_text = html_path.read_text(encoding="utf-8")
        blocks = list(re.finditer(
            r"(<script\b[^>]*>)(.*?)(</script\s*>)",
            target_text,
            flags=re.IGNORECASE | re.DOTALL,
        ))
        chosen = next((block for block in blocks if block.group(2).strip()), None)
        if chosen is None:
            raise RuntimeError("No non-empty inline <script> block found in index.html.")
        source = chosen.group(2)
        script_span = (chosen.start(2), chosen.end(2))
    else:
        raise FileNotFoundError("No script.js or index.html was found in source_project.")

    row = read_csv_row(csv_path, args.benchmark_id, args.req_id, args.test_id)
    feature_text = row.get("excutable_test_test_case", "")
    step_code = row.get("excutable_test_step_code", "")
    if not feature_text.strip() or not step_code.strip():
        raise ValueError("CSV row lacks executable test case or step code.")

    output_dir = Path(args.output_dir).resolve()
    run_name = f"{args.benchmark_id}_req{args.req_id}_test{args.test_id}_mutation_{int(time.time())}"
    report_dir = output_dir / run_name
    report_dir.mkdir(parents=True, exist_ok=False)

    print("Running baseline test against the unmodified application...")
    baseline = execute_against_app(
        source_project, feature_text, step_code, args.timeout, args.keep_workspace,
        "e2edev_baseline_",
    )
    (report_dir / "baseline_stdout.txt").write_text(baseline["stdout"], encoding="utf-8")
    (report_dir / "baseline_stderr.txt").write_text(baseline["stderr"], encoding="utf-8")

    if baseline["execution_status"] != "pass":
        report = {
            "benchmark_id": args.benchmark_id,
            "req_id": str(args.req_id),
            "test_id": str(args.test_id),
            "mutation_status": "not_run_baseline_not_pass",
            "baseline": {k: v for k, v in baseline.items() if k not in {"stdout", "stderr"}},
        }
        (report_dir / "mutation_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 1

    mutants = generate_mutants(source, args.max_per_operator)[:args.max_mutants]
    if not mutants:
        raise RuntimeError(
            "No mutation sites found in the selected JavaScript. "
            "Inspect the inline script and add an operator appropriate to that app."
        )

    print(f"Baseline passed. Found {len(mutants)} mutant(s); executing them...")
    results: list[dict[str, Any]] = []

    for index, mutant in enumerate(mutants, start=1):
        mutant_project_root = Path(tempfile.mkdtemp(prefix=f"e2edev_project_{mutant.mutant_id}_"))
        try:
            mutant_app = mutant_project_root / "app"
            shutil.copytree(source_project, mutant_app)

            if target_kind == "external_js":
                (mutant_app / "script.js").write_text(mutant.mutated_source, encoding="utf-8")
            else:
                mutant_html = (mutant_app / "index.html").read_text(encoding="utf-8")
                assert script_span is not None
                start, end = script_span
                mutated_html = mutant_html[:start] + mutant.mutated_source + mutant_html[end:]
                (mutant_app / "index.html").write_text(mutated_html, encoding="utf-8")

            execution = execute_against_app(
                mutant_app, feature_text, step_code, args.timeout, args.keep_workspace,
                f"e2edev_{mutant.mutant_id}_",
            )
        finally:
            shutil.rmtree(mutant_project_root, ignore_errors=True)

        if execution["execution_status"] == "fail":
            verdict = "killed"
        elif execution["execution_status"] == "pass":
            verdict = "survived"
        else:
            verdict = "invalid_or_error"

        result = {
            "mutant_id": mutant.mutant_id,
            "operator": mutant.operator,
            "line_number": mutant.line_number,
            "original": mutant.original,
            "replacement": mutant.replacement,
            "verdict": verdict,
            **{k: v for k, v in execution.items() if k not in {"stdout", "stderr"}},
        }
        results.append(result)
        (report_dir / f"{mutant.mutant_id}_stdout.txt").write_text(execution["stdout"], encoding="utf-8")
        (report_dir / f"{mutant.mutant_id}_stderr.txt").write_text(execution["stderr"], encoding="utf-8")
        print(f"[{index}/{len(mutants)}] {mutant.mutant_id}: {mutant.operator} -> {verdict}")

    killed = sum(r["verdict"] == "killed" for r in results)
    survived = sum(r["verdict"] == "survived" for r in results)
    invalid = sum(r["verdict"] == "invalid_or_error" for r in results)
    score = round(100 * killed / (killed + survived), 2) if killed + survived else None

    summary = {
        "benchmark_id": args.benchmark_id,
        "req_id": str(args.req_id),
        "test_id": str(args.test_id),
        "mutation_target": str(mutation_target.relative_to(source_project)),
        "mutation_target_kind": target_kind,
        "mutants_generated": len(mutants),
        "mutants_killed": killed,
        "mutants_survived": survived,
        "mutants_invalid_or_error": invalid,
        "mutation_score_percent": score,
        "baseline": {k: v for k, v in baseline.items() if k not in {"stdout", "stderr"}},
        "mutants": results,
        "limitations": [
            "The operator set is intentionally small and application-aware.",
            "Equivalent mutants may survive even when a test is adequate.",
            "Invalid mutants and tool/runtime errors are excluded from the mutation-score denominator.",
        ],
    }
    (report_dir / "mutation_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (report_dir / "mutation_results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = sorted({key for result in results for key in result})
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    print("\n========== Mutation Summary ==========")
    print(f"Killed: {killed}; survived: {survived}; invalid/error: {invalid}")
    print(f"Mutation score: {score}%")
    print(f"Reports: {report_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Mutation agent failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
