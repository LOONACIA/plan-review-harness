"""Unit tests for template/.claude/skills/harness/scripts/harness.py.

The harness is run as a subprocess (so stdout encoding and argv handling are
exercised) inside a throwaway git repository, with a fake `codex` on PATH.
PYTHONUTF8 is removed from the environment so cp949 defaults are not masked.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parents[1] / "template"
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"

KOREAN = "한글 요약 ✓"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8").stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@example.com")
    shutil.copytree(TEMPLATE / ".harness", repo / ".harness")
    shutil.copytree(TEMPLATE / ".claude", repo / ".claude")
    shutil.copy(TEMPLATE / "AGENTS.md", repo / "AGENTS.md")
    (repo / "README.md").write_text("# target\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def fake_codex(tmp_path: Path):
    """Installs a fake codex on PATH and returns a function to set its scenario."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record_dir = tmp_path / "codex-record"
    record_dir.mkdir()
    scenario_path = record_dir / "scenario.json"
    if sys.platform == "win32":
        (bin_dir / "codex.cmd").write_text(
            f'@"{sys.executable}" "{FAKE_CODEX}" %*\r\n', encoding="utf-8"
        )
    else:
        script = bin_dir / "codex"
        script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CODEX}" "$@"\n', encoding="utf-8")
        script.chmod(0o755)

    class Fake:
        path = bin_dir
        scenario = scenario_path
        record = record_dir

        @staticmethod
        def set(write_files=None, last_message="default", exit_code=0, sleep_sec=0, git_commands=None):
            if last_message == "default":
                last_message = {
                    "summary": KOREAN,
                    "changed_files": list((write_files or {}).keys()),
                    "decisions": [],
                    "blockers": [],
                }
            scenario_path.write_text(
                json.dumps({
                    "write_files": write_files or {},
                    "git_commands": git_commands or [],
                    "last_message": last_message,
                    "exit_code": exit_code,
                    "sleep_sec": sleep_sec,
                }, ensure_ascii=False),
                encoding="utf-8",
            )

        @staticmethod
        def prompt() -> str:
            return (record_dir / "prompt.md").read_text(encoding="utf-8")

        @staticmethod
        def argv() -> list[str]:
            return json.loads((record_dir / "argv.json").read_text(encoding="utf-8"))

    Fake.set()
    return Fake


@pytest.fixture
def harness(repo: Path, fake_codex):
    env = dict(os.environ)
    env.pop("PYTHONUTF8", None)
    env.pop("PYTHONIOENCODING", None)
    env["PATH"] = str(fake_codex.path) + os.pathsep + env["PATH"]
    env["FAKE_CODEX_SCENARIO"] = str(fake_codex.scenario)

    def call(*args: str, expect_code: int = 0, stdin: str | None = None) -> dict:
        proc = subprocess.run(
            [sys.executable, str(repo / ".claude" / "skills" / "harness" / "scripts" / "harness.py"), *args],
            cwd=repo, env=env, capture_output=True, input=stdin.encode("utf-8") if stdin else None,
        )
        out = proc.stdout.decode("utf-8")
        assert proc.returncode == expect_code, f"exit {proc.returncode}\nstdout:\n{out}\nstderr:\n{proc.stderr.decode('utf-8', 'replace')}"
        return json.loads(out)

    return call


PASS_IF_EXISTS = f'"{sys.executable}" -c "import pathlib,sys; sys.exit(0 if pathlib.Path(\'src/feature.txt\').exists() else 1)"'
ALWAYS_PASS = f'"{sys.executable}" -c "pass"'


