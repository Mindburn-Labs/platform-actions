# Runbook: platform-actions

This repository ships reusable workflows and runs no service. Callers pin the
moving `v2` tag, so a release is a move of `v2` (see `AGENTS.md`).

## Release Verification
Before `v2` moves, `self-ci.yml` runs the changed workflows on the pull
request: `ci.yml` (`make check`), a dry run of `auto-tag.yml` and a dry-run
multi-arch build of `release-image.yml`. After moving `v2`, confirm the remote
tag points at the merge commit:

```bash
git ls-remote --tags origin v2   # the v2^{} line is the commit callers run
```

## Recovery Procedures
If a `v2` move breaks callers, move `v2` back to the last good commit, then fix
forward on `main`:

```bash
git tag -fa v2 -m "v2: back to <sha>" <sha> && git push -f origin refs/tags/v2
```
