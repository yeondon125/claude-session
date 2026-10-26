#!/usr/bin/env node
// 설치:  node setup.js <봇 주소> <토큰>
// 제거:  node setup.js --remove
// - reporter.js를 ~/.claude/usage-check/에 복사 (플러그인 업데이트로 경로가 바뀌어도 상태줄이 깨지지 않게)
// - settings.json의 statusLine을 reporter.js로 바꾸고, 기존 상태줄은 chain으로 보존
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const CLAUDE_DIR = process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), '.claude');
const DATA_DIR = path.join(CLAUDE_DIR, 'usage-check');
const CONFIG_PATH = path.join(DATA_DIR, 'config.json');
const STATE_PATH = path.join(DATA_DIR, 'state.json');
const SETTINGS_PATH = path.join(CLAUDE_DIR, 'settings.json');
const REPORTER_PATH = path.join(DATA_DIR, 'reporter.js');
const COMMAND = `node "${REPORTER_PATH.replace(/\\/g, '/')}"`;

function fail(msg) {
  console.error(`실패: ${msg}`);
  process.exit(1);
}

function readJson(file, fallback) {
  if (!fs.existsSync(file)) return fallback;
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    fail(`${file}을 읽을 수 없어요 (${e.message})`);
  }
}

function saveSettings(settings) {
  if (fs.existsSync(SETTINGS_PATH)) fs.copyFileSync(SETTINGS_PATH, `${SETTINGS_PATH}.usage-check.bak`);
  fs.writeFileSync(SETTINGS_PATH, JSON.stringify(settings, null, 2) + '\n');
}

function isOurs(statusLine) {
  return Boolean(statusLine && typeof statusLine.command === 'string' && statusLine.command.includes('usage-check'));
}

async function install(url, token) {
  url = (url || '').replace(/\/+$/, '');
  if (!/^https?:\/\/\S+$/.test(url) || !token) {
    fail('사용법: /usage-check:setup <봇 주소> <토큰>  (디스코드에서 /연동을 하면 그대로 복사할 수 있어요)');
  }

  let res;
  try {
    res = await fetch(`${url}/check`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
      signal: AbortSignal.timeout(10000),
    });
  } catch (e) {
    fail(`봇 서버(${url})에 연결할 수 없어요: ${e.message}`);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) fail(body.error || `봇 서버 응답 HTTP ${res.status}`);

  const settings = readJson(SETTINGS_PATH, {});
  const oldConfig = readJson(CONFIG_PATH, {});
  // 다시 설치하는 경우 이미 우리 상태줄이므로 예전에 보존해 둔 상태줄을 유지
  const previous = isOurs(settings.statusLine) ? oldConfig.previous || null : settings.statusLine || null;
  const chain = (previous && previous.command) || null;

  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.copyFileSync(path.join(__dirname, 'reporter.js'), REPORTER_PATH);
  fs.writeFileSync(CONFIG_PATH, JSON.stringify({ url, token, chain, previous }, null, 2));
  fs.rmSync(STATE_PATH, { force: true });
  settings.statusLine = { type: 'command', command: COMMAND };
  saveSettings(settings);

  console.log(`연동 완료: ${body.name}`);
  console.log(chain ? `기존 상태줄은 그대로 보여요: ${chain}` : '상태줄에 주간 사용량이 표시돼요.');
  console.log('Claude Code에서 메시지를 한 번 보내면 사용량이 처음으로 제출돼요.');
}

function remove() {
  const settings = readJson(SETTINGS_PATH, {});
  const config = readJson(CONFIG_PATH, {});
  if (isOurs(settings.statusLine)) {
    if (config.previous) settings.statusLine = config.previous;
    else delete settings.statusLine;
    saveSettings(settings);
  }
  fs.rmSync(DATA_DIR, { recursive: true, force: true });
  console.log(config.chain ? `제거 완료. 상태줄을 원래대로 돌렸어요: ${config.chain}` : '제거 완료.');
}

if (process.argv[2] === '--remove') remove();
else install(process.argv[2], process.argv[3]);
