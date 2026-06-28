"""Central, typed configuration for evaluation runs."""
from __future__ import annotations

import json
import os
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_patterns(name: str) -> list[str]:
    return [
        item.strip() for item in os.getenv(name, "").split(",")
        if item.strip()
    ]


def env_command(name: str) -> list[str]:
    value = os.getenv(name, "").strip()
    return shlex.split(value, posix=os.name != "nt") if value else []


def _text(value: Any, default: str = "") -> str:
    if value is None or value != value:  # NaN without importing pandas.
        return default
    return str(value)


@dataclass(frozen=True)
class EvaluationConfig:
    """One serializable configuration object for graph and batch execution."""

    input_file: str = "data/e2edev_sample.csv"
    output_file: str = "artifacts/reports/evaluation_results.csv"
    max_cases: int = 0
    case_sleep_seconds: float = 5.0

    enable_dynamic: bool = False
    enable_coverage: bool = False
    enable_mutation: bool = False
    enable_dynamic_analyst: bool = False
    enable_critic: bool = True
    enable_refiner: bool = True
    enable_refinement_validation: bool = False
    enable_refinement_mutation_validation: bool = False

    reference_workspace_root: str = "artifacts"
    reference_network_enabled: bool = False
    reference_timeout_seconds: int = 90
    reference_expected_patterns: list[str] = field(default_factory=list)

    coverage_source_dir: str = ""
    coverage_timeout_seconds: int = 120
    coverage_include_patterns: list[str] = field(default_factory=list)
    coverage_exclude_patterns: list[str] = field(default_factory=list)
    coverage_branch_enabled: bool = True
    coverage_include_tests: bool = False

    max_mutants: int = 5
    mutation_max_mutants_per_file: int = 5
    mutation_seed: int = 1337
    mutation_include_patterns: list[str] = field(default_factory=list)
    mutation_exclude_patterns: list[str] = field(default_factory=list)
    mutation_project_validation_command: list[str] = field(default_factory=list)
    mutation_keep_workspaces: bool = False

    execution_timeout_seconds: int = 90
    mutant_timeout_seconds: int = 45
    relevant_mutation_refine_threshold: float = 80.0
    headless: bool = True

    @classmethod
    def from_env(cls) -> "EvaluationConfig":
        cache = Path(os.getenv(
            "E2E_REFERENCE_CACHE", "artifacts/reference_cache"
        ))
        dynamic = env_bool("E2E_ENABLE_DYNAMIC", False)
        return cls(
            input_file=os.getenv("E2E_INPUT_FILE", cls.input_file),
            output_file=os.getenv("E2E_OUTPUT_FILE", cls.output_file),
            max_cases=int(os.getenv("E2E_MAX_CASES", "0")),
            case_sleep_seconds=float(
                os.getenv("E2E_CASE_SLEEP_SECONDS", "5")
            ),
            enable_dynamic=dynamic,
            enable_coverage=env_bool("E2E_ENABLE_COVERAGE", False),
            enable_mutation=env_bool("E2E_ENABLE_MUTATION", False),
            enable_dynamic_analyst=env_bool(
                "E2E_ENABLE_DYNAMIC_ANALYST", dynamic
            ),
            enable_critic=env_bool("E2E_ENABLE_CRITIC", True),
            enable_refiner=env_bool("E2E_ENABLE_REFINER", True),
            enable_refinement_validation=env_bool(
                "E2E_ENABLE_REFINEMENT_VALIDATION", False
            ),
            enable_refinement_mutation_validation=env_bool(
                "E2E_ENABLE_REFINEMENT_MUTATION_VALIDATION", False
            ),
            reference_workspace_root=os.getenv(
                "E2E_REFERENCE_WORKSPACE_ROOT", str(cache.parent)
            ),
            reference_network_enabled=env_bool(
                "E2E_REFERENCE_NETWORK_ENABLED", False
            ),
            reference_timeout_seconds=int(
                os.getenv("E2E_REFERENCE_TIMEOUT_SECONDS", "90")
            ),
            reference_expected_patterns=env_patterns(
                "E2E_REFERENCE_EXPECTED_PATTERNS"
            ),
            coverage_source_dir=os.getenv("E2E_COVERAGE_SOURCE_DIR", ""),
            coverage_timeout_seconds=int(
                os.getenv("E2E_COVERAGE_TIMEOUT_SECONDS", "120")
            ),
            coverage_include_patterns=env_patterns(
                "E2E_COVERAGE_INCLUDE_PATTERNS"
            ),
            coverage_exclude_patterns=env_patterns(
                "E2E_COVERAGE_EXCLUDE_PATTERNS"
            ),
            coverage_branch_enabled=env_bool(
                "E2E_COVERAGE_BRANCH_ENABLED", True
            ),
            coverage_include_tests=env_bool(
                "E2E_COVERAGE_INCLUDE_TESTS", False
            ),
            max_mutants=int(os.getenv("E2E_MAX_MUTANTS", "5")),
            mutation_max_mutants_per_file=int(
                os.getenv("E2E_MUTATION_MAX_PER_FILE", "5")
            ),
            mutation_seed=int(os.getenv("E2E_MUTATION_SEED", "1337")),
            mutation_include_patterns=env_patterns(
                "E2E_MUTATION_INCLUDE_PATTERNS"
            ),
            mutation_exclude_patterns=env_patterns(
                "E2E_MUTATION_EXCLUDE_PATTERNS"
            ),
            mutation_project_validation_command=env_command(
                "E2E_MUTATION_PROJECT_VALIDATION_COMMAND"
            ),
            mutation_keep_workspaces=env_bool(
                "E2E_MUTATION_KEEP_WORKSPACES", False
            ),
            execution_timeout_seconds=int(
                os.getenv("E2E_EXECUTION_TIMEOUT_SECONDS", "90")
            ),
            mutant_timeout_seconds=int(
                os.getenv("E2E_MUTANT_TIMEOUT_SECONDS", "45")
            ),
            relevant_mutation_refine_threshold=float(os.getenv(
                "E2E_RELEVANT_MUTATION_REFINE_THRESHOLD", "80"
            )),
            headless=env_bool("E2E_HEADLESS", True),
        )

    def build_state(
        self, row: Mapping[str, Any], row_index: int
    ) -> dict[str, Any]:
        case_id = _text(row.get("id"), f"UNK_{row_index}")
        req_id = _text(row.get("req_id"), "NA")
        test_id = _text(row.get("test_id"), "NA")
        source_dir = _text(
            row.get("source_project_dir"), self.coverage_source_dir
        )
        return {
            "executable_test_code": _text(
                row.get("excutable_test_step_code")
            ),
            "fine_grained_reqs": _text(row.get("fine_grained_reqs")),
            "excutable_test_test_case": _text(
                row.get("excutable_test_test_case")
            ),
            "requirement_summary": _text(row.get("requirement_summary")),
            "prompt": _text(row.get("prompt")),
            "reference_answer": _text(row.get("reference_answer")),
            "benchmark_id": case_id,
            "case_uid": (
                f"{case_id}_req{req_id}_test{test_id}_row{row_index}"
            ),
            "revision_count": 0,
            **self.graph_options(),
            "coverage_source_dir": source_dir,
        }

    def graph_options(self) -> dict[str, Any]:
        excluded = {"input_file", "output_file", "max_cases", "case_sleep_seconds"}
        return {
            key: value for key, value in asdict(self).items()
            if key not in excluded
        }

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_manifest(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

