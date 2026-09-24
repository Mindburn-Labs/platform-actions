# Agent Operational Guidelines for platform-actions

Reusable GitHub Actions workflows (`agent-preflight.yml`,
`production-readiness.yml`, `doc-fingerprint-guard.yml`) that other Mindburn
repos call through `workflow_call`. Most callers pin a commit SHA, so a change
here reaches a repo only when its pin moves.

## Dev Commands
* Test: `make test` runs the Python unit tests in `tests/`, including the drift
  guard between `.github/actions/validate-agent-risk/validate-agent-risk.py` and
  its inlined copy in `agent-preflight.yml`. Edit both together.
* `make setup`, `make lint`, and `make build` only echo placeholders.

## Architectural Boundaries
* Maintain zero active static credentials inside the codebase.
* Rely on OIDC trust relationships for any third-party cloud brokers or identity enclaves.
