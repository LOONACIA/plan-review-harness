"""Fake `codex` used by the tests.

Reads the prompt from stdin, records argv and prompt into the scenario's
directory, applies the scenario (write files, git commands, sleep, exit code)
and writes the configured last message to the `-o` path.

Scenario file path comes from FAKE_CODEX_SCENARIO. Format:
{
  "write_files": {"relative/path": "content"},
  "git_commands": [["add", "-A"], ...],
  "last_message": {...} | "raw string" | null,
  "exit_code": 0,
  "sleep_sec": 0
}
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    scenario_path = Path(os.environ["FAKE_CODEX_SCENARIO"])
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    record_dir = scenario_path.parent

    prompt = sys.stdin.buffer.read().decode("utf-8")
    argv = sys.argv[1:]
    (record_dir / "argv.json").write_text(json.dumps(argv, ensure_ascii=False), encoding="utf-8")
    (record_dir / "env.json").write_text(
        json.dumps({k: v for k, v in os.environ.items() if k.startswith("HARNESS_")}), encoding="utf-8"
    )
    (record_dir / "prompt.md").write_text(prompt, encoding="utf-8")

    cwd = Path.cwd()
    if "-C" in argv:
        cwd = Path(argv[argv.index("-C") + 1])

    for rel, content in scenario.get("write_files", {}).items():
        target = cwd / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    for args in scenario.get("git_commands", []):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    time.sleep(scenario.get("sleep_sec", 0))

    sys.stdout.buffer.write(b'{"type":"item.completed","item":{"type":"agent_message","text":"fake"}}\n')
    sys.stdout.flush()

    last = scenario.get("last_message")
    if last is not None and "-o" in argv:
        out = Path(argv[argv.index("-o") + 1])
        text = last if isinstance(last, str) else json.dumps(last, ensure_ascii=False)
        out.write_text(text, encoding="utf-8")
    return int(scenario.get("exit_code", 0))


if __name__ == "__main__":
    sys.exit(main())
