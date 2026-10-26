"""
Claude 사용량 제출 디스코드 봇
- 학생이 자기 주간 한도 초기화 요일·시각을 등록 (/리셋시각)
- 초기화 12시간 전에 DM으로 제출 알림
- 학생이 Settings > Usage의 주간 %를 제출 (/사용량, 스크린샷 선택)
- 관리자가 현황 확인 (/현황)
- 명령어 안내 (/도움말)
- Claude Code 플러그인 자동 제출 (/연동으로 토큰 발급 → POST /report)

설정: .env.example을 .env로 복사해서 값 채우기
설치: pip install -r requirements.txt   (Python 3.10+)
실행: python claude_usage_bot.py
"""
import os
import secrets
import sqlite3
import datetime as dt
from contextlib import closing
from zoneinfo import ZoneInfo

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

KST = ZoneInfo("Asia/Seoul")
TOKEN = os.environ["DISCORD_TOKEN"]
GUILD = discord.Object(id=int(os.environ["GUILD_ID"]))
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", "0"))
REMIND_BEFORE = dt.timedelta(hours=float(os.environ.get("REMIND_BEFORE_HOURS", "12")))  # 초기화 몇 시간 전에 알릴지
HTTP_PORT = int(os.environ.get("HTTP_PORT", "8765"))  # 플러그인 자동 제출을 받는 포트
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")  # 학생 PC에서 접속할 봇 주소
PLUGIN_MARKETPLACE = "yeondon125/claude-session"  # 플러그인이 들어 있는 GitHub 저장소
DB_PATH = "usage.db"
DAYS = ["월", "화", "수", "목", "금", "토", "일"]


# ---------- DB ----------
def query(sql, params=(), one=False):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        with conn:
            cur = conn.execute(sql, params)
            return cur.fetchone() if one else cur.fetchall()


