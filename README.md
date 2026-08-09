# platform-actions

> [!NOTE]
> **Ecosystem boundary**: This repository is a **Non-HELM** system. It carries no HELM cryptographic verification core and makes no governance or receipt claims. It hosts reusable CI plumbing for the Mindburn Labs polyrepo estate.

Reusable GitHub Actions workflows shared across Mindburn Labs repositories. Each
consuming repo calls these via `uses:` / `workflow_call` so CI logic lives in one
place. The workflows are toolchain-agnostic: they auto-detect Go / Node / Python /
Rust / Flutter and run whatever `make` gates the consuming repo exposes.

## Reusable workflow catalog

| Workflow | Trigger | Purpose |
| --- | --- | --- |
| `.github/workflows/agent-preflight.yml` | `workflow_call`, `pull_request`, `workflow_dispatch` | Agent-native preflight: detect toolchains and set up the matching runtime; assert `.codegraph/` is not committed and (re)init CodeGraph when the CLI is present; validate the `agent.yaml` contract has its canonical keys; run the consuming repo's `setup`/`lint`/`test`/`build` Make targets when they exist. |
| `.github/workflows/ci.yml` | `push` / `pull_request` on `main` | Thin entry point — reuses `agent-preflight.yml` so a repo gets the full preflight by referencing one workflow. |
| `.github/workflows/production-readiness.yml` | `workflow_call`, `workflow_dispatch` | Pre-promotion gate for deployable repos. Runs `make lint`/`test`/`build`, and, when present, a release-manifest validator (`scripts/validate-release-manifest.rb`) and a GitOps environment validator (`scripts/validate-gitops-environments.rb`). It does not deploy. |

### Consuming a workflow

From another repository's `.github/workflows/`:

```yaml
jobs:
  preflight:
    uses: Mindburn-Labs/platform-actions/.github/workflows/agent-preflight.yml@main
```

The `agent.yaml` contract that `agent-preflight.yml` validates requires these keys:
`repo_type`, `owners`, `commands`, `risk`, `agent_entrypoints` (see this repo's own
`agent.yaml` for the canonical shape).

## Repository layout

```text
.
├── .github/workflows/   # The three reusable workflows above
├── .devcontainer/       # Devcontainer definition (ubuntu base + common-utils)
├── docs/
│   ├── adr/             # Architecture Decision Records
│   └── runbook.md       # Operational runbook
├── agent.yaml           # Agent contract for this repo (the schema preflight enforces)
├── catalog-info.yaml    # Backstage component descriptor (kind: library)
├── Makefile             # Placeholder setup/test/lint/build/agent-context targets
├── CODEOWNERS           # Ownership: @mindburn-labs/architecture-platform
├── SECURITY.md          # Vulnerability disclosure policy
└── renovate.json        # Renovate dependency config
```

## Local commands

The `Makefile` targets in this repo are placeholders (they echo and do nothing) —
this repository ships configuration, not buildable code. The targets exist so the
reusable gates have something to call; real implementations live in consuming repos.

```bash
make setup    # placeholder
make lint     # placeholder
make test     # placeholder
make build    # placeholder
```

## Ownership and security

- **Owner:** `architecture-platform` (see `CODEOWNERS`).
- **Dependencies:** managed by Renovate (`renovate.json`), with lockfile maintenance enabled.
- **Security disclosures:** see [SECURITY.md](SECURITY.md) — report privately to `security@mindburn.org`.
