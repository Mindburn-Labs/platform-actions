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

from workflow_fixtures import WORKFLOWS, git, outputs, run_bash, step_script


class HostedCheckRunnerTest(unittest.TestCase):
    SCRIPT = step_script("ci.yml", "Validate hosted check runner")

    def test_supported_hosted_runners_are_accepted(self) -> None:
        for runner in ("ubuntu-latest", "ubuntu-24.04", "ubuntu-22.04"):
            with self.subTest(runner=runner), tempfile.TemporaryDirectory() as tmp:
                result = run_bash(self.SCRIPT, Path(tmp), {"CHECK_RUNNER": runner, "CHECK_SHARDS": "1"})
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_other_runner_values_are_rejected(self) -> None:
        for runner in (
            "self-hosted", "private-linux", "ubuntu-20.04", "ubuntu-22.04-gpu", "",
            '["self-hosted", "linux"]', "${{ inputs.runner }}",
            "ubuntu-24.04; touch runner-injection", "ubuntu-22.04$(touch runner-injection)",
            "ubuntu-22.04\nself-hosted", " ubuntu-22.04",
        ):
            with self.subTest(runner=runner), tempfile.TemporaryDirectory() as tmp:
                result = run_bash(self.SCRIPT, Path(tmp), {"CHECK_RUNNER": runner, "CHECK_SHARDS": "1"})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("::error::check-runner", result.stdout)
                self.assertFalse((Path(tmp) / "runner-injection").exists())

    def test_runner_allocation_uses_only_fixed_hosted_labels(self) -> None:
        workflow = (WORKFLOWS / "ci.yml").read_text()
        check = workflow.split("  check:\n", 1)[1].split("    steps:\n", 1)[0]
        selection = re.search(r"    runs-on: (.+)", check).group(1)
        self.assertEqual(selection, "${{ inputs.check-runner == 'ubuntu-24.04' && 'ubuntu-24.04' || inputs.check-runner == 'ubuntu-22.04' && 'ubuntu-22.04' || 'ubuntu-latest' }}")

    def test_runner_validation_is_the_first_step(self) -> None:
        workflow = (WORKFLOWS / "ci.yml").read_text()
        check = workflow.split("  check:\n", 1)[1]
        steps = check.split("    steps:\n", 1)[1]
        self.assertTrue(steps.lstrip().startswith("- name: Validate hosted check runner\n"))


