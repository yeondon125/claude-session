"""
Claude 사용량 제출 디스코드 봇
- 학생이 자기 주간 한도 초기화 요일·시각을 등록 (/리셋시각)
- 초기화 12시간 전에 DM으로 제출 알림
- 학생이 Settings > Usage의 주간 %를 제출 (/사용량, 스크린샷 선택)
- 관리자가 현황 확인 (/현황)

설정: .env.example을 .env로 복사해서 값 채우기
설치: pip install -r requirements.txt   (Python 3.10+)
실행: python claude_usage_bot.py
"""
import os
import sqlite3
import datetime as dt
from contextlib import closing
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

KST = ZoneInfo("Asia/Seoul")
TOKEN = os.environ["DISCORD_TOKEN"]
GUILD = discord.Object(id=int(os.environ["GUILD_ID"]))
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", "0"))
REMIND_BEFORE = dt.timedelta(hours=float(os.environ.get("REMIND_BEFORE_HOURS", "12")))  # 초기화 몇 시간 전에 알릴지
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
    print(f"로그인: {client.user}")


client.run(TOKEN)
