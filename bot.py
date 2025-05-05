import asyncio
import os
import json
import datetime
import requests
from fake_useragent import UserAgent
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

load_dotenv()

# --- Конфігурація ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CACHE_FILE = os.getenv("CACHE_FILE")
ITEMS_PER_PAGE = os.getenv("ITEMS_PER_PAGE")

# --- Ініціалізація ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- Стани для збереження групи ---
class Form(StatesGroup):
    group_link = State()

# --- Розклад дзвінків (інтервали) ---
BELLS_INTERVALS = [
    ("07:05", "07:50"),
    ("08:00", "08:45"),
    ("08:55", "09:40"),
    ("09:50", "10:35"),
    ("10:45", "11:30"),
    ("11:40", "12:25"),
    ("12:45", "13:30"),
    ("13:40", "14:25"),
    ("14:35", "15:20"),
]

# --- Допоміжні функції: кеш груп ---
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

# --- Парсер таблиці розкладу ---
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

# --- Inline клавіатури ---
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

# --- Хендлери ---
@dp.message(F.text == "/start")
async def cmd_start(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    if data.get("group_link"):
        await msg.answer(
            "Привіт! Групу вибрано.\n"
            "Доступні:\n"
            "/bells  /schedule  /today  /current  /profile  /help"
        )
    else:
        await msg.answer("Ласкаво просимо! Оберіть групу:", reply_markup=gen_group_kb(0))
        await state.clear()

@dp.message(F.text.in_({"/help", "/commands"}))
async def cmd_help(msg: types.Message):
    await msg.answer(
        "Команди:\n"
        "/setgroup — змінити групу\n"
        "/bells — дзвінки\n"
        "/schedule — вибір дня\n"
        "/today — сьогодні\n"
        "/current — зараз\n"
        "/profile — профіль\n"
        "/help — це"
    )

@dp.message(F.text == "/profile")
async def cmd_profile(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    link = data.get("group_link")
    if link:
        await msg.answer(f"Ваша група: {link}")
    else:
        await msg.answer("Не встановлено. /setgroup")

@dp.message(F.text == "/setgroup")
async def cmd_setgroup(msg: types.Message):
    await msg.answer("Оберіть групу:", reply_markup=gen_group_kb(0))

@dp.callback_query(F.data.startswith("pg|"))
async def cb_pg(cb: types.CallbackQuery):
    page = int(cb.data.split("|")[1])
    await cb.message.edit_reply_markup(reply_markup=gen_group_kb(page))
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
        await msg.answer("Спочатку /setgroup"); return
    await msg.answer("Розклад дзвінків:\n" + "\n".join(f"{i+1}. {s[0]}–{s[1]}" for i,s in enumerate(BELLS_INTERVALS)))

@dp.message(F.text == "/schedule")
async def cmd_schedule(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    link = data.get("group_link")
    if not link:
        await msg.answer("Спочатку /setgroup"); return
    parsed, err = parse_schedule_table(link)
    if err:
        await msg.answer(err); return
    await msg.answer("Оберіть день:", reply_markup=gen_days_kb(link))

@dp.callback_query(F.data.startswith("day|"))
async def cb_day(cb: types.CallbackQuery):
    _, di, link = cb.data.split("|",2)
    d = int(di)
    parsed, err = parse_schedule_table(link)
    if err:
        await cb.message.answer(err)
    else:
        headers, rows = parsed
        col = d+2
        day = headers[col] if col < len(headers) else None
        text = [f"📅 {day}:"]
        for r in rows:
            if col < len(r) and r[col].strip():
                text.append(f"{r[1]} → {r[col]}")
        if len(text)==1: text.append("Немає занять.")
        await cb.message.answer("\n".join(text))
    await cb.answer()

@dp.message(F.text == "/today")
async def cmd_today(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    link = data.get("group_link")
    if not link:
        await msg.answer("Спочатку /setgroup"); return
    parsed, err = parse_schedule_table(link)
    if err:
        await msg.answer(err); return
    headers, rows = parsed
    wd = datetime.datetime.now().weekday()
    col = wd+2
    day = headers[col] if col < len(headers) else None
    text = [f"📆 {day}:"]
    for r in rows:
        if col < len(r) and r[col].strip():
            text.append(f"{r[1]} → {r[col]}")
    if len(text)==1: text.append("Немає занять.")
    await msg.answer("\n".join(text))

@dp.message(F.text == "/current")
async def cmd_current(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    link = data.get("group_link")
    if not link:
        await msg.answer("Спочатку /setgroup"); return
    # знайти теперешній дзвінок
    now = datetime.datetime.now().time()
    period = None
    for i, (start, end) in enumerate(BELLS_INTERVALS):
        st = datetime.datetime.strptime(start, "%H:%M").time()
        en = datetime.datetime.strptime(end, "%H:%M").time()
        if st <= now <= en:
            period = i
            break
    if period is None:
        await msg.answer("Зараз перерва або поза розкладом."); return

    parsed, err = parse_schedule_table(link)
    if err:
        await msg.answer(err); return
    headers, rows = parsed
    wd = datetime.datetime.now().weekday()
    col = wd+2
    if col >= len(headers):
        await msg.answer("Сьогодні вихідний."); return

    # вивести урок
    lesson = ""
    for r in rows:
        if r and r[0].isdigit() and int(r[0])==period+1:
            lesson = r[col].strip()
            break
    if not lesson:
        await msg.answer("Немає уроку зараз.")
    else:
        await msg.answer(f"Зараз ({period+1} урок):\n{lesson}")

# --- Меню команд ---
async def set_commands():
    cmds = [
        BotCommand(command="/start", description="Головне меню"),
        BotCommand(command="/setgroup", description="Змінити групу"),
        BotCommand(command="/bells", description="Розклад дзвінків"),
        BotCommand(command="/schedule", description="Вибрати день розкладу"),
        BotCommand(command="/today", description="Розклад на сьогодні"),
        BotCommand(command="/current", description="Який зараз урок"),
        BotCommand(command="/profile", description="Ваш профіль"),
        BotCommand(command="/help", description="Допомога"),
    ]
    await bot.set_my_commands(cmds)

async def main():
    await set_commands()
    print("✅ Бот запущений")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())