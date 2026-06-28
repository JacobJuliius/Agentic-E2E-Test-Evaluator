"""Dependency preflight checks used by reproducibility tooling."""
from __future__ import annotations

import importlib.util
import shutil
import sys
from typing import Any


PYTHON_MODULES = {
    "langgraph": "langgraph",
    "langchain-google-genai": "langchain_google_genai",
    "python-dotenv": "dotenv",
    "pandas": "pandas",
    "tqdm": "tqdm",
    "behave": "behave",
    "selenium": "selenium",
}


def check_dependencies(
    *, coverage_enabled: bool = False, playwright_enabled: bool = False
) -> dict[str, Any]:
    modules = dict(PYTHON_MODULES)
    if coverage_enabled:
        modules["coverage"] = "coverage"
    if playwright_enabled:
        modules["playwright"] = "playwright"
    python = {
        package: importlib.util.find_spec(module) is not None
        for package, module in modules.items()
    }
    executables = {
        "git": shutil.which("git"),
        "node": shutil.which("node"),
        "npm": shutil.which("npm"),
    }
    missing = [name for name, available in python.items() if not available]
    return {
        "python_version": sys.version,
        "python_executable": sys.executable,
        "python_modules": python,
        "executables": executables,
        "missing_required": missing,
        "ready": not missing,
    }

