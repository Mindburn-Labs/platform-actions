from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "validate-agent-risk.py"
SPEC = importlib.util.spec_from_file_location("validate_agent_risk", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"unable to load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_repo(root: Path, *, name: str, agent_yaml: str, surfaces: tuple[str, ...]) -> Path:
    repo_root = root / name
    repo_root.mkdir()
    (repo_root / "agent.yaml").write_text(agent_yaml, encoding="utf-8")
    for surface in surfaces:
        target = repo_root / surface
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    return repo_root


class ValidateAgentRiskTests(unittest.TestCase):
    def test_gitops_repo_with_underreported_risk_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = write_repo(
                Path(tmpdir),
                name="gitops-platform",
                agent_yaml="""repo_type: platform
owners:
  team: architecture-platform
commands:
  setup: make setup
  test: make test
  lint: make lint
  build: make build
risk:
  external_writes: false
  secrets_required: false
  protected_paths:
    - security/**
agent_entrypoints:
  default_task: make agent-context
""",
                surfaces=("environments/production/example.yaml", "secrets/example.env"),
            )
            issues = MODULE.validate_repo(repo_root)
        self.assertTrue(any("external_writes" in issue for issue in issues))
        self.assertTrue(any("secrets_required" in issue for issue in issues))
        self.assertTrue(any("protected_paths" in issue for issue in issues))

    def test_gitops_repo_with_truthful_risk_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = write_repo(
                Path(tmpdir),
                name="infra-live",
                agent_yaml="""repo_type: platform
owners:
  team: architecture-platform
commands:
  setup: make setup
  test: make test
  lint: make lint
  build: make build
risk:
  external_writes: true
  secrets_required: true
  protected_paths:
    - environments/**
    - secrets/**
agent_entrypoints:
  default_task: make agent-context
""",
                surfaces=("environments/production/example.yaml", "secrets/example.env"),
            )
            issues = MODULE.validate_repo(repo_root)
        self.assertEqual(issues, [])

    def test_non_gitops_repo_keeps_generic_contract_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = write_repo(
                Path(tmpdir),
                name="platform-actions",
                agent_yaml="""repo_type: platform
owners:
  team: architecture-platform
commands:
  setup: make setup
  test: make test
  lint: make lint
  build: make build
risk:
  external_writes: false
  secrets_required: false
  protected_paths:
    - security/**
agent_entrypoints:
  default_task: make agent-context
""",
                surfaces=(".github/workflows/agent-preflight.yml",),
            )
            issues = MODULE.validate_repo(repo_root)
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