def write_plan(repo: Path, plan_id: str = "demo", approved: bool = True, steps=None, extra: str = "") -> Path:
    if steps is None:
        steps = f"""
  - id: s1
    title: Create the feature file
    type: feat
    scope: web
    description: 기능 파일 src/feature.txt 를 만든다.
    expected_files:
      - src/*
    ac:
      - {json.dumps(PASS_IF_EXISTS)}
  - id: s2
    title: Second step
    type: test
    description: 두 번째 스텝.
    expected_files:
      - src/*
    ac:
      - {json.dumps(ALWAYS_PASS)}
"""
    path = repo / ".harness" / "plans" / plan_id / "plan.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"id: {plan_id}\ngoal: 데모 목표 — 한글 포함\napproved: {'true' if approved else 'false'}\n{extra}steps:{steps}",
        encoding="utf-8",
    )
    return path


def edit_plan(repo: Path, change, plan_id: str = "demo") -> None:
    path = repo / ".harness" / "plans" / plan_id / "plan.yaml"
    plan = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(plan)
    path.write_text(yaml.safe_dump(plan, allow_unicode=True, sort_keys=False), encoding="utf-8")


NEW_STEP = {"id": "s3", "title": "Third step", "type": "feat", "description": "세 번째 스텝.", "expected_files": ["src/*"], "ac": [ALWAYS_PASS]}


# --------------------------------------------------------------------------- #
# start
# --------------------------------------------------------------------------- #


def test_start_creates_branch_commits_plan_and_state(repo, harness):
    write_plan(repo)
    out = harness("start", "demo")
    assert out["branch"] == "harness/demo"
    assert out["next_step"] == "s1"
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "harness/demo"
    assert _git(repo, "log", "-1", "--format=%s").strip() == "chore(harness): add demo plan"
    assert _git(repo, "status", "--porcelain").strip() == ""  # state.json is ignored
    assert (repo / ".harness/plans/demo/state.json").exists()
    assert out["goal"].startswith("데모 목표")


def test_start_uses_branch_prefix(repo, harness):
    write_plan(repo, extra="branch_prefix: fix\n")
    out = harness("start", "demo")
    assert out["branch"] == "fix/demo"
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "fix/demo"


def test_start_rejects_invalid_branch_prefix(repo, harness):
    write_plan(repo, extra="branch_prefix: Fix/x\n")
    out = harness("start", "demo", expect_code=2)
    assert "branch_prefix" in out["error"]
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_start_rejects_unapproved_plan(repo, harness):
    write_plan(repo, approved=False)
    out = harness("start", "demo", expect_code=2)
    assert "approved" in out["error"]
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


def test_start_rejects_dirty_tree(repo, harness):
    write_plan(repo)
    (repo / "junk.txt").write_text("x", encoding="utf-8")
    out = harness("start", "demo", expect_code=2)
    assert "junk.txt" in out["error"]


def test_start_rejects_invalid_plan(repo, harness):
    write_plan(repo, steps="""
  - id: s1
    title: Missing ac
    type: feat
    description: x
    expected_files: [src/*]
""")
    out = harness("start", "demo", expect_code=2)
    assert "'ac'" in out["error"]


def test_start_rejects_step_without_type(repo, harness):
    write_plan(repo, steps=f"""
  - id: s1
    title: Missing type
    description: x
    expected_files: [src/*]
    ac:
      - {json.dumps(ALWAYS_PASS)}
""")
    out = harness("start", "demo", expect_code=2)
    assert "'type'" in out["error"]


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #


def test_run_success_is_pending_review_and_not_committed(repo, harness, fake_codex):
    write_plan(repo)
    harness("start", "demo")
    fake_codex.set(write_files={"src/feature.txt": "hello"})

    out = harness("run", "s1")
    assert out["status"] == "pending_review"
    assert out["ac_passed"] is True
    assert out["changed_files"] == ["src/feature.txt"]
    assert out["out_of_scope_files"] == []
    assert out["codex"]["output"]["summary"] == KOREAN
    assert out["attempt"] == 1
    assert not Path(out["run_dir"]).is_absolute()
    assert (repo / out["run_dir"] / "result.json").exists()
    assert (repo / out["run_dir"] / "prompt.md").exists()
    # passing runs keep command output out of the result; it stays in the log file
    assert "stdout_tail" not in out["ac"][0] and "stderr_tail" not in out["ac"][0]
    assert (repo / out["ac"][0]["log"]).exists()
    assert "stderr_tail" not in out["codex"]
    # not committed yet
    assert "feature" not in _git(repo, "log", "--format=%s")
    assert _git(repo, "status", "--porcelain").strip() != ""

    argv = fake_codex.argv()
    assert argv[:2] == ["exec", "--json"]
    assert "--output-schema" in argv and "-o" in argv and argv[-1] == "-"
    assert "--dangerously-bypass-hook-trust" in argv
    env = json.loads((fake_codex.record / "env.json").read_text(encoding="utf-8"))
    assert env["HARNESS_STEP"] == "s1" and env["HARNESS_PLAN"] == "demo"
    prompt = fake_codex.prompt()
    assert "데모 목표" in prompt
    assert "src/feature.txt" in prompt  # AC command exposed to Codex
    assert ("cmd.exe" if sys.platform == "win32" else "/bin/sh") in prompt  # AC shell stated
    assert "`src/*`" in prompt
    assert ".gitignore" in prompt.split("## Rules")[1]  # AC artifacts must be ignored, not committed
    assert "attempt" not in prompt.lower().split("## rules")[1]  # no feedback section on first run


def test_run_reports_diff_and_scope_without_touching_index(repo, harness, fake_codex):
    write_plan(repo)
    harness("start", "demo")
    fake_codex.set(write_files={
        "src/feature.txt": "새 파일\n", "src/blob.bin": "\x00\x01", "README.md": "# target\nchanged\n",
    })
    out = harness("run", "s1")
    # out-of-scope files are flagged for review but do not fail the run
    assert out["status"] == "pending_review"
    assert out["out_of_scope_files"] == ["README.md"]
    diff = (repo / out["diff_file"]).read_text(encoding="utf-8")
    assert "+새 파일" in diff  # untracked files are included
    assert "+changed" in diff
    assert out["diff_stat"] == ["+1 -0 README.md", "binary src/blob.bin", "+1 -0 src/feature.txt"]
    assert _git(repo, "status", "--porcelain", "-uall") == " M README.md\n?? src/blob.bin\n?? src/feature.txt\n"
    assert not list((repo / out["run_dir"]).glob("*.index"))


def test_run_ac_failure_then_discard_retry_with_feedback(repo, harness, fake_codex):
    write_plan(repo)
    harness("start", "demo")
    fake_codex.set(write_files={"src/wrong.txt": "nope"})

    out = harness("run", "s1")
    assert out["status"] == "ac_failed"
    assert out["ac"][0]["exit_code"] == 1
    assert "stdout_tail" in out["ac"][0] and "stderr_tail" in out["ac"][0]
    assert "src/feature.txt" in (repo / out["ac"][0]["log"]).read_text(encoding="utf-8")  # log starts with the command
    assert out["attempt"] == 1

    # dirty tree blocks another run
    out = harness("run", "s1", expect_code=2)
    assert "dirty" in out["error"]

    out = harness("discard", "s1")
    assert out["discarded_files"] == ["src/wrong.txt"]
    assert not (repo / "src/wrong.txt").exists()
    assert _git(repo, "status", "--porcelain").strip() == ""

    feedback = repo / ".harness/plans/demo/runs/feedback-s1-1.md"
    feedback.write_text("파일 이름이 틀렸다. src/feature.txt 를 만들어라.", encoding="utf-8")
    fake_codex.set(write_files={"src/feature.txt": "fixed"})
    out = harness("run", "s1", "--feedback-file", str(feedback))
    assert out["status"] == "pending_review"
    assert out["attempt"] == 2
    prompt = fake_codex.prompt()
    assert "attempt 2" in prompt
    assert "파일 이름이 틀렸다" in prompt
    assert (repo / out["run_dir"] / "feedback.md").exists()


