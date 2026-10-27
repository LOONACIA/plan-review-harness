---
name: harness
description: Claude plans and reviews, Codex implements. Use for multi-step implementation work in this repository, or when the user says /harness, "하네스", "코덱스에게 시켜", "plan and delegate".
---

# 하네스: Claude가 계획하고 Codex가 구현합니다

당신은 오케스트레이터입니다. 계획은 당신이 쓰고, 사용자가 승인하고, 스크립트가 스텝마다 Codex를 실행해 인수 조건(AC)을 검사하며, 커밋 전에 모든 스텝을 당신이 리뷰합니다. 스텝 구현은 절대 직접 하지 않습니다.

스크립트: 저장소 루트에서 `uv run .claude/skills/harness/scripts/harness.py <subcommand>`. 모든 서브커맨드는 JSON 객체 하나를 출력합니다. 종료 코드 2는 전제 조건이 실패해 아무 일도 일어나지 않았다는 뜻이니 `error`를 읽으세요.

커밋하는 서브커맨드(`start`, `commit`, `replan`)에는 시스템 리마인더가 알려주는 git 커밋 attribution 줄을 한 줄씩 `--trailer`로 넘깁니다(예: `--trailer "Co-Authored-By: ..." --trailer "Claude-Session: ..."`). 스크립트는 모델 이름과 세션을 모릅니다. `Harness-Plan`과 Codex 트레일러는 스크립트가 직접 붙이니 넘기지 마세요.

## 모드

사용자의 말에서 모드를 고릅니다.

- **plan** (`/harness plan <작업>`): 계획을 쓰고 승인을 받습니다.
- **run** (`/harness run`): 승인된 계획을 스텝별로 실행합니다.
- **resume** / **status**: 새 세션이 진행 중인 계획을 이어받습니다.

### 모드: plan

1. 구체적인 스텝을 쓸 수 있을 때까지 코드베이스를 직접 탐색합니다. `CLAUDE.md`와, `docs/` 디렉토리가 있으면 PRD / ARCHITECTURE / ADR 문서를 먼저 읽으세요. 모든 스텝이 지켜야 할 제약이 거기 있습니다. 작업을 두 가지로 읽을 수 있고 그에 따라 계획이 달라질 때만 짧게 질문하고, 그 외에는 인터뷰하지 않습니다.
2. plan id를 정합니다: 소문자, 숫자, `-` (예: `add-user-export`). 브랜치 이름 `harness/<plan-id>`가 됩니다.
3. `.harness/plans/<plan-id>/plan.yaml`을 `approved: false`로 작성합니다(스키마는 아래).
4. 사용자에게 간결한 요약을 보여줍니다: 목표, 스텝마다 한 줄(id, 제목, AC 개수), 보이는 위험. YAML 전체를 붙여넣지 마세요.
5. 사용자가 승인하면 `approved: true`로 바꾸고 `start <plan-id>`를 실행합니다. 수정을 요청하면 YAML을 고치고 요약을 다시 보여줍니다. 대화에서 명시적인 승인 없이 `approved: true`로 바꾸지 마세요.
6. `start` 뒤에는 바로 실행하지 말고, 새 세션에서 `/harness run`을 시작하라고 권합니다. 계획하며 탐색한 내용이 컨텍스트에 남아 스텝마다 다시 읽히는 것을 막기 위해서입니다. 실행에 필요한 정보는 `plan.yaml`과 `status`에 모두 있습니다. 사용자가 이 세션에서 계속하길 원하면 그대로 run 모드로 갑니다.

#### 스텝 설계 규칙

- 각 스텝은 **얇은 수직 슬라이스**입니다. 처음부터 끝까지 동작하고 명령으로 검증할 수 있는 동작 단위입니다. 레이어별로(모델, 다음 서비스, 다음 API) 나누지 마세요.
- 큰 기능은 동작 기준으로 나눕니다: 정상 경로 먼저, 그다음 엣지 케이스와 에러 처리, 마지막으로 다듬기.
- 크기 상한: 한 스텝이 건드리는 파일은 8개 정도까지, 숙련된 엔지니어가 한 번에 끝낼 수 있는 분량이어야 합니다. 한 문단으로 설명할 수 없으면 나누세요.
- 모든 스텝에는 스텝이 완료됐을 때만 0으로 끝나는 `ac` 명령이 하나 이상 있어야 합니다. 실제 테스트를 우선하세요. UI처럼 자동화하기 어려운 작업은 빌드/컴파일 검사와 기존 기본 테스트 스위트를 최소 기준으로 씁니다.
- `ac` 명령은 저장소 루트에서 항목별로 실행되고, 셸은 Windows에서 cmd.exe, 그 밖에서는 `/bin/sh`입니다. 두 셸에서 똑같이 동작하도록 쓰세요.
  - 항목 하나에 명령 하나. 명령을 이어야 하면 `;`나 `&&` 대신 항목을 나눕니다. cmd에서 `;`는 명령 구분자가 아닙니다.
  - 인자는 큰따옴표로 묶습니다. cmd는 작은따옴표를 인용 부호로 취급하지 않습니다.
  - `$(...)`, `test -f`, PowerShell cmdlet 같은 특정 셸 전용 문법은 쓰지 않습니다. 복잡한 검사는 테스트 코드로 옮기세요.
