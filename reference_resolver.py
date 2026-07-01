"""Reproducible reference-source resolution for dynamic E2E evaluation.

Retrieval is deliberately conservative:

* local directories are validated and used directly;
* explicit archive URLs are downloaded and safely extracted;
* other supported network URLs are passed to ``git clone``;
* arbitrary HTML pages are never scraped for repository links.

All network activity is opt-in. Remote content is prepared in a staging directory,
validated, and only then moved into the persistent cache.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterable

from e2e_eval.utils.paths import sanitize_export_payload

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
DEFAULT_SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".go", ".htm", ".html", ".java",
    ".js", ".jsx", ".php", ".py", ".rb", ".rs", ".scala", ".ts", ".tsx",
}
IGNORED_PARTS = {
    ".git", ".hg", ".svn", ".pytest_cache", "__pycache__", "artifacts",
    "node_modules", "venv", ".venv",
}
ARCHIVE_SUFFIXES = (
    ".zip", ".tar.gz", ".tgz", ".tar", ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
)
REPOSITORY_ROOT = Path(__file__).resolve().parent
LOCAL_BENCHMARK_ROOT = REPOSITORY_ROOT / "E2E_data"
ANONYMOUS_BENCHMARK_HOST = "anonymous.4open.science"
BENCHMARK_NAME_RE = re.compile(r"^E2ESD_Bench_[0-9]+$")


def _safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    return cleaned or "reference-project"


def derive_project_identifier(source_url: str) -> str:
    """Derive a readable, collision-resistant identifier from a URL or path."""
    normalized = str(source_url).strip().rstrip("/")
    parsed = urllib.parse.urlparse(normalized)
    candidate = Path(parsed.path or normalized).name
    lower = candidate.lower()
    for suffix in sorted(ARCHIVE_SUFFIXES + (".git",), key=len, reverse=True):
        if lower.endswith(suffix):
            candidate = candidate[:-len(suffix)]
            break
    slug = _safe_slug(candidate)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    return f"{slug}--{digest}"


def _legacy_identifier(source_url: str) -> str:
    """Return the historical basename cache key for backward compatibility."""
    parsed = urllib.parse.urlparse(str(source_url).strip().rstrip("/"))
    return _safe_slug(Path(parsed.path or str(source_url)).name)


def _anonymous_benchmark_name(source_url: str) -> str | None:
    """Extract a safe E2E benchmark directory name from a known host URL."""
    parsed = urllib.parse.urlparse(str(source_url).strip())
    if (parsed.hostname or "").lower() != ANONYMOUS_BENCHMARK_HOST:
        return None
    for part in reversed([
        urllib.parse.unquote(item)
        for item in parsed.path.split("/")
        if item
    ]):
        if BENCHMARK_NAME_RE.fullmatch(part):
            return part
    return None


def _patterns(value: Iterable[str] | None) -> list[str]:
    return [str(item).strip() for item in (value or []) if str(item).strip()]


def validate_source_directory(
    directory: Path,
    expected_patterns: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Validate a resolved project and return auditable evidence."""
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        message = (
            f"Source directory does not exist: {directory}"
            if not directory.exists()
            else f"Resolved path is not a directory: {directory}"
        )
        return {
            "valid": False,
            "expected_patterns": _patterns(expected_patterns),
            "matched_files": [],
            "missing_patterns": [],
            "message": message,
        }

    expected = _patterns(expected_patterns)
    matched: list[str] = []
    missing: list[str] = []
    if expected:
        for pattern in expected:
            matches = [
                path for path in directory.glob(pattern)
                if path.is_file()
                and not any(
                    part in IGNORED_PARTS
                    for part in path.relative_to(directory).parts
                )
            ]
            if not matches:
                missing.append(pattern)
            matched.extend(path.relative_to(directory).as_posix() for path in matches)
    else:
        for path in directory.rglob("*"):
            if (
                path.is_file()
                and path.suffix.lower() in DEFAULT_SOURCE_SUFFIXES
                and not any(
                    part in IGNORED_PARTS
                    for part in path.relative_to(directory).parts
                )
            ):
                matched.append(path.relative_to(directory).as_posix())

    matched = sorted(set(matched))
    valid = bool(matched) and not missing
    if missing:
        message = "Missing expected source patterns: " + ", ".join(missing)
    elif not matched:
        message = "No recognized source files were found."
    else:
        message = f"Validated {len(matched)} source file(s)."
    return {
        "valid": valid,
        "expected_patterns": expected,
        "matched_files": matched[:200],
        "missing_patterns": missing,
        "message": message,
    }


