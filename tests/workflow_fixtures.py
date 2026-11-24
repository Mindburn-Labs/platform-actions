"""Read shipped workflow steps and execute them in local fixtures."""

from __future__ import annotations

import os
import subprocess
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