def test_run_refuses_after_max_attempts_unless_forced(repo, harness, fake_codex):
    write_plan(repo)
    harness("start", "demo")
    fake_codex.set(write_files={"src/wrong.txt": "nope"})
    for _ in range(3):
        assert harness("run", "s1")["status"] == "ac_failed"
        harness("discard", "s1")
    out = harness("run", "s1", expect_code=2)
    assert "3/3" in out["error"]
    out = harness("run", "s1", "--force")
    assert out["attempt"] == 4


def test_run_requires_previous_step_committed(repo, harness):
    write_plan(repo)
    harness("start", "demo")
    out = harness("run", "s2", expect_code=2)
    assert "'s1' must be committed" in out["error"]


def test_run_requires_plan_branch(repo, harness):
    write_plan(repo)
    harness("start", "demo")
    _git(repo, "checkout", "-q", "main")
    # plan.yaml only exists on the plan branch, so the error must point there
    out = harness("run", "s1", "--plan", "demo", expect_code=2)
    assert "harness/demo" in out["error"] and "'main'" in out["error"]
    # same plan file present on another branch: branch check must still refuse
    _git(repo, "checkout", "-q", "-b", "other", "harness/demo")
    out = harness("run", "s1", "--plan", "demo", expect_code=2)
    assert "expected 'harness/demo'" in out["error"]


def test_run_codex_error_and_unparseable_output(repo, harness, fake_codex):
    write_plan(repo)
    harness("start", "demo")
    fake_codex.set(write_files={"src/feature.txt": "x"}, last_message="not json", exit_code=3)
    out = harness("run", "s1")
    assert out["status"] == "codex_error"
    assert out["codex"]["exit_code"] == 3
    assert out["codex"]["output"] is None
    assert "not valid JSON" in out["codex"]["parse_error"]
    assert "stderr_tail" in out["codex"]


def test_run_timeout_kills_codex(repo, harness, fake_codex):
    write_plan(repo, steps=f"""
  - id: s1
    title: Slow
    type: feat
    description: x
    expected_files: [src/*]
    ac:
      - {json.dumps(ALWAYS_PASS)}
    timeout_sec: 1
""")
    harness("start", "demo")
    fake_codex.set(sleep_sec=15)
    out = harness("run", "s1")
    assert out["status"] == "timeout"
    assert out["codex"]["timed_out"] is True
    assert out["codex"]["duration_sec"] < 10


def test_run_detects_codex_commit_and_discard_removes_it(repo, harness, fake_codex):
    write_plan(repo)
    harness("start", "demo")
    plan_head = _git(repo, "rev-parse", "HEAD").strip()
    fake_codex.set(
        write_files={"src/feature.txt": "hello"},
        git_commands=[["add", "-A"], ["commit", "-q", "-m", "codex commit"]],
    )
    out = harness("run", "s1")
    assert out["status"] == "git_state_changed"
    assert out["git_state"]["changed"] is True
    assert out["git_state"]["head_before"] == plan_head
    assert "src/feature.txt" in (repo / out["diff_file"]).read_text(encoding="utf-8")  # diff covers the commit
    harness("commit", "s1", expect_code=2)

    out = harness("discard", "s1")
    assert len(out["removed_commits"]) == 1 and "codex commit" in out["removed_commits"][0]
    assert _git(repo, "rev-parse", "HEAD").strip() == plan_head
    assert not (repo / "src/feature.txt").exists()


def test_run_detects_codex_branch_switch(repo, harness, fake_codex):
    write_plan(repo)
    harness("start", "demo")
    fake_codex.set(git_commands=[["checkout", "-q", "-b", "other"]])
    out = harness("run", "s1")
    assert out["status"] == "git_state_changed"
    assert out["git_state"]["branch"] == "other"