def _locate_index(directory: Path) -> Path | None:
    direct = directory / "index.html"
    if direct.is_file():
        return direct.resolve()
    matches = sorted(directory.rglob("index.html"))
    return matches[0].resolve() if matches else None


def resolve_project_source(
    *,
    source_project_dir: str = "",
    reference_url: str = "",
    workspace_root: str | Path = "artifacts",
    allow_network: bool = False,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    expected_patterns: Iterable[str] | None = None,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
) -> dict[str, Any]:
    """Resolve a project local-first and attach execution provenance.

    A supplied local directory is accepted only when an ``index.html`` can be
    found recursively. Its parent becomes the canonical project root. Invalid
    overrides are recorded and then fall back to the normal cache/network
    resolver.
    """
    input_dir = str(source_project_dir or "").strip()
    override_diagnostic = ""
    if input_dir:
        candidate = Path(input_dir).expanduser()
        if not candidate.is_dir():
            override_diagnostic = (
                (
                    f"Local source override does not exist: {candidate}"
                    if not candidate.exists()
                    else f"Local source override is not a directory: {candidate}"
                )
            )
        else:
            entrypoint = _locate_index(candidate.resolve())
            if entrypoint is None:
                override_diagnostic = (
                    f"Local source override contains no index.html: {candidate}"
                )
            else:
                project_root = entrypoint.parent
                validation = validate_source_directory(
                    project_root, expected_patterns
                )
                return {
                    "resolution_status": "success",
                    "source_url": reference_url,
                    "project_identifier": derive_project_identifier(
                        reference_url or str(project_root)
                    ),
                    "local_path": str(project_root),
                    "cache_hit": False,
                    "retrieval_method": "local_override",
                    "validation_result": validation,
                    "failure_reason": "",
                    "source_origin": "LOCAL_SOURCE_OVERRIDE",
                    "input_source_project_dir": input_dir,
                    "resolved_source_project_dir": str(project_root),
                    "resolved_entrypoint": str(entrypoint),
                    "local_override_diagnostic": "",
                }

    resolution = resolve_reference_source(
        reference_url,
        workspace_root,
        allow_network=allow_network,
        timeout_seconds=timeout_seconds,
        expected_patterns=expected_patterns,
        max_archive_bytes=max_archive_bytes,
    )
    resolved_dir = ""
    resolved_entrypoint = ""
    if resolution["resolution_status"] == "success":
        root = Path(resolution["local_path"])
        entrypoint = _locate_index(root)
        if entrypoint is None:
            resolution = {
                **resolution,
                "resolution_status": "failed",
                "failure_reason": (
                    "Resolved reference contains no index.html: " + str(root)
                ),
            }
        else:
            resolved_dir = str(entrypoint.parent)
            resolved_entrypoint = str(entrypoint)
            resolution["local_path"] = resolved_dir

    retrieval_method = resolution.get("retrieval_method")
    network_method = retrieval_method in {
        "archive_download", "git_clone"
    } and not resolution.get("cache_hit", False)
    if (
        resolution.get("resolution_status") != "success"
        and override_diagnostic
    ):
        resolution["failure_reason"] = (
            override_diagnostic
            + (
                " Fallback resolution failed: "
                + str(resolution.get("failure_reason", ""))
                if resolution.get("failure_reason")
                else ""
            )
        )
    return {
        **resolution,
        "source_origin": (
            "NETWORK_REFERENCE"
            if network_method
            else (
                "LOCAL_BENCHMARK_SOURCE"
                if retrieval_method == "local_benchmark_directory"
                else "REFERENCE_CACHE"
            )
        ),
        "input_source_project_dir": input_dir,
        "resolved_source_project_dir": resolved_dir,
        "resolved_entrypoint": resolved_entrypoint,
        "local_override_diagnostic": override_diagnostic,
    }


