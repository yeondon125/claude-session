---
description: Usage Check Bot 자동 제출 연동 (디스코드 /연동에서 받은 주소와 토큰)
argument-hint: <봇 주소> <토큰>
allowed-tools: Bash
---

디스코드 Usage Check Bot과 이 컴퓨터의 Claude Code를 연동한다. 사용자가 준 인자: `$ARGUMENTS`

1. 인자가 `<봇 주소> <토큰>` 두 개가 아니면 실행하지 말고, 디스코드에서 `/연동`을 하면 나오는 `/usage-check:setup ...` 줄을 그대로 붙여 넣으라고 안내한다.
2. `node --version`으로 Node.js 18 이상인지 확인한다. 없거나 18 미만이면 https://nodejs.org 에서 LTS를 설치한 뒤 Claude Code를 다시 켜고 이 명령을 다시 실행하라고 안내하고 멈춘다.
3. 아래 명령을 그대로 실행한다. 스크립트가 설정 파일을 직접 고치므로 settings.json을 손으로 편집하지 않는다.

   ```bash
   node "${CLAUDE_PLUGIN_ROOT}/scripts/setup.js" $ARGUMENTS
   ```

4. 결과를 한국어로 짧게 전한다.
   - 성공하면 스크립트 출력을 전하고, 메시지를 한 번 보낸 뒤 상태줄에 `주간 N%`가 보이면 제출된 것이라고 알려 준다. 기존 상태줄을 쓰던 경우에는 그 상태줄이 그대로 보이고, 제출이 실패할 때만 아래에 경고 줄이 생긴다고 알려 준다.
   - 실패하면 스크립트가 출력한 이유를 그대로 전한다. 토큰 오류면 디스코드에서 `/연동`을 다시 하라고, 연결 오류면 관리자에게 봇 서버가 켜져 있는지 물어보라고 안내한다.
5. 토큰을 응답에 다시 적지 않는다.