def test_discard_removes_staged_changes(repo, harness, fake_codex):
    write_plan(repo)
    harness("start", "demo")
    fake_codex.set(
        write_files={"src/wrong.txt": "nope", "README.md": "codex edit\n"},
        git_commands=[["add", "-A"]],
    )
    assert harness("run", "s1")["status"] == "ac_failed"
    harness("discard", "s1")
    assert _git(repo, "status", "--porcelain").strip() == ""
    assert (repo / "README.md").read_text(encoding="utf-8") == "# target\n"


def test_run_reports_both_paths_of_a_staged_rename(repo, harness, fake_codex):
    write_plan(repo)
    harness("start", "demo")
    fake_codex.set(write_files={"src/feature.txt": "x"}, git_commands=[["mv", "README.md", "src/README.md"]])
    out = harness("run", "s1")
    assert out["changed_files"] == ["README.md", "src/README.md", "src/feature.txt"]
    assert out["out_of_scope_files"] == ["README.md"]


# --------------------------------------------------------------------------- #
# commit / status / resume
# --------------------------------------------------------------------------- #


def test_commit_records_summary_and_feeds_next_prompt(repo, harness, fake_codex):
    write_plan(repo)
    harness("start", "demo")
    fake_codex.set(write_files={"src/feature.txt": "hello"})
    harness("run", "s1")

    out = harness("commit", "s1")
    assert out["next_step"] == "s2"
    assert (out["done"], out["total"], out["dirty_files"]) == (1, 2, [])
    assert "steps" not in out  # the full step list comes from `status` only
    st = harness("status")
    assert st["steps"][0]["status"] == "done"
    assert st["steps"][0]["commit"] == out["commit"]
    assert _git(repo, "status", "--porcelain").strip() == ""
    assert _git(repo, "log", "-1", "--format=%s").strip() == "feat(web): Create the feature file"

    fake_codex.set(write_files={"src/second.txt": "x"})
    harness("run", "s2")
    prompt = fake_codex.prompt()
    assert "## Steps already completed" in prompt
    assert f"s1 — Create the feature file: {KOREAN}" in prompt

    out = harness("commit", "s2")
    assert out["complete"] is True
    assert out["next_step"] is None
    assert _git(repo, "log", "-1", "--format=%s").strip() == "test: Second step"  # no scope


def test_commit_refuses_without_passing_run(repo, harness, fake_codex):
    write_plan(repo)
    harness("start", "demo")
    out = harness("commit", "s1", expect_code=2)
    assert "'pending'" in out["error"]
    fake_codex.set(write_files={"src/wrong.txt": "nope"})
    harness("run", "s1")
    out = harness("commit", "s1", expect_code=2)
    assert "only a step whose last run passed AC" in out["error"]


def test_commits_carry_plan_and_given_trailers_and_step_commits_credit_codex(repo, harness, fake_codex):
    plan_trailer = "Harness-Plan: demo"
    claude = "Co-Authored-By: Claude <noreply@anthropic.com>"
    session = "Claude-Session: https://claude.ai/code/session_test"

    def trailers():
        return _git(repo, "log", "-1", "--format=%(trailers)").strip().splitlines()

    write_plan(repo)
    harness("start", "demo", "--trailer", claude, "--trailer", session)
    assert trailers() == [plan_trailer, claude, session]

    fake_codex.set(write_files={"src/feature.txt": "hello"})
    harness("run", "s1")
    harness("commit", "s1", "--trailer", claude)
    assert trailers() == [plan_trailer, "Co-Authored-By: Codex <noreply@openai.com>", claude]

    edit_plan(repo, lambda plan: plan["steps"].append(dict(NEW_STEP)))
    harness("replan")
    assert trailers() == [plan_trailer]


