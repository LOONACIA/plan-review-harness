"""Codex PreToolUse guard for shell commands.

Codex runs this before every shell command and passes one JSON object on
stdin (`tool_input.command` holds the command). Printing a deny decision
blocks the command and the reason is shown to the model.

Two tiers:
  always        destructive commands nobody should run unattended
  harness mode  (HARNESS_STEP env var set by harness.py) additionally blocks
                anything that changes git state or touches .harness/ or
                .claude/skills/harness/

Hooks are fail-open: any crash here lets the command through. Keep it small,
standard library only, and exit 0 on unexpected input.
"""

import json
import os
import re
import sys

# --- tier 1: always blocked -------------------------------------------------

ALWAYS_BLOCKED = [
    # (regex, reason, flags). git flags are case-sensitive: -d (safe) vs -D (force).
    (r"\bgit\s+push\b.*\s(--force|-f|--force-with-lease)\b", "force push", 0),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard destroys uncommitted work", 0),
    (r"\bgit\s+clean\b.*\s-[a-zA-Z]*[fdx]", "git clean deletes untracked files", 0),
    (r"\bgit\s+branch\s+(-D|--delete\s+--force)\b", "force-deleting a branch", 0),
    (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+(/|~|\.|\.\.|\*|[A-Za-z]:[\\/]?)(\s|$)",
     "recursive delete of a root, home, current directory or wildcard", 0),
    (r"\b(rmdir|rd)\s+/s\b.*\s([A-Za-z]:\\?|\\|\.|\.\.)(\s|$)", "recursive delete of a drive root or current directory", re.IGNORECASE),
    (r"\bRemove-Item\b.*-Recurse\b.*\s(/|~|\.|\.\.|\*|[A-Za-z]:\\?)(\s|$)", "recursive delete of a root or current directory", re.IGNORECASE),
    (r"\b(DROP|TRUNCATE)\s+(TABLE|DATABASE|SCHEMA)\b", "destructive SQL", re.IGNORECASE),
    (r"\bmkfs\b|\bformat\s+[A-Za-z]:|>\s*/dev/sd", "disk-level destruction", re.IGNORECASE),
]

# --- tier 2: blocked while the harness is driving the session ---------------

GIT_STATE_CHANGING = (
    "commit", "checkout", "switch", "stash", "rebase", "merge", "reset",
    "push", "pull", "tag", "cherry-pick", "revert", "restore", "am", "worktree",
    "rm", "mv", "notes", "filter-branch", "reflog",
)
GIT_BRANCH_READ_FLAGS = {"-a", "-r", "-v", "-vv", "--all", "--list", "--show-current", "--contains", "--merged", "--no-merged"}
READ_ONLY_TOOLS = {
    "cat", "type", "less", "more", "head", "tail", "ls", "dir", "tree", "find", "grep", "rg", "egrep",
    "wc", "stat", "file", "Get-Content", "Get-ChildItem", "Select-String", "gc", "gci", "sls", "echo",
}


def split_simple_commands(cmd: str):
    """Split on shell separators. Good enough for a guard; not a parser."""
    return [c.strip() for c in re.split(r"\|\||&&|;|\||\n", cmd) if c.strip()]


def first_word(segment: str) -> str:
    segment = re.sub(r"^\s*(cd\s+\S+\s*(&&|;)\s*)+", "", segment)
    m = re.match(r"\s*([^\s]+)", segment)
    return m.group(1) if m else ""


def harness_violation(cmd: str):
    for seg in split_simple_commands(cmd):
        m = re.match(r"\s*git(?:\s+-[cC]\s+\S+|\s+--[a-z-]+(?:=\S+)?)*\s+([a-z-]+)(.*)$", seg)
        if m:
            sub, rest = m.group(1), m.group(2).split()
            if sub in GIT_STATE_CHANGING:
                return f"git {sub} changes repository state; the harness commits after review"
            if sub == "branch" and any(not (a.startswith("-") and a in GIT_BRANCH_READ_FLAGS) for a in rest):
                return "git branch with arguments creates, deletes or renames branches"
            if sub == "add" and rest:
                return "git add is unnecessary; the harness stages everything at commit time"
        if re.search(r"\.harness[\\/]|\.claude[\\/]skills[\\/]harness[\\/]", seg):
            tool = first_word(seg)
            if tool not in READ_ONLY_TOOLS or re.search(r">|>>", seg):
                return "modifying or executing anything under .harness/ or .claude/skills/harness/ is reserved for the harness"
    return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
        cmd = (data.get("tool_input") or {}).get("command") or ""
    except Exception:
        return 0
    if not isinstance(cmd, str) or not cmd.strip():
        return 0

    reason = None
    for pattern, why, flags in ALWAYS_BLOCKED:
        if re.search(pattern, cmd, flags=flags):
            reason = why
            break
    if reason is None and os.environ.get("HARNESS_STEP"):
        reason = harness_violation(cmd)

    if reason:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Blocked by .codex/hooks/guard.py: {reason}. Command: {cmd[:200]}",
            }
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