def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS members(
            user_id INTEGER PRIMARY KEY, name TEXT,
            reset_weekday INTEGER, reset_hour INTEGER, reset_minute INTEGER,
            last_reminded TEXT);
        CREATE TABLE IF NOT EXISTS submissions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            cycle_end TEXT, percent INTEGER, has_screenshot INTEGER,
            submitted_at TEXT);
        CREATE TABLE IF NOT EXISTS links(
            token TEXT PRIMARY KEY, user_id INTEGER UNIQUE, name TEXT, created_at TEXT);
        """)


# ---------- 시간 계산 ----------
def next_reset(weekday, hour, minute, now=None):
    """지금 이후 가장 가까운 초기화 시각 (= 현재 주기의 끝)"""
    now = now or dt.datetime.now(KST)
    t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    t += dt.timedelta(days=(weekday - now.weekday()) % 7)
    if t <= now:
        t += dt.timedelta(days=7)
    return t


def fmt(t):
    return f"{t:%m/%d}({DAYS[t.weekday()]}) {t:%H:%M}"


def member_cycle(m, now=None):
    return next_reset(m["reset_weekday"], m["reset_hour"], m["reset_minute"], now)


# ---------- 봇 ----------
client = discord.Client(intents=discord.Intents.default())
tree = app_commands.CommandTree(client)


@tree.command(name="리셋시각", description="내 주간 한도 초기화 요일·시각 등록 (claude.ai Settings > Usage에서 확인)", guild=GUILD)
@app_commands.describe(요일="주간 한도가 초기화되는 요일", 시="0~23 (한국 시간)", 분="0~59")
@app_commands.choices(요일=[app_commands.Choice(name=d, value=i) for i, d in enumerate(DAYS)])
async def set_reset(inter: discord.Interaction, 요일: app_commands.Choice[int],
                    시: app_commands.Range[int, 0, 23], 분: app_commands.Range[int, 0, 59] = 0):
    query("""INSERT INTO members(user_id, name, reset_weekday, reset_hour, reset_minute)
             VALUES(?,?,?,?,?)
             ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,
               reset_weekday=excluded.reset_weekday, reset_hour=excluded.reset_hour,
               reset_minute=excluded.reset_minute""",
          (inter.user.id, inter.user.display_name, 요일.value, 시, 분))
    nr = next_reset(요일.value, 시, 분)
    await inter.response.send_message(
        f"등록 완료! 다음 초기화는 **{fmt(nr)}**이에요. "
        f"그 {int(REMIND_BEFORE.total_seconds() // 3600)}시간 전에 DM으로 제출 알림을 보낼게요.",
        ephemeral=True)


@tree.command(name="사용량", description="이번 주 Claude 주간 사용량(%) 제출", guild=GUILD)
@app_commands.describe(주간퍼센트="Settings > Usage의 주간 사용량 %", 스크린샷="(선택) Usage 화면 캡처")
async def submit(inter: discord.Interaction, 주간퍼센트: app_commands.Range[int, 0, 100],
                 스크린샷: discord.Attachment | None = None):
    m = query("SELECT * FROM members WHERE user_id=?", (inter.user.id,), one=True)
    if not m:
        await inter.response.send_message("먼저 `/리셋시각`으로 초기화 시각을 등록해 주세요.", ephemeral=True)
        return
    cycle = member_cycle(m)
    query("INSERT INTO submissions(user_id, cycle_end, percent, has_screenshot, submitted_at) VALUES(?,?,?,?,?)",
          (inter.user.id, cycle.isoformat(), 주간퍼센트, int(스크린샷 is not None),
           dt.datetime.now(KST).isoformat()))
    await inter.response.send_message(f"제출 완료: **{주간퍼센트}%** (주기 종료 {fmt(cycle)})", ephemeral=True)

    # 디스코드 첨부 링크는 만료되므로 관리자 채널에 파일째로 보관
    if 스크린샷 and LOG_CHANNEL_ID:
        ch = client.get_channel(LOG_CHANNEL_ID)
        if ch:
            await ch.send(f"{inter.user.display_name} · {주간퍼센트}% · 주기 종료 {fmt(cycle)}",
                          file=await 스크린샷.to_file())


@tree.command(name="내기록", description="내 최근 제출 기록 보기", guild=GUILD)
async def my_history(inter: discord.Interaction):
    rows = query("""SELECT cycle_end, percent FROM submissions WHERE user_id=?
                    GROUP BY cycle_end HAVING MAX(id) ORDER BY cycle_end DESC LIMIT 8""",
                 (inter.user.id,))
    if not rows:
        await inter.response.send_message("아직 제출 기록이 없어요.", ephemeral=True)
        return
    lines = [f"{fmt(dt.datetime.fromisoformat(r['cycle_end']))} 종료 주기: **{r['percent']}%**" for r in rows]
    await inter.response.send_message("\n".join(lines), ephemeral=True)


@tree.command(name="연동", description="Claude Code 플러그인 자동 제출용 토큰 발급 (다시 하면 이전 토큰은 무효)", guild=GUILD)
async def link_plugin(inter: discord.Interaction):
    if not PUBLIC_URL:
        await inter.response.send_message("관리자가 아직 자동 제출 주소를 설정하지 않았어요. `/사용량`으로 직접 제출해 주세요.",
                                          ephemeral=True)
        return
    token = secrets.token_urlsafe(24)
    query("""INSERT INTO links(token, user_id, name, created_at) VALUES(?,?,?,?)
             ON CONFLICT(user_id) DO UPDATE SET token=excluded.token, name=excluded.name,
               created_at=excluded.created_at""",
          (token, inter.user.id, inter.user.display_name, dt.datetime.now(KST).isoformat()))
    await inter.response.send_message(
        "Claude Code에서 아래 세 줄을 차례로 입력하세요. 그 뒤로는 Claude Code를 쓸 때마다 주간 사용량이 자동으로 제출돼요.\n"
        f"```\n/plugin marketplace add {PLUGIN_MARKETPLACE}\n"
        f"/plugin install usage-check@claude-session\n"
        f"/usage-check:setup {PUBLIC_URL} {token}\n```"
        "이 토큰은 다른 사람에게 보여주지 마세요. 잃어버리면 `/연동`을 다시 하면 돼요.",
        ephemeral=True)


def latest_percent(user_id, cycle):
    r = query("SELECT percent FROM submissions WHERE user_id=? AND cycle_end=? ORDER BY id DESC LIMIT 1",
              (user_id, cycle.isoformat()), one=True)
    return f"{r['percent']}%" if r else "미제출"


@tree.command(name="현황", description="[관리자] 전체 제출 현황", guild=GUILD)
@app_commands.default_permissions(manage_guild=True)
async def overview(inter: discord.Interaction):
    members = query("SELECT * FROM members ORDER BY name")
    if not members:
        await inter.response.send_message("등록된 학생이 없어요.", ephemeral=True)
        return
    lines = ["이름 | 직전 주기 | 현재 주기"]
    for m in members:
        cur = member_cycle(m)
        prev = cur - dt.timedelta(days=7)
        lines.append(f"{m['name']} | {latest_percent(m['user_id'], prev)} | {latest_percent(m['user_id'], cur)}")
    await inter.response.send_message("```\n" + "\n".join(lines)[:1900] + "\n```", ephemeral=True)


@tree.command(name="도움말", description="사용할 수 있는 명령어와 사용 방법 보기", guild=GUILD)
async def help_cmd(inter: discord.Interaction):
    hours = int(REMIND_BEFORE.total_seconds() // 3600)
    e = discord.Embed(title="Usage Check Bot 도움말",
                      description="claude.ai **Settings > Usage**의 주간 사용량(%)을 제출하는 봇이에요.",
                      color=0xD97757)
    e.add_field(name="/리셋시각 요일 시 [분]",
                value="내 주간 한도가 초기화되는 요일·시각을 등록해요 (한국 시간). 처음 한 번만 하면 돼요.",
                inline=False)
    e.add_field(name="/사용량 주간퍼센트 [스크린샷]",
                value="이번 주 사용량(%)을 제출해요. 스크린샷은 선택이에요.", inline=False)
    e.add_field(name="/내기록", value="최근 8주 제출 기록을 확인해요.", inline=False)
    e.add_field(name="/연동",
                value="Claude Code 플러그인을 설치해서 사용량을 자동으로 제출해요. 하면 `/리셋시각`·`/사용량`이 필요 없어요.",
                inline=False)
    e.add_field(name="/도움말", value="이 안내를 다시 보여줘요.", inline=False)
    if inter.guild and inter.user.guild_permissions.manage_guild:
        e.add_field(name="/현황 (관리자)",
                    value="학생별 직전·현재 주기 제출값을 확인해요.", inline=False)
    e.add_field(name="이용 순서",
                value=(f"1. `/리셋시각`으로 초기화 시각 등록\n"
                       f"2. 초기화 {hours}시간 전에 DM 알림이 오면 `/사용량`으로 제출\n"
                       f"3. 알림을 받으려면 서버 멤버 DM을 허용해 주세요"),
                inline=False)
    await inter.response.send_message(embed=e, ephemeral=True)


# ---------- 플러그인 자동 제출 ----------
async def handle_report(request: web.Request):
    """학생 PC의 상태줄 스크립트가 보내는 {token, weekly_percent, resets_at(유닉스 초)}"""
    try:
        body = await request.json()
        token = str(body["token"])
        percent = round(float(body["weekly_percent"]))
        resets = dt.datetime.fromtimestamp(float(body["resets_at"]), KST)
    except (ValueError, KeyError, TypeError, OverflowError):
        return web.json_response({"error": "형식이 잘못됐어요"}, status=400)
    link = query("SELECT * FROM links WHERE token=?", (token,), one=True)
    if not link:
        return web.json_response({"error": "토큰이 올바르지 않아요. 디스코드에서 /연동을 다시 해 주세요"}, status=401)
    now = dt.datetime.now(KST)
    if not 0 <= percent <= 100 or resets > now + dt.timedelta(days=8):
        return web.json_response({"error": "값이 범위를 벗어났어요"}, status=400)
    if resets <= now:
        # 초기화 직후 Claude Code가 아직 이전 주 값을 들고 있는 경우
        return web.json_response({"ok": True, "skipped": "stale"})

    resets = (resets + dt.timedelta(seconds=30)).replace(second=0, microsecond=0)
    query("""INSERT INTO members(user_id, name, reset_weekday, reset_hour, reset_minute)
             VALUES(?,?,?,?,?)
             ON CONFLICT(user_id) DO UPDATE SET reset_weekday=excluded.reset_weekday,
               reset_hour=excluded.reset_hour, reset_minute=excluded.reset_minute""",
          (link["user_id"], link["name"], resets.weekday(), resets.hour, resets.minute))
    cycle = member_cycle(query("SELECT * FROM members WHERE user_id=?", (link["user_id"],), one=True), now)
    query("INSERT INTO submissions(user_id, cycle_end, percent, has_screenshot, submitted_at) VALUES(?,?,?,?,?)",
          (link["user_id"], cycle.isoformat(), percent, 0, now.isoformat()))
    return web.json_response({"ok": True, "percent": percent, "cycle_end": cycle.isoformat()})


async def handle_check(request: web.Request):
    """플러그인 설정 직후 토큰·주소가 맞는지 확인"""
    try:
        token = str((await request.json())["token"])
    except (ValueError, KeyError, TypeError):
        return web.json_response({"error": "형식이 잘못됐어요"}, status=400)
    link = query("SELECT name FROM links WHERE token=?", (token,), one=True)
    if not link:
        return web.json_response({"error": "토큰이 올바르지 않아요. 디스코드에서 /연동을 다시 해 주세요"}, status=401)
    return web.json_response({"ok": True, "name": link["name"]})


web_runner = None


async def start_web():
    global web_runner
    app = web.Application()
    app.router.add_post("/report", handle_report)
    app.router.add_post("/check", handle_check)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, port=HTTP_PORT).start()
    web_runner = runner  # 포트 열기에 실패하면 다음 on_ready에서 다시 시도
    print(f"자동 제출 수신 대기: 포트 {HTTP_PORT}")


@tasks.loop(minutes=10)
async def reminder():
    now = dt.datetime.now(KST)
    for m in query("SELECT * FROM members"):
        cycle = member_cycle(m, now)
        key = cycle.isoformat()
        if now < cycle - REMIND_BEFORE or m["last_reminded"] == key:
            continue
        query("UPDATE members SET last_reminded=? WHERE user_id=?", (key, m["user_id"]))
        if latest_percent(m["user_id"], cycle) != "미제출":
            continue
        try:
            user = await client.fetch_user(m["user_id"])
            await user.send(f"주간 한도가 **{fmt(cycle)}**에 초기화돼요. 그 전에 `/사용량`으로 이번 주 사용량을 제출해 주세요!")
        except discord.HTTPException:
            pass  # DM을 막아 둔 학생


@client.event
async def on_ready():
    init_db()
    await tree.sync(guild=GUILD)
    if not reminder.is_running():
        reminder.start()
    if web_runner is None:
        await start_web()
    print(f"로그인: {client.user}")


client.run(TOKEN)
