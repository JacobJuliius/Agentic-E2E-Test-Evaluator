"""Tests for conservative, reproducible reference-source resolution."""
from __future__ import annotations

import io
import logging
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import reference_resolver as module


def _write_source(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.html").write_text("<html></html>", encoding="utf-8")
    (directory / "app.js").write_text("const ready = true;", encoding="utf-8")


def test_local_directory_resolution(tmp_path: Path):
    source = tmp_path / "local-project"
    _write_source(source)

    result = module.resolve_reference_source(source, tmp_path / "workspace")

    assert result["resolution_status"] == "success"
    assert result["retrieval_method"] == "local_directory"
    assert result["cache_hit"] is False
    assert result["validation_result"]["valid"] is True


def test_anonymous_benchmark_url_prefers_local_e2e_data_without_network(
    monkeypatch, tmp_path: Path, caplog
):
    benchmark_root = tmp_path / "E2E_data"
    bench_02 = benchmark_root / "E2ESD_Bench_02"
    _write_source(bench_02)
    monkeypatch.setattr(module, "LOCAL_BENCHMARK_ROOT", benchmark_root)
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("HTTP must not run")
        ),
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Git must not run")
        ),
    )
    url = (
        "https://anonymous.4open.science/r/E2EDev/"
        "E2EDev_data/E2ESD_Bench_02/"
    )

    with caplog.at_level(logging.INFO):
        result = module.resolve_project_source(
            reference_url=url,
            workspace_root=tmp_path / "artifacts",
            allow_network=True,
        )

    assert result["resolution_status"] == "success"
    assert result["retrieval_method"] == "local_benchmark_directory"
    assert result["source_origin"] == "LOCAL_BENCHMARK_SOURCE"
    assert Path(result["resolved_source_project_dir"]) == bench_02.resolve()
    assert "[reference] Using local benchmark source:" in caplog.text


def test_missing_anonymous_benchmark_reports_expected_path_without_git(
    monkeypatch, tmp_path: Path
):
    benchmark_root = tmp_path / "E2E_data"
    monkeypatch.setattr(module, "LOCAL_BENCHMARK_ROOT", benchmark_root)
    monkeypatch.setattr(
        module,
        "PACKAGED_BENCHMARK_ROOT",
        tmp_path / "packaged-reference-sources",
    )
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("HTTP must not run")
        ),
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Git must not run")
        ),
    )

    result = module.resolve_reference_source(
        "https://anonymous.4open.science/r/E2EDev/"
        "E2EDev_data/E2ESD_Bench_05/",
        tmp_path / "artifacts",
        allow_network=True,
    )

    expected = (benchmark_root / "E2ESD_Bench_05").resolve()
    assert result["resolution_status"] == "failed"
    assert result["retrieval_method"] == "local_benchmark_directory"
    assert str(expected) in result["failure_reason"]
    assert "Network and git retrieval are not attempted" in result["failure_reason"]


def test_anonymous_benchmark_falls_back_to_packaged_reference_sources(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(
        module, "LOCAL_BENCHMARK_ROOT", tmp_path / "missing-E2E_data"
    )
    packaged_root = tmp_path / "packaged"
    packaged_benchmark = packaged_root / "E2ESD_Bench_03"
    _write_source(packaged_benchmark)
    monkeypatch.setattr(
        module, "PACKAGED_BENCHMARK_ROOT", packaged_root
    )

    result = module.resolve_reference_source(
        "https://anonymous.4open.science/r/E2EDev/"
        "E2EDev_data/E2ESD_Bench_03/",
        tmp_path / "artifacts",
        allow_network=False,
    )

    assert result["resolution_status"] == "success"
    assert Path(result["local_path"]) == packaged_benchmark.resolve()


def test_cached_remote_reference_is_reused_without_network(
    monkeypatch, tmp_path: Path
):
    url = "https://example.test/team/sample-project.git"
    project_id = module.derive_project_identifier(url)
    cached = tmp_path / "workspace" / "reference_cache" / project_id
    _write_source(cached)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("network subprocess must not run")
        ),
    )

    result = module.resolve_reference_source(
        url, tmp_path / "workspace", allow_network=False
    )

    assert result["resolution_status"] == "success"
    assert result["cache_hit"] is True
    assert result["retrieval_method"] == "cache"
    assert Path(result["local_path"]) == cached.resolve()


