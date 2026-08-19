import json
import logging
import os
import random
import re
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_FILE = Path(__file__).parent / "data.json"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

LINE_RE = re.compile(r"^(.+?)\s*[-–—:=]\s*(.+)$")
EXAMPLE_RE = re.compile(r"^(.*?)\s*\(([^)]+)\)\s*$")


# ---------------- storage ----------------

def load_data() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {}


def save_data(data: dict) -> None:
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def user_state(data: dict, chat_id: str) -> dict:
    if chat_id not in data:
        data[chat_id] = {"lessons": [], "session": None}
    return data[chat_id]


# ---------------- parsing ----------------

def parse_lesson_text(raw: str):
    words = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = LINE_RE.match(line)
        if not m:
            continue
        en = m.group(1).strip()
        rest = m.group(2).strip()
        example = ""
        ex = EXAMPLE_RE.match(rest)
        ru = rest
        if ex:
            ru = ex.group(1).strip()
            example = ex.group(2).strip()
        if en and ru:
            words.append({
                "en": en, "ru": ru, "example": example,
                "seen": 0, "correct": 0, "wrong": 0, "box": 1,
            })
    return words


# ---------------- handlers ----------------

WELCOME = (
    "Привет! Я помогу тебе закреплять слова из уроков английского.\n\n"
    "1. Перешли мне (или просто вставь) текст задания от преподавателя, "
    "формат строки: `word - перевод` или `word - перевод (пример)`.\n"
    "2. Я сам найду все пары слово–перевод и сохраню как новый урок.\n"
    "3. Дальше тренируйся:\n"
    "   /learn — карточки\n"
    "   /quiz — тест с выбором ответа\n"
    "   /lessons — список уроков\n"
    "   /stats — статистика прогресса\n"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, parse_mode="Markdown")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Any plain/forwarded text is treated as lesson material to parse."""
    chat_id = str(update.effective_chat.id)
    data = load_data()
    st = user_state(data, chat_id)

    text = update.message.text or ""
    words = parse_lesson_text(text)

    if not words:
        await update.message.reply_text(
            "Не нашёл слов в формате `word - перевод`. Пришли текст задания "
            "построчно, например:\n\njourney - путешествие\nluggage - багаж",
            parse_mode="Markdown",
        )
        return

    lesson_name = f"Урок {len(st['lessons']) + 1} — {time.strftime('%d.%m.%Y')}"
    st["lessons"].append({"name": lesson_name, "created": time.time(), "words": words})
    save_data(data)

    await update.message.reply_text(
        f"Добавил урок «{lesson_name}»: {len(words)} слов ✅\n"
        f"Список: {', '.join(w['en'] for w in words)}\n\n"
        f"Начать тренировку: /learn или /quiz"
    )


async def lessons_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    st = user_state(data, chat_id)
    if not st["lessons"]:
        await update.message.reply_text("Уроков пока нет. Пришли текст задания от преподавателя.")
        return
    lines = [f"{i+1}. {l['name']} — {len(l['words'])} слов" for i, l in enumerate(st["lessons"])]
    await update.message.reply_text("\n".join(lines))


def all_words(st: dict):
    return [w for l in st["lessons"] for w in l["words"]]


# ---- flashcards ----

async def learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    st = user_state(data, chat_id)
    words = all_words(st)
    if not words:
        await update.message.reply_text("Сначала добавь урок — пришли текст задания.")
        return
    weighted = []
    for w in words:
        weight = max(1, 5 - w.get("box", 1))
        weighted += [w] * weight
    random.shuffle(weighted)
    st["session"] = {"mode": "learn", "queue": weighted[:15], "index": 0}
    save_data(data)
    await send_flashcard(update.effective_chat.id, context, st)


async def send_flashcard(chat_id, context, st):
    sess = st["session"]
    if sess["index"] >= len(sess["queue"]):
        await context.bot.send_message(chat_id, "Карточки закончились 🎉 Запусти /learn ещё раз для новой партии.")
        return
    w = sess["queue"][sess["index"]]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Показать перевод 👁", callback_data="reveal")]])
    await context.bot.send_message(chat_id, f"*{w['en']}*", parse_mode="Markdown", reply_markup=kb)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(query.message.chat.id)
    data = load_data()
    st = user_state(data, chat_id)
    sess = st.get("session")
    await query.answer()

    if not sess:
        return

    if sess["mode"] == "learn":
        w = sess["queue"][sess["index"]]
        if query.data == "reveal":
            text = f"*{w['en']}* → {w['ru']}"
            if w.get("example"):
                text += f"\n_{w['example']}_"
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("Не знал 😕", callback_data="know_no"),
                InlineKeyboardButton("Знал ✓", callback_data="know_yes"),
            ]])
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        elif query.data in ("know_yes", "know_no"):
            w["seen"] += 1
            if query.data == "know_yes":
                w["correct"] += 1
                w["box"] = min(5, w.get("box", 1) + 1)
            else:
                w["wrong"] += 1
                w["box"] = 1
            sess["index"] += 1
            save_data(data)
            await send_flashcard(int(chat_id), context, st)
        return

    if sess["mode"] == "quiz":
        w = sess["queue"][sess["index"]]
        chosen = query.data.replace("ans::", "", 1)
        correct = w["ru"]
        w["seen"] += 1
        if chosen == correct:
            w["correct"] += 1
            w["box"] = min(5, w.get("box", 1) + 1)
            sess["score"] += 1
            result = f"✅ Верно! {w['en']} = {w['ru']}"
        else:
            w["wrong"] += 1
            w["box"] = 1
            result = f"❌ Неверно. {w['en']} = {w['ru']} (ты выбрал: {chosen})"
        sess["index"] += 1
        save_data(data)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Дальше →", callback_data="quiz_next")]])
        await query.edit_message_text(result, reply_markup=kb)
        return

    if query.data == "quiz_next":
        await send_quiz_question(int(chat_id), context, st)


# ---- quiz ----

async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    st = user_state(data, chat_id)
    words = all_words(st)
    if len(words) < 2:
        await update.message.reply_text("Нужно минимум 2 слова для теста. Добавь ещё урок.")
        return
    q = words[:]
    random.shuffle(q)
    st["session"] = {"mode": "quiz", "queue": q[:10], "index": 0, "score": 0}
    save_data(data)
    await send_quiz_question(update.effective_chat.id, context, st)


async def send_quiz_question(chat_id, context, st):
    sess = st["session"]
    if sess["index"] >= len(sess["queue"]):
        total = len(sess["queue"])
        await context.bot.send_message(chat_id, f"Тест завершён 🎉 Результат: {sess['score']}/{total}\nЗапусти /quiz ещё раз.")
        return
    words = all_words(st)
    w = sess["queue"][sess["index"]]
    distractors = [x["ru"] for x in random.sample([x for x in words if x is not w], min(3, len(words) - 1))]
    options = distractors + [w["ru"]]
    random.shuffle(options)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(opt, callback_data=f"ans::{opt}")] for opt in options])
    await context.bot.send_message(chat_id, f"Перевод слова *{w['en']}*?", parse_mode="Markdown", reply_markup=kb)


# ---- stats ----

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    data = load_data()
    st = user_state(data, chat_id)
    words = all_words(st)
    if not words:
        await update.message.reply_text("Пока нет данных — добавь урок и потренируйся.")
        return
    seen = sum(1 for w in words if w["seen"] > 0)
    mastered = sum(1 for w in words if w.get("box", 1) >= 4)
    struggling = [w for w in words if w["wrong"] > w["correct"] and w["seen"] > 0]
    text = (
        f"Всего слов: {len(words)}\n"
        f"Повторено: {seen}\n"
        f"Выучено хорошо: {mastered}\n"
        f"Уроков: {len(st['lessons'])}\n"
    )
    if struggling:
        text += "\nСтоит повторить:\n" + "\n".join(f"• {w['en']} — {w['ru']}" for w in struggling[:15])
    await update.message.reply_text(text)


def main():
    if not BOT_TOKEN:
        raise SystemExit("Установи переменную окружения BOT_TOKEN (токен от @BotFather)")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("lessons", lessons_cmd))
    app.add_handler(CommandHandler("learn", learn))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