def _result(
    *,
    status: str,
    source_url: str,
    project_identifier: str,
    local_path: str = "",
    cache_hit: bool = False,
    retrieval_method: str = "none",
    validation_result: dict[str, Any] | None = None,
    failure_reason: str = "",
) -> dict[str, Any]:
    return {
        "resolution_status": status,
        "source_url": source_url,
        "project_identifier": project_identifier,
        "local_path": local_path,
        "cache_hit": cache_hit,
        "retrieval_method": retrieval_method,
        "validation_result": validation_result or {
            "valid": False,
            "matched_files": [],
            "message": "Not validated.",
        },
        "failure_reason": failure_reason,
    }


def _archive_kind(source_url: str) -> str | None:
    """Recognize explicit archive endpoints without fetching an HTML page."""
    parsed = urllib.parse.urlparse(source_url)
    path = urllib.parse.unquote(parsed.path).lower()
    query_values = " ".join(
        value.lower()
        for values in urllib.parse.parse_qs(parsed.query).values()
        for value in values
    )
    descriptor = f"{path} {query_values}"
    if path.endswith(".zip") or "/zip/" in path or ".zip" in query_values:
        return "zip"
    if (
        any(path.endswith(suffix) for suffix in ARCHIVE_SUFFIXES if suffix != ".zip")
        or "/tarball/" in path
        or "/tar.gz/" in path
        or any(suffix in query_values for suffix in ARCHIVE_SUFFIXES if suffix != ".zip")
    ):
        return "tar"
    return None


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath([str(root), str(candidate)]) == str(root)
    except ValueError:
        return False


def _safe_extract_zip(
    archive: Path,
    destination: Path,
    max_extracted_bytes: int,
) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive) as handle:
        total_size = sum(member.file_size for member in handle.infolist())
        if total_size > max_extracted_bytes:
            raise ValueError(
                f"Extracted ZIP exceeds size limit ({max_extracted_bytes} bytes)."
            )
        for member in handle.infolist():
            target = (destination / member.filename).resolve()
            if not _is_within(root, target):
                raise ValueError(f"Unsafe ZIP member path: {member.filename}")
        handle.extractall(destination)


def _safe_extract_tar(
    archive: Path,
    destination: Path,
    max_extracted_bytes: int,
) -> None:
    root = destination.resolve()
    with tarfile.open(archive, mode="r:*") as handle:
        members = handle.getmembers()
        total_size = sum(member.size for member in members if member.isfile())
        if total_size > max_extracted_bytes:
            raise ValueError(
                f"Extracted TAR exceeds size limit ({max_extracted_bytes} bytes)."
            )
        for member in members:
            target = (destination / member.name).resolve()
            if not _is_within(root, target):
                raise ValueError(f"Unsafe TAR member path: {member.name}")
            if member.issym() or member.islnk():
                raise ValueError(f"Archive links are not accepted: {member.name}")
            if not member.isfile() and not member.isdir():
                raise ValueError(
                    f"Unsupported special archive member: {member.name}"
                )
        handle.extractall(destination, members=members)


def _download_archive(
    source_url: str,
    archive_path: Path,
    *,
    timeout_seconds: int,
    max_archive_bytes: int,
) -> None:
    started = time.monotonic()
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "Agentic-E2E-Evaluator/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > max_archive_bytes:
            raise ValueError(
                f"Archive exceeds configured size limit ({max_archive_bytes} bytes)."
            )
        total = 0
        with archive_path.open("wb") as output:
            while True:
                if time.monotonic() - started > timeout_seconds:
                    raise TimeoutError(
                        f"Archive download exceeded {timeout_seconds}s."
                    )
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_archive_bytes:
                    raise ValueError(
                        f"Archive exceeds configured size limit ({max_archive_bytes} bytes)."
                    )
                output.write(chunk)


def _write_cache_metadata(target: Path, result: dict[str, Any]) -> None:
    metadata = {
        "source_url": result["source_url"],
        "project_identifier": result["project_identifier"],
        "retrieval_method": result["retrieval_method"],
        "validation_result": result["validation_result"],
    }
    (target / ".reference-resolution.json").write_text(
        json.dumps(
            sanitize_export_payload(metadata),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def resolve_reference_source(
    source_url: str,
    workspace_root: str | Path,
    *,
    allow_network: bool = False,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    expected_patterns: Iterable[str] | None = None,
    max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
) -> dict[str, Any]:
    """Resolve a local/reference URL into a validated reusable project directory."""
    source_url = str(source_url or "").strip()
    project_identifier = derive_project_identifier(source_url or "missing")
    if not source_url:
        return _result(
            status="failed",
            source_url="",
            project_identifier=project_identifier,
            failure_reason="Reference source URL/path is empty.",
        )

    timeout_seconds = max(1, int(timeout_seconds))
    workspace = Path(workspace_root).expanduser().resolve()
    cache_root = workspace / "reference_cache"

    parsed = urllib.parse.urlparse(source_url)
    benchmark_name = _anonymous_benchmark_name(source_url)
    is_anonymous_benchmark_host = (
        (parsed.hostname or "").lower() == ANONYMOUS_BENCHMARK_HOST
    )
    if benchmark_name:
        local_benchmark = (LOCAL_BENCHMARK_ROOT / benchmark_name).resolve()
        validation = validate_source_directory(
            local_benchmark, expected_patterns
        )
        if validation["valid"]:
            logger.info(
                "[reference] Using local benchmark source: %s",
                local_benchmark,
            )
            return _result(
                status="success",
                source_url=source_url,
                project_identifier=project_identifier,
                local_path=str(local_benchmark),
                cache_hit=False,
                retrieval_method="local_benchmark_directory",
                validation_result=validation,
            )
        return _result(
            status="failed",
            source_url=source_url,
            project_identifier=project_identifier,
            local_path=str(local_benchmark),
            retrieval_method="local_benchmark_directory",
            validation_result=validation,
            failure_reason=(
                f"Local benchmark source is required for {source_url}. "
                f"Expected a usable project at: {local_benchmark}. "
                f"{validation['message']} Network and git retrieval are not "
                f"attempted for {ANONYMOUS_BENCHMARK_HOST} URLs."
            ),
        )
    if is_anonymous_benchmark_host:
        return _result(
            status="failed",
            source_url=source_url,
            project_identifier=project_identifier,
            retrieval_method="local_benchmark_directory",
            failure_reason=(
                f"Cannot identify an E2ESD_Bench_<number> folder in "
                f"{source_url}. Expected a matching local project below: "
                f"{LOCAL_BENCHMARK_ROOT.resolve()}. Network and git retrieval "
                f"are not attempted for {ANONYMOUS_BENCHMARK_HOST} URLs."
            ),
        )

    windows_path = bool(re.match(r"^[A-Za-z]:[\\/]", source_url))
    scp_git_url = bool(re.match(r"^[^@\s]+@[^:\s]+:.+", source_url))
    if windows_path:
        local_candidate = Path(source_url).expanduser()
    elif parsed.scheme == "file":
        local_candidate = Path(urllib.request.url2pathname(parsed.path))
    elif parsed.scheme in {"", None} and not scp_git_url:
        local_candidate = Path(source_url).expanduser()
    else:
        local_candidate = None

    if local_candidate is not None:
        validation = validate_source_directory(local_candidate, expected_patterns)
        if validation["valid"]:
            resolved = str(local_candidate.resolve())
            result = _result(
                status="success",
                source_url=source_url,
                project_identifier=project_identifier,
                local_path=resolved,
                retrieval_method="local_directory",
                validation_result=validation,
            )
            logger.info("Resolved local reference %s -> %s", source_url, resolved)
            return result
        return _result(
            status="failed",
            source_url=source_url,
            project_identifier=project_identifier,
            local_path=str(local_candidate),
            retrieval_method="local_directory",
            validation_result=validation,
            failure_reason=validation["message"],
        )

    if parsed.scheme not in {"http", "https", "git", "ssh"} and not scp_git_url:
        return _result(
            status="failed",
            source_url=source_url,
            project_identifier=project_identifier,
            failure_reason=f"Unsupported reference URL scheme: {parsed.scheme!r}",
        )

    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / project_identifier

    # Reuse caches produced by this resolver. Also accept a valid historical
    # basename cache so existing evaluator artifacts remain usable.
    cache_candidates = [target, cache_root / _legacy_identifier(source_url)]
    for candidate in dict.fromkeys(cache_candidates):
        if not candidate.is_dir():
            continue
        validation = validate_source_directory(candidate, expected_patterns)
        if validation["valid"]:
            return _result(
                status="success",
                source_url=source_url,
                project_identifier=project_identifier,
                local_path=str(candidate.resolve()),
                cache_hit=True,
                retrieval_method="cache",
                validation_result=validation,
            )
        if candidate == target:
            return _result(
                status="failed",
                source_url=source_url,
                project_identifier=project_identifier,
                local_path=str(candidate),
                cache_hit=True,
                retrieval_method="cache",
                validation_result=validation,
                failure_reason="Cached reference failed validation: "
                               + validation["message"],
            )

    if not allow_network:
        return _result(
            status="skipped",
            source_url=source_url,
            project_identifier=project_identifier,
            failure_reason=(
                "Reference is not cached and network retrieval is disabled. "
                "Enable E2E_REFERENCE_NETWORK_ENABLED or provide a local directory."
            ),
        )

    staging = cache_root / f".{project_identifier}.staging-{uuid.uuid4().hex}"
    content = staging / "content"
    try:
        staging.mkdir(parents=True, exist_ok=False)
        archive_kind = _archive_kind(source_url)
        if archive_kind:
            archive_path = staging / "reference.archive"
            content.mkdir()
            _download_archive(
                source_url,
                archive_path,
                timeout_seconds=timeout_seconds,
                max_archive_bytes=max_archive_bytes,
            )
            if archive_kind == "zip":
                _safe_extract_zip(
                    archive_path, content, max_archive_bytes
                )
            else:
                _safe_extract_tar(
                    archive_path, content, max_archive_bytes
                )
            retrieval_method = "archive_download"
        else:
            command = ["git", "clone", "--depth", "1", source_url, str(content)]
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                )
            except FileNotFoundError as exc:
                raise RuntimeError("Git is not installed or not available on PATH.") from exc
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError(
                    f"Git clone exceeded {timeout_seconds}s."
                ) from exc
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
                raise RuntimeError(
                    "URL is not a usable Git repository and no explicit archive "
                    f"endpoint was provided. Git said: {detail or 'unknown error'}"
                )
            retrieval_method = "git_clone"

        validation = validate_source_directory(content, expected_patterns)
        if not validation["valid"]:
            return _result(
                status="failed",
                source_url=source_url,
                project_identifier=project_identifier,
                retrieval_method=retrieval_method,
                validation_result=validation,
                failure_reason="Retrieved content failed validation: "
                               + validation["message"],
            )

        # Another process may have completed the same cache while retrieval ran.
        if target.exists():
            existing_validation = validate_source_directory(target, expected_patterns)
            if existing_validation["valid"]:
                return _result(
                    status="success",
                    source_url=source_url,
                    project_identifier=project_identifier,
                    local_path=str(target.resolve()),
                    cache_hit=True,
                    retrieval_method="cache",
                    validation_result=existing_validation,
                )
            raise RuntimeError(
                f"Cache target appeared but is invalid: {target}"
            )

        content.replace(target)
        result = _result(
            status="success",
            source_url=source_url,
            project_identifier=project_identifier,
            local_path=str(target.resolve()),
            cache_hit=False,
            retrieval_method=retrieval_method,
            validation_result=validation,
        )
        _write_cache_metadata(target, result)
        logger.info(
            "Resolved remote reference %s via %s -> %s",
            source_url,
            retrieval_method,
            target,
        )
        return result
    except Exception as exc:
        logger.warning("Reference resolution failed for %s: %s", source_url, exc)
        return _result(
            status="failed",
            source_url=source_url,
            project_identifier=project_identifier,
            retrieval_method=(
                "archive_download" if _archive_kind(source_url) else "git_clone"
            ),
            failure_reason=str(exc),
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