def test_uncached_remote_is_skipped_when_network_disabled(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("HTTP must not run")
        ),
    )
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Git must not run")
        ),
    )

    result = module.resolve_reference_source(
        "https://example.test/team/project",
        tmp_path / "workspace",
        allow_network=False,
    )

    assert result["resolution_status"] == "skipped"
    assert result["retrieval_method"] == "none"
    assert "network retrieval is disabled" in result["failure_reason"]


def test_git_repository_is_cloned_and_cached(monkeypatch, tmp_path: Path):
    url = "https://git.example.test/team/project.git"

    def fake_run(command, **kwargs):
        assert command[:4] == ["git", "clone", "--depth", "1"]
        _write_source(Path(command[-1]))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.resolve_reference_source(
        url,
        tmp_path / "workspace",
        allow_network=True,
        timeout_seconds=12,
    )

    assert result["resolution_status"] == "success"
    assert result["retrieval_method"] == "git_clone"
    assert result["cache_hit"] is False
    assert Path(result["local_path"]).is_dir()
    assert (Path(result["local_path"]) / ".reference-resolution.json").is_file()


class _FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


def test_explicit_archive_is_downloaded_and_cached(monkeypatch, tmp_path: Path):
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("repository/index.html", "<html></html>")
        archive.writestr("repository/src/app.js", "const value = 1;")
    payload = archive_buffer.getvalue()

    def fake_urlopen(request, timeout):
        assert request.full_url.endswith(".zip")
        assert timeout == 15
        return _FakeResponse(payload)

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    result = module.resolve_reference_source(
        "https://downloads.example.test/project-main.zip",
        tmp_path / "workspace",
        allow_network=True,
        timeout_seconds=15,
    )

    assert result["resolution_status"] == "success"
    assert result["retrieval_method"] == "archive_download"
    assert result["validation_result"]["valid"] is True
    assert "repository/src/app.js" in result["validation_result"]["matched_files"]


def test_explicit_codeload_zip_endpoint_is_supported(monkeypatch, tmp_path: Path):
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as archive:
        archive.writestr("repo-main/index.html", "<html></html>")
    payload = archive_buffer.getvalue()
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda request, timeout: _FakeResponse(payload),
    )

    result = module.resolve_reference_source(
        "https://codeload.example.test/org/repo/zip/refs/heads/main",
        tmp_path / "workspace",
        allow_network=True,
    )

    assert result["resolution_status"] == "success"
    assert result["retrieval_method"] == "archive_download"


def test_non_git_page_failure_does_not_fall_back_to_scraping(
    monkeypatch, tmp_path: Path
):
    def fake_run(command, **kwargs):
        return SimpleNamespace(
            returncode=128,
            stdout="",
            stderr="fatal: repository not found",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    monkeypatch.setattr(
        module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("resolver must not scrape the page")
        ),
    )

    result = module.resolve_reference_source(
        "https://example.test/a/directory-page/",
        tmp_path / "workspace",
        allow_network=True,
    )

    assert result["resolution_status"] == "failed"
    assert result["retrieval_method"] == "git_clone"
    assert "no explicit archive endpoint" in result["failure_reason"]


def test_git_timeout_is_structured_failure(monkeypatch, tmp_path: Path):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module.resolve_reference_source(
        "https://git.example.test/team/slow.git",
        tmp_path / "workspace",
        allow_network=True,
        timeout_seconds=3,
    )

    assert result["resolution_status"] == "failed"
    assert "exceeded 3s" in result["failure_reason"]


def test_dynamic_tools_use_shared_resolver(monkeypatch, tmp_path: Path):
    import dynamic_agents

    source = tmp_path / "resolved-project"
    _write_source(source)
    captured = {}

    def fake_resolve(source_url, workspace_root, **kwargs):
        captured["source_url"] = source_url
        captured["workspace_root"] = Path(workspace_root)
        return {
            "resolution_status": "success",
            "source_url": source_url,
            "project_identifier": "mock-project",
            "local_path": str(source),
            "cache_hit": True,
            "retrieval_method": "cache",
            "validation_result": {"valid": True},
            "failure_reason": "",
        }

    monkeypatch.setattr(
        dynamic_agents, "resolve_reference_source", fake_resolve
    )
    resolved = dynamic_agents._clone_reference(
        "https://example.test/mock.git",
        workspace_root=tmp_path / "resolver-workspace",
        allow_network=False,
    )

    assert resolved == source
    assert captured["source_url"] == "https://example.test/mock.git"
    assert captured["workspace_root"] == tmp_path / "resolver-workspace"
