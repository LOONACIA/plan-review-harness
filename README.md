# plan-review-harness

Claude Code가 계획과 리뷰를 맡고, Codex CLI가 구현을 맡는 하네스의 **템플릿 저장소**입니다. `template/` 아래 파일을 대상 프로젝트에 복사해서 사용합니다.

Anthropic 또는 OpenAI의 공식 프로젝트가 아니며, 두 회사와 관련이 없는 비공식 프로젝트입니다.

## 역할

| 역할 | 담당 |
|---|---|
| 계획 작성, 스텝 결과 해석, diff 리뷰, 재시도 피드백 작성, commit/discard 결정 | Claude Code 대화형 세션 (`/harness` 스킬) |
| 스텝 구현 | Codex CLI (`codex exec`, 스텝마다 새 세션, 사용자 `~/.codex/config.toml` 상속) |
| Codex 호출, AC 명령 실행, 범위 이탈 감지, 상태 기록, 브랜치/커밋 | `.claude/skills/harness/scripts/harness.py` (Python, uv) |
| Codex 셸 명령 차단 (위험 명령, 하네스 실행 중 git 상태 변경) | `.codex/hooks/guard.py` (Codex PreToolUse 훅) |

## 요구 사항

- Claude Code
- Codex CLI (`codex`가 PATH에 있어야 함)
- [uv](https://docs.astral.sh/uv/) (PyYAML을 인라인 스크립트 의존성으로 가져옴)
- git

## 설치 (대상 프로젝트에 수동 복사)

대상 프로젝트 루트에서:

1. `template/.harness/` 디렉토리를 `.harness/`로 복사합니다 (`.gitignore`만 들어 있고, 계획과 실행 기록이 여기 쌓입니다).
2. `template/.codex/` 디렉토리를 `.codex/`로 복사합니다 (`hooks.json`, `hooks/guard.py`). 이미 `.codex/hooks.json`이 있으면 `PreToolUse` 항목을 병합합니다.
3. `template/.claude/skills/harness/` 디렉토리를 `.claude/skills/harness/`로 복사합니다 (`SKILL.md`, `scripts/harness.py`). 계획은 `.harness/`에 따로 있으므로 템플릿을 업데이트할 때 이 디렉토리는 그대로 덮어써도 됩니다.
4. `template/AGENTS.md`를 저장소 루트에 둡니다. 이미 `AGENTS.md`가 있으면 "하네스 실행 규칙" 섹션만 기존 파일 끝에 붙입니다. 이 섹션 마지막 줄이 Codex 보고(`summary`, `decisions`, `blockers`)를 한국어로 받는 규칙이고, Claude 쪽은 SKILL.md의 "한국어로 쓰기" 항목이 짝입니다. 언어를 바꾸려면 두 곳을 함께 고칩니다.
5. 프로젝트 코딩 규칙은 `CLAUDE.md`에 있어야 합니다. `AGENTS.md`가 그 파일을 참조합니다.
6. `.gitignore`가 테스트·빌드 산출물(`__pycache__/`, `dist/`, `coverage/` 등)을 덮는지 확인합니다. 하네스는 스텝을 `git add -A`로 커밋하므로 ignore되지 않은 산출물이 커밋에 섞입니다.
7. (선택) 새 프로젝트라면 `template/docs/`(PRD, ARCHITECTURE, ADR 뼈대)를 `docs/`로 복사해 채웁니다. Claude가 계획을 세울 때 읽고, 스텝 description의 "읽어야 할 파일"로 지정합니다. 기존 프로젝트에 이미 설계 문서가 있으면 건너뜁니다.
8. 복사한 파일을 커밋합니다.

PowerShell 예:

```powershell
$T = "<clone한 경로>\template"
Copy-Item "$T\.harness" ".harness" -Recurse
Copy-Item "$T\.codex" ".codex" -Recurse   # 기존 .codex/hooks.json이 있으면 수동 병합
New-Item -ItemType Directory -Force ".claude\skills" | Out-Null
Copy-Item "$T\.claude\skills\harness" ".claude\skills\harness" -Recurse
Copy-Item "$T\AGENTS.md" "AGENTS.md"   # 기존 AGENTS.md가 있으면 수동 병합
```

## Codex 가드 훅

`.codex/hooks/guard.py`는 Codex가 셸 명령을 실행하기 직전에 호출되는 PreToolUse 훅입니다. `python`이 PATH에 있어야 합니다.

- **항상 차단**: 강제 push, `git reset --hard`, `git clean -f`, `git branch -D`, 루트/홈/현재 디렉토리/와일드카드에 대한 재귀 삭제, `DROP`/`TRUNCATE` SQL, 디스크 포맷.
- **하네스 실행 중에만 차단** (하네스가 `HARNESS_STEP` 환경 변수를 넘길 때): git 상태를 바꾸는 모든 명령(`commit`, `checkout`, `switch`, `stash`, `rebase`, `merge`, `reset`, `push`, `pull`, `tag`, `restore`, `rm`, `add`, 브랜치 생성/삭제)과 `.harness/`, `.claude/skills/harness/` 아래 쓰기·실행. 읽기(`git status`, `git diff`, `cat .harness/...`)는 허용.

사용자가 작성한 Codex 훅은 원래 대화형 `/hooks`에서 한 번 신뢰해야 하고, 신뢰되지 않은 훅은 `codex exec`에서 조용히 건너뜁니다. 이 하네스는 훅이 템플릿에서 복사된 파일이라는 전제로 `codex exec`에 `--dangerously-bypass-hook-trust`를 항상 넘겨 훅을 실행합니다. 따라서 **낯선 저장소에서 하네스를 돌리면 그 저장소의 `.codex/hooks.json`이 그대로 실행됩니다.** 하네스를 쓰기 전에 그 파일을 확인하세요.

훅은 fail-open입니다. 가드 스크립트가 에러나 타임아웃으로 끝나면 명령은 그대로 실행됩니다. 정규식 기반이라 우회도 가능하므로, 가드는 Claude 리뷰 게이트를 대체하는 게 아니라 보강하는 장치입니다.

## 사용 흐름

1. Claude Code에서 `/harness plan <작업 설명>` → Claude가 코드베이스를 탐색하고 `.harness/plans/<plan-id>/plan.yaml` 초안을 작성해 요약을 보여줍니다.
2. 승인하면 Claude가 `approved: true`로 바꾸고 `start`를 실행합니다. `harness/<plan-id>` 브랜치가 만들어지고 plan.yaml이 커밋됩니다.
3. `/harness run`(계획 단계의 탐색 내용을 끌고 가지 않도록 새 세션에서 권장) → 스텝마다 Codex 실행 → AC 명령 실행 → Claude 리뷰 → commit 또는 discard. 스텝마다 한 줄 보고, 재시도는 스텝당 총 3회.
4. 실행 중 계획을 바꿔야 하면 Claude가 변경 내용을 보여주고, 승인 후 `replan`으로 plan.yaml만 따로 커밋합니다. 이미 커밋된 스텝은 바꿀 수 없고(보정이 필요하면 새 스텝 추가), 고친 스텝의 시도 횟수와 `max_attempts`는 그대로입니다.
5. 세션이 끊기면 새 세션에서 `/harness resume`.
6. 끝나면 사용자가 브랜치를 직접 머지합니다.

멈추는 지점: 계획 승인, 재시도 소진, Codex가 사람 결정이 필요한 blocker 보고, 계획 자체가 틀렸다고 판단될 때.

## 스크립트 서브커맨드

```
uv run .claude/skills/harness/scripts/harness.py start <plan-id> [--trailer T ...]
uv run .claude/skills/harness/scripts/harness.py status            # resume도 동일
uv run .claude/skills/harness/scripts/harness.py run <step-id> [--feedback-file F] [--force]
uv run .claude/skills/harness/scripts/harness.py commit <step-id> [--trailer T ...]
uv run .claude/skills/harness/scripts/harness.py discard <step-id>
uv run .claude/skills/harness/scripts/harness.py replan [--trailer T ...]   # 승인된 plan.yaml 변경을 커밋
```

커밋 제목은 Conventional Commits 형식입니다. 스텝 커밋은 스텝의 `type`, `scope`(선택), `title`로 `<type>(<scope>): <title>`을 만들고, 계획 커밋은 `chore(harness): add <plan-id> plan`, `chore(harness): revise <plan-id> plan`입니다. 모든 커밋에 `Harness-Plan: <plan-id>` 트레일러가 붙고(`git log --grep "Harness-Plan: <plan-id>"`로 플랜별 조회), 이어서 `--trailer`로 받은 줄(Claude의 `Co-Authored-By`, `Claude-Session` 등)이 붙습니다. 스텝 커밋에는 `Co-Authored-By: Codex <noreply@openai.com>`도 항상 붙습니다.

모든 명령은 JSON 하나를 출력합니다. 종료 코드 2는 전제 조건 실패(아무 것도 하지 않음)입니다. `run`은 AC가 실패해도 종료 코드 0이며 `status` 필드(`pending_review` / `ac_failed` / `codex_error` / `timeout` / `git_state_changed`)로 판단합니다. `git_state_changed`는 가드를 우회해 Codex가 커밋하거나 브랜치를 바꾼 경우이고, `discard`는 실행 직전 커밋으로 되돌립니다(Codex가 만든 커밋과 스테이징 포함).

`.harness/plans/<plan-id>/` 안의 `plan.yaml`만 git에 추적되고 `state.json`, `runs/`(프롬프트, Codex 이벤트 로그, AC 명령별 전체 출력 로그, 결과 JSON, untracked 파일까지 포함한 `diff.patch`)는 무시됩니다. `diff.patch`는 discard 뒤에도 남아 실패한 시도를 다시 볼 수 있습니다.

## 템플릿 개발

이 저장소에서 스크립트 단위 테스트를 돌립니다. `tests/test_harness.py`는 PATH에 가짜 `codex`를 주입하고 `harness.py`를 서브프로세스로 실행하며, cp949 환경에서도 깨지지 않는지 확인하기 위해 `PYTHONUTF8`을 제거한 채 실행합니다. `tests/test_guard.py`는 가드 훅에 명령 목록을 넣어 차단/허용을 확인합니다.

```powershell
uv run --with pytest --with pyyaml pytest tests -q
```
