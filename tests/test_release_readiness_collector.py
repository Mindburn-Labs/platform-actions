"""Negative controls for actual RC-04 policy observations, with no network."""

import copy
import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("posture", Path(__file__).parents[1] / "scripts/collect-release-readiness.py")
posture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(posture)


def rules():
    base = {"target": "tag", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/tags/v*", "refs/tags/chart-v*"], "exclude": []}}}
    locked = {**copy.deepcopy(base), "rules": [{"type": "update"}, {"type": "deletion"}], "bypass_actors": []}
    create = {**copy.deepcopy(base), "rules": [{"type": "creation"}],
              "bypass_actors": [{"actor_type": "Integration", "actor_id": 3, "bypass_mode": "always"}]}
    return [locked, create]


class Readback:
    def __init__(self):
        self.security = {name: {"status": "enabled"} for name in
                         ("secret_scanning", "secret_scanning_push_protection")}
        self.policies = [{"type": "branch", "name": "main"}]
        self.rules = [{**row, "id": index} for index, row in enumerate(rules(), 1)]
        self.workflow = {"permissions": {"contents": "read"}, "jobs": {"sign": {
            "environment": "release-signing", "steps": [
                {"uses": "actions/checkout@" + "a" * 40}, {"run": "cosign sign --yes image@sha256:digest"}]}}}

    def get(self, path):
        suffix = path.removeprefix("repos/Mindburn-Labs/platform-actions")
        if suffix == "":
            return {"default_branch": "main", "security_and_analysis": self.security}
        if suffix == "/git/ref/heads/main":
            return {"object": {"sha": "a" * 40}}
        if suffix.startswith("/rulesets/"):
            return self.rules[int(suffix.rsplit("/", 1)[1]) - 1]
        if suffix.startswith("/contents/.github/workflows?"):
            return [{"type": "file", "name": "release.yml", "path": ".github/workflows/release.yml"}]
        if suffix.startswith("/contents/.github/workflows/release.yml?"):
            return {"type": "file", "encoding": "base64", "content": base64.b64encode(json.dumps(self.workflow).encode()).decode()}
        if suffix == "/environments/release-signing":
            return {"deployment_branch_policy": {"custom_branch_policies": True, "protected_branches": False},
                    "protection_rules": [{"type": "branch_policy"}]}
        raise AssertionError("unexpected API read: " + path)

    def rows(self, path, key=None):
        if path.endswith("/rulesets?includes_parents=true"):
            return [{"id": row["id"]} for row in self.rules]
        if key == "environments":
            return [{"name": "release-signing"}]
        if key == "branch_policies":
            return self.policies
        raise AssertionError("unexpected API inventory: " + path)


class PostureTest(unittest.TestCase):
    def test_complete_readbacks_require_every_control(self):
        api = Readback()
        row = posture.collect_repository(api, "platform-actions")
        self.assertTrue(all(row["observed"].values()))
        self.assertEqual([], row["unmet"])
        self.assertEqual({"scanning": "enabled", "push_protection": "enabled"}, row["security_status"])
        for change, failed in (("protection", "secret_push_protection"), ("tag", "tag_rulesets"),
                               ("environment", "signing_environments"), ("action", "privileged_actions_pinned")):
            api = Readback()
            if change == "protection":
                api.security["secret_scanning_push_protection"]["status"] = "disabled"
            elif change == "tag":
                api.rules[0]["bypass_actors"] = api.rules[1]["bypass_actors"]
            elif change == "environment":
                api.policies = []
            else:
                api.workflow["jobs"]["sign"]["steps"][0]["uses"] = "actions/checkout@main"
            with self.subTest(change=change):
                result = posture.collect_repository(api, "platform-actions")
                self.assertFalse(result["observed"][failed])
                self.assertTrue(result["unmet"])

    def test_covers_both_release_families_without_update_bypass(self):
        self.assertTrue(posture.tag_controls(rules())[0])
        for mutation in ("bypass", "chart_missing", "exclusion", "disabled", "broad_creation"):
            with self.subTest(mutation=mutation):
                values = rules()
                if mutation == "bypass":
                    values[0]["bypass_actors"] = values[1]["bypass_actors"]
                elif mutation == "chart_missing":
                    values[0]["conditions"]["ref_name"]["include"] = ["refs/tags/v*"]
                elif mutation == "exclusion":
                    values[0]["conditions"]["ref_name"]["exclude"] = ["refs/tags/v1*"]
                elif mutation == "disabled":
                    values[0]["enforcement"] = "evaluate"
                else:
                    values[1]["bypass_actors"][0]["actor_type"] = "RepositoryRole"
                self.assertFalse(posture.tag_controls(values)[0])

    def test_sampled_semver_pattern_is_not_whole_family_coverage(self):
        values = rules()
        values[0]["conditions"]["ref_name"]["include"] = ["refs/tags/v*.*.*", "refs/tags/chart-v*"]
        self.assertFalse(posture.tag_controls(values)[0])

    def test_technical_environment_control_requires_exact_refs_not_reviewers(self):
        env = {"deployment_branch_policy": {"custom_branch_policies": True, "protected_branches": False},
               "protection_rules": [{"type": "branch_policy"}]}
        self.assertTrue(posture.environment_control(env, [{"type": "branch", "name": "main"}]))
        self.assertTrue(posture.environment_control(env, [{"type": "tag", "name": "v*"}]))
        self.assertFalse(posture.environment_control(env, [{"type": "branch", "name": "*"}]))
        self.assertFalse(posture.environment_control(env, []))
        env["protection_rules"].append({"type": "required_reviewers"})
        self.assertFalse(posture.environment_control(env, [{"type": "tag", "name": "v*"}]))

    def test_inherited_writes_and_local_composite_mutable_action_are_checked(self):
        doc = {"permissions": {"contents": "write"}, "jobs": {"release": {
            "steps": [{"uses": "./.github/actions/publish"}]}}}
        _, errors, _ = posture.workflow_controls({".github/workflows/r.yml": doc}, lambda _: ["actions/checkout@v4"])
        self.assertIn("mutable privileged", errors[0])
        doc["jobs"]["release"]["permissions"] = {"contents": "read"}
        _, errors, _ = posture.workflow_controls({".github/workflows/r.yml": doc}, lambda _: self.fail("read-only job traversed"))
        self.assertFalse(errors)

    def test_unbounded_reusable_caller_and_signing_environment_fail_closed(self):
        doc = {"on": {"workflow_call": {}}, "jobs": {"release": {
            "steps": [{"uses": "docker/login-action@v3"}, {"run": "cosign sign --yes x@y"}]}}}
        _, errors, _ = posture.workflow_controls({"release.yml": doc}, lambda _: [])
        self.assertEqual(2, len(errors))

    def test_sha_action_and_local_recursion_controls(self):
        self.assertTrue(posture.pinned_use("actions/checkout@" + "a" * 40))
        self.assertFalse(posture.pinned_use("actions/checkout@main"))
        doc = {"jobs": {"release": {"steps": [{"uses": "./a"}]}}}
        with self.assertRaises(posture.Unmet):
            posture.workflow_controls({"r.yml": doc}, lambda _: ["./a"])

    def test_signing_and_secret_using_jobs_are_privileged_with_read_only_github_token(self):
        for authority in ({"env": {"SIGNER": "${{ secrets.SIGNING_KEY }}"}},
                          {"secrets": "inherit"}, {}):
            steps = [{"uses": "actions/checkout@main"}]
            if not authority:
                steps.append({"run": "cosign sign --yes image@sha256:digest"})
            doc = {"permissions": {"contents": "read"}, "jobs": {"release": {
                **authority, "steps": steps, "environment": "release-signing"}}}
            jobs, errors, _ = posture.workflow_controls({"release.yml": doc}, lambda _: [])
            self.assertTrue(jobs[0]["privileged"])
            self.assertTrue(any("mutable privileged" in error for error in errors))

    def test_missing_api_visibility_cannot_be_a_pass(self):
        class Denied:
            def get(self, _):
                raise posture.Unmet("GitHub readback 403")
        row = posture.collect_repository(Denied(), "platform-actions")
        self.assertFalse(any(row["observed"].values()))
        self.assertIn("GitHub readback 403", row["unmet"])

    def test_candidate_cannot_silently_add_an_unaudited_product(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "clusters/staging-do"
            target.mkdir(parents=True)
            file = target / "app.yaml"
            file.write_text("image: ghcr.io/mindburn-labs/helm-ai-kernel@sha256:abc\n")
            self.assertEqual(["helm-ai-kernel"], posture.candidate_coverage(root, "staging-do"))
            file.write_text("url: oci://ghcr.io/mindburn-labs/helm-ai-enterprise/charts/helm-ai-enterprise-controlplane\n")
            self.assertEqual(["helm-ai-enterprise"], posture.candidate_coverage(root, "staging-do"))
            file.write_text("image: ghcr.io/mindburn-labs/helm-docs:v1\n")
            with self.assertRaisesRegex(posture.Unmet, "outside RC-04 cohort"):
                posture.candidate_coverage(root, "staging-do")
            file.write_text("image: ghcr.io/mindburn-labs/new-product:v1\n")
            with self.assertRaisesRegex(posture.Unmet, "unowned artifact"):
                posture.candidate_coverage(root, "staging-do")
            for artifact in ("helm-ai-kernel_unowned", "helm-ai-kernel.unowned", "helm-ai-kernel/unowned"):
                file.write_text("image: ghcr.io/mindburn-labs/" + artifact + ":v1\n")
                with self.subTest(artifact=artifact), self.assertRaisesRegex(posture.Unmet, "unowned artifact"):
                    posture.candidate_coverage(root, "staging-do")


if __name__ == "__main__":
    unittest.main()