def test_status_and_resume_find_plan_from_branch(repo, harness):
    write_plan(repo)
    harness("start", "demo")
    _git(repo, "checkout", "-q", "main")
    write_plan(repo, "other", extra="branch_prefix: feat\n")
    harness("start", "other")
    # with two started plans only the current branch can pick one
    assert harness("status")["plan"] == "other"
    _git(repo, "checkout", "-q", "harness/demo")
    out = harness("status")
    assert out["plan"] == "demo" and out["next_step"] == "s1" and out["dirty_files"] == []
    out = harness("resume")
    assert out["action"] == "status"
    assert out["current_branch"] == "harness/demo"


def test_status_without_started_plan_errors(repo, harness):
    out = harness("status", expect_code=2)
    assert "no started plan" in out["error"]


# --------------------------------------------------------------------------- #
# replan
# --------------------------------------------------------------------------- #


def test_replan_commits_plan_change_and_adds_steps(repo, harness, fake_codex):
    write_plan(repo)
    harness("start", "demo")
    fake_codex.set(write_files={"src/feature.txt": "hello"})
    harness("run", "s1")
    harness("commit", "s1")

    def change(plan):
        plan["steps"][1]["description"] = "두 번째 스텝, 보강됨."
        plan["steps"].append(dict(NEW_STEP))

    edit_plan(repo, change)
    out = harness("replan")
    assert out["added_steps"] == ["s3"]
    assert out["changed_steps"] == ["s2"]
    assert out["removed_steps"] == []
    assert [s["id"] for s in out["steps"]] == ["s1", "s2", "s3"]
    assert out["next_step"] == "s2"
    assert _git(repo, "log", "-1", "--format=%s").strip() == "chore(harness): revise demo plan"
    assert _git(repo, "status", "--porcelain").strip() == ""


def test_replan_keeps_committed_steps_fixed(repo, harness, fake_codex):
    write_plan(repo)
    harness("start", "demo")
    fake_codex.set(write_files={"src/feature.txt": "hello"})
    harness("run", "s1")
    harness("commit", "s1")
    for change in (
        lambda p: p["steps"][0].update(title="changed"),
        lambda p: p["steps"].reverse(),
        lambda p: p["steps"].pop(0),
    ):
        edit_plan(repo, change)
        out = harness("replan", expect_code=2)
        assert "committed steps" in out["error"]
        _git(repo, "checkout", "-q", "--", ".")


def test_replan_keeps_attempts_and_max_attempts(repo, harness, fake_codex):
    write_plan(repo)
    harness("start", "demo")
    fake_codex.set(write_files={"src/wrong.txt": "nope"})
    harness("run", "s1")
    harness("discard", "s1")

    edit_plan(repo, lambda p: p.update(max_attempts=5))
    assert "max_attempts" in harness("replan", expect_code=2)["error"]
    _git(repo, "checkout", "-q", "--", ".")

    edit_plan(repo, lambda p: p["steps"][0].update(description="파일 이름은 src/feature.txt 다."))
    out = harness("replan")
    assert out["changed_steps"] == ["s1"]
    assert out["steps"][0]["attempts"] == 1


def test_replan_keeps_branch_prefix(repo, harness):
    write_plan(repo)
    harness("start", "demo")
    edit_plan(repo, lambda p: p.update(branch_prefix="feat"))
    assert "branch_prefix" in harness("replan", expect_code=2)["error"]


def test_replan_preconditions(repo, harness):
    write_plan(repo)
    harness("start", "demo")
    assert "unchanged" in harness("replan", expect_code=2)["error"]

    edit_plan(repo, lambda p: p["steps"].append(dict(NEW_STEP)))
    # an edited plan is reported as JSON, not a traceback, until it is replanned
    assert "replan" in harness("status", expect_code=2)["error"]

    edit_plan(repo, lambda p: p.update(approved=False))
    assert "approved" in harness("replan", expect_code=2)["error"]

    edit_plan(repo, lambda p: p.update(approved=True))
    (repo / "src").mkdir()
    (repo / "src/stray.txt").write_text("x", encoding="utf-8")
    assert "src/stray.txt" in harness("replan", expect_code=2)["error"]