class CheckShardsTest(unittest.TestCase):
    SCRIPT = step_script("ci.yml", "Validate hosted check runner")
    WORKFLOW = (WORKFLOWS / "ci.yml").read_text()
    CHECK = WORKFLOW.split("  check:\n", 1)[1].split("    steps:\n", 1)[0]

    def validate(self, shards: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            return run_bash(self.SCRIPT, Path(tmp), {"CHECK_RUNNER": "ubuntu-latest", "CHECK_SHARDS": shards})

    def test_one_to_eight_shards_are_accepted(self) -> None:
        for shards in range(1, 9):
            with self.subTest(shards=shards):
                self.assertEqual(self.validate(str(shards)).returncode, 0)

    def test_other_shard_counts_are_rejected(self) -> None:
        for shards in ("0", "9", "-1", "2.5", "", "two", "4; touch x", "1\n9"):
            with self.subTest(shards=shards):
                result = self.validate(shards)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("::error::check-shards", result.stdout)

    def test_matrix_lists_exactly_the_requested_shards(self) -> None:
        matrix = re.search(r"        shard: \$\{\{ fromJSON\((.+)\) \}\}", self.CHECK).group(1)
        branches = dict(re.findall(r"inputs\.check-shards == (\d) && '(\[[\d,]+\])'", matrix))
        self.assertEqual(sorted(branches), [str(n) for n in range(2, 9)])
        for count, listed in branches.items():
            self.assertEqual(json.loads(listed), list(range(1, int(count) + 1)))
        # One shard, and any count the first step rejects, is a single job.
        self.assertTrue(matrix.endswith("|| '[1]'"))

    def test_every_shard_reports_and_knows_its_part(self) -> None:
        self.assertIn("      fail-fast: false\n", self.CHECK)
        self.assertIn(
            "      CHECK_SHARD: ${{ inputs.check-shards > 1 && format('{0}/{1}', matrix.shard, inputs.check-shards) || '' }}\n",
            self.CHECK,
        )

    def test_gate_still_needs_the_whole_check_matrix(self) -> None:
        gate = self.WORKFLOW.split("  gate:\n", 1)[1]
        self.assertIn("    if: always()\n    needs: [check, dependency-scan, release-readiness]\n", gate)


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


class LaneGateTest(unittest.TestCase):
    """gate.yml: `ci / gate` for lanes that ran beside `make check`."""

    SCRIPT = step_script("gate.yml", "Require success from every needed job")

    def gate(self, results: object) -> int:
        raw = results if isinstance(results, str) else json.dumps(
            {job: {"result": result, "outputs": {}} for job, result in results.items()}
        )
        with tempfile.TemporaryDirectory() as tmp:
            return run_bash(self.SCRIPT, Path(tmp), {"RESULTS": raw}).returncode

    def test_all_successful_lanes_pass(self) -> None:
        self.assertEqual(self.gate({"check": "success"}), 0)
        self.assertEqual(self.gate({"check": "success", "lane-a": "success", "lane-b": "success"}), 0)

    def test_any_lane_that_did_not_succeed_fails(self) -> None:
        for result in ("failure", "cancelled", "skipped", "unknown", ""):
            for failing in ("check", "lane-a"):
                with self.subTest(result=result, failing=failing):
                    lanes = {"check": "success", "lane-a": "success", "lane-b": "success", failing: result}
                    self.assertNotEqual(self.gate(lanes), 0)

    def test_a_check_job_is_required(self) -> None:
        self.assertNotEqual(self.gate({"lane-a": "success", "lane-b": "success"}), 0)
        self.assertNotEqual(self.gate({}), 0)

    def test_malformed_results_fail(self) -> None:
        for raw in ("", "not json", "[]", "null", "true", '"success"', '{"check": "success"}', '[{"result": "success"}]'):
            with self.subTest(raw=raw):
                self.assertNotEqual(self.gate(raw), 0)

    def test_more_than_one_json_document_fails(self) -> None:
        good = '{"check": {"result": "success"}}'
        bad = '{"lane": {"result": "failure"}}'
        for raw in (f"{bad} {good}", f"{bad}\n{good}", f"{good} {good}", f"{good} {bad}",
                    '{"check": {"result": "failure"}, "check": {"result": "success"}} ' + good):
            with self.subTest(raw=raw):
                self.assertNotEqual(self.gate(raw), 0)

    def test_duplicate_keys_resolve_as_github_would_emit_them(self) -> None:
        # toJSON(needs) never repeats a key; if it did, jq keeps the last one,
        # and a last result that is not success still fails.
        self.assertNotEqual(self.gate('{"check": {"result": "success"}, "check": {"result": "failure"}}'), 0)

    def test_gate_job_reports_as_gate_and_holds_no_token_permission(self) -> None:
        workflow = (WORKFLOWS / "gate.yml").read_text()
        self.assertIn("\npermissions: {}\n", workflow)
        self.assertIn("\njobs:\n  gate:\n    runs-on: ubuntu-latest\n", workflow)
        self.assertIn("        required: true\n", workflow)

    def test_ci_workflow_gate_is_unchanged_by_lanes(self) -> None:
        self.assertNotIn("upstream", (WORKFLOWS / "ci.yml").read_text().lower())

    def test_this_repository_gates_on_make_check_and_both_dry_runs(self) -> None:
        # self-ci.yml is the worked example other callers copy, and its `ci`
        # job is this repository's required check.
        text = (WORKFLOWS / "self-ci.yml").read_text()
        ci = text.split("\n  ci:\n", 1)[1].split("\n\n", 1)[0].splitlines()
        self.assertEqual([line.strip() for line in ci], [
            "needs: [check, auto-tag-dry-run, release-image-dry-run]",
            "if: always()",
            "uses: ./.github/workflows/gate.yml",
            "with:",
            "results: ${{ toJSON(needs) }}",
        ])
        check = text.split("\n  check:\n", 1)[1].split("\n  auto-tag-dry-run:\n", 1)[0]
        self.assertIn("    uses: ./.github/workflows/ci.yml\n", check)
        self.assertNotIn("\n    if:", check)
        self.assertNotIn("\n    needs:", check)


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