- `expected_files`는 저장소 루트 기준 fnmatch glob입니다(`*`는 `/`도 넘어갑니다). 스텝이 만들거나 고칠 파일을 나열합니다. 구체적으로 쓰세요. 패턴이 넓으면 범위 검사가 의미 없어집니다.
- 스텝은 엄격하게 순서대로 실행됩니다. 각 스텝이 커밋된 이전 작업 위에 쌓이도록 순서를 정하세요.
- AC 명령이 만드는 산출물(`__pycache__/`, `dist/`, `coverage/` 등)이 `.gitignore`에 있는지 확인합니다. ignore되지 않은 파일은 하네스 커밋에 모두 들어갑니다. 빠진 패턴이 있으면 첫 스텝의 `expected_files`에 `.gitignore`를 넣고 description에 추가할 패턴을 적으세요.

#### 스텝 `description` 쓰기

Codex는 매 스텝을 계획 대화를 전혀 모르는 새 세션에서 시작합니다. AGENTS.md, CLAUDE.md, 이전 스텝 요약 말고는 description이 전부입니다. 이 저장소를 처음 보는 엔지니어를 위해 쓰세요.

- **먼저 읽을 파일.** Codex가 무엇이든 쓰기 전에 읽어야 할 경로를 나열합니다: 이 스텝을 제약하는 문서, 확장할 모듈, 이전 스텝이 만든 파일. "`src/api/users.py`를 읽고 그 에러 처리 패턴을 따르라"가 "기존 관례를 따르라"보다 낫습니다.
- **시그니처 수준의 지시.** 파일, 함수, 클래스와 그 시그니처를 명시합니다. 본문은 Codex에게 맡기되, 굽히면 안 되는 규칙(멱등성, 인증 검사, 데이터 무결성, 트랜잭션)은 명시적으로 적습니다.
- **이유가 붙은 금지.** "X를 조심하라"가 아니라 "X를 하지 마라. 이유: Y"로 씁니다. 예: "새 ORM 모델을 추가하지 마라. 이유: 공용 모델은 스텝 s3에서 도입하며 이 스텝과 충돌하면 안 된다."
- **대화를 참조하지 않기.** "논의한 대로", "합의한 것처럼"이라고 쓰지 마세요. 그 정보를 description에 직접 넣습니다.
- **한국어로 쓰기.** `goal`, `description`, 재시도 피드백처럼 Codex에게 전달하는 글은 한국어로 씁니다(Codex도 `summary`, `decisions`, `blockers`를 한국어로 돌려줍니다). 파일 경로, 식별자, 명령은 원래 표기 그대로 둡니다. `type`, `scope`(선택), `title`은 커밋 제목 `<type>(<scope>): <title>`(예: `feat(api): add CSV export endpoint`)이 되므로 저장소의 커밋 관례와 언어를 따릅니다.

#### plan.yaml 스키마

```yaml
id: add-user-export            # 디렉토리 이름과 같아야 함
goal: >
  한 문단: 사용자가 요청한 것과 "완료"가 무엇인지.
approved: false                # 사용자가 승인한 뒤에만 Claude가 true로 바꿈
max_attempts: 3                # 선택, 기본 3 (첫 실행 + 재시도 2회)
ac_timeout_sec: 600            # 선택, AC 명령 하나당
steps:
  - id: s1-export-endpoint
    title: add CSV export endpoint for the happy path
    type: feat
    scope: api                 # 선택
    description: |
      먼저 읽을 파일: docs/ARCHITECTURE.md ("API layer" 절), src/api/users.py (에러 처리 패턴).

      src/api/export.py에 text/csv를 반환하는 `GET /users/export`를 추가한다.
      시그니처: `def export_users(filters: UserFilter) -> StreamingResponse`.
      src/repo/users.py의 `UserRepository.list()`를 재사용한다. DB를 직접 조회하지 마라.
      행은 스트리밍해야 한다. 이유: 테이블이 수백만 행이라 응답을 버퍼링하면 안 된다.

      페이지네이션 파라미터를 추가하지 마라. 이유: 스텝 s2에서 테스트와 함께 추가한다.
    expected_files:
      - src/api/export.py
      - tests/test_export.py
    ac:
      - uv run pytest tests/test_export.py -q
    timeout_sec: 1800          # 선택, 기본 1800 (이 스텝의 Codex 실행 시간 상한)
```

