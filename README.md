# platform-actions

> [!NOTE]
> **Ecosystem boundary**: This repository is a **Non-HELM** system. It carries no HELM cryptographic verification core and makes no governance or receipt claims. It hosts reusable CI plumbing for the Mindburn Labs polyrepo estate.

Reusable GitHub Actions workflows shared across Mindburn Labs repositories.
Every repository calls them through `workflow_call`, so CI and release logic
lives in one place. The design is `docs/architecture/agent-delivery.md` in the
`docs` repository: agents build, merge, release and deploy, and automated
checks are the only thing that stops a change.

## The v2 contract

Every repository:

1. has a `make check` target that runs its real gates (lint, typecheck, tests,
   build; for docs or config repos, whatever validates them). A target that
   only echoes is a defect.
2. copies [`templates/ci.yml`](templates/ci.yml) to `.github/workflows/ci.yml`.
   It calls `ci.yml@v2` on `pull_request`, `push` to the default branch and
   `merge_group`, with no path filters.
3. requires one status check, `ci / gate` (the caller job id is `ci`, the
   reusable job is `gate`), through the org ruleset.
4. copies [`templates/dependabot.yml`](templates/dependabot.yml) to
   `.github/dependabot.yml` and keeps the blocks for ecosystems it has.
5. pins `@v2`. `v2` is a moving major tag: compatible changes move it,
   breaking changes become `v3`.

```yaml
jobs:
  ci:
    uses: Mindburn-Labs/platform-actions/.github/workflows/ci.yml@v2
```

Repositories that ship images add a tag-triggered caller:

```yaml
on:
  push:
    tags: ['v*']
permissions:
  contents: read
jobs:
  image:
    uses: Mindburn-Labs/platform-actions/.github/workflows/release-image.yml@v2
    permissions:
      contents: read
      packages: write
      id-token: write
      attestations: write
```

### `ci.yml`

| Input / secret | Default | Meaning |
| --- | --- | --- |
| `setup-commands` | `''` | Shell run after toolchain setup, before `make check` (for example `npm ci`). |
| `extra-commands` | `''` | Shell run after `make check`, in the same job. |
| secret `MINDBURN_ORG_READ_TOKEN` | none | Only for `go.mod` `replace ... => ../<repo>`: clones those sibling repositories. |

Jobs:

- `make check`: installs what the tracked files call for, with caches. Go from
  the shallowest `go.mod`; Node from `package-lock.json` or `pnpm-lock.yaml`
  (version from `.nvmrc`/`.node-version`, else LTS); Python from
  `pyproject.toml` or `requirements*.txt` (`.python-version`, else 3.12); Ruby
  from a root `Gemfile`; Rust from `Cargo.toml`; Terraform and OpenTofu from
  `*.tf`; Helm from `Chart.yaml`. Then `make check`.
- `dependency scan` (pull requests and merge groups): osv-scanner runs on the
  base and on the change, and the job fails only on a HIGH or CRITICAL
  advisory (CVSS 7.0 or more, or rated HIGH/CRITICAL by GitHub) that the
  change adds. Inherited findings are reported as a notice and left to
  Dependabot.
- `gate`: needs every other job, runs with `if: always()`, and fails if any of
  them failed or was cancelled. Skipped jobs pass.

### `auto-tag.yml`

Called after `ci` on push (see the `tag` job in `templates/ci.yml`). It takes
the newest `vMAJOR.MINOR.PATCH` tag and bumps it from the conventional commits
since then: `type!:` or `BREAKING CHANGE:` is major, `feat` minor,
`fix`/`perf`/`revert` patch, anything else releases nothing. The tag is pushed
with a `mindburn-flux` App installation token, because a tag pushed with
`GITHUB_TOKEN` would not trigger the release workflow.

| Input / secret | Default | Meaning |
| --- | --- | --- |
| `dry-run` | `false` | Compute and print the tag; mint no token, push nothing. |
| `initial-version` | `0.1.0` | First tag when the repository has no release tag. |
| secrets `MINDBURN_FLUX_APP_ID`, `MINDBURN_FLUX_PRIVATE_KEY` | none | Required unless `dry-run`. |
| output `tag` | | The tag pushed, or empty. |

### `release-image.yml`

On a `v<semver>` tag whose commit is on the default branch: buildx builds every
platform and pushes the index to GHCR by digest; syft writes an SPDX SBOM per
platform, attached with `cosign attest`; `cosign sign --recursive` signs keyless
through GitHub OIDC; `actions/attest-build-provenance` adds SLSA provenance;
then the signature is verified and the tag is created on that digest. A tag
that already exists in GHCR is never overwritten. The certificate identity is
`https://github.com/Mindburn-Labs/platform-actions/.github/workflows/release-image.yml@refs/tags/v2`.

| Input | Default | Meaning |
| --- | --- | --- |
| `image` | `ghcr.io/<owner>/<repo>` (lowercase) | Image name without tag. |
| `context` | `.` | Build context. |
| `dockerfile` | `<context>/Dockerfile` | Dockerfile path. |
| `platforms` | `linux/amd64,linux/arm64` | Target platforms. |
| `build-args` | `''` | Newline-separated `KEY=VALUE`. |
| `tag` | the pushed git tag | Release tag. |
| `dry-run` | `false` | Build every platform; no push, signature or tag. |
| outputs `image`, `tag`, `digest` | | What was published. |

### Deprecated

`agent-preflight.yml` and `doc-fingerprint-guard.yml` are deprecated in favour
of `ci.yml@v2`. They keep working for the repositories that still call them
until those move to v2. `production-readiness.yml` has no known callers.

## Repository layout

```text
.
├── .github/workflows/
│   ├── ci.yml, auto-tag.yml, release-image.yml   # v2 reusable workflows
│   ├── self-ci.yml                                # this repo's CI: calls the three above
│   └── agent-preflight.yml, doc-fingerprint-guard.yml, production-readiness.yml
├── templates/           # caller ci.yml and dependabot.yml for other repositories
├── tests/               # unit tests for the inline workflow scripts
├── agent.yaml           # agent contract checked by agent-preflight.yml
└── Makefile             # check = lint + test
```

## Local commands

```bash
make check   # lint + test
make lint    # actionlint (with shellcheck on every run: block) and shellcheck on *.sh
make test    # Python unit tests in tests/
```

The workflow scripts are inline, because a reusable workflow checks out the
caller and cannot read files from this repository. `tests/` runs those inline
scripts against fixtures. On a pull request, `self-ci.yml` runs `ci.yml`, a dry
run of `auto-tag.yml` and a dry-run multi-arch build of `release-image.yml`.

## Ownership and security

- **Owner:** `architecture-platform` (see `CODEOWNERS`).
- **Dependencies:** Dependabot (`.github/dependabot.yml`) keeps the pinned action SHAs current.
- **Security disclosures:** see [SECURITY.md](SECURITY.md) — report privately to `security@mindburn.org`.
