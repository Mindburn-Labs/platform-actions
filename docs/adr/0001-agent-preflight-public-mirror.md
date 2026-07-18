# ADR 0001 — Make `platform-actions` internal instead of mirroring `agent-preflight`

- **Status:** Accepted
- **Date:** 2026-07-18
- **Issue:** HELM-216
- **Supersedes:** the inline-validator fix from PR #8 (necessary, but not sufficient)

> **Filename note.** This file is named `…-public-mirror` because it began life
> as the ADR for a public-mirror decision, and the docs-truth ledger row was
> already merged against that path. The mirror was **rejected**; the path is kept
> so the registry stays valid. The decision is below.

## Context

`platform-actions/.github/workflows/agent-preflight.yml` is the shared
agent-native preflight gate, consumed cross-repo as
`uses: Mindburn-Labs/platform-actions/.github/workflows/agent-preflight.yml@main`
by `docs_for_team` and `mindburn-infra`.

Every such call was a GitHub **startup failure**: the run completed as `failure`
with **0 jobs**, no logs, and no commit check-run. Because the run died before
job creation, anything the workflow gated never executed — the gate was not
merely red, it was *absent*, silently, for weeks.

The failure was not a defect in the workflow file. `actionlint` reported nothing,
the file parsed, and it ran **green in-repo** on the same commits on which
internal callers startup-failed. PR #8 had already removed the one genuine
in-file defect (a `./` composite-action reference, which inside a reusable
workflow resolves against the *caller* repo); callers kept failing after it
merged.

What remained was a **visibility asymmetry** in reusable-workflow resolution.
GitHub shares a repository's reusable workflows by visibility: a **private**
repo's workflows resolve for **private** callers, an **internal** repo's resolve
for **internal and private** callers. `platform-actions` was private; the failing
callers are internal:

| Caller | Caller visibility | Callee visibility | Result |
| --- | --- | --- | --- |
| `docs_for_team` | internal | private | **startup failure** |
| `mindburn-infra` | internal | private | **startup failure** |
| `docs_for_team` | internal | public (`.github`) | success |

The repo-level grant was already in place and is **not** sufficient on its own:

```
$ gh api repos/Mindburn-Labs/platform-actions/actions/permissions/access
{"access_level":"organization"}
```

## Decision

**Change `platform-actions` visibility from private to internal.** Nothing else:
no mirror, no workflow edits, no caller re-pinning.

## Evidence

Before — the startup-failure signature is `workflowName` reporting the *file
path* instead of the workflow's real `name:`, together with zero jobs:

```
$ gh run list --repo Mindburn-Labs/docs_for_team --workflow agent-preflight.yml \
    --json databaseId,conclusion,workflowName
29655860023 failure workflowName=".github/workflows/agent-preflight.yml"   jobs=0
29655839222 failure workflowName=".github/workflows/agent-preflight.yml"   jobs=0
29625718519 failure workflowName=".github/workflows/agent-preflight.yml"   jobs=0
```

After the visibility change, across three independent runs in two repos:

| repo / workflow | `workflowName` | jobs | result |
| --- | --- | --- | --- |
| `docs_for_team` / agent-preflight | `Agent Preflight` | 1 | success |
| `mindburn-infra` / agent-preflight | `Agent Preflight` | 1 | success |
| `docs_for_team` / doc-guard (SHA-pinned) | `Doc Guard` | 1 | success |

The job itself ran and passed: `preflight / Agent contract and focused gates:
success`. A green conclusion alone would not have proved this — a startup
failure also "completes" — which is why both signals are recorded: the real
`name:` resolving *and* a non-zero job count.

`doc-guard` was a second broken caller that nobody was tracking; it was fixed by
the same change.

## Rejected alternative — mirror the workflow into the public `.github` repo

The original plan (following the Docs Truth precedent, `docs-truth-public.yml`)
was a byte-identical copy of the workflow in the public `Mindburn-Labs/.github`
repo, with callers pinned to its commit SHA. Rejected because:

- **Exposure.** The workflow would become world-readable, including the
  `mindburn-infra` repo name and the `gitops-` / `infra-` risk prefixes. Internal
  exposes it to the six organisation members instead — the same people already
  working in the repo.
- **Permanent synchronisation burden.** A mirror must stay byte-identical, and
  callers must be re-pinned on every edit. The draft implementation had no
  automated drift check: the sync rule lived in a comment and an ADR. Canonical
  edited, mirror forgotten, callers pinned to a stale SHA — and the gate goes
  quietly wrong instead of loudly absent, which is worse.
- **Cost.** The mirror route needed a 144-line mirror-safety test, workflow
  trigger surgery so the copy would not self-trigger in `.github`, and the
  mirror file itself. The chosen route needed one settings change.

The mirror remains the fallback if internal visibility is ever withdrawn.

## Consequences

- `platform-actions` is readable by all organisation members. Given six members,
  all of whom already work in this repo's dependents, this is close to a no-op.
- `agent-preflight.yml` keeps a single source of truth. No mirror, no drift.
- Callers keep `@main` and need no change.
- A previously-unnoticed correction: an earlier draft of this ADR asserted that
  the private→private call from `dev-orchestration` succeeded. Its runs from
  2026-07-16 show the same `workflowName`-as-path / `jobs=0` signature, i.e. that
  caller was probably also broken. It has not run since the visibility change, so
  this is recorded as unverified rather than claimed as fixed.
