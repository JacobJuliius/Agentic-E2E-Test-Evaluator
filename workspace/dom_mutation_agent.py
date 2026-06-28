"""DOM/inline-JS mutation agent for E2ESD_Bench_01.

This is a deliberately reliable, benchmark-specific prototype. It mutates
literal fragments in index.html rather than trying to parse/select a script tag.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
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
    original: str
    replacement: str
    mutated_html: str


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_server(app_dir: Path) -> tuple[ThreadingHTTPServer, str]:
    port = get_free_port()

    def handler(*args: Any, **kwargs: Any) -> QuietHandler:
        return QuietHandler(*args, directory=str(app_dir), **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


def first_replace(text: str, old: str, new: str) -> str | None:
    pos = text.find(old)
    if pos < 0:
        return None
    return text[:pos] + new + text[pos + len(old):]


def build_mutants(html: str) -> list[Mutant]:
    # Each replacement is designed to remain syntactically valid HTML/JavaScript.
    rules = [
        (
            "corrupt_drag_title_payload",
            "ev.dataTransfer.setData('title', aP[0].innerHTML);",
            "ev.dataTransfer.setData('title', 'MUTANT_TITLE');",
        ),
        (
            "corrupt_drag_price_payload",
            "ev.dataTransfer.setData('money', aP[1].innerHTML);",
            "ev.dataTransfer.setData('money', 'MUTANT_PRICE');",
        ),
        (
            "corrupt_drop_title_key",
            "ev.dataTransfer.getData('title')",
            "ev.dataTransfer.getData('title_mutant')",
        ),
        (
            "corrupt_drop_price_key",
            "ev.dataTransfer.getData('money')",
            "ev.dataTransfer.getData('money_mutant')",
        ),
        (
            "break_quantity_increment",
            "parseInt(box1[i].innerHTML) + 1",
            "parseInt(box1[i].innerHTML) + 0",
        ),
        (
            "break_total_accumulation",
            "iNum += moneyValue;",
            "iNum += 0;",
        ),
        (
            "wrong_total_precision",
            "iNum.toFixed(2)",
            "iNum.toFixed(0)",
        ),
        (
            "disable_drop_default_prevention",
            "oDiv.ondragover = function (ev) {\n        ev.preventDefault();",
            "oDiv.ondragover = function (ev) {\n        return;",
        ),
        (
            "change_product_1_price",
            '<p data-testid="product-price-1">$40</p>',
            '<p data-testid="product-price-1">$999</p>',
        ),
    ]

    mutants: list[Mutant] = []
    for idx, (operator, original, replacement) in enumerate(rules, start=1):
        mutated = first_replace(html, original, replacement)
        if mutated is not None:
            mutants.append(
                Mutant(
                    mutant_id=f"M{idx:03d}",
                    operator=operator,
                    original=original,
                    replacement=replacement,
                    mutated_html=mutated,
                )
            )
    return mutants


def execute(
    app_dir: Path,
    feature: str,
    step_code: str,
    timeout: int,
    keep_workspace: bool,
    prefix: str,
) -> dict[str, Any]:
    work = Path(tempfile.mkdtemp(prefix=prefix))
    copied_app = work / "app"
    shutil.copytree(app_dir, copied_app)

    server = None
    started = time.perf_counter()
    stdout = ""
    stderr = ""
    return_code: int | None = None
    timed_out = False

    try:
        server, base_url = start_server(copied_app)
        normalized_code, normalized_url = normalize_generated_code(step_code, base_url)
        write_behave_harness(work, feature, normalized_code, base_url)

        completed = subprocess.run(
            [sys.executable, "-m", "behave", "--no-capture", "--no-capture-stderr"],
            cwd=work,
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
        normalized_url = False
    except Exception as exc:
        stderr = f"{type(exc).__name__}: {exc}"
        normalized_url = False
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()

    status, error_type, error_message = classify_result(return_code, timed_out, stderr)
    if return_code is None and not timed_out:
        status = "error"
        error_type = "MutationRunnerError"
        error_message = stderr[-1500:]

    output = {
        "execution_status": status,
        "return_code": return_code,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "normalized_url": normalized_url,
        "error_type": error_type,
        "error_message": error_message,
        "stdout": stdout,
        "stderr": stderr,
        "workspace": str(work),
    }

    if not keep_workspace:
        shutil.rmtree(work, ignore_errors=True)
        output["workspace"] = "deleted_after_execution"
    return output


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
    parser.add_argument("--keep-workspace", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv).resolve()
    source_project = Path(args.dataset_root).resolve() / args.benchmark_id / "source_project"
    index_path = source_project / "index.html"

    if not index_path.is_file():
        raise FileNotFoundError(f"index.html not found: {index_path}")

    row = read_csv_row(csv_path, args.benchmark_id, args.req_id, args.test_id)
    feature = row.get("excutable_test_test_case", "")
    step_code = row.get("excutable_test_step_code", "")
    if not feature.strip() or not step_code.strip():
        raise ValueError("CSV row lacks executable test-case or step-code fields.")

    report_dir = Path(args.output_dir).resolve() / (
        f"{args.benchmark_id}_req{args.req_id}_test{args.test_id}_dom_mutation_{int(time.time())}"
    )
    report_dir.mkdir(parents=True, exist_ok=False)

    print("Running baseline...")
    baseline = execute(source_project, feature, step_code, args.timeout, args.keep_workspace, "e2edev_baseline_")
    (report_dir / "baseline_stdout.txt").write_text(baseline["stdout"], encoding="utf-8")
    (report_dir / "baseline_stderr.txt").write_text(baseline["stderr"], encoding="utf-8")

    if baseline["execution_status"] != "pass":
        raise RuntimeError(f"Baseline did not pass: {baseline['execution_status']}")

    html = index_path.read_text(encoding="utf-8")
    mutants = build_mutants(html)[:args.max_mutants]
    if not mutants:
        raise RuntimeError(
            "No benchmark-specific mutation sites were found in index.html. "
            "The file content is not the expected drag-shopping-cart implementation."
        )

    print(f"Baseline passed. Running {len(mutants)} DOM/inline-JS mutants...")
    rows: list[dict[str, Any]] = []

    for num, mutant in enumerate(mutants, start=1):
        project_root = Path(tempfile.mkdtemp(prefix=f"e2edev_mutant_{mutant.mutant_id}_"))
        try:
            app = project_root / "app"
            shutil.copytree(source_project, app)
            (app / "index.html").write_text(mutant.mutated_html, encoding="utf-8")
            outcome = execute(app, feature, step_code, args.timeout, args.keep_workspace, f"e2edev_{mutant.mutant_id}_")
        finally:
            shutil.rmtree(project_root, ignore_errors=True)

        verdict = (
            "killed" if outcome["execution_status"] == "fail"
            else "survived" if outcome["execution_status"] == "pass"
            else "invalid_or_error"
        )
        record = {
            "mutant_id": mutant.mutant_id,
            "operator": mutant.operator,
            "original": mutant.original,
            "replacement": mutant.replacement,
            "verdict": verdict,
            **{k: v for k, v in outcome.items() if k not in {"stdout", "stderr"}},
        }
        rows.append(record)
        (report_dir / f"{mutant.mutant_id}_stdout.txt").write_text(outcome["stdout"], encoding="utf-8")
        (report_dir / f"{mutant.mutant_id}_stderr.txt").write_text(outcome["stderr"], encoding="utf-8")
        print(f"[{num}/{len(mutants)}] {mutant.operator}: {verdict}")

    killed = sum(r["verdict"] == "killed" for r in rows)
    survived = sum(r["verdict"] == "survived" for r in rows)
    invalid = sum(r["verdict"] == "invalid_or_error" for r in rows)
    score = round(killed * 100 / (killed + survived), 2) if killed + survived else None

    report = {
        "benchmark_id": args.benchmark_id,
        "req_id": str(args.req_id),
        "test_id": str(args.test_id),
        "mutation_type": "DOM_and_inline_JS",
        "mutation_target": "index.html",
        "baseline": {k: v for k, v in baseline.items() if k not in {"stdout", "stderr"}},
        "mutants_generated": len(rows),
        "mutants_killed": killed,
        "mutants_survived": survived,
        "mutants_invalid_or_error": invalid,
        "mutation_score_percent": score,
        "mutants": rows,
        "limitations": [
            "This is a benchmark-specific DOM/inline-JS mutation prototype.",
            "Mutation score is meaningful only for tests that pass on the original app.",
            "Equivalent mutants and browser drag-and-drop simulation limitations can affect results."
        ],
    }
    (report_dir / "mutation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (report_dir / "mutation_results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = sorted({k for row in rows for k in row})
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nMutation score: {score}%")
    print(f"Reports: {report_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Mutation agent failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
