#!/usr/bin/env python3
"""Prints what CI has to check, in the GITHUB_OUTPUT format.

Only documentation is safe to skip. An unknown path runs everything, so a mistake here
costs time and not a missed check.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
STUB = "infra/benchmark/stub"


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True, cwd=ROOT
    )
    return result.stdout.strip()


def services_with(manifest: str) -> list[str]:
    return sorted(path.parent.name for path in ROOT.glob(f"services/*/{manifest}"))


def changed_paths() -> list[str] | None:
    """None means everything: a push to main has no branch to compare with."""
    base = git("merge-base", "origin/main", "HEAD")
    if base == git("rev-parse", "HEAD"):
        return None
    return git("diff", "--name-only", base, "HEAD").splitlines()


def touched_areas(paths: list[str], services: list[str]) -> set[str] | None:
    """Returns the changed areas, or None if a path needs a full run."""
    areas = set()
    for path in paths:
        parts = path.split("/")
        if path.endswith(".md") or parts[0] == "docs":
            continue
        if parts[0] == "services" and parts[1] in services:
            areas.add(parts[1])
        elif parts[0] == "contracts":
            areas.add("contracts")
        elif path.startswith(f"{STUB}/"):
            areas.add("stub")
        elif path.startswith("infra/observability/"):
            areas.add("observability")
        else:
            return None
    return areas


def plan(
    paths: list[str] | None, python: list[str], rust: list[str]
) -> dict[str, list[str] | bool]:
    areas = None if paths is None else touched_areas(paths, python + rust)

    def selected(names: list[str]) -> list[str]:
        return [name for name in names if areas is None or name in areas]

    python_services = selected(python)
    rust_services = selected(rust)
    stub = [STUB] if areas is None or "stub" in areas else []
    return {
        "python": python_services,
        "rust": [f"services/{name}" for name in rust_services] + stub,
        "rust_services": [f"services/{name}" for name in rust_services],
        "images": sorted(python_services + rust_services),
        "contracts": areas is None or bool(python_services) or "contracts" in areas,
        "observability": areas is None or "observability" in areas,
    }


def main() -> None:
    result = plan(
        changed_paths(), services_with("pyproject.toml"), services_with("Cargo.toml")
    )
    for name, value in result.items():
        print(f"{name}={json.dumps(value)}")


if __name__ == "__main__":
    main()
