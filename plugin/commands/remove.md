---
description: Usage Check Bot 자동 제출 연동 해제 (상태줄을 원래대로 복원)
allowed-tools: Bash
---

아래 명령을 그대로 실행하고, 출력 내용을 한국어로 짧게 전한다. 앞으로는 디스코드에서 `/사용량`으로 직접 제출하면 된다고 알려 준다.

```bash
node "${CLAUDE_PLUGIN_ROOT}/scripts/setup.js" --remove
```
