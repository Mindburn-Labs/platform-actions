# Release and recover reusable workflows

Use an immutable full-version tag for each compatible workflow release. Release
breaking workflow contract changes under a new major version and migrate
callers explicitly.

## Release

1. Merge after `self-ci.yml` passes `make check`, the auto-tag dry run,
   and the multi-platform image build dry run.
2. Create a new annotated `v2.x.y` tag at the verified merge commit and
   push that tag.
3. Read back the tag object and peeled commit. Confirm the peeled commit
   is the verified merge commit before changing callers.

```sh
tag=v2.x.y
git ls-remote --tags origin "refs/tags/$tag" "refs/tags/$tag^{}"
```

The `^{}` ref gives the commit the annotated tag points to. Update callers
through pull requests pinned to the new full-version tag.

## Recover a caller

Restore the caller’s last known good immutable workflow tag through a pull
request and run its required `ci / gate`. Leave published tags in place,
including `v2`. Ship the fix as a new version and verify the affected callers
before they adopt it.
