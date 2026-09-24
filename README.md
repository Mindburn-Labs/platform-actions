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
   `.github/dependabot.yml` and keeps the blocks for ecosystems it has, and
   copies [`templates/dependabot-auto-merge.yml`](templates/dependabot-auto-merge.yml)
   to `.github/workflows/` so patch and minor updates merge themselves on a
   green `ci / gate`.
5. pins `@v2`. `v2` is a moving major tag: compatible changes move it,
   breaking changes become `v3`.

```yaml
jobs:
  ci:
    uses: Mindburn-Labs/platform-actions/.github/workflows/ci.yml@v2
```

Repositories that release keep the `tag` and `image` jobs of
`templates/ci.yml`: on a push to the default branch, `ci` passes, `auto-tag`
pushes the next `v<semver>` tag, and `release-image` builds that tag, all in
one run:

```yaml
jobs:
  ci:
    uses: Mindburn-Labs/platform-actions/.github/workflows/ci.yml@v2
  tag:
    needs: ci
    if: github.event_name == 'push'
    permissions:
      contents: write
    uses: Mindburn-Labs/platform-actions/.github/workflows/auto-tag.yml@v2
  image:
    needs: tag
    if: needs.tag.outputs.tag != ''
    permissions:
      contents: read
      packages: write
      id-token: write
      attestations: write
    uses: Mindburn-Labs/platform-actions/.github/workflows/release-image.yml@v2
    with:
      tag: ${{ needs.tag.outputs.tag }}
```

No App token is needed: the tag is pushed with `GITHUB_TOKEN`, which starts no
other workflow, so the release is chained on the `tag` output instead. A tag
cut by hand or by an agent (`git push origin v1.2.3`) goes through a
tag-triggered caller, which can coexist with the chain:

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

To keep one definition, make that file both tag-triggered and
`workflow_call`-able (input `tag`), and let the chain's `image` job call it
with `uses: ./.github/workflows/<file>.yml` and `secrets: inherit`.

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
with the calling job's `GITHUB_TOKEN`, so that job grants `contents: write`;
the release is chained on the `tag` output in the same run. Optionally, pass
the `mindburn-flux` App secrets to push as the App instead: an App push also
starts `on: push: tags` workflows, and `contents: read` is then enough.

| Input / secret | Default | Meaning |
| --- | --- | --- |
| `dry-run` | `false` | Compute and print the tag; mint no token, push nothing. |
| `initial-version` | `0.1.0` | First tag when the repository has no release tag. |
| secrets `MINDBURN_FLUX_APP_ID`, `MINDBURN_FLUX_PRIVATE_KEY` | none | Optional: push as the `mindburn-flux` App. |
| output `tag` | | The tag pushed, or empty. |

### `release-image.yml`

On a `v<semver>` tag (pushed, or passed as `tag` by the chain) whose commit is
on the default branch: `setup-commands` stages anything the build needs beyond
the checkout, buildx builds every platform and pushes the index to GHCR by digest; syft writes an SPDX SBOM per
platform, attached with `cosign attest`; `cosign sign --recursive` signs keyless
through GitHub OIDC; `actions/attest-build-provenance` adds SLSA provenance;
then the signature is verified and the tag is created on that digest.
`verify-commands` runs against the pushed digest before the SBOM, signature
and tag, so a failed smoke test leaves no tag behind. A tag
that already exists in GHCR is never overwritten. The certificate identity is
`https://github.com/Mindburn-Labs/platform-actions/.github/workflows/release-image.yml@refs/tags/v2`.

| Input | Default | Meaning |
| --- | --- | --- |
| `image` | `ghcr.io/<owner>/<repo>` (lowercase) | Image name without tag. |
| `context` | `.` | Build context. |
| `dockerfile` | `<context>/Dockerfile` | Dockerfile path. |
| `platforms` | `linux/amd64,linux/arm64` | Target platforms. |
| `build-args` | `''` | Newline-separated `KEY=VALUE`; a bare `KEY` takes its value from the environment (for example from `setup-commands` via `$GITHUB_ENV`). |
| `build-contexts` | `''` | Newline-separated `name=path` named contexts, relative to the workspace. |
| `tag` | the pushed git tag | Release tag. |
| `dry-run` | `false` | Build every platform; no push, signature or tag. `setup-commands` still runs; `verify-commands` does not. |
| `setup-commands` | `''` | Shell run at the workspace root after checkout, before the build: clone sibling repositories, stage contexts inside the workspace. Env: `MINDBURN_ORG_READ_TOKEN`, `RELEASE_IMAGE`, `RELEASE_TAG`, `SOURCE_SHA`. |
| `verify-commands` | `''` | Shell run after the push by digest, before SBOM, signature and tag (for example a runtime smoke test). Env: `RELEASE_IMAGE`, `RELEASE_TAG`, `RELEASE_DIGEST`, `SOURCE_SHA`, `RELEASE_PLATFORMS_FILE` (`os/arch<TAB>digest` lines). |
| secret `MINDBURN_ORG_READ_TOKEN` | none | Read token for sibling repositories, exposed to `setup-commands` only. |
| outputs `image`, `tag`, `digest` | | What was published. |

### `dependabot-auto-merge.yml`

Called from `pull_request` (see
[`templates/dependabot-auto-merge.yml`](templates/dependabot-auto-merge.yml)).
For a Dependabot PR whose update type is patch or minor, it arms GitHub
auto-merge (`gh pr merge --auto --squash`), so the PR merges when the required
checks pass and stays open when they fail. Majors are left for review. It arms
nothing while the base branch has no required-status-check rule, because
auto-merge would then merge without CI. The repository needs "Allow
auto-merge".

| Input / secret | Default | Meaning |
| --- | --- | --- |
| `update-types` | `version-update:semver-patch,version-update:semver-minor` | Update types that merge automatically. |
| secrets `MINDBURN_FLUX_APP_ID`, `MINDBURN_FLUX_PRIVATE_KEY` | none | Optional, stored as Dependabot secrets: arm auto-merge as the App, so the merge push runs CI on the default branch (and auto-tag). A `GITHUB_TOKEN` merge starts no push workflow. |

### Deprecated

`agent-preflight.yml` and `doc-fingerprint-guard.yml` are deprecated in favour
of `ci.yml@v2`. They keep working for the repositories that still call them
until those move to v2. `production-readiness.yml` has no known callers.

## Repository layout

```text
.
├── .github/workflows/
│   ├── ci.yml, auto-tag.yml, release-image.yml,  # v2 reusable workflows
│   │   dependabot-auto-merge.yml
│   ├── self-ci.yml                                # this repo's CI: calls the four above
│   └── agent-preflight.yml, doc-fingerprint-guard.yml, production-readiness.yml
├── templates/           # caller ci.yml, dependabot.yml and dependabot-auto-merge.yml
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
run of `auto-tag.yml`, a dry-run multi-arch build of `release-image.yml` with
`setup-commands` and a named build context, and, on this repository's own
Dependabot PRs, `dependabot-auto-merge.yml`.

## Ownership and security

- **Owner:** `architecture-platform` (see `CODEOWNERS`).
- **Dependencies:** Dependabot (`.github/dependabot.yml`) keeps the pinned action SHAs current.
- **Security disclosures:** see [SECURITY.md](SECURITY.md) — report privately to `security@mindburn.org`.
