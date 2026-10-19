#!/usr/bin/env python3
"""Collect RC-04 from GitHub readbacks; missing visibility is an unmet control.

This is a repository workflow, not a reusable caller workflow. No API mutation,
secret value read, key generation, or readiness signing happens here.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import urllib.request

OWNER = "Mindburn-Labs"
REPOSITORIES = (
    "helm-ai-kernel", "helm-ai-enterprise", "svc-helm-control-plane",
    "app-helm-console", "gitops-platform", "platform-actions",
)
FIELDS = ("tag_rulesets", "signing_environments", "privileged_actions_pinned", "secret_push_protection")
SHA = re.compile(r"[0-9a-f]{40}\Z")
PIN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}\Z")
SIGNING = re.compile(r"\bcosign\s+(?:sign(?:-blob)?|attest)\b|\b(?:npm|cargo)\s+publish\b|\btwine\s+upload\b|build_release_readiness_attestation\.py\s+attest\b")
ENV_TAGS = {"v*", "chart-v*", "v*.*.*", "chart-v*.*.*"}
# Source-owned inventory of the release cohort. Unknown Mindburn artifacts fail
# closed; editing this inventory requires a source review, not a dispatch input.
ARTIFACT_OWNERS = {
    "helm-ai-kernel": "helm-ai-kernel", "helm-ai-enterprise": "helm-ai-enterprise",
    "helm-ai-enterprise-controlplane": "helm-ai-enterprise",
    "helm-ai-enterprise/charts/helm-ai-enterprise-controlplane": "helm-ai-enterprise",
    "svc-helm-control-plane": "svc-helm-control-plane", "helm-console": "app-helm-console",
    "app-helm-console": "app-helm-console", "helm-admin": "app-helm-admin",
    "helm-docs": "app-helm-docs", "app-mindburn-web": "app-mindburn-web",
}


class Unmet(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Unmet(message)


class API:
    def __init__(self, credential):
        require(bool(credential), "posture read credential absent")
        self.credential = credential

    def get(self, path):
        require(path.startswith(f"repos/{OWNER}/") and ".." not in path, "API path outside release cohort")
        request = urllib.request.Request(
            "https://api.github.com/" + path,
            headers={"Authorization": "Bearer " + self.credential,
                     "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )
        try:
            # These endpoints return JSON directly; do not follow redirects with credentials.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
                data = response.read(2_000_001)
            require(len(data) <= 2_000_000, "API response too large")
            return json.loads(data)
        except Unmet:
            raise
        except Exception as error:
            code = getattr(error, "code", "unavailable")
            raise Unmet(f"GitHub readback {code}: {path}") from error

    def rows(self, path, key=None):
        value = self.get(path + ("&" if "?" in path else "?") + "per_page=100")
        rows = value.get(key) if key else value
        require(isinstance(rows, list) and len(rows) < 100, "API inventory absent or truncated")
        if key:
            require(type(value.get("total_count")) is int and value["total_count"] == len(rows),
                    "API inventory count mismatch")
        return rows


def yaml_document(data):
    # Ruby/Psych is already used by the estate. No parser installation at run time.
    result = subprocess.run(
        ["ruby", "-rjson", "-ryaml", "-e", "print JSON.generate(YAML.safe_load(STDIN.read, aliases: false))"],
        input=data, capture_output=True, timeout=15,
    )
    require(result.returncode == 0, "workflow YAML cannot be parsed safely")
    result = json.loads(result.stdout)
    require(isinstance(result, dict), "workflow YAML is not a mapping")
    return result


def tag_controls(rulesets):
    """Prove coverage of both whole release families, never sample a single tag."""
    coverage = {}
    for pattern in ("refs/tags/v*", "refs/tags/chart-v*"):
        locked, creators = set(), False
        for row in rulesets:
            condition = row.get("conditions", {}).get("ref_name", {})
            if (row.get("target") != "tag" or row.get("enforcement") != "active" or
                    condition.get("exclude") != [] or
                    not set(condition.get("include", [])) & {pattern, "~ALL"}):
                continue
            rules = {rule.get("type") for rule in row.get("rules", []) if isinstance(rule, dict)}
            actors = row.get("bypass_actors")
            if actors == []:
                locked |= rules & {"update", "deletion"}
            if "creation" in rules and isinstance(actors, list) and actors:
                creators |= all(
                    actor.get("bypass_mode") == "always" and
                    (actor.get("actor_type") == "OrganizationAdmin" or
                     (actor.get("actor_type") == "Integration" and type(actor.get("actor_id")) is int and actor["actor_id"] > 0))
                    for actor in actors if isinstance(actor, dict)
                ) and all(isinstance(actor, dict) for actor in actors)
        coverage[pattern] = {"updates_blocked": "update" in locked,
                             "deletions_blocked": "deletion" in locked,
                             "creation_restricted": creators}
    return all(all(row.values()) for row in coverage.values()), coverage


def environment_control(environment, policies):
    protection = environment.get("deployment_branch_policy")
    if not isinstance(protection, dict) or protection != {"protected_branches": False, "custom_branch_policies": True}:
        return False
    # Estate R5 forbids a human reviewer gate. A custom ref policy is the technical control.
    rules = environment.get("protection_rules", [])
    if any(row.get("type") in {"required_reviewers", "wait_timer"} for row in rules):
        return False
    return bool(policies) and all(
        (row.get("type") == "branch" and row.get("name") == "main") or
        (row.get("type") == "tag" and row.get("name") in ENV_TAGS)
        for row in policies
    )


def pinned_use(value):
    return isinstance(value, str) and bool(
        PIN.fullmatch(value) or re.fullmatch(r"docker://[^\s@]+@sha256:[0-9a-f]{64}", value)
    )


def uses_secrets(value):
    if isinstance(value, dict):
        return any(uses_secrets(child) for child in value.values())
    if isinstance(value, list):
        return any(uses_secrets(child) for child in value)
    return isinstance(value, str) and bool(re.search(r"\bsecrets\s*(?:\.|\[)", value))


def privileged(permissions, fallback):
    value = permissions if permissions is not None else fallback
    if value in ("write-all", None):
        return True  # reusable caller/default permissions are not a proof of read-only
    if value == "read-all":
        return False
    require(isinstance(value, dict), "workflow permissions are not statically resolvable")
    return any(value.get(name) == "write" for name in ("id-token", "packages", "contents"))


def workflow_controls(workflows, local_action):
    jobs, failures, signing_environments = [], [], set()
    for path, doc in workflows.items():
        require(isinstance(doc.get("jobs"), dict) and doc["jobs"], "workflow jobs missing")
        triggers = doc.get("on", doc.get("true", {}))
        reusable = isinstance(triggers, dict) and "workflow_call" in triggers or triggers == "workflow_call"
        top = doc.get("permissions")
        for name, job in doc["jobs"].items():
            require(isinstance(job, dict), "workflow job malformed")
            is_privileged = privileged(job.get("permissions"), top)
            # An unbounded reusable workflow can inherit writes from another repo.
            if reusable and job.get("permissions") is None and top is None:
                is_privileged = True
            steps = job.get("steps", [])
            require(isinstance(steps, list), "workflow steps malformed")
            uses = ([job["uses"]] if "uses" in job else []) + [s["uses"] for s in steps if isinstance(s, dict) and "uses" in s]
            signing = bool(re.search(r"/release-image\.ya?ml@", str(job.get("uses", "")))) or any(
                SIGNING.search(str(s.get("run", ""))) or "attest" in str(s.get("uses", ""))
                for s in steps if isinstance(s, dict))
            is_privileged |= signing or uses_secrets(job) or uses_secrets(doc.get("env")) or bool(job.get("secrets"))
            env = job.get("environment")
            env = env.get("name") if isinstance(env, dict) else env
            if signing:
                if not isinstance(env, str) or "${{" in env:
                    failures.append(f"{path}:{name}: signing job has no static protected environment")
                else:
                    signing_environments.add(env)
            checked = []
            pending = [(value, ()) for value in uses] if is_privileged else []
            while pending:
                value, ancestors = pending.pop()
                require(isinstance(value, str), "uses target is not a string")
                if value.startswith("./"):
                    require(".." not in Path(value).parts and len(ancestors) < 12 and value not in ancestors,
                            "local action recursion or path escape")
                    if value.startswith("./.github/workflows/"):
                        require(value[2:] in workflows, "local reusable workflow missing")
                    else:
                        nested = local_action(value[2:])
                        pending.extend((child, (*ancestors, value)) for child in nested)
                    checked.append({"uses": value, "immutable_local_source": True})
                else:
                    valid = pinned_use(value)
                    checked.append({"uses": value, "sha_pinned": valid})
                    if not valid:
                        failures.append(f"{path}:{name}: mutable privileged uses {value}")
            jobs.append({"workflow": path, "job": name, "privileged": is_privileged,
                         "signing": signing, "environment": env, "actions": checked})
    return jobs, failures, signing_environments


def collect_repository(api, short):
    repo = f"{OWNER}/{short}"
    prefix = f"repos/{repo}"
    evidence, unmet = {"repository": repo}, []
    observed = dict.fromkeys(FIELDS, False)
    try:
        meta = api.get(prefix)
        require(meta.get("default_branch") == "main", "default branch is not main")
        ref = api.get(prefix + "/git/ref/heads/main")
        source = ref.get("object", {}).get("sha", "")
        require(isinstance(source, str) and SHA.fullmatch(source), "source main SHA missing")
        evidence["source_sha"] = source
        security = meta.get("security_and_analysis", {})
        # Retain public control statuses without fields that look like secret
        # values to the shared evidence packager's sensitive-data guard.
        evidence["security_status"] = {
            "scanning": security.get("secret_scanning", {}).get("status"),
            "push_protection": security.get("secret_scanning_push_protection", {}).get("status"),
        }
        observed["secret_push_protection"] = all(
            isinstance(security.get(key), dict) and security[key].get("status") == "enabled"
            for key in ("secret_scanning", "secret_scanning_push_protection"))

        rulesets = []
        for summary in api.rows(prefix + "/rulesets?includes_parents=true"):
            require(type(summary.get("id")) is int, "ruleset identity absent")
            row = api.get(prefix + f"/rulesets/{summary['id']}")
            require(row.get("id") == summary["id"], "ruleset identity changed")
            rulesets.append({key: row.get(key) for key in
                             ("id", "target", "enforcement", "conditions", "rules", "bypass_actors")})
        observed["tag_rulesets"], evidence["tag_coverage"] = tag_controls(rulesets)
        evidence["rulesets"] = rulesets

        def contents(path):
            row = api.get(prefix + "/contents/" + urllib.parse.quote(path, safe="/") + "?ref=" + source)
            require(row.get("type") == "file" and row.get("encoding") == "base64", "workflow content absent")
            data = base64.b64decode(row["content"], validate=False)
            require(len(data) < 1_000_000, "workflow content too large")
            return yaml_document(data)

        directory = api.get(prefix + "/contents/.github/workflows?ref=" + source)
        require(isinstance(directory, list) and bool(directory), "workflow inventory missing")
        workflows = {row["path"]: contents(row["path"]) for row in directory
                     if row.get("type") == "file" and row.get("name", "").endswith((".yml", ".yaml"))}
        require(bool(workflows), "workflow inventory empty")

        def local_action(path):
            # A missing action.yml is a failed readback, not permission to skip it.
            doc = contents(path + "/action.yml")
            runs = doc.get("runs", {})
            require(isinstance(runs, dict), "local action runs missing")
            if runs.get("using") != "composite":
                require(runs.get("using") in {"node20", "node24", "docker"}, "unknown local action runtime")
                if runs.get("using") == "docker" and str(runs.get("image", "")).startswith("docker://"):
                    return [runs["image"]]
                return []
            return [step["uses"] for step in runs.get("steps", []) if isinstance(step, dict) and "uses" in step]

        jobs, errors, signing = workflow_controls(workflows, local_action)
        evidence["workflow_jobs"] = jobs
        observed["privileged_actions_pinned"] = not any("mutable privileged" in error for error in errors)
        unmet.extend(errors)
        envs = api.rows(prefix + "/environments", "environments")
        selected = {row.get("name") for row in envs if
                    re.search(r"(?:production$|sign|maven-central)", str(row.get("name", "")))} | signing
        evidence["signing_environment_controls"] = []
        env_good = bool(selected) and not any("signing job" in error for error in errors)
        for name in sorted(selected):
            require(isinstance(name, str) and "${{" not in name, "dynamic signing environment")
            target = prefix + "/environments/" + urllib.parse.quote(name, safe="")
            env = api.get(target)
            policies = api.rows(target + "/deployment-branch-policies", "branch_policies")
            valid = environment_control(env, policies)
            env_good &= valid
            evidence["signing_environment_controls"].append({
                "name": name, "deployment_branch_policy": env.get("deployment_branch_policy"),
                "protection_rule_types": [row.get("type") for row in env.get("protection_rules", [])],
                "ref_policies": [{"name": row.get("name"), "type": row.get("type")} for row in policies],
                "restricted": valid,
            })
        observed["signing_environments"] = env_good
    except (Unmet, AttributeError, KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        # A partial inventory cannot prove any of the four cohort-wide controls.
        observed = dict.fromkeys(FIELDS, False)
        unmet.append(str(error) if isinstance(error, Unmet) else "malformed or incomplete posture readback")
    unmet.extend(key + " unmet" for key, value in observed.items() if not value)
    evidence["observed"] = observed
    evidence["unmet"] = sorted(set(unmet))
    return evidence


def candidate_coverage(candidate, environment):
    root = candidate / "clusters" / environment
    require(root.is_dir() and not root.is_symlink(), "candidate cluster tree absent")
    paths = sorted(root.rglob("*"))
    require(all(not path.is_symlink() for path in paths), "candidate cluster tree contains a symlink")
    owners = set()
    for path in paths:
        if not path.is_file() or path.suffix not in {".yaml", ".yml", ".json"}:
            continue
        result = subprocess.run([
            "ruby", "-rjson", "-ryaml", "-e",
            "print JSON.generate(YAML.parse_stream(STDIN.read).children.map { |node| stream = Psych::Nodes::Stream.new; stream.children << node; YAML.safe_load(stream.to_yaml, aliases: false) })",
        ], input=path.read_bytes(), capture_output=True, timeout=15)
        require(result.returncode == 0, "candidate YAML cannot be parsed safely")
        values = json.loads(result.stdout)
        def strings(value):
            if isinstance(value, dict):
                for child in value.values():
                    yield from strings(child)
            elif isinstance(value, list):
                for child in value:
                    yield from strings(child)
            elif isinstance(value, str):
                yield value
        for value in strings(values):
            for artifact in re.findall(r"(?i)ghcr\.io/mindburn-labs/([a-z0-9_./-]+)", value):
                artifact = artifact.lower().removeprefix("charts/")
                require(artifact in ARTIFACT_OWNERS, "candidate references an unowned artifact: " + artifact)
                owners.add(ARTIFACT_OWNERS[artifact])
    require(bool(owners), "candidate has no owned release artifacts")
    uncovered = sorted(owners - set(REPOSITORIES))
    require(not uncovered, "candidate repositories outside RC-04 cohort: " + ", ".join(uncovered))
    return sorted(owners)


def collect(api, candidate, environment):
    rows = [collect_repository(api, short) for short in REPOSITORIES]
    result = {"schema": "release-readiness-observation-v1", "id": "RC-04",
            "observed": {key: all(row["observed"][key] for row in rows) for key in FIELDS},
            "evidence": {"release_cohort": list(REPOSITORIES), "repositories": rows},
            "unmet": [row["repository"] + ": " + failure for row in rows for failure in row["unmet"]],
            "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")}
    try:
        result["evidence"]["candidate_repositories"] = candidate_coverage(candidate, environment)
    except (Unmet, OSError, ValueError, subprocess.SubprocessError) as error:
        result["observed"] = dict.fromkeys(FIELDS, False)
        result["unmet"].append(str(error) if isinstance(error, Unmet) else "candidate repository inventory unavailable")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--environment", required=True, choices=("qa-arm", "staging-do", "production-do"))
    args = parser.parse_args()
    try:
        report = collect(API(os.environ.get("READINESS_POSTURE_TOKEN", "")), args.candidate, args.environment)
    except Unmet as error:
        report = {"schema": "release-readiness-observation-v1", "id": "RC-04",
                  "observed": dict.fromkeys(FIELDS, False), "evidence": {}, "unmet": [str(error)]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(f"RC-04: {len(report['unmet'])} unmet observations")
    for reason in report["unmet"]:
        print("unmet: " + reason)
    return 1 if report["unmet"] else 0


if __name__ == "__main__":
    sys.exit(main())
