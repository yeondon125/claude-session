#!/usr/bin/env node
// Claude Code 상태줄 스크립트
// - Claude Code가 stdin으로 주는 rate_limits.seven_day(주간 사용량 %, 초기화 시각)를 봇에 보낸다
// - 값이 바뀌었을 때만 보내고, 전송은 별도 프로세스로 넘겨 상태줄 표시를 늦추지 않는다
// - 기존 상태줄(chain)이 있으면 같은 입력으로 실행해서 그 출력을 그대로 보여준다
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn, spawnSync } = require('child_process');

const CLAUDE_DIR = process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), '.claude');
const DATA_DIR = path.join(CLAUDE_DIR, 'usage-check');
const CONFIG_PATH = path.join(DATA_DIR, 'config.json');
const STATE_PATH = path.join(DATA_DIR, 'state.json');
const RETRY_AFTER_MS = 60 * 1000; // 전송 실패 후 다시 시도하기까지
const DAYS = ['일', '월', '화', '수', '목', '금', '토'];

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return null;
  }
}

function writeJson(file, value) {
  try {
    fs.writeFileSync(file, JSON.stringify(value));
  } catch {
    // 상태 저장 실패는 다음 틱에 다시 보내면 그만
  }
}

// ---------- 전송 (분리된 자식 프로세스) ----------
async function send(payloadJson) {
  const config = readJson(CONFIG_PATH);
  const payload = JSON.parse(payloadJson);
  const state = readJson(STATE_PATH) || {};
  try {
    const res = await fetch(`${config.url}/report`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: config.token, ...payload }),
      signal: AbortSignal.timeout(10000),
    });
    const body = await res.json().catch(() => ({}));
    if (res.ok) {
      writeJson(STATE_PATH, { ...payload, sent_at: Date.now() });
    } else {
      writeJson(STATE_PATH, { ...state, failed_at: Date.now(), error: body.error || `HTTP ${res.status}` });
    }
  } catch {
    writeJson(STATE_PATH, { ...state, failed_at: Date.now(), error: '봇 서버에 연결할 수 없음' });
  }
}

function maybeSend(sevenDay) {
  const config = readJson(CONFIG_PATH);
  if (!config || !config.url || !config.token) return;
  const payload = {
    weekly_percent: Math.round(sevenDay.used_percentage),
    resets_at: Math.round(sevenDay.resets_at),
  };
  const state = readJson(STATE_PATH) || {};
  // 초기화 시각은 초 단위로 흔들릴 수 있어서 2분 이내 차이는 같은 주기로 본다
  const unchanged = state.weekly_percent === payload.weekly_percent && Math.abs(state.resets_at - payload.resets_at) < 120;
  if (unchanged && state.sent_at) return;
  if (state.failed_at && Date.now() - state.failed_at < RETRY_AFTER_MS) return;

  spawn(process.execPath, [__filename, '--send', JSON.stringify(payload)], {
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
  }).unref();
}

// ---------- 표시 ----------
function findBash() {
  const candidates = [process.env.CLAUDE_CODE_GIT_BASH_PATH];
  if (process.platform === 'win32') {
    candidates.push('C:\\Program Files\\Git\\bin\\bash.exe', 'C:\\Program Files (x86)\\Git\\bin\\bash.exe');
  }
  return candidates.find((p) => p && fs.existsSync(p)) || 'bash';
}

function runChain(command, input) {
  const opts = { input, encoding: 'utf8', timeout: 5000, windowsHide: true };
  let r = spawnSync(findBash(), ['-c', command], opts);
  if (r.error && r.error.code === 'ENOENT') r = spawnSync(command, { ...opts, shell: true });
  return r.stdout || '';
}

function ownLine(sevenDay) {
  if (!sevenDay) return '주간 사용량: 정보 없음 (Pro/Max 로그인 후 첫 응답부터 표시)';
  const t = new Date(sevenDay.resets_at * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  const when = `${pad(t.getMonth() + 1)}/${pad(t.getDate())}(${DAYS[t.getDay()]}) ${pad(t.getHours())}:${pad(t.getMinutes())}`;
  return `주간 ${Math.round(sevenDay.used_percentage)}% · 초기화 ${when}`;
}

function failure() {
  const state = readJson(STATE_PATH) || {};
  return state.failed_at ? `사용량 제출 실패: ${state.error}` : '';
}

function main() {
  let raw = '';
  try {
    raw = fs.readFileSync(0, 'utf8');
  } catch {
    // stdin 없음
  }
  let input = {};
  try {
    input = JSON.parse(raw);
  } catch {
    // 상태줄 입력이 아니면 표시만 한다
  }

  const sd = input.rate_limits && input.rate_limits.seven_day;
  const sevenDay = sd && Number.isFinite(sd.used_percentage) && Number.isFinite(sd.resets_at) ? sd : null;
  if (sevenDay) maybeSend(sevenDay);

  const config = readJson(CONFIG_PATH) || {};
  const warn = failure();
  if (config.chain) {
    const out = runChain(config.chain, raw).replace(/\n+$/, '');
    process.stdout.write(warn ? `${out}\n${warn}` : out);
  } else {
    process.stdout.write(warn ? `${ownLine(sevenDay)} · ${warn}` : ownLine(sevenDay));
  }
}

if (process.argv[2] === '--send') {
  send(process.argv[3]);
} else {
  main();
}
