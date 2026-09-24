"""Behaviour tests for the inline scripts of the v2 reusable workflows.

The scripts live inside the workflow YAML because a reusable workflow checks
out the caller, not this repository. These tests pull a step's `run:` block
out of the workflow and execute it against fixtures, so the logic that decides
releases, vulnerability failures and the gate result is exercised as shipped.
"""

from __future__ import annotations

import json
import os
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
    SCRIPT = step_script("ci.yml", "Require every job to succeed or be skipped")

    def gate(self, **results: str) -> int:
        needs = {job: {"result": result, "outputs": {}} for job, result in results.items()}
        with tempfile.TemporaryDirectory() as tmp:
            return run_bash(self.SCRIPT, Path(tmp), {"NEEDS": json.dumps(needs)}).returncode

    def test_success_and_skipped_pass(self) -> None:
        self.assertEqual(self.gate(check="success", scan="skipped"), 0)

    def test_failure_fails(self) -> None:
        self.assertNotEqual(self.gate(check="failure", scan="success"), 0)

    def test_cancelled_fails(self) -> None:
        self.assertNotEqual(self.gate(check="success", scan="cancelled"), 0)


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
