# AGENTS.md

이 저장소에서 작업하는 Codex CLI를 위한 지침입니다.

## 저장소 규칙

저장소 루트에 `CLAUDE.md`가 있으면 읽으세요. 그 코딩 규칙은 당신에게도 적용됩니다.

## 하네스 실행 규칙

프롬프트가 `# Harness step execution`으로 시작하면, 당신은 하네스가 진행하는 계획의 한 스텝을 실행하고 있습니다. 이때는 다음을 지킵니다.

- 현재 스텝만 구현합니다. 쉬워 보여도 이후 스텝을 시작하지 마세요.
- 나열된 `expected_files` 패턴 안에서만 작업합니다. 다른 파일은 꼭 필요할 때만 건드리고, 그런 파일은 모두 `decisions`에 이유를 적습니다.
- 끝내기 전에 나열된 인수 조건(acceptance criteria) 명령을 직접 실행합니다. 하네스가 저장소 루트에서 적힌 그대로 다시 실행하며, 모두 0으로 끝나야 합니다.
- 인수 조건을 실행한 뒤 `git status`에 ignore되지 않은 빌드·테스트 산출물(`__pycache__/`, `dist/`, `coverage/` 등)이 보이면 `.gitignore`에 패턴을 추가하고 `decisions`에 적습니다. 하네스는 ignore되지 않은 파일을 모두 커밋합니다.
- 확인하려고 dev 서버나 watch 모드처럼 계속 실행되는 프로세스를 띄웠다면 끝내기 전에 직접 종료합니다. 셸만 끝내면 자식 프로세스가 남을 수 있으니 그 프로세스까지 종료됐는지 확인하세요. 남은 프로세스가 있으면 Codex가 종료하지 못해 하네스가 스텝 타임아웃까지 멈춥니다.
- 인수 조건을 통과시키려고 테스트를 약화, 스킵, 삭제하거나 특수 처리하지 마세요. 조건이 잘못돼 보이면 `blockers`에 적고 실패한 채로 둡니다.
- git 상태를 바꾸지 마세요: `commit`, `checkout`, `stash`, `branch`, `reset`, `rebase` 금지. 커밋은 리뷰 후 하네스가 합니다.
- `.harness/`와 `.claude/skills/harness/` 아래는 아무것도 수정하지 마세요.
- PreToolUse 훅(`.codex/hooks/guard.py`)이 위 두 규칙을 강제하고 위험한 명령(force push, `git reset --hard`, 루트 재귀 삭제, 파괴적 SQL)을 막습니다. 명령이 "Blocked by .codex/hooks/guard.py"로 막히면 우회 방법을 찾지 말고, 그 명령 없이 스텝을 끝낸 뒤 `decisions`나 `blockers`에 적으세요.
- 최종 메시지는 출력 스키마가 요구하는 JSON 객체여야 합니다: `summary`, `changed_files`, `decisions`, `blockers`. 막힌 것이 없으면 `blockers`는 빈 리스트입니다.
- `summary`, `decisions`, `blockers`는 한국어로 씁니다. 코드, 주석, 식별자는 지시문 언어와 상관없이 저장소의 기존 관례와 `CLAUDE.md`를 따릅니다.
