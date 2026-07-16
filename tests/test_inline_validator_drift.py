"""Drift guard: the validator inlined into agent-preflight.yml must match the canonical script.

HELM-216: the agent.yaml validator is inlined into the reusable workflow
(.github/workflows/agent-preflight.yml) as a heredoc, because a composite action
referenced from a reusable workflow resolves against the CALLER repo and startup-fails
cross-repo callers. The canonical source of truth stays at
.github/actions/validate-agent-risk/validate-agent-risk.py (with its own unit tests);
this test fails if the inlined copy drifts from it, and if the broken cross-repo
`uses:` reference ever comes back.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "agent-preflight.yml"
CANONICAL = ROOT / ".github" / "actions" / "validate-agent-risk" / "validate-agent-risk.py"
DELIMITER = "VALIDATE_AGENT_RISK_PY"


def _extract_inlined_validator() -> list[str]:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(
        i for i, line in enumerate(lines) if line.rstrip().endswith(f"<<'{DELIMITER}'")
    )
    indent = len(lines[start]) - len(lines[start].lstrip())
    body: list[str] = []
    for line in lines[start + 1 :]:
        dedented = line[indent:] if len(line) >= indent else line
        if dedented == DELIMITER:
            return body
        body.append(dedented)
    raise AssertionError(f"closing heredoc delimiter {DELIMITER} not found")


class InlineValidatorDriftTest(unittest.TestCase):
    def test_inlined_validator_matches_canonical(self) -> None:
        inlined = _extract_inlined_validator()
        canonical = CANONICAL.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            inlined,
            canonical,
            "Inlined validator in agent-preflight.yml drifted from "
            f"{CANONICAL.relative_to(ROOT)}; re-inline the canonical script.",
        )

    def test_no_cross_repo_composite_action_reference(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn(
            "actions/validate-agent-risk@",
            text,
            "agent-preflight.yml must not reference validate-agent-risk as a cross-repo "
            "composite action (startup-fails cross-repo callers, HELM-216); keep it inlined.",
        )


if __name__ == "__main__":
    unittest.main()