### 모드: run

`complete: true`가 나오거나 멈춰야 할 때까지 반복합니다. `status`는 루프에 처음 들어갈 때만 실행합니다. `commit`과 `discard`도 `next_step`, `complete`, `dirty_files`, `done`/`total`을 돌려주므로 그 뒤에는 `status`를 다시 부르지 마세요.

1. `next_step`을 확인합니다. `dirty_files`가 비어 있지 않으면 무언가 하다 만 상태입니다. `git status`와 `git diff`로 확인한 뒤, 다른 일보다 먼저 그 스텝을 `commit`하거나 `discard`하세요. dirty 파일이 계획의 `plan.yaml` 하나뿐이면 계획 수정이 진행 중이던 것입니다. discard하지 말고 "실행 중 계획 변경"을 이어가세요.
2. 스텝을 **백그라운드에서** 실행합니다(Codex는 최대 30분까지 걸릴 수 있어 포그라운드 Bash 호출 한도를 넘습니다):
   `uv run .claude/skills/harness/scripts/harness.py run <step-id>`를 `run_in_background`로 실행하고 완료 알림을 기다립니다. 같은 JSON이 `<run_dir>/result.json`에도 저장됩니다. 출력의 경로는 모두 저장소 루트 기준 상대경로입니다. AC 항목에서 출력 tail은 실패한 명령에만 붙고, 각 명령의 전체 출력은 `log` 파일에 있습니다.
3. 결과의 `status`를 읽습니다.
   - `pending_review`: AC 통과. 리뷰로 갑니다.
   - `ac_failed`, `codex_error`, `timeout`: 재시도로 갑니다.
   - `git_state_changed`: 규칙을 어기고 Codex가 커밋, reset 또는 브랜치 전환을 했습니다(`git_state` 참고). 재시도로 갑니다. `discard`는 실행 직전 커밋으로 되돌리고 `removed_commits`를 보고합니다. 현재 브랜치가 바뀌어 `discard`가 거부되면 멈추고 보고하세요.
4. **리뷰** (`pending_review`). 결과의 `diff_stat`(파일별 `+추가 -삭제`)을 먼저 보고 `diff_file`을 읽습니다. 실행 직전 커밋 대비 전체 diff이며 새 파일도 들어 있습니다. `git diff`는 untracked 파일을 보여주지 않으므로 리뷰 근거로 쓰지 마세요. lockfile이나 생성 파일처럼 줄 단위로 볼 필요가 없는 큰 변경이 있으면 `diff_file`을 통째로 읽지 말고, `^diff --git`을 줄 번호와 함께 검색해 리뷰할 파일 구간만 읽으세요. 그런 파일은 변경량이 스텝 내용(의존성 추가 등)과 맞는지만 확인합니다. 그다음 모든 항목을 확인합니다.
   - `codex.output`이 있습니다. null이면(`codex.parse_error` 참고) Codex의 요약, 결정, blocker가 없으므로 리뷰 실패입니다.
   - 스텝 description과 일치합니다. 이후 스텝의 내용도, 관련 없는 변경도 없습니다.
   - `out_of_scope_files`의 모든 경로가 Codex의 `decisions`에서 정당화됩니다. 정당화되지 않은 범위 밖 수정은 리뷰 실패입니다. 빌드·테스트 산출물이 들어 있으면 `.gitignore` 추가를 피드백하세요. `.gitignore`에 추가된 패턴은 산출물만 덮어야 합니다. 넓은 패턴은 의도한 새 파일까지 diff와 커밋에서 숨깁니다.
   - 테스트를 약화, 스킵, 삭제하거나 통과용 특수 처리를 하지 않았습니다. 새 테스트가 실제로 그 동작을 검증합니다. 스킵이 의심되면 AC `log`에서 테스트 수를 확인하세요.
   - 디버그 흔적이 없습니다: print, 주석 처리된 코드, 지금 끝냈어야 할 TODO, 떠도는 파일.
   - `blockers`가 비어 있거나, 각 blocker가 사용자가 결정해야 할 사항입니다. 사용자가 필요한 blocker는 실행을 멈춥니다(멈춤 조건 참고). `.codex/hooks/guard.py`가 git 명령이나 위험한 명령을 막았다는 메모는 예상된 동작이지 blocker가 아닙니다. 스텝에 그 명령이 필요하지 않았는지만 확인하세요.
   - CLAUDE.md와 기존 코드 스타일을 따릅니다.

   통과 → `commit <step-id>`. 실패 → 실패한 시도로 보고 재시도로 갑니다.
