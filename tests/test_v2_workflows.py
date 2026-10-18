"""Behaviour tests for the inline scripts of the v2 reusable workflows.

The scripts live inside the workflow YAML because a reusable workflow checks
out the caller, not this repository. These tests pull a step's `run:` block
out of the workflow and execute it against fixtures, so the logic that decides
releases, vulnerability failures and the gate result is exercised as shipped.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def step_script(workflow: str, step_name: str) -> str:
    lines = (WORKFLOWS / workflow).read_text(encoding="utf-8").splitlines()
    start = lines.index(f"      - name: {step_name}")
    run = next(
        i
        for i in range(start + 1, len(lines))
        if lines[i] == "        run: |" or lines[i].startswith("      - name: ")
    )
    if lines[run] != "        run: |":
        raise AssertionError(f"step {step_name!r} in {workflow} has no run block")
    body: list[str] = []
    for line in lines[run + 1 :]:
        if line.startswith("          ") or line == "":
            body.append(line[10:])
        else:
            break
    return "\n".join(body) + "\n"


def run_bash(script: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    # GitHub runs `shell: bash` as `bash -eo pipefail`.
    return subprocess.run(
        ["bash", "-eo", "pipefail", "-c", script],
        cwd=cwd,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def outputs(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        key, sep, value = lines[i].partition("=")
        if sep:
            result[key] = value
        elif "<<" in lines[i]:
            key, delimiter = lines[i].split("<<", 1)
            end = lines.index(delimiter, i + 1)
            result[key] = "\n".join(lines[i + 1 : end])
            i = end
        i += 1
    return result


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


class ReleaseSignatureIdentityTest(unittest.TestCase):
    SCRIPT = step_script("release-image.yml", "Verify the signature, then publish the tag")
    BASE = (
        "https://github.com/Mindburn-Labs/platform-actions/"
        ".github/workflows/release-image.yml@"
    )

    def test_only_complete_v2_tag_refs_are_accepted(self) -> None:
        match = re.search(r"--certificate-identity-regexp '([^']+)'", self.SCRIPT)
        self.assertIsNotNone(match)
        pattern = re.compile(match.group(1))
        for ref in ("refs/tags/v2", "refs/tags/v2.1.0"):
            with self.subTest(ref=ref):
                self.assertIsNotNone(pattern.search(self.BASE + ref))
        for identity in (
            self.BASE + "refs/heads/main",
            self.BASE + "refs/tags/v2-evil",
            self.BASE + "refs/tags/v2.1.0-extra",
            self.BASE + "refs/tags/v2.1.0/evil",
            self.BASE + "refs/tags/v3",
            self.BASE.replace("release-image.yml", "release-image-evil.yml")
            + "refs/tags/v2",
        ):
            with self.subTest(identity=identity):
                self.assertIsNone(pattern.search(identity))
        self.assertIn(
            "--certificate-oidc-issuer https://token.actions.githubusercontent.com",
            self.SCRIPT,
        )
        self.assertIn(
            '--certificate-github-workflow-repository "${GITHUB_REPOSITORY}"',
            self.SCRIPT,
        )


class AutoTagTest(unittest.TestCase):
    SCRIPT = step_script("auto-tag.yml", "Compute the next tag")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "test@example.invalid")
        git(self.repo, "config", "user.name", "test")
        git(self.repo, "config", "commit.gpgsign", "false")
        git(self.repo, "config", "tag.gpgsign", "false")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def commit(self, message: str) -> str:
        git(self.repo, "commit", "-q", "--allow-empty", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def next_tag(self, sha: str | None = None) -> subprocess.CompletedProcess[str]:
        out = self.repo / "github_output"
        out.write_text("", encoding="utf-8")
        result = run_bash(
            self.SCRIPT,
            self.repo,
            {
                "SOURCE_SHA": sha or git(self.repo, "rev-parse", "HEAD"),
                "INITIAL_VERSION": "0.1.0",
                "GITHUB_OUTPUT": str(out),
            },
        )
        result.tag = outputs(out).get("tag")  # type: ignore[attr-defined]
        return result

    def released(self, message: str = "chore: base") -> None:
        self.commit(message)
        git(self.repo, "tag", "v1.2.3")

    def test_first_release_uses_initial_version(self) -> None:
        self.commit("chore: scaffold")
        self.assertEqual(self.next_tag().tag, "v0.1.0")

    def test_fix_is_patch(self) -> None:
        self.released()
        self.commit("fix(api): handle nil")
        self.assertEqual(self.next_tag().tag, "v1.2.4")

    def test_feat_is_minor_and_wins_over_fix(self) -> None:
        self.released()
        self.commit("fix: one")
        self.commit("feat: two (#12)")
        self.assertEqual(self.next_tag().tag, "v1.3.0")

    def test_bang_is_major(self) -> None:
        self.released()
        self.commit("refactor(core)!: drop v1 routes")
        self.assertEqual(self.next_tag().tag, "v2.0.0")

    def test_breaking_change_footer_is_major(self) -> None:
        self.released()
        self.commit("feat: new config\n\nBREAKING CHANGE: the old key is gone")
        self.assertEqual(self.next_tag().tag, "v2.0.0")

    def test_no_releasable_commit_means_no_tag(self) -> None:
        self.released()
        self.commit("docs: typo")
        self.commit("chore(deps-dev): bump eslint")
        result = self.next_tag()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.tag, "")

    def test_commit_already_in_newest_release_is_not_retagged(self) -> None:
        self.commit("chore: base")
        older = self.commit("fix: older")
        self.commit("feat: newer")
        git(self.repo, "tag", "v1.3.0")
        result = self.next_tag(older)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.tag, "")

    def test_non_release_tags_are_ignored(self) -> None:
        self.released()
        for tag in ("chart-v9.9.9", "v2", "v9.0.0-rc1"):
            git(self.repo, "tag", tag)
        self.commit("fix: patch")
        self.assertEqual(self.next_tag().tag, "v1.2.4")

    def test_diverged_history_fails(self) -> None:
        self.commit("chore: base")
        git(self.repo, "checkout", "-q", "-b", "side")
        self.commit("feat: side")
        git(self.repo, "tag", "v1.0.0")
        git(self.repo, "checkout", "-q", "main")
        self.commit("fix: main")
        result = self.next_tag()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to tag diverged history", result.stdout)


def osv(*packages: tuple[str, str, str, str, str]) -> dict:
    """Build osv-scanner JSON from (name, version, advisory, cvss, ghsa_severity)."""
    return {
        "results": [
            {
                "source": {"path": "package-lock.json", "type": "lockfile"},
                "packages": [
                    {
                        "package": {"name": name, "version": version, "ecosystem": "npm"},
                        "vulnerabilities": [
                            {"id": advisory, "database_specific": {"severity": ghsa}}
                        ],
                        "groups": [{"ids": [advisory], "max_severity": cvss}],
                    }
                    for name, version, advisory, cvss, ghsa in packages
                ],
            }
        ]
        if packages
        else None
    }


class DependencyScanTest(unittest.TestCase):
    SCRIPT = step_script("ci.yml", "Fail on HIGH or CRITICAL vulnerabilities new in this change")
    MINIMIST = ("minimist", "1.2.5", "GHSA-xvch-5gv4-984h", "9.8", "CRITICAL")
    LODASH = ("lodash", "4.17.20", "GHSA-35jh-r3h4-6jhm", "7.2", "HIGH")
    MODERATE = ("lodash", "4.17.20", "GHSA-29mw-wpgm-hmr9", "5.3", "MODERATE")

    def compare(self, base: dict, head: dict) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "base.json").write_text(json.dumps(base), encoding="utf-8")
            (work / "head.json").write_text(json.dumps(head), encoding="utf-8")
            return run_bash(self.SCRIPT, work, {})

    def test_new_high_fails(self) -> None:
        result = self.compare(osv(self.LODASH), osv(self.LODASH, self.MINIMIST))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("minimist@1.2.5: GHSA-xvch-5gv4-984h", result.stdout)
        self.assertNotIn("::error::npm lodash", result.stdout)

    def test_new_high_fails_when_base_is_clean(self) -> None:
        result = self.compare(osv(), osv(self.MINIMIST))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_inherited_high_passes_with_notice(self) -> None:
        result = self.compare(osv(self.LODASH), osv(self.LODASH))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 HIGH/CRITICAL finding(s) already exist", result.stdout)

    def test_version_bump_that_keeps_an_advisory_is_not_new(self) -> None:
        bumped = ("lodash", "4.17.21", *self.LODASH[2:])
        result = self.compare(osv(self.LODASH), osv(bumped))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_new_moderate_passes(self) -> None:
        result = self.compare(osv(), osv(self.MODERATE))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_advisory_rating_counts_without_cvss(self) -> None:
        unscored = ("left-pad", "1.0.0", "GHSA-aaaa-bbbb-cccc", "", "HIGH")
        result = self.compare(osv(), osv(unscored))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)


class DependabotAutoMergeTest(unittest.TestCase):
    SCRIPT = step_script("dependabot-auto-merge.yml", "Arm auto-merge for patch and minor updates")
    DEFAULT_TYPES = "version-update:semver-patch,version-update:semver-minor"

    def arm(self, update_type: str, required_rules: int) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            log = work / "gh.log"
            gh = work / "gh"
            # A stand-in for the gh CLI: `api` answers with the number of
            # required-status-check rules, every call is logged.
            gh.write_text(
                "#!/usr/bin/env bash\n"
                f'echo "$*" >>"{log}"\n'
                f'if [[ "$1" == api ]]; then echo {required_rules}; fi\n',
                encoding="utf-8",
            )
            gh.chmod(0o755)
            result = run_bash(
                self.SCRIPT,
                work,
                {
                    "PATH": f"{work}:{os.environ['PATH']}",
                    "GITHUB_REPOSITORY": "Mindburn-Labs/example",
                    "PR_URL": "https://github.com/Mindburn-Labs/example/pull/7",
                    "BASE": "main",
                    "UPDATE_TYPE": update_type,
                    "UPDATE_TYPES": self.DEFAULT_TYPES,
                    "DEPENDENCIES": "lodash",
                },
            )
            calls = log.read_text(encoding="utf-8") if log.exists() else ""
            return result, calls

    def test_patch_arms_auto_merge(self) -> None:
        result, calls = self.arm("version-update:semver-patch", required_rules=1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pr merge --auto --squash https://github.com/Mindburn-Labs/example/pull/7", calls)
        self.assertIn("api repos/Mindburn-Labs/example/rules/branches/main", calls)

    def test_minor_arms_auto_merge(self) -> None:
        _, calls = self.arm("version-update:semver-minor", required_rules=2)
        self.assertIn("pr merge --auto --squash", calls)

    def test_major_is_left_for_review(self) -> None:
        result, calls = self.arm("version-update:semver-major", required_rules=1)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls, "")
        self.assertIn("left for review", result.stdout)

    def test_unknown_update_type_is_left_for_review(self) -> None:
        _, calls = self.arm("", required_rules=1)
        self.assertEqual(calls, "")

    def test_no_required_check_arms_nothing(self) -> None:
        result, calls = self.arm("version-update:semver-patch", required_rules=0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("pr merge", calls)
        self.assertIn("no required status check", result.stdout)


class ReleaseReadinessTargetTest(unittest.TestCase):
    SCRIPT = step_script("ci.yml", "Identify exact promotion target")

    def classify(self, changed_path: str | None, branch: str, *, base_sha: str | None = None,
                 event: str = "pull_request", move: tuple[str, str] | None = None,
                 delete: str | None = None,
                 head_repository: str = "Mindburn-Labs/gitops-platform",
                 changed_paths: tuple[str, ...] | None = None,
                 typed_kind: str = "", invalid_record: bool = False,
                 return_argv: bool = False) -> tuple[int, str, str] | tuple[int, str, str, dict]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = root / "candidate"
            candidate.mkdir()
            git(candidate, "init", "-q")
            git(candidate, "config", "user.email", "readiness-test@example.invalid")
            git(candidate, "config", "user.name", "Readiness Test")
            for name in ("README.md", "clusters/staging-do/pin.yaml",
                         "clusters/staging-do/odd\npin.yaml", "clusters/production-do/pin.yaml"):
                path = candidate / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"{name}\n", encoding="utf-8")
            git(candidate, "add", ".")
            git(candidate, "commit", "-qm", "base")
            base = git(candidate, "rev-parse", "HEAD")
            if move:
                source, destination = (candidate / name for name in move)
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.rename(destination)
            elif delete:
                (candidate / delete).unlink()
            else:
                paths = changed_paths or ((changed_path,) if changed_path is not None else ())
                assert paths
                for name in paths:
                    path = candidate / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("changed\n", encoding="utf-8")
            git(candidate, "add", "-A")
            git(candidate, "commit", "-qm", "candidate")
            head = git(candidate, "rev-parse", "HEAD")
            for checkout in ("gate", "trusted"):
                path = root / checkout
                subprocess.run(["git", "clone", "-q", str(candidate), str(path)], check=True)
                git(path, "checkout", "-q", base)
            script = root / "trusted" / "scripts" / "validate-readiness-operation.rb"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(
                "require 'json'\n"
                "args = ARGV.each_slice(2).to_h\n"
                "File.write(ENV.fetch('VALIDATOR_LOG'), JSON.generate(args))\n"
                "kind = ENV.fetch('STUB_KIND')\n"
                "if kind.empty?\n"
                "  warn '::error::readiness operation refused: fixture'\n"
                "  exit 1\n"
                "end\n"
                "record = {'schema' => 'gitops-readiness-operation-validation-v1',\n"
                "  'status' => 'VALIDATED', 'kind' => kind,\n"
                "  'policyCommitSha' => args.fetch('--policy-sha'),\n"
                "  'diffBaseSha' => args.fetch('--diff-base-sha'),\n"
                "  'candidateSha' => args.fetch('--candidate-sha'),\n"
                "  'servingChange' => ENV['INVALID_RECORD'] == 'true',\n"
                "  'environmentReadinessAssessed' => false, 'productAccepted' => false}\n"
                "File.write(args.fetch('--record'), JSON.generate(record))\n"
                "puts kind\n",
                encoding="utf-8",
            )
            output = root / "output"
            validator_log = root / "validator-argv.json"
            result = run_bash(self.SCRIPT, root, {
                "EVENT_NAME": event, "HEAD_REF": branch,
                "HEAD_REPOSITORY": head_repository,
                "BASE_REF": "main", "DEFAULT_BRANCH": "main",
                "BASE_SHA": base_sha or base, "CANDIDATE_SHA": head,
                "POLICY_SHA": base, "RUNNER_TEMP": str(root),
                "VALIDATOR_LOG": str(validator_log), "STUB_KIND": typed_kind,
                "INVALID_RECORD": str(invalid_record).lower(),
                "GITHUB_REPOSITORY": "Mindburn-Labs/gitops-platform",
                "GITHUB_OUTPUT": str(output),
            })
            values = (result.returncode, output.read_text() if output.exists() else "", result.stderr + result.stdout)
            if return_argv:
                return (*values, json.loads(validator_log.read_text()) if validator_log.exists() else {})
            return values

    def test_regular_source_pr_passes_with_no_target(self) -> None:
        code, output, message = self.classify("README.md", "feature/readme")
        self.assertEqual(code, 0, message)
        self.assertEqual(output, "targets=\n")

    def test_non_promotion_branch_cannot_change_target_cluster(self) -> None:
        code, _, message = self.classify("clusters/staging-do/pin.yaml", "feature/staging-pin")
        self.assertNotEqual(code, 0)
        self.assertIn("target cluster changes require", message)

    def test_unusual_filename_cannot_hide_target_change(self) -> None:
        code, _, message = self.classify("clusters/staging-do/odd\npin.yaml", "feature/staging-pin")
        self.assertNotEqual(code, 0)
        self.assertIn("target cluster changes require", message)

    def test_matching_promotion_branch_produces_exact_target(self) -> None:
        code, output, message = self.classify("clusters/staging-do/pin.yaml", "promote/staging-cp-v1")
        self.assertEqual(code, 0, message)
        self.assertEqual(output, "targets=staging-do\n")

    def test_other_target_on_promotion_branch_fails(self) -> None:
        code, _, message = self.classify("clusters/production-do/pin.yaml", "promote/staging-cp-v1")
        self.assertNotEqual(code, 0)
        self.assertIn("unrelated path", message)

    def test_move_out_of_staging_cannot_look_like_docs_only(self) -> None:
        code, _, message = self.classify(None, "maintenance/move-probe",
                                         move=("clusters/staging-do/pin.yaml", "docs/probe.yaml"))
        self.assertNotEqual(code, 0)
        self.assertIn("target cluster changes require", message)

    def test_move_into_staging_is_classified(self) -> None:
        code, _, message = self.classify(None, "maintenance/move-readme",
                                         move=("README.md", "clusters/staging-do/readme.yaml"))
        self.assertNotEqual(code, 0)
        self.assertIn("target cluster changes require", message)

    def test_cross_environment_move_cannot_hide_source_deletion(self) -> None:
        code, _, message = self.classify(None, "promote/production-cp-v1",
                                         move=("clusters/staging-do/pin.yaml", "clusters/production-do/staging-pin.yaml"))
        self.assertNotEqual(code, 0)
        self.assertIn("unrelated path", message)

    def test_target_deletion_is_classified(self) -> None:
        code, _, message = self.classify(None, "maintenance/delete-pin", delete="clusters/staging-do/pin.yaml")
        self.assertNotEqual(code, 0)
        self.assertIn("target cluster changes require", message)

    def test_move_with_unusual_filename_cannot_hide_source_deletion(self) -> None:
        code, _, message = self.classify(None, "maintenance/move-odd",
                                         move=("clusters/staging-do/odd\npin.yaml", "docs/odd\npin.yaml"))
        self.assertNotEqual(code, 0)
        self.assertIn("target cluster changes require", message)

    def test_merge_group_classifies_target_with_unusual_filename(self) -> None:
        code, output, message = self.classify("clusters/staging-do/odd\npin.yaml", "", event="merge_group")
        self.assertEqual(code, 0, message)
        self.assertEqual(output, "targets=staging-do\n")

    def test_merge_group_cross_environment_move_checks_both(self) -> None:
        code, output, message = self.classify(None, "", event="merge_group",
                                              move=("clusters/staging-do/pin.yaml", "clusters/production-do/staging-pin.yaml"))
        self.assertEqual(code, 0, message)
        self.assertEqual(set(output.strip().removeprefix("targets=").split()),
                         {"staging-do", "production-do"})

    def test_failed_diff_cannot_look_like_empty_target(self) -> None:
        code, _, message = self.classify("README.md", "feature/readme", base_sha="0" * 40)
        self.assertNotEqual(code, 0)
        self.assertIn("cannot classify changed cluster paths", message)

    def test_fork_promotion_fails(self) -> None:
        code, _, message = self.classify("clusters/staging-do/pin.yaml", "promote/staging-cp-v1",
                                         head_repository="Mindburn-Labs/other-repository")
        self.assertNotEqual(code, 0)
        self.assertIn("must originate in this repository", message)

    def test_reviewed_credential_pr_uses_trusted_validator(self) -> None:
        code, output, message, argv = self.classify(
            "clusters/staging-do/approved.sops.yaml", "operations/credential",
            typed_kind="credential-preparation", return_argv=True)
        self.assertEqual(code, 0, message)
        self.assertEqual(output, "targets=\noperation_kind=credential-preparation\n")
        self.assertEqual(argv["--policy-repo"], "trusted")
        self.assertEqual(argv["--diff-base-repo"], "gate")
        self.assertEqual(argv["--candidate-repo"], "candidate")
        self.assertEqual(argv["--event"], "pull_request")
        self.assertEqual(argv["--base-ref"], "main")
        self.assertEqual(argv["--default-branch"], "main")

    def test_reviewed_diagnostic_pr_with_three_paths_uses_one_permit(self) -> None:
        code, output, message, argv = self.classify(
            None, "operations/readback", typed_kind="diagnostic-readback",
            changed_paths=(
                "clusters/staging-do/kustomization.yaml",
                "clusters/staging-do/readback/r2.json",
                "clusters/staging-do/readback/kustomization.yaml",
            ), return_argv=True)
        self.assertEqual(code, 0, message)
        self.assertEqual(output, "targets=\noperation_kind=diagnostic-readback\n")
        self.assertEqual(argv["--repository"], "Mindburn-Labs/gitops-platform")

    def test_reviewed_merge_group_uses_typed_validator(self) -> None:
        code, output, message, argv = self.classify(
            "clusters/staging-do/approved.sops.yaml", "", event="merge_group",
            typed_kind="credential-preparation", return_argv=True)
        self.assertEqual(code, 0, message)
        self.assertEqual(output, "targets=\noperation_kind=credential-preparation\n")
        self.assertEqual(argv["--event"], "merge_group")

    def test_invalid_operation_record_fails_pr(self) -> None:
        code, output, message = self.classify(
            "clusters/staging-do/approved.sops.yaml", "operations/credential",
            typed_kind="credential-preparation", invalid_record=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(output, "")
        self.assertIn("invalid record", message)

    def test_unpermitted_merge_group_falls_back_to_full_readiness(self) -> None:
        code, output, message, argv = self.classify(
            "clusters/staging-do/pin.yaml", "", event="merge_group", return_argv=True)
        self.assertEqual(code, 0, message)
        self.assertEqual(output, "targets=staging-do\n")
        self.assertEqual(argv["--event"], "merge_group")
        self.assertIn("evaluating full readiness", message)

    def test_cross_target_merge_group_does_not_try_typed_permit(self) -> None:
        code, output, message, argv = self.classify(
            None, "", event="merge_group", typed_kind="credential-preparation",
            changed_paths=("clusters/staging-do/pin.yaml", "clusters/production-do/pin.yaml"),
            return_argv=True)
        self.assertEqual(code, 0, message)
        self.assertEqual(set(output.strip().removeprefix("targets=").split()),
                         {"staging-do", "production-do"})
        self.assertEqual(argv, {})

    def test_readiness_job_is_scheduled_for_every_gitops_pr(self) -> None:
        workflow = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        job = workflow.split("  release-readiness:\n", 1)[1].split("  dependency-scan:\n", 1)[0]
        self.assertIn("github.event_name == 'merge_group' || github.event_name == 'pull_request'", job)
        self.assertNotIn("startsWith(github.event.pull_request.head.ref", job)
        self.assertIn("ruby trusted/scripts/validate-readiness-operation.rb", job)
        self.assertIn("if: steps.target.outputs.targets != ''", job)
        self.assertIn("name: gitops-typed-operation-validation", job)
        typed_outcome = job.split("      - name: Record bounded operation outcome\n", 1)[1].split("      - name:", 1)[0]
        self.assertNotIn("MINDBURN_ORG_READ_TOKEN", typed_outcome)
        full_rc = job.split("      - name: Evaluate every touched environment\n", 1)[1].split("      - name:", 1)[0]
        self.assertIn("if: steps.target.outputs.targets != ''", full_rc)
        self.assertIn("MINDBURN_ORG_READ_TOKEN", full_rc)


class DefaultMainPolicyTest(unittest.TestCase):
    RESOLVE = step_script("ci.yml", "Resolve actual default-main operation policy")
    VERIFY = step_script("ci.yml", "Verify current default-main policy checkout")

    def run_step(self, script: str, *, event_branch: str = "main",
                 api_branch: str = "main", api_sha: str = "a" * 40) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gh = root / "gh"
            gh.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$2\" == repos/Mindburn-Labs/gitops-platform ]]; then\n"
                "  printf '%s\\n' \"${FAKE_BRANCH}\"\n"
                "else\n"
                "  printf '%s\\n' \"${FAKE_SHA}\"\n"
                "fi\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            output = root / "output"
            result = run_bash(script, root, {
                "PATH": f"{root}:{os.environ['PATH']}",
                "GITHUB_REPOSITORY": "Mindburn-Labs/gitops-platform",
                "GITHUB_OUTPUT": str(output), "EVENT_DEFAULT_BRANCH": event_branch,
                "FAKE_BRANCH": api_branch, "FAKE_SHA": api_sha,
            })
            return result, output.read_text() if output.exists() else ""

    def test_policy_resolves_actual_main_commit(self) -> None:
        result, output = self.run_step(self.RESOLVE)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(output, f"sha={'a' * 40}\n")

    def test_policy_refuses_nonmain_default_or_invalid_sha(self) -> None:
        for branch, sha in (("trunk", "a" * 40), ("main", "bad-sha")):
            with self.subTest(branch=branch, sha=sha):
                result, _ = self.run_step(self.RESOLVE, api_branch=branch, api_sha=sha)
                self.assertNotEqual(result.returncode, 0)

    def test_policy_refuses_stale_event_default_branch(self) -> None:
        result, _ = self.run_step(self.RESOLVE, event_branch="trunk")
        self.assertNotEqual(result.returncode, 0)

    def test_checkout_must_match_live_main(self) -> None:
        # The API head must equal the separately checked-out commit.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trusted = root / "trusted"
            trusted.mkdir()
            git(trusted, "init", "-q", "-b", "main")
            git(trusted, "config", "user.email", "test@example.invalid")
            git(trusted, "config", "user.name", "Test")
            git(trusted, "commit", "-qm", "policy", "--allow-empty")
            sha = git(trusted, "rev-parse", "HEAD")
            gh = root / "gh"
            gh.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"${FAKE_SHA}\"\n", encoding="utf-8")
            gh.chmod(0o755)
            env = {"PATH": f"{root}:{os.environ['PATH']}",
                   "GITHUB_REPOSITORY": "Mindburn-Labs/gitops-platform",
                   "POLICY_SHA": sha, "FAKE_SHA": sha}
            passed = run_bash(self.VERIFY, root, env)
            self.assertEqual(passed.returncode, 0, passed.stderr + passed.stdout)
            moved = run_bash(self.VERIFY, root, {**env, "FAKE_SHA": "b" * 40})
            self.assertNotEqual(moved.returncode, 0)

    def test_checkout_uses_independent_resolved_commit(self) -> None:
        workflow = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
        checkout = workflow.split("      - name: Check out actual default-main operation policy\n", 1)[1].split("      - name:", 1)[0]
        self.assertIn("ref: ${{ steps.policy.outputs.sha }}", checkout)
        self.assertIn("fetch-depth: 0", checkout)
        self.assertIn("path: trusted", checkout)
        self.assertIn("persist-credentials: false", checkout)


class GateTest(unittest.TestCase):
    SCRIPT = step_script("ci.yml", "Require success with explicit skip policy")

    def gate(
        self,
        event: str = "pull_request",
        repo: str = "Mindburn-Labs/platform-actions",
        head: str = "feature/test",
        **results: str,
    ) -> int:
        needs = {job: {"result": result, "outputs": {}} for job, result in results.items()}
        with tempfile.TemporaryDirectory() as tmp:
            return run_bash(
                self.SCRIPT,
                Path(tmp),
                {
                    "NEEDS": json.dumps(needs),
                    "EVENT_NAME": event,
                    "REPOSITORY": repo,
                    "HEAD_REF": head,
                },
            ).returncode

    def test_event_and_result_matrix(self) -> None:
        for event in ("pull_request", "merge_group", "push", "workflow_dispatch", "repository_dispatch", "schedule"):
            for check in ("success", "failure", "cancelled", "skipped"):
                for scan in ("success", "failure", "cancelled", "skipped"):
                    for readiness in ("success", "failure", "cancelled", "skipped"):
                        with self.subTest(event=event, check=check, scan=scan, readiness=readiness):
                            allowed = (
                                check == "success"
                                and (scan == "success" or (scan == "skipped" and event not in ("pull_request", "merge_group")))
                                and readiness in ("success", "skipped")
                            )
                            result = self.gate(event, **{"check": check, "dependency-scan": scan, "release-readiness": readiness})
                            self.assertEqual(result == 0, allowed)

    def test_every_gitops_pr_and_merge_queue_require_readiness(self) -> None:
        base = {"check": "success", "dependency-scan": "success"}
        for event, head in (("pull_request", "feature/staging-pin"), ("pull_request", "promote/staging-cp-v1"),
                            ("pull_request", "promote/production-cp-v1"), ("merge_group", "")):
            for readiness in ("success", "failure", "cancelled", "skipped"):
                with self.subTest(event=event, head=head, readiness=readiness):
                    result = self.gate(event, repo="Mindburn-Labs/gitops-platform", head=head,
                                       **{**base, "release-readiness": readiness})
                    self.assertEqual(result == 0, readiness == "success")

    def test_missing_required_job_fails(self) -> None:
        for results in ({}, {"check": "success"}, {"dependency-scan": "success"},
                        {"check": "success", "dependency-scan": "skipped"}):
            with self.subTest(results=results):
                self.assertNotEqual(self.gate("push", **results), 0)

    def test_additional_jobs_have_no_skip_allowance(self) -> None:
        for result in ("success", "failure", "cancelled", "skipped", "unknown"):
            with self.subTest(result=result):
                code = self.gate(
                    "push", **{"check": "success", "dependency-scan": "skipped", "release-readiness": "skipped", "new-job": result}
                )
                self.assertEqual(code == 0, result == "success")

    def test_missing_event_fails(self) -> None:
        self.assertNotEqual(self.gate("", **{"check": "success", "dependency-scan": "skipped", "release-readiness": "skipped"}), 0)


class DetectToolchainsTest(unittest.TestCase):
    SCRIPT = step_script("ci.yml", "Detect toolchains")

    def detect(self, files: dict[str, str]) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, "init", "-q")
            for name, content in files.items():
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            git(repo, "add", "-A")
            out = repo.parent / f"{repo.name}.out"
            out.write_text("", encoding="utf-8")
            try:
                result = run_bash(self.SCRIPT, repo, {"GITHUB_OUTPUT": str(out)})
                self.assertEqual(result.returncode, 0, result.stderr)
                return outputs(out)
            finally:
                out.unlink()

    def test_nested_polyglot_repository(self) -> None:
        detected = self.detect(
            {
                "core/go.mod": "module x\n",
                "core/sub/go.mod": "module y\n",
                "sdk/ts/package-lock.json": "{}",
                "sdk/py/pyproject.toml": "",
                "sdk/rust/Cargo.toml": "",
                "sdk/rust/Cargo.lock": "",
                "deploy/chart/Chart.yaml": "",
                "infra/main.tf": "",
                ".nvmrc": "22\n",
            }
        )
        self.assertEqual(detected["go_version_file"], "core/go.mod")
        self.assertEqual(detected["node_cache"], "npm")
        self.assertEqual(detected["node_version_file"], ".nvmrc")
        self.assertEqual(detected["rust_workspaces"], "sdk/rust")
        for key in ("go", "node", "python", "rust", "helm", "terraform"):
            self.assertEqual(detected.get(key), "true", key)
        self.assertNotIn("ruby", detected)
        self.assertNotIn("pnpm", detected)

    def test_pnpm_version_only_without_package_manager(self) -> None:
        pinned = self.detect(
            {"package.json": '{"packageManager": "pnpm@10.4.0"}', "pnpm-lock.yaml": ""}
        )
        self.assertEqual(pinned["node_cache"], "pnpm")
        self.assertNotIn("pnpm_version", pinned)
        unpinned = self.detect({"package.json": "{}", "pnpm-lock.yaml": ""})
        self.assertEqual(unpinned["pnpm_version"], "10")

    def test_docs_only_repository_detects_nothing(self) -> None:
        detected = self.detect({"README.md": "# docs\n", "Makefile": "check:\n"})
        self.assertEqual(detected, {})


if __name__ == "__main__":
    unittest.main()
