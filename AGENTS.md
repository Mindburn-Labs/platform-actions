# Agent Operational Guidelines for platform-actions

Reusable GitHub Actions workflows that other Mindburn repos call through
`workflow_call`. The v2 set is `ci.yml`, `auto-tag.yml` and
`release-image.yml`; callers pin `@v2`, a moving major tag, so a change here
reaches every caller when `v2` moves. `agent-preflight.yml`,
`doc-fingerprint-guard.yml` and `production-readiness.yml` are deprecated and
kept for their remaining callers.

## Dev Commands
* `make check` runs `lint` and `test`; `self-ci.yml` runs it on every PR
  through `ci.yml`, plus dry runs of `auto-tag.yml` and `release-image.yml`.
* `make lint` needs `actionlint` and `shellcheck` on PATH.
* `make test` runs the Python unit tests in `tests/`: the inline v2 scripts
  against fixtures, and the drift guard between
  `.github/actions/validate-agent-risk/validate-agent-risk.py` and its inlined
  copy in `agent-preflight.yml`. Edit both together.

## Releasing
* After a compatible change merges, move `v2` to the merge commit:
  `git tag -fa v2 -m "v2: <summary>" <sha> && git push -f origin refs/tags/v2`.
  A breaking change to an input, secret, output or job name becomes `v3`.

## Architectural Boundaries
* Workflow scripts stay inline: a reusable workflow checks out the caller, not
  this repository.
* Maintain zero active static credentials inside the codebase.
* Rely on OIDC trust relationships for any third-party cloud brokers or identity enclaves.