5. **재시도**. `diff_file`과 실패한 AC의 `stdout_tail`/`stderr_tail`(잘렸으면 `log` 파일)을 보고 *왜* 실패했는지 파악한 뒤 `discard <step-id>`를 실행합니다(`diff_file`은 discard 후에도 남습니다). 피드백은 직접 `.harness/plans/<plan-id>/runs/feedback-<step-id>-<attempt>.md`에 한국어로 씁니다: 무엇이 틀렸는지, 다음 시도에서 무엇을 다르게 해야 하는지, 가능하면 구체적인 파일/줄 위치. 그다음 `--feedback-file <그 파일>`로 다시 실행합니다. `max_attempts`를 다 쓰면 스크립트가 거부합니다. 사용자가 요청하지 않는 한 `--force`를 넘기지 마세요.
6. `commit`이나 `discard` 뒤마다 사용자에게 한 줄로 알립니다: `스텝 2/5 커밋됨 (abc1234): <요약>` 또는 `스텝 2/5 시도 1 실패: <이유>, 재시도합니다`. 스텝 사이에 멈춰서 묻지 마세요.

### 멈춤 조건

다음 경우 멈추고 사용자에게 보고합니다.

- 한 스텝의 시도 횟수를 다 썼을 때
- Codex가 사람의 결정이 필요한 blocker를 보고했을 때
- 리뷰 결과 계획 자체가 틀렸을 때(스텝이 불가능하거나 설계가 코드와 맞지 않음)
- 하네스가 만든 것으로 볼 수 없는 변경이 working tree에 있을 때

보고 내용: 어느 스텝에서 무슨 일이 있었고, 무엇을 권하는지(계획을 고쳐 재시도, 스텝 제외, 사용자가 넘겨받기). 계획 변경은 다음 절을 따릅니다.

### 실행 중 계획 변경

`start` 이후 `plan.yaml`의 모든 변경은 처음 계획처럼 사용자의 명시적 승인이 필요합니다.

1. 현재 스텝을 먼저 끝냅니다: `commit` 또는 `discard`. `plan.yaml` 외에 다른 변경이 있으면 `replan`이 거부합니다.
2. `plan.yaml`을 고치고 `approved: false`로 바꿉니다.
3. 사용자에게 바뀌는 내용을 보여줍니다: 추가·삭제·순서 변경된 스텝, 수정된 모든 `description`과 `ac`. AC가 약해졌다면 분명히 말하세요.
4. 명시적 승인을 받으면 `approved: true`로 바꾸고 `replan`을 실행합니다. `plan.yaml`만 따로 커밋하고 `added_steps`, `removed_steps`, `changed_steps`를 보고합니다.

스크립트가 강제하는 규칙: 커밋된 스텝은 순서와 내용 그대로 맨 앞에 있어야 합니다(커밋된 작업을 고치려면 새 스텝 추가). `max_attempts`는 바꿀 수 없습니다. 고친 스텝은 이미 쓴 시도 횟수를 유지합니다(다 쓰면 `--force` 여부는 사용자가 정합니다).

### 마무리

`commit`이 `complete: true`를 돌려주면 `status`를 한 번 실행해 계획 전체를 요약합니다: 커밋된 스텝과 해시, 받아들인 범위 밖 파일, 미룬 것. `harness/<plan-id>` 브랜치는 사용자가 직접 머지합니다. 머지나 push를 하지 마세요.

### 모드: resume / status

`status`를 실행합니다. 계획이 어디까지 왔는지 짧은 목록으로 보고하고, 사용자가 계속하길 원하면 run 루프 1단계부터 이어갑니다.

## 이 스킬에서 절대 하지 않는 것

- 스텝 코드를 직접 구현하거나 고치기. 대신 계획이나 피드백을 고치세요.
- 명시적 승인 없이 `approved: true`로 바꾸기.
- `plan.yaml`을 직접 커밋하기. 계획 변경은 `replan`으로만 합니다.
- diff를 읽지 않고 `commit` 실행하기.
- 머지, rebase, push, 브랜치 전환.
