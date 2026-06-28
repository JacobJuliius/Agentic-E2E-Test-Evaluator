"""Dependency-free structural lint for active framework Python files."""
from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT_FILES = {
    "agents.py", "coverage_agent.py", "dynamic_agents.py", "graph.py",
    "main.py", "mutation_testing.py", "reference_resolver.py",
}
TIMEOUT_CALL = re.compile(r"subprocess\.run\s*\(")


def active_files() -> list[Path]:
    files = [Path(name) for name in sorted(ROOT_FILES)]
    for root in (Path("e2e_eval"), Path("scripts"), Path("tests")):
        if root.exists():
            files.extend(sorted(root.rglob("*.py")))
    return files


def main() -> int:
    errors: list[str] = []
    for path in active_files():
        text = path.read_text(encoding="utf-8")
        try:
            ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{path}:{exc.lineno}: syntax error: {exc.msg}")
        for match in TIMEOUT_CALL.finditer(text):
            call_tail = text[match.start():match.start() + 800]
            if "timeout=" not in call_tail:
                line = text.count("\n", 0, match.start()) + 1
                errors.append(
                    f"{path}:{line}: subprocess.run without visible timeout"
                )
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Structural lint passed for {len(active_files())} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

