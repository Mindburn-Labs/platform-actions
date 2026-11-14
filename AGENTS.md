# Agent Operational Guidelines for platform-actions

<!-- helm-sdlc-baseline:v1 -->
Apply the [HELM SDLC baseline](https://github.com/Mindburn-Labs/docs/blob/main/ai/sdlc.md).
Before implementation, define the outcome and observable acceptance conditions.
Use the existing tracker and isolated worktree; verify in proportion to risk with
the owning repository's checks. Bind results to the tested source and configuration.
Missing, skipped or inconclusive required checks are not passes. Report source,
CI, release, deployment, runtime and user acceptance separately; verify applicable
rollback, restoration and external-effect recovery. Use `Refs` while acceptance
work remains and `Closes` only when the whole issue is fulfilled. Preserve existing
authority boundaries and never bypass failed checks or move published tags.
<!-- /helm-sdlc-baseline -->

Reusable GitHub Actions workflows that other Mindburn repos call through
`workflow_call`. The v2 set is `ci.yml`, `gate.yml`, `auto-tag.yml` and
`release-image.yml`. New callers pin the protected full-version tag
`@v2.0.4`; existing `@v2` callers remain on that legacy ref until their
workflows migrate. `agent-preflight.yml`,
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
* After a compatible workflow change merges and passes its gate, cut a new annotated
  `v2.x.y` tag at the merge commit, then verify its remote tag object and
  peeled commit. Update callers through reviewed PRs to the new full-version
  ref. Preserve published tags, including legacy `v2`; never force-move them.
  A breaking input, secret, output or job-name change requires a new major
  release and explicit caller migration.

## Architectural Boundaries
* Workflow scripts stay inline: a reusable workflow checks out the caller, not
  this repository.
* Maintain zero active static credentials inside the codebase.
* Rely on OIDC trust relationships for any third-party cloud brokers or identity enclaves.
