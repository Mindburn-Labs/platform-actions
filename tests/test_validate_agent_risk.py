from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "actions"
    / "validate-agent-risk"
    / "validate-agent-risk.py"
)
SPEC = importlib.util.spec_from_file_location("validate_agent_risk", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"unable to load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def focused_gate_script() -> str:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "agent-preflight.yml"
    ).read_text(encoding="utf-8")
    lines = workflow.splitlines()
    step = lines.index("      - name: Run focused repository gates")
    run = lines.index("        run: |", step)
    body: list[str] = []
    for line in lines[run + 1 :]:
        if line.startswith("          "):
            body.append(line[10:])
        elif line == "":
            body.append("")
        else:
            break
    return "\n".join(body)


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
    def test_focused_gates_execute_present_targets_and_skip_missing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "Makefile").write_text(
                ".PHONY: setup test build\n"
                "setup:\n\t@printf 'setup\\n' >> gate.log\n"
                "test:\n\t@printf 'test\\n' >> gate.log\n"
                "build:\n\t@printf 'build\\n' >> gate.log\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", "-c", focused_gate_script()],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (repo_root / "gate.log").read_text(encoding="utf-8").splitlines(),
                ["setup", "test", "build"],
            )
            self.assertIn("No Makefile target 'lint'", result.stdout)

    def test_focused_gates_propagate_present_target_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "Makefile").write_text(
                ".PHONY: setup lint test build\n"
                "setup:\n\t@printf 'setup\\n' >> gate.log\n"
                "lint:\n\t@printf 'lint\\n' >> gate.log\n\t@exit 17\n"
                "test:\n\t@printf 'test\\n' >> gate.log\n"
                "build:\n\t@printf 'build\\n' >> gate.log\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", "-c", focused_gate_script()],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                (repo_root / "gate.log").read_text(encoding="utf-8").splitlines(),
                ["setup", "lint"],
            )

    def test_focused_gates_reject_invalid_makefile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / "Makefile").write_text(
                "setup: missing-input\n\t@printf 'setup\\n' >> gate.log\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["bash", "-c", focused_gate_script()],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((repo_root / "gate.log").exists())

    def test_reusable_workflow_inlines_the_validator(self) -> None:
        # HELM-216: a composite action referenced from a reusable workflow resolves against
        # the CALLER repo and startup-fails cross-repo callers, so the validator is inlined.
        # Drift between the inline copy and the canonical script is guarded by
        # tests/test_inline_validator_drift.py.
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "agent-preflight.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("python3 - \".\" <<'VALIDATE_AGENT_RISK_PY'", workflow)
        self.assertNotRegex(
            workflow,
            r"(?m)^\s*uses: Mindburn-Labs/platform-actions/\.github/actions/validate-agent-risk@",
        )
        self.assertNotIn("scripts/validate-agent-risk.py", workflow)

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
