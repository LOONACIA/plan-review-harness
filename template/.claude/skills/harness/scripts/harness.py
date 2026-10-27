# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6.0"]
# ///
"""Harness step runner: Claude plans and reviews, Codex implements.

Deterministic part of the harness. Claude Code (the orchestrator) calls the
subcommands below; every judgment call (review, retry prompt, commit/discard)
stays with Claude.

    uv run .claude/skills/harness/scripts/harness.py start <plan-id> [--trailer T ...]
    uv run .claude/skills/harness/scripts/harness.py status
    uv run .claude/skills/harness/scripts/harness.py run <step-id> [--feedback-file F] [--force]
    uv run .claude/skills/harness/scripts/harness.py commit <step-id> [--trailer T ...]
    uv run .claude/skills/harness/scripts/harness.py discard <step-id>
    uv run .claude/skills/harness/scripts/harness.py replan [--trailer T ...]

Plans and run state live in .harness/plans/ at the repository root.

All subcommands print a JSON object to stdout. Exit code 0 means the command
itself succeeded (a step whose AC failed still exits 0; look at "status").
Exit code 2 means a precondition failed and nothing was done.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

# This file is <repo>/.claude/skills/harness/scripts/harness.py.
REPO_ROOT = Path(__file__).resolve().parents[4]
PLANS_DIR = REPO_ROOT / ".harness" / "plans"

PLAN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
STEP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_STEP_TIMEOUT_SEC = 30 * 60
DEFAULT_AC_TIMEOUT_SEC = 10 * 60
TAIL_CHARS = 4000
# Codex implements every step, so step commits always credit it.
CODEX_TRAILER = "Co-Authored-By: Codex <noreply@openai.com>"

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "What was implemented, 2-5 sentences. Used as context for later steps.",
        },
        "changed_files": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Repo-relative paths you created, modified or deleted.",
        },
        "decisions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Design decisions or deviations from the step description, with reasons.",
        },
        "blockers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Anything you could not do or that needs a human decision. Empty if none.",
        },
    },
    "required": ["summary", "changed_files", "decisions", "blockers"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #


class HarnessError(Exception):
    """Precondition failure. Reported as JSON with exit code 2."""


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def decode_output(data: bytes) -> str:
    """Child-process output on Windows is not reliably UTF-8."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp949", errors="replace")


def tail(text: str, limit: int = TAIL_CHARS) -> str:
    return text if len(text) <= limit else "...[truncated]...\n" + text[-limit:]


def rel(path: Path) -> str:
    """Repo-relative path for output; Claude runs the harness from the repo root."""
    return path.relative_to(REPO_ROOT).as_posix()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def emit(data: Any) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


def git(*args: str, check: bool = True, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, check=False, env=env
    )
    out = decode_output(proc.stdout)
    if check and proc.returncode != 0:
        raise HarnessError(f"git {' '.join(args)} failed: {decode_output(proc.stderr).strip()}")
    return out


def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD").strip()


def git_commit(plan_id: str, subject: str, trailers: list[str]) -> None:
    git("commit", "-q", "-m", "\n".join([subject, "", f"Harness-Plan: {plan_id}", *trailers]))


