#!/usr/bin/env python3
"""Validate agent.yaml structure and gitops/infra risk semantics for a caller repo."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
import re
import sys

GITOPS_INFRA_PREFIXES = ("gitops-", "infra-")
GITOPS_INFRA_REPOS = {"mindburn-infra"}
SENSITIVE_SURFACES = {
    "environments/**": "environments/production/example.yaml",
    "secrets/**": "secrets/example.env",
    ".github/workflows/**": ".github/workflows/example.yml",
    "terraform/**": "terraform/main.tf",
    "ansible/**": "ansible/site.yml",
}


@dataclass(frozen=True)
class AgentContract:
    repo_type: str
    external_writes: bool
    secrets_required: bool
    protected_paths: tuple[str, ...]


def _require_match(pattern: str, text: str, label: str) -> re.Match[str]:
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise ValueError(f"agent.yaml missing canonical key: {label}")
    return match


def _parse_protected_paths(text: str) -> tuple[str, ...]:
    lines = text.splitlines()
    items: list[str] = []
    in_block = False
    for line in lines:
        if line == "  protected_paths:":
            in_block = True
            continue
        if not in_block:
            continue
        if line.startswith("    - "):
            items.append(line.removeprefix("    - ").strip())
            continue
        if line.startswith("  ") and not line.startswith("    "):
            break
        if line and not line.startswith(" "):
            break
    return tuple(items)


def load_agent_contract(path: Path) -> AgentContract:
    text = path.read_text(encoding="utf-8")
    repo_type = _require_match(r"^repo_type:\s*(\S+)\s*$", text, "repo_type").group(1)
    _require_match(r"^owners:\s*$", text, "owners")
    _require_match(r"^commands:\s*$", text, "commands")
    _require_match(r"^risk:\s*$", text, "risk")
    _require_match(r"^agent_entrypoints:\s*$", text, "agent_entrypoints")
    external_writes = _require_match(
        r"^  external_writes:\s*(true|false)\s*$",
        text,
        "external_writes",
    ).group(1) == "true"
    secrets_required = _require_match(
        r"^  secrets_required:\s*(true|false)\s*$",
        text,
        "secrets_required",
    ).group(1) == "true"
    return AgentContract(
        repo_type=repo_type,
        external_writes=external_writes,
        secrets_required=secrets_required,
        protected_paths=_parse_protected_paths(text),
    )


def is_gitops_or_infra_repo(repo_root: Path) -> bool:
    repo_name = repo_root.name
    return repo_name.startswith(GITOPS_INFRA_PREFIXES) or repo_name in GITOPS_INFRA_REPOS


def expected_sensitive_surfaces(repo_root: Path) -> list[str]:
    expected: list[str] = []
    for pattern in SENSITIVE_SURFACES:
        surface_root = pattern.split("/", 1)[0]
        if surface_root == ".github":
            if (repo_root / ".github" / "workflows").exists():
                expected.append(pattern)
            continue
        if (repo_root / surface_root).exists():
            expected.append(pattern)
    return expected


def _covers_surface(protected_paths: tuple[str, ...], surface_pattern: str) -> bool:
    sample = SENSITIVE_SURFACES[surface_pattern]
    return any(fnmatch(sample, pattern) for pattern in protected_paths)


def validate_repo(repo_root: Path) -> list[str]:
    contract = load_agent_contract(repo_root / "agent.yaml")
    if not is_gitops_or_infra_repo(repo_root):
        return []

    issues: list[str] = []
    if not contract.external_writes:
        issues.append(
            f"{repo_root.name}: gitops/infra repos must declare risk.external_writes: true",
        )
    if not contract.secrets_required:
        issues.append(
            f"{repo_root.name}: gitops/infra repos must declare risk.secrets_required: true",
        )

    missing_surfaces = [
        surface
        for surface in expected_sensitive_surfaces(repo_root)
        if not _covers_surface(contract.protected_paths, surface)
    ]
    if missing_surfaces:
        issues.append(
            f"{repo_root.name}: risk.protected_paths underreports deploy surfaces; add coverage for {', '.join(missing_surfaces)}",
        )
    return issues


def main(argv: list[str]) -> int:
    repo_root = Path(argv[1]).resolve() if len(argv) > 1 else Path.cwd()
    agent_path = repo_root / "agent.yaml"
    if not agent_path.is_file():
        print("agent.yaml is required for agent-native repositories", file=sys.stderr)
        return 1

    try:
        issues = validate_repo(repo_root)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
