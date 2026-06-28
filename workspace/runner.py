"""Run one generated Behave/Selenium test against an E2EDev source project.

Example (run from your project root):
    python runner.py \
      --csv e2edev_sample.csv \
      --dataset-root E2EDev_data \
      --benchmark-id E2ESD_Bench_01 \
      --req-id 1 --test-id 1

Expected dataset layout:
    E2EDev_data/
      E2ESD_Bench_01/
        source_project/
          index.html
          script.js
          ...

This runner intentionally executes each test in an isolated temporary workspace,
so later mutation-testing experiments cannot corrupt the original benchmark.
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
import textwrap
import threading
import time
import traceback
from dataclasses import asdict, dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


@dataclass
class ExecutionResult:
    benchmark_id: str
    req_id: str
    test_id: str
    execution_status: str  # pass | fail | error | timeout | setup_error
    return_code: int | None
    duration_seconds: float
    normalized_url: bool
    workspace: str
    stdout_path: str
    stderr_path: str
    report_path: str
    error_type: str | None = None
    error_message: str | None = None


class QuietHTTPRequestHandler(SimpleHTTPRequestHandler):
    """Suppress normal HTTP access logs; keep runner output readable."""

    def log_message(self, fmt: str, *args: object) -> None:
        return


def free_port() -> int:
    """Ask the operating system for an available local TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_static_server(app_dir: Path) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    """Serve app_dir on localhost in a background thread."""
    port = free_port()

    def handler(*args: Any, **kwargs: Any) -> QuietHTTPRequestHandler:
        return QuietHTTPRequestHandler(*args, directory=str(app_dir), **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{port}"


def read_csv_row(csv_path: Path, benchmark_id: str, req_id: str, test_id: str) -> dict[str, str]:
    """Return exactly one benchmark row, failing clearly for duplicates/missing rows."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    matches = [
        row
        for row in rows
        if str(row.get("id", "")) == str(benchmark_id)
        and str(row.get("req_id", "")) == str(req_id)
        and str(row.get("test_id", "")) == str(test_id)
    ]
    if not matches:
        raise ValueError(
            f"No row found for id={benchmark_id!r}, req_id={req_id!r}, test_id={test_id!r}."
        )
    if len(matches) > 1:
        raise ValueError(
            f"Found {len(matches)} duplicate rows for id={benchmark_id!r}, "
            f"req_id={req_id!r}, test_id={test_id!r}."
        )
    return matches[0]


def normalize_generated_code(code: str, base_url: str) -> tuple[str, bool]:
    """Patch only known local-file navigation patterns into the local HTTP URL.

    This preserves the rest of the generated test verbatim. The transformation is
    recorded in the final JSON result because it changes execution environment,
    not the test's assertion logic.
    """
    if not code or not code.strip():
        raise ValueError("Column 'excutable_test_step_code' is empty.")

    escaped_url = json.dumps(f"{base_url}/index.html")
    original = code

    # Common E2EDev outputs: driver.get(f"file://index.html"), driver.get("file://index.html")
    patterns = [
        r"context\.driver\.get\(f?[\"']file://index\.html[\"']\)",
        r"driver\.get\(f?[\"']file://index\.html[\"']\)",
        r"context\.driver\.get\(f?[\"']file:///?[^\"']*index\.html[\"']\)",
        r"driver\.get\(f?[\"']file:///?[^\"']*index\.html[\"']\)",
    ]
    for pattern in patterns:
        code = re.sub(pattern, lambda _: f"context.driver.get({escaped_url})", code)

    # Some generated tests define a standalone file_path variable and interpolate it.
    code = re.sub(
        r"^\s*file_path\s*=\s*[\"']index\.html[\"']\s*$",
        f"file_path = {escaped_url}",
        code,
        flags=re.MULTILINE,
    )
    code = code.replace('context.driver.get(f"file://{file_path}")', f"context.driver.get({escaped_url})")
    code = code.replace("context.driver.get(f'file://{file_path}')", f"context.driver.get({escaped_url})")

    return code, code != original


def write_behave_harness(workspace: Path, feature_text: str, step_code: str, base_url: str) -> None:
    """Create an isolated Behave project plus a Selenium Chrome monkeypatch.

    The generated code usually calls webdriver.Chrome() without options. The
    environment hook swaps this for a headless Chrome instance, which makes the
    benchmark repeatable in CI/headless machines without editing test semantics.
    """
    features = workspace / "features"
    steps = features / "steps"
    steps.mkdir(parents=True, exist_ok=True)

    (features / "generated.feature").write_text(feature_text.strip() + "\n", encoding="utf-8")
    (steps / "generated_steps.py").write_text(step_code.rstrip() + "\n", encoding="utf-8")

    environment_py = f'''\
import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

BASE_URL = {base_url!r}
_original_chrome = webdriver.Chrome


def _headless_chrome(*args, **kwargs):
    options = kwargs.pop("options", None) or Options()
    # Safe to add repeatedly; Selenium/Chrome ignores duplicate arguments.
    for argument in [
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--window-size=1440,1000",
        "--disable-gpu",
    ]:
        options.add_argument(argument)
    kwargs["options"] = options
    return _original_chrome(*args, **kwargs)


def before_all(context):
    webdriver.Chrome = _headless_chrome
    context.base_url = BASE_URL


def after_all(context):
    webdriver.Chrome = _original_chrome
    driver = getattr(context, "driver", None)
    if driver is not None:
        try:
            driver.quit()
        except Exception:
            pass
'''
    (features / "environment.py").write_text(textwrap.dedent(environment_py), encoding="utf-8")


def classify_result(return_code: int | None, timed_out: bool, stderr: str) -> tuple[str, str | None, str | None]:
    if timed_out:
        return "timeout", "TimeoutExpired", "Behave did not finish within the configured timeout."
    if return_code == 0:
        return "pass", None, None

    # Behave returns 1 for scenario failures; setup/import/runtime failures are
    # usually visible in stderr/stdout. Keep classification conservative.
    merged = stderr.lower()
    setup_markers = ["modulenotfounderror", "webdriverexception", "sessionnotcreatedexception", "syntaxerror"]
    if any(marker in merged for marker in setup_markers):
        return "setup_error", "EnvironmentOrImportError", stderr.strip()[-1500:] or None
    return "fail", "BehaveScenarioFailure", stderr.strip()[-1500:] or None


def run_one_test(args: argparse.Namespace) -> ExecutionResult:
    csv_path = Path(args.csv).expanduser().resolve()
    dataset_root = Path(args.dataset_root).expanduser().resolve()
    source_project = dataset_root / args.benchmark_id / "source_project"

    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    if not source_project.is_dir():
        raise FileNotFoundError(f"Source project not found: {source_project}")
    if not (source_project / "index.html").is_file():
        raise FileNotFoundError(f"index.html not found under: {source_project}")

    row = read_csv_row(csv_path, args.benchmark_id, args.req_id, args.test_id)
    feature_text = row.get("excutable_test_test_case", "")
    if not feature_text.strip():
        raise ValueError("Column 'excutable_test_test_case' is empty.")

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_name = f"{args.benchmark_id}_req{args.req_id}_test{args.test_id}_{int(time.time())}"
    artifact_dir = output_dir / run_name
    artifact_dir.mkdir(parents=True, exist_ok=False)

    # Workspace must remain available if the user wants screenshots/debug files.
    # --keep-workspace controls whether it survives after a successful run.
    workspace = Path(tempfile.mkdtemp(prefix=f"e2edev_{run_name}_"))
    app_dir = workspace / "app"
    shutil.copytree(source_project, app_dir)

    server: ThreadingHTTPServer | None = None
    started = time.perf_counter()
    return_code: int | None = None
    timed_out = False
    stdout = ""
    stderr = ""
    normalized = False

    try:
        server, _thread, base_url = start_static_server(app_dir)
        normalized_code, normalized = normalize_generated_code(
            row.get("excutable_test_step_code", ""), base_url
        )
        write_behave_harness(workspace, feature_text, normalized_code, base_url)

        command = [sys.executable, "-m", "behave", "--no-capture", "--no-capture-stderr"]
        completed = subprocess.run(
            command,
            cwd=workspace,
            text=True,
            capture_output=True,
            timeout=args.timeout,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        return_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
    except Exception as exc:  # Harness-level error, not a test failure.
        stderr = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()

    duration = round(time.perf_counter() - started, 3)
    status, error_type, error_message = classify_result(return_code, timed_out, stderr)
    if return_code is None and not timed_out:
        status = "error"
        error_type = "RunnerError"
        error_message = stderr.strip()[-1500:] or "Runner stopped before Behave started."

    stdout_path = artifact_dir / "stdout.txt"
    stderr_path = artifact_dir / "stderr.txt"
    report_path = artifact_dir / "execution.json"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")

    result = ExecutionResult(
        benchmark_id=args.benchmark_id,
        req_id=str(args.req_id),
        test_id=str(args.test_id),
        execution_status=status,
        return_code=return_code,
        duration_seconds=duration,
        normalized_url=normalized,
        workspace=str(workspace),
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
        report_path=str(report_path),
        error_type=error_type,
        error_message=error_message,
    )
    report_path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")

    if status == "pass" and not args.keep_workspace:
        shutil.rmtree(workspace, ignore_errors=True)
        # Keep the JSON honest: the workspace used during execution no longer exists.
        payload = asdict(result)
        payload["workspace"] = "deleted_after_success"
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute one E2EDev Behave/Selenium generated test.")
    parser.add_argument("--csv", required=True, help="Path to e2edev_sample.csv")
    parser.add_argument("--dataset-root", required=True, help="Directory containing E2ESD_Bench_XX folders")
    parser.add_argument("--benchmark-id", required=True, help="For example: E2ESD_Bench_01")
    parser.add_argument("--req-id", required=True, help="CSV req_id")
    parser.add_argument("--test-id", required=True, help="CSV test_id")
    parser.add_argument("--output-dir", default="reports", help="Directory for execution artifacts")
    parser.add_argument("--timeout", type=int, default=60, help="Whole Behave run timeout in seconds")
    parser.add_argument("--keep-workspace", action="store_true", help="Keep temporary test/app workspace after success")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_one_test(args)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0 if result.execution_status == "pass" else 1
    except Exception as exc:
        print(f"Runner failed before execution: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
