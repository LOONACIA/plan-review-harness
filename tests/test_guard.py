"""Tests for template/.codex/hooks/guard.py (Codex PreToolUse guard)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "template" / ".codex" / "hooks" / "guard.py"


def run_guard(command: str, harness: bool, raw_stdin: str | None = None):
    env = dict(os.environ)
    env.pop("HARNESS_STEP", None)
    if harness:
        env["HARNESS_STEP"] = "s1"
    payload = raw_stdin if raw_stdin is not None else json.dumps({
        "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
    })
    proc = subprocess.run([sys.executable, str(GUARD)], input=payload.encode("utf-8"), capture_output=True, env=env)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    out = proc.stdout.decode("utf-8").strip()
    return json.loads(out) if out else None


def denied(result) -> bool:
    return bool(result) and result["hookSpecificOutput"]["permissionDecision"] == "deny"


ALWAYS = [
    "git push --force origin main",
    "git push -f",
    "git push origin main --force-with-lease",
    "git reset --hard HEAD~1",
    "git clean -fd",
    "git branch -D feature",
    "rm -rf /",
    "rm -rf ~",
    "rm -rf .",
    "rm -rf *",
    "rm -fr C:\\",
    "cd src && rm -rf ..",
    "rmdir /s /q C:\\",
    "Remove-Item -Recurse -Force .",
    "psql -c 'DROP TABLE users'",
    "sqlite3 db.sqlite 'truncate table logs'",
]

HARNESS_ONLY = [
    "git commit -m test",
    "git -c user.name=x commit -m test",
    "git checkout -- .",
    "git switch main",
    "git stash",
    "git rebase main",
    "git merge feature",
    "git reset HEAD~1",
    "git push",
    "git pull",
    "git tag v1",
    "git restore src/a.py",
    "git rm src/a.py",
    "git add -A",
    "git branch feature",
    "git branch -d feature",
    "npm test && git commit -am done",
    "echo x > .harness/plans/demo/state.json",
    "python -c 'open(\".harness/x\",\"w\")'",
    "uv run .claude/skills/harness/scripts/harness.py commit s1",
    "python .claude\\skills\\harness\\scripts\\harness.py discard s1",
    "echo x >> .claude/skills/harness/SKILL.md",
    "rm .harness/plans/demo/plan.yaml",
    "sed -i 's/a/b/' .harness\\plans\\demo\\plan.yaml",
]

ALLOWED = [
    "git status --short",
    "git diff",
    "git log --oneline -5",
    "git branch",
    "git branch --show-current",
    "git branch -a",
    "git show HEAD:src/a.py",
    "git blame src/a.py",
    "rm -rf node_modules",
    "rm -rf build/ dist/",
    "rm -f src/tmp.txt",
    "cat .harness/plans/demo/plan.yaml",
    "ls .harness/plans",
    "Get-Content .harness/plans/demo/plan.yaml",
    "grep -n ac .harness/plans/demo/plan.yaml",
    "cat .claude/skills/harness/SKILL.md",
    "python scripts/build.py",
    "uv run pytest -q",
    "npm run build && npm test",
    "python -m pytest tests/test_export.py",
    "echo done",
]


@pytest.mark.parametrize("cmd", ALWAYS)
def test_always_blocked_in_both_modes(cmd):
    assert denied(run_guard(cmd, harness=False)), cmd
    assert denied(run_guard(cmd, harness=True)), cmd


@pytest.mark.parametrize("cmd", HARNESS_ONLY)
def test_blocked_only_in_harness_mode(cmd):
    assert denied(run_guard(cmd, harness=True)), cmd
    assert run_guard(cmd, harness=False) is None, cmd


@pytest.mark.parametrize("cmd", ALLOWED)
def test_allowed_in_both_modes(cmd):
    assert run_guard(cmd, harness=False) is None, cmd
    assert run_guard(cmd, harness=True) is None, cmd


def test_deny_payload_shape_and_reason():
    out = run_guard("git commit -m x", harness=True)
    h = out["hookSpecificOutput"]
    assert h["hookEventName"] == "PreToolUse"
    assert "git commit" in h["permissionDecisionReason"]
    assert "guard.py" in h["permissionDecisionReason"]


def test_bad_input_is_silent_allow():
    assert run_guard("", harness=True, raw_stdin="not json") is None
    assert run_guard("", harness=True, raw_stdin=json.dumps({"tool_name": "Bash"})) is None
    assert run_guard("", harness=True, raw_stdin=json.dumps({"tool_input": {"command": 5}})) is None
