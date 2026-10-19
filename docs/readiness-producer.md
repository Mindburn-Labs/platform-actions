# RC-04 source producer

`.github/workflows/produce-release-readiness.yml` produces RC-04 for one exact
GitOps candidate commit and environment. Dispatch it on protected `main` with
`environment` and a full `candidate_sha`; `repository_dispatch` accepts the
same fields in a `release-readiness-request` payload. Pull requests run only
the offline collector tests.

The source-owned release cohort contains `helm-ai-kernel`,
`helm-ai-enterprise`, `svc-helm-control-plane`, `app-helm-console`,
`gitops-platform` and `platform-actions`. The collector reads GitHub metadata,
full rulesets, signing environments and workflow content at each repository's
resolved main SHA. It checks both release tag families, restricted signing
refs, immutable actions in jobs with write permissions or signing authority,
and enabled secret scanning plus push protection. Local composite actions are
traversed. Missing API visibility, partial inventories and unresolved workflow
controls fail the observation.

The candidate cluster tree supplies a lower-bound inventory of Mindburn GHCR
artifact owners. An unknown artifact or an owner outside the six repositories
fails RC-04. Adding a component requires an explicit source change to the
inventory and cohort. The collector does not treat the present cohort as proof
of coverage for every future candidate.

`scripts/collect-release-readiness.py` writes a
`release-readiness-observation-v1` report. Its `evidence` contains sanitized
GitHub readbacks and per-repository results. All four `observed` fields must
be true and `unmet` must be empty; an unavailable enterprise security feature
is an unmet control. The collector cannot enable settings or bypass an
enterprise policy.

The workflow uses the immutable `helm-rollout-evidence` helper at
`cdfb169f2df22e6443180c9bed8cb96c0f76cce2`. It binds the observation to the
exact candidate SHA, environment and cluster-tree fingerprint, uploads the
single raw JSON file, verifies the uploaded bytes and API digest, signs the
record, then uploads only `RC-04.json` and `RC-04.sig`. The evidence ingest
independently requires the successful producer run, source SHA and attempt.

The `release-readiness-signing` environment must supply the existing external
`READINESS_SIGNING_KEY`. Its matching public key must be enrolled as
`readiness/trust/RC-04.pem` on protected GitOps main. The workflow reads that
policy from an independently resolved protected-main checkout. It writes the
private key only to runner temporary storage and removes it after signing.

Read access is separate from signing custody: `READINESS_GITOPS_READ_TOKEN`
reads the candidate and protected policy, `READINESS_TOOLS_READ_TOKEN` reads
the pinned helper, and `READINESS_POSTURE_TOKEN` reads metadata, contents,
rulesets and environments across the cohort. No credential values belong in
an observation or repository. Missing custody or read access fails the run.

Run the focused checks with:

```sh
python3 -B -m unittest discover -s tests -p test_release_readiness_collector.py
actionlint .github/workflows/produce-release-readiness.yml
```

`make check` runs the repository's complete offline gate. Passing source tests
does not establish a live RC-04 result; that requires the actual protected
producer run and evidence evaluation.
