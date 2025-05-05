import asyncio
import os
import json
import datetime
import requests
from fake_useragent import UserAgent
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, Update
from aiogram.exceptions import TelegramRetryAfter  # Correct import for TelegramRetryAfter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Filter as F  # Correct import for F (filtering)

from aiohttp import web

# --- Load environment ---
load_dotenv()
BOT_TOKEN    = os.getenv("BOT_TOKEN")
CACHE_FILE   = os.getenv("CACHE_FILE", "group_cache.json")
ITEMS_PER_PAGE = int(os.getenv("ITEMS_PER_PAGE", 5))
WEBHOOK_URL  = os.getenv("WEBHOOK_URL")  # e.g. https://your-app.up.railway.app
PORT         = int(os.getenv("PORT", 8000))

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# --- FSM state ---
class Form(StatesGroup):
    group_link = State()

# --- Bell intervals ---
BELLS_INTERVALS = [
    ("07:05", "07:50"), ("08:00", "08:45"), ("08:55", "09:40"),
    ("09:50", "10:35"), ("10:45", "11:30"), ("11:40", "12:25"),
    ("12:45", "13:30"), ("13:40", "14:25"), ("14:35", "15:20"),
]

# --- Cache helpers ---
def is_cache_stale():
    if not os.path.exists(CACHE_FILE):
        return True
    data = json.load(open(CACHE_FILE, encoding="utf-8"))
    last = datetime.datetime.fromisoformat(data["last_updated"])
    return (datetime.datetime.now() - last).days >= 1

def fetch_groups():
    ua = UserAgent()
    r = requests.get("https://zsm1.bydgoszcz.pl/strony/plan/", headers={"User-Agent": ua.random})
    soup = BeautifulSoup(r.text, "html.parser")
    nav = soup.find("nav", class_="nav-menu")
    out = []
    for a in nav.find_all("a"):
        box = a.find("div", class_="box")
        if box:
            out.append({
                "group_title": box.text.strip(),
                "group_link": f"https://zsm1.bydgoszcz.pl/strony/plan/{a['href']}"
            })
    return out