def changed_paths() -> list[str]:
    """Tracked changes plus untracked files, repo-relative with forward slashes."""
    raw = git("status", "--porcelain", "-z", "--untracked-files=all")
    paths: list[str] = []
    entries = iter(raw.split("\0"))
    for entry in entries:
        if not entry:
            continue
        # Format: XY<space>path ; renames/copies use "XY new\0old" and both paths count as changed.
        paths.append(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:
            paths.append(next(entries))
    return sorted(set(paths))


def write_diff(base: str, path: Path) -> list[str]:
    """Diff from `base` to the working tree, untracked files included.

    Uses a throwaway index so the real index (and `git status`) is untouched.
    Returns one "+added -deleted path" line per file ("binary path" for binaries)
    so Claude can size the diff before reading it.
    """
    index = path.with_name(path.name + ".index")
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    try:
        git("read-tree", base, env=env)
        git("add", "-A", env=env)
        diff = git("-c", "core.quotepath=false", "diff", "--cached", base, env=env)
        numstat = git("-c", "core.quotepath=false", "diff", "--cached", "--numstat", base, env=env)
    finally:
        index.unlink(missing_ok=True)
    path.write_text(diff, encoding="utf-8", newline="")
    stat = []
    for line in numstat.splitlines():
        added, deleted, name = line.split("\t", 2)
        stat.append(f"binary {name}" if added == "-" else f"+{added} -{deleted} {name}")
    return stat


def kill_process_tree(proc: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, check=False,
        )
    else:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


# --------------------------------------------------------------------------- #
# Plan and state
# --------------------------------------------------------------------------- #


def plan_dir(plan_id: str) -> Path:
    return PLANS_DIR / plan_id


def load_plan(plan_id: str) -> dict[str, Any]:
    if not PLAN_ID_RE.match(plan_id):
        raise HarnessError(f"invalid plan id {plan_id!r}: use lowercase letters, digits and '-'")
    path = plan_dir(plan_id) / "plan.yaml"
    if not path.exists():
        hint = ""
        if state_path(plan_id).exists():
            hint = f" (plan {plan_id!r} was started; its files live on branch 'harness/{plan_id}', current branch is {current_branch()!r})"
        raise HarnessError(f"plan file not found: {path}{hint}")
    plan = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_plan(plan, plan_id)
    return plan


def validate_plan(plan: Any, plan_id: str) -> None:
    if not isinstance(plan, dict):
        raise HarnessError("plan.yaml must be a mapping")
    if plan.get("id") != plan_id:
        raise HarnessError(f"plan.yaml 'id' must be {plan_id!r}")
    if not isinstance(plan.get("goal"), str) or not plan["goal"].strip():
        raise HarnessError("plan.yaml needs a non-empty 'goal'")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise HarnessError("plan.yaml needs a non-empty 'steps' list")
    seen: set[str] = set()
    for i, step in enumerate(steps):
        where = f"steps[{i}]"
        if not isinstance(step, dict):
            raise HarnessError(f"{where} must be a mapping")
        sid = step.get("id")
        if not isinstance(sid, str) or not STEP_ID_RE.match(sid):
            raise HarnessError(f"{where} needs an 'id' (letters, digits, '-', '_')")
        if sid in seen:
            raise HarnessError(f"duplicate step id {sid!r}")
        seen.add(sid)
        for key in ("title", "description", "type"):
            if not isinstance(step.get(key), str) or not step[key].strip():
                raise HarnessError(f"step {sid!r} needs a non-empty '{key}'")
        files = step.get("expected_files")
        if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files):
            raise HarnessError(f"step {sid!r} needs a non-empty 'expected_files' list of globs")
        ac = step.get("ac")
        if not isinstance(ac, list) or not ac or not all(isinstance(c, str) and c.strip() for c in ac):
            raise HarnessError(f"step {sid!r} needs a non-empty 'ac' list of shell commands")
        timeout = step.get("timeout_sec", DEFAULT_STEP_TIMEOUT_SEC)
        if not isinstance(timeout, int) or timeout <= 0:
            raise HarnessError(f"step {sid!r} 'timeout_sec' must be a positive integer")


def step_by_id(plan: dict[str, Any], step_id: str) -> dict[str, Any]:
    for step in plan["steps"]:
        if step["id"] == step_id:
            return step
    raise HarnessError(f"step {step_id!r} not in plan")


def state_path(plan_id: str) -> Path:
    return plan_dir(plan_id) / "state.json"


def load_state(plan_id: str) -> dict[str, Any]:
    path = state_path(plan_id)
    if not path.exists():
        raise HarnessError(f"plan {plan_id!r} has not been started (run `start {plan_id}`)")
    return read_json(path)


def save_state(plan_id: str, state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    write_json(state_path(plan_id), state)


def new_step_state() -> dict[str, Any]:
    return {"status": "pending", "attempts": 0, "commit": None, "summary": None, "last_result": None}


def check_state_covers_plan(plan: dict[str, Any], state: dict[str, Any]) -> None:
    missing = [s["id"] for s in plan["steps"] if s["id"] not in state["steps"]]
    if missing:
        raise HarnessError(
            f"steps {missing} are in plan.yaml but not in state: the plan was edited; "
            "run `replan` after the user approves the change"
        )


def find_active_plan(explicit: str | None) -> str:
    if explicit:
        return explicit
    branch = current_branch()
    if branch.startswith("harness/"):
        candidate = branch[len("harness/"):]
        if state_path(candidate).exists():
            return candidate
    started = sorted(p.parent.name for p in PLANS_DIR.glob("*/state.json")) if PLANS_DIR.exists() else []
    if len(started) == 1:
        return started[0]
    if not started:
        raise HarnessError("no started plan found; run `start <plan-id>` first or pass --plan")
    raise HarnessError(f"several started plans {started}; check out the plan branch or pass --plan")


def ensure_on_plan_branch(state: dict[str, Any]) -> None:
    branch = current_branch()
    if branch != state["branch"]:
        raise HarnessError(f"current branch is {branch!r}, expected {state['branch']!r}")


def progress(plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Short progress block. commit/discard return only this; the step list comes from `status`."""
    statuses = [state["steps"][step["id"]]["status"] for step in plan["steps"]]
    next_step = next((step["id"] for step, st in zip(plan["steps"], statuses) if st != "done"), None)
    return {
        "done": statuses.count("done"),
        "total": len(statuses),
        "next_step": next_step,
        "complete": next_step is None,
        "dirty_files": changed_paths(),
    }


def summarize(plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    steps = []
    for step in plan["steps"]:
        s = state["steps"][step["id"]]
        steps.append({
            "id": step["id"],
            "title": step["title"],
            "status": s["status"],
            "attempts": s["attempts"],
            "commit": s.get("commit"),
            "last_result": s.get("last_result"),
        })
    return {
        "plan": plan["id"],
        "goal": plan["goal"],
        "branch": state["branch"],
        "current_branch": current_branch(),
        "steps": steps,
        **progress(plan, state),
    }


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #


def cmd_start(args: argparse.Namespace) -> dict[str, Any]:
    plan_id: str = args.plan_id
    plan = load_plan(plan_id)
    if plan.get("approved") is not True:
        raise HarnessError("plan is not approved: set `approved: true` in plan.yaml after the user approves it")
    if state_path(plan_id).exists():
        raise HarnessError(f"plan {plan_id!r} already started; use `status`")

    plan_rel = (plan_dir(plan_id) / "plan.yaml").relative_to(REPO_ROOT).as_posix()
    dirty = [p for p in changed_paths() if p != plan_rel]
    if dirty:
        raise HarnessError(f"working tree must be clean before start; dirty: {dirty}")

    branch = f"harness/{plan_id}"
    existing = git("branch", "--list", branch).strip()
    if existing:
        raise HarnessError(f"branch {branch!r} already exists; delete it or choose another plan id")
    base = git("rev-parse", "HEAD").strip()
    git("checkout", "-q", "-b", branch)
    git("add", "--", plan_rel)
    git_commit(plan_id, f"chore(harness): add {plan_id} plan", args.trailer)

    state = {
        "plan_id": plan_id,
        "branch": branch,
        "base_commit": base,
        "created_at": now_iso(),
        "steps": {step["id"]: new_step_state() for step in plan["steps"]},
    }
    save_state(plan_id, state)
    return {"action": "start", **summarize(plan, state)}


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    plan_id = find_active_plan(args.plan)
    plan = load_plan(plan_id)
    state = load_state(plan_id)
    check_state_covers_plan(plan, state)
    return {"action": "status", **summarize(plan, state)}


def build_prompt(
    plan: dict[str, Any],
    state: dict[str, Any],
    step: dict[str, Any],
    attempt: int,
    feedback: str | None,
) -> str:
    lines: list[str] = []
    lines.append("# Harness step execution")
    lines.append("")
    lines.append(
        "You are implementing exactly one step of an approved plan inside an existing repository. "
        "Read AGENTS.md (and CLAUDE.md if present) for repository rules."
    )
    lines.append("")
    lines.append("## Overall goal of the plan")
    lines.append(plan["goal"].strip())
    lines.append("")

    done = [s for s in plan["steps"] if state["steps"][s["id"]]["status"] == "done"]
    if done:
        lines.append("## Steps already completed and committed")
        for s in done:
            summary = state["steps"][s["id"]].get("summary") or "(no summary recorded)"
            lines.append(f"- {s['id']} — {s['title']}: {summary}")
        lines.append("")

    lines.append(f"## Current step: {step['id']} — {step['title']}")
    lines.append(step["description"].strip())
    lines.append("")
    lines.append("### Files you are expected to touch (glob patterns)")
    for pattern in step["expected_files"]:
        lines.append(f"- `{pattern}`")
    lines.append(
        "Changes outside these patterns are flagged for human review. Touch other files only when "
        "strictly necessary and explain each case in `decisions`."
    )
    lines.append("")
    lines.append("### Acceptance criteria")
    ac_shell = "cmd.exe" if sys.platform == "win32" else "/bin/sh"  # what shell=True uses
    lines.append(
        f"The harness runs each command verbatim with {ac_shell} from the repository root after you finish. "
        "All must exit 0. Run them yourself the same way before finishing."
    )
    for cmd in step["ac"]:
        lines.append(f"- `{cmd}`")
    lines.append("")
    lines.append("## Rules")
    lines.append("- Do not run `git commit`, `git checkout`, `git stash`, `git branch` or anything that changes git state. The harness commits.")
    lines.append("- Do not modify anything under `.harness/` or `.claude/skills/harness/`.")
    lines.append("- Do not weaken, skip or delete tests to make acceptance criteria pass.")
    lines.append(
        "- If running the acceptance criteria leaves build or test artifacts that `git status` shows as untracked "
        "(e.g. `__pycache__/`, `dist/`, `coverage/`), add ignore patterns for them to `.gitignore` and explain in "
        "`decisions`. The harness commits every file that is not ignored."
    )
    lines.append("- Do not implement later steps of the plan.")
    lines.append("- Your final message must be the JSON object required by the output schema (summary, changed_files, decisions, blockers).")
    if attempt > 1:
        lines.append("")
        lines.append(f"## This is attempt {attempt}. Feedback from the reviewer on the previous attempt")
        lines.append((feedback or "(no feedback provided)").strip())
    return "\n".join(lines) + "\n"


def run_codex(prompt: str, run_dir: Path, timeout_sec: int, plan_id: str, step_id: str) -> dict[str, Any]:
    codex_bin = os.environ.get("HARNESS_CODEX_BIN") or shutil.which("codex")
    if not codex_bin:
        raise HarnessError("codex executable not found on PATH (or set HARNESS_CODEX_BIN)")

    schema_path = run_dir / "output_schema.json"
    write_json(schema_path, OUTPUT_SCHEMA)
    last_msg_path = run_dir / "last_message.txt"
    events_path = run_dir / "codex_events.jsonl"
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")

    cmd = [
        codex_bin, "exec",
        "--json",
        "--color", "never",
        # Project hooks (.codex/hooks.json) are skipped silently in headless runs
        # unless trusted; the hooks come from the harness template, so run them.
        "--dangerously-bypass-hook-trust",
        "--output-schema", str(schema_path),
        "-o", str(last_msg_path),
        "-C", str(REPO_ROOT),
        "-",
    ]
    # Inherited by Codex and by its hooks: .codex/hooks/guard.py switches to
    # harness mode (blocks git state changes and harness file writes) when set.
    env = {**os.environ, "HARNESS_PLAN": plan_id, "HARNESS_STEP": step_id}
    started = time.monotonic()
    timed_out = False
    with events_path.open("wb") as events:
        proc = subprocess.Popen(
            cmd, cwd=REPO_ROOT, env=env, stdin=subprocess.PIPE, stdout=events, stderr=subprocess.PIPE,
        )
        try:
            _, stderr = proc.communicate(prompt.encode("utf-8"), timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_process_tree(proc)
            try:
                _, stderr = proc.communicate(timeout=10)
            except (subprocess.TimeoutExpired, ValueError, OSError):
                stderr = b""
    duration = round(time.monotonic() - started, 1)

    raw_last = last_msg_path.read_text(encoding="utf-8", errors="replace") if last_msg_path.exists() else ""
    output: dict[str, Any] | None = None
    parse_error: str | None = None
    if raw_last.strip():
        try:
            parsed = json.loads(raw_last)
            if isinstance(parsed, dict):
                output = parsed
            else:
                parse_error = "last message is JSON but not an object"
        except json.JSONDecodeError as exc:
            parse_error = f"last message is not valid JSON: {exc}"
    elif not timed_out:
        parse_error = "codex produced no final message"

    result: dict[str, Any] = {
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "duration_sec": duration,
        "output": output,
        "parse_error": parse_error,
        "events_file": rel(events_path),
    }
    # Every result is read into Claude's context; stderr only matters when the run went wrong.
    if timed_out or proc.returncode != 0 or output is None:
        result["stderr_tail"] = tail(decode_output(stderr or b""))
    return result


def run_acceptance(step: dict[str, Any], plan: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    ac_timeout = int(plan.get("ac_timeout_sec", DEFAULT_AC_TIMEOUT_SEC))
    results: list[dict[str, Any]] = []
    for i, cmd in enumerate(step["ac"], 1):
        started = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=REPO_ROOT, capture_output=True, timeout=ac_timeout, check=False,
            )
            exit_code, timed_out, out, err = proc.returncode, False, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            exit_code, timed_out, out, err = None, True, exc.stdout or b"", exc.stderr or b""
        stdout, stderr = decode_output(out), decode_output(err)
        log = run_dir / f"ac-{i}.log"
        log.write_text(f"$ {cmd}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}", encoding="utf-8")
        result: dict[str, Any] = {
            "cmd": cmd,
            "exit_code": exit_code,
            "duration_sec": round(time.monotonic() - started, 1),
            "log": rel(log),
        }
        if timed_out:
            result["timed_out"] = True
        # Output of passing commands stays in the log file only.
        if exit_code != 0:
            result["stdout_tail"] = tail(stdout)
            result["stderr_tail"] = tail(stderr)
        results.append(result)
    return results


def out_of_scope(paths: list[str], patterns: list[str]) -> list[str]:
    return [p for p in paths if not any(fnmatch.fnmatch(p, pat) for pat in patterns)]


def cmd_run(args: argparse.Namespace) -> dict[str, Any]:
    plan_id = find_active_plan(args.plan)
    plan = load_plan(plan_id)
    state = load_state(plan_id)
    ensure_on_plan_branch(state)
    check_state_covers_plan(plan, state)
    step = step_by_id(plan, args.step_id)
    s = state["steps"][step["id"]]

    if s["status"] == "done":
        raise HarnessError(f"step {step['id']!r} is already committed")
    for prior in plan["steps"]:
        if prior["id"] == step["id"]:
            break
        if state["steps"][prior["id"]]["status"] != "done":
            raise HarnessError(f"step {prior['id']!r} must be committed before running {step['id']!r}")
    dirty = changed_paths()
    if dirty:
        raise HarnessError(
            f"working tree is dirty; run `commit {step['id']}` or `discard {step['id']}` first: {dirty}"
        )
    max_attempts = int(plan.get("max_attempts", DEFAULT_MAX_ATTEMPTS))
    if s["attempts"] >= max_attempts and not args.force:
        raise HarnessError(
            f"step {step['id']!r} used {s['attempts']}/{max_attempts} attempts; "
            "report to the user (or pass --force to allow one more)"
        )

    feedback: str | None = None
    if args.feedback_file:
        if args.feedback_file == "-":
            feedback = sys.stdin.read()
        else:
            feedback = Path(args.feedback_file).read_text(encoding="utf-8")

    attempt = s["attempts"] + 1
    run_dir = plan_dir(plan_id) / "runs" / f"{step['id']}-{attempt:02d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    if feedback is not None:
        (run_dir / "feedback.md").write_text(feedback, encoding="utf-8")

    # Saved before Codex starts so `discard` can reset to it even if this run dies.
    head_before = git("rev-parse", "HEAD").strip()
    s["run_head"] = head_before
    save_state(plan_id, state)

    prompt = build_prompt(plan, state, step, attempt, feedback)
    timeout_sec = int(step.get("timeout_sec", DEFAULT_STEP_TIMEOUT_SEC))
    codex = run_codex(prompt, run_dir, timeout_sec, plan_id, step["id"])

    # The guard hook is fail-open and regex based; verify git state here instead of trusting it.
    git_state = {
        "head_before": head_before,
        "head_after": git("rev-parse", "HEAD").strip(),
        "branch": current_branch(),
    }
    git_state["changed"] = git_state["head_after"] != head_before or git_state["branch"] != state["branch"]

    ac = run_acceptance(step, plan, run_dir)
    ac_passed = all(r.get("exit_code") == 0 for r in ac)
    changed = changed_paths()
    oos = out_of_scope(changed, step["expected_files"])
    # Kept after `discard` so failed attempts can still be inspected.
    diff_path = run_dir / "diff.patch"
    diff_stat: list[str] = []
    try:
        diff_stat = write_diff(head_before, diff_path)
    except HarnessError as exc:  # never lose the run result over the diff
        diff_path.write_text(f"diff failed: {exc}\n", encoding="utf-8")

    if git_state["changed"]:
        status = "git_state_changed"
    elif codex["timed_out"]:
        status = "timeout"
    elif codex["exit_code"] != 0:
        status = "codex_error"
    elif not ac_passed:
        status = "ac_failed"
    else:
        status = "pending_review"

    result = {
        "action": "run",
        "plan": plan_id,
        "step": step["id"],
        "title": step["title"],
        "attempt": attempt,
        "max_attempts": max_attempts,
        "status": status,
        "ac_passed": ac_passed,
        "ac": ac,
        "codex": codex,
        "git_state": git_state,
        "changed_files": changed,
        "out_of_scope_files": oos,
        "diff_stat": diff_stat,
        "run_dir": rel(run_dir),
        "diff_file": rel(diff_path),
    }
    write_json(run_dir / "result.json", result)

    s["attempts"] = attempt
    s["status"] = "pending_review" if status == "pending_review" else "pending"
    s["last_result"] = rel(run_dir / "result.json")
    save_state(plan_id, state)
    return result


def cmd_commit(args: argparse.Namespace) -> dict[str, Any]:
    plan_id = find_active_plan(args.plan)
    plan = load_plan(plan_id)
    state = load_state(plan_id)
    ensure_on_plan_branch(state)
    check_state_covers_plan(plan, state)
    step = step_by_id(plan, args.step_id)
    s = state["steps"][step["id"]]

    if s["status"] != "pending_review":
        raise HarnessError(
            f"step {step['id']!r} is {s['status']!r}; only a step whose last run passed AC can be committed"
        )
    if not changed_paths():
        raise HarnessError("nothing to commit: working tree is clean")

    git("add", "-A")
    scope = f"({step['scope']})" if step.get("scope") else ""
    git_commit(plan_id, f"{step['type']}{scope}: {step['title']}", [CODEX_TRAILER, *args.trailer])
    sha = git("rev-parse", "--short", "HEAD").strip()

    summary = None
    if s.get("last_result"):
        try:
            summary = (read_json(REPO_ROOT / s["last_result"]).get("codex") or {}).get("output", {}).get("summary")
        except (OSError, json.JSONDecodeError, AttributeError):
            summary = None
    s["status"] = "done"
    s["commit"] = sha
    s["summary"] = summary
    save_state(plan_id, state)
    return {"action": "commit", "step": step["id"], "commit": sha, **progress(plan, state)}


def cmd_discard(args: argparse.Namespace) -> dict[str, Any]:
    plan_id = find_active_plan(args.plan)
    plan = load_plan(plan_id)
    state = load_state(plan_id)
    ensure_on_plan_branch(state)
    check_state_covers_plan(plan, state)
    step = step_by_id(plan, args.step_id)
    s = state["steps"][step["id"]]
    if s["status"] == "done":
        raise HarnessError(f"step {step['id']!r} is already committed; nothing to discard")

    # Reset index and HEAD too: Codex may have staged files or committed despite the guard.
    discarded = changed_paths()
    target = s.get("run_head") or git("rev-parse", "HEAD").strip()
    removed_commits = git("log", "--format=%h %s", f"{target}..HEAD").splitlines()
    git("reset", "-q", "--hard", target)
    git("clean", "-fdq")
    s["status"] = "pending"
    save_state(plan_id, state)
    return {
        "action": "discard",
        "step": step["id"],
        "discarded_files": discarded,
        "removed_commits": removed_commits,
        **progress(plan, state),
    }


def cmd_replan(args: argparse.Namespace) -> dict[str, Any]:
    plan_id = find_active_plan(args.plan)
    plan = load_plan(plan_id)
    state = load_state(plan_id)
    ensure_on_plan_branch(state)
    if plan.get("approved") is not True:
        raise HarnessError("plan change is not approved: set `approved: true` in plan.yaml after the user approves it")

    plan_rel = (plan_dir(plan_id) / "plan.yaml").relative_to(REPO_ROOT).as_posix()
    dirty = changed_paths()
    if plan_rel not in dirty:
        raise HarnessError("plan.yaml is unchanged; nothing to replan")
    others = [p for p in dirty if p != plan_rel]
    if others:
        raise HarnessError(f"only plan.yaml may be changed; commit or discard the current step first: {others}")

    # The last committed plan.yaml is the plan the user approved most recently.
    old = yaml.safe_load(git("show", f"HEAD:{plan_rel}"))
    check_state_covers_plan(old, state)
    old_steps = {s["id"]: s for s in old["steps"]}
    done = [s for s in old["steps"] if state["steps"][s["id"]]["status"] == "done"]
    if plan["steps"][:len(done)] != done:
        raise HarnessError(
            f"committed steps {[s['id'] for s in done]} must stay first, in order and unchanged; "
            "add a new step to fix committed work"
        )
    if plan.get("max_attempts", DEFAULT_MAX_ATTEMPTS) != old.get("max_attempts", DEFAULT_MAX_ATTEMPTS):
        raise HarnessError("max_attempts cannot change mid-run; the user decides on extra attempts with --force")

    new_ids = [s["id"] for s in plan["steps"]]
    added = [sid for sid in new_ids if sid not in old_steps]
    removed = [sid for sid in old_steps if sid not in new_ids]
    changed = [s["id"] for s in plan["steps"] if s["id"] in old_steps and s != old_steps[s["id"]]]
    # State of removed steps is kept, so a step that comes back keeps its attempts.
    for sid in added:
        state["steps"].setdefault(sid, new_step_state())

    git("add", "--", plan_rel)
    git_commit(plan_id, f"chore(harness): revise {plan_id} plan", args.trailer)
    save_state(plan_id, state)
    return {
        "action": "replan",
        "added_steps": added,
        "removed_steps": removed,
        "changed_steps": changed,
        **summarize(plan, state),
    }


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    trailer_help = "commit message trailer line, e.g. Co-Authored-By (repeatable)"

    p = sub.add_parser("start", help="create the plan branch, commit plan.yaml, initialise state")
    p.add_argument("plan_id")
    p.add_argument("--trailer", action="append", default=[], help=trailer_help)
    p.set_defaults(func=cmd_start)

    for name in ("status", "resume"):
        p = sub.add_parser(name, help="show plan progress (resume is an alias)")
        p.add_argument("--plan")
        p.set_defaults(func=cmd_status)

    p = sub.add_parser("run", help="run one step with Codex and evaluate acceptance criteria")
    p.add_argument("step_id")
    p.add_argument("--plan")
    p.add_argument("--feedback-file", help="reviewer feedback for a retry ('-' reads stdin)")
    p.add_argument("--force", action="store_true", help="allow a run past max_attempts")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("commit", help="commit the reviewed step")
    p.add_argument("step_id")
    p.add_argument("--plan")
    p.add_argument("--trailer", action="append", default=[], help=trailer_help)
    p.set_defaults(func=cmd_commit)

    p = sub.add_parser("discard", help="throw away the uncommitted changes of a step")
    p.add_argument("step_id")
    p.add_argument("--plan")
    p.set_defaults(func=cmd_discard)

    p = sub.add_parser("replan", help="commit an approved plan.yaml change made mid-run")
    p.add_argument("--plan")
    p.add_argument("--trailer", action="append", default=[], help=trailer_help)
    p.set_defaults(func=cmd_replan)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        emit(args.func(args))
        return 0
    except HarnessError as exc:
        emit({"action": args.command, "error": str(exc)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