def get_groups():
    if is_cache_stale():
        groups = fetch_groups()
        json.dump({
            "last_updated": datetime.datetime.now().isoformat(),
            "groups": groups
        }, open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return groups
    return json.load(open(CACHE_FILE, encoding="utf-8"))["groups"]

# --- Parse schedule table ---
def parse_schedule_table(url: str):
    r = requests.get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    tbl = soup.select_one("table.tabela")
    if not tbl:
        return None, "Не вдалося знайти розклад."
    headers = [th.get_text(strip=True) for th in tbl.select("tr:first-child th")]
    rows = []
    for tr in tbl.select("tr")[1:]:
        rows.append([td.get_text(" ", strip=True) for td in tr.find_all("td")])
    return (headers, rows), None

# --- Inline keyboards ---
def gen_group_kb(page: int = 0):
    groups = get_groups()
    start, end = page * ITEMS_PER_PAGE, (page + 1) * ITEMS_PER_PAGE
    kb = []
    for g in groups[start:end]:
        kb.append([InlineKeyboardButton(text=g["group_title"], callback_data=f"set|{g['group_link']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"pg|{page-1}"))
    if end < len(groups):
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"pg|{page+1}"))
    if nav:
        kb.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=kb)

def gen_days_kb(link: str):
    days = ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek"]
    kb = [[InlineKeyboardButton(text=day, callback_data=f"day|{i}|{link}")] for i, day in enumerate(days)]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- Handlers ---
@dp.message(types.F.CommandStart())
async def cmd_start(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    if data.get("group_link"):
        await msg.answer("Групу вибрано. Використайте /bells, /schedule, /today, /current, /profile, /help")
    else:
        await msg.answer("Ласкаво просимо! Оберіть групу:", reply_markup=gen_group_kb(0))
        await state.clear()

@dp.message(F.text.in_({"/help","/commands"}))
async def cmd_help(msg: types.Message):
    await msg.answer(
        "Команди:\n"
        "/setgroup — змінити групу\n"
        "/bells — дзвінки\n"
        "/schedule — вибір дня\n"
        "/today — сьогодні\n"
        "/current — зараз\n"
        "/profile — профіль\n"
        "/help — ця довідка"
    )

@dp.message(F.text == "/profile")
async def cmd_profile(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    link = data.get("group_link")
    await msg.answer(link or "Групу не встановлено. /setgroup")

@dp.message(F.text == "/setgroup")
async def cmd_setgroup(msg: types.Message):
    await msg.answer("Оберіть групу:", reply_markup=gen_group_kb(0))

@dp.callback_query(F.data.startswith("pg|"))
async def cb_pg(cb: types.CallbackQuery):
    page = int(cb.data.split("|")[1])
    await cb.message.edit_reply_markup(gen_group_kb(page))
    await cb.answer()

@dp.callback_query(F.data.startswith("set|"))
async def cb_set(cb: types.CallbackQuery, state: FSMContext):
    link = cb.data.split("|",1)[1]
    await state.update_data(group_link=link)
    await cb.message.answer("✅ Групу встановлено!")
    await cb.answer()

@dp.message(F.text == "/bells")
async def cmd_bells(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("group_link"):
        return await msg.answer("Спочатку /setgroup")
    lines = [f"{i+1}. {s[0]}–{s[1]}" for i,s in enumerate(BELLS_INTERVALS)]
    await msg.answer("Розклад дзвінків:\n" + "\n".join(lines))

@dp.message(F.text == "/schedule")
async def cmd_schedule(msg: types.Message, state: FSMContext):
    data = await state.get_data(); link = data.get("group_link")
    if not link:
        return await msg.answer("Спочатку /setgroup")
    parsed, err = parse_schedule_table(link)
    if err:
        return await msg.answer(err)
    await msg.answer("Оберіть день:", reply_markup=gen_days_kb(link))

@dp.callback_query(F.data.startswith("day|"))
async def cb_day(cb: types.CallbackQuery):
    _, di, link = cb.data.split("|",2); d = int(di)
    parsed, err = parse_schedule_table(link)
    if err:
        return await cb.message.answer(err)
    headers, rows = parsed; col = d+2
    day = headers[col] if col < len(headers) else None
    out = [f"📅 {day}:"]
    for r in rows:
        if col < len(r) and r[col].strip():
            out.append(f"{r[1]} → {r[col]}")
    if len(out)==1: out.append("Немає занять.")
    await cb.message.answer("\n".join(out))
    await cb.answer()

@dp.message(F.text == "/today")
async def cmd_today(msg: types.Message, state: FSMContext):
    data = await state.get_data(); link = data.get("group_link")
    if not link:
        return await msg.answer("Спочатку /setgroup")
    parsed, err = parse_schedule_table(link)
    if err:
        return await msg.answer(err)
    headers, rows = parsed
    wd = datetime.datetime.now().weekday(); col = wd+2
    day = headers[col] if col < len(headers) else None
    out = [f"📆 {day}:"]
    for r in rows:
        if col < len(r) and r[col].strip():
            out.append(f"{r[1]} → {r[col]}")
    if len(out)==1: out.append("Немає занять.")
    await msg.answer("\n".join(out))

@dp.message(F.text == "/current")
async def cmd_current(msg: types.Message, state: FSMContext):
    data = await state.get_data(); link = data.get("group_link")
    if not link:
        return await msg.answer("Спочатку /setgroup")
    now = datetime.datetime.now().time(); period=None
    for i,(start,end) in enumerate(BELLS_INTERVALS):
        st = datetime.datetime.strptime(start, "%H:%M").time()
        en = datetime.datetime.strptime(end, "%H:%M").time()
        if st <= now <= en:
            period = i; break
    if period is None:
        return await msg.answer("Зараз перерва або поза розкладом.")
    parsed, err = parse_schedule_table(link)
    if err:
        return await msg.answer(err)
    headers, rows = parsed; wd = datetime.datetime.now().weekday(); col = wd+2
    if col >= len(headers):
        return await msg.answer("Сьогодні вихідний.")
    lesson = ""
    for r in rows:
        if r and r[0].isdigit() and int(r[0])==period+1:
            lesson = r[col].strip(); break
    if not lesson:
        return await msg.answer("Немає уроку зараз.")
    await msg.answer(f"Зараз {period+1}-й урок:\n{lesson}")

# --- Webhook setup with retry ---
async def set_webhook_with_retry():
    info = await bot.get_webhook_info()
    if info.url and info.url.endswith("/webhook"):
        return
    try:
        await bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        await set_webhook_with_retry()

async def on_startup(app):
    await set_webhook_with_retry()
    # register commands
    cmds = [
        BotCommand(command="/start",    description="Головне меню"),
        BotCommand(command="/setgroup", description="Змінити групу"),
        BotCommand(command="/bells",    description="Розклад дзвінків"),
        BotCommand(command="/schedule", description="Вибрати день"),
        BotCommand(command="/today",    description="Розклад на сьогодні"),
        BotCommand(command="/current",  description="Який зараз урок"),
        BotCommand(command="/profile",  description="Ваш профіль"),
        BotCommand(command="/help",     description="Допомога"),
    ]
    await bot.set_my_commands(cmds)

async def handle_webhook(request):
    data = await request.json()
    update = Update(**data)
    await dp.feed_update(bot, update)
    return web.Response()

app = web.Application()
app.router.add_post("/webhook", handle_webhook)
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, port=PORT)