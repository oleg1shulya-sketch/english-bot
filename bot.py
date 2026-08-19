"""
English Trainer Bot
-------------------
Пересылай боту сообщения преподавателя прямо во время/после урока.
Бот сам распознаёт тип задания и превращает его в тренировку:
  • дриллы     I am happy – Am I happy? – I am not happy.
  • пропуски   1. I ... in the theatre.  (am/is/are)
  • перевод    1. Я в парке.  → I am in the park.
  • словарь    Abroad / On holiday / In bed
Команды:
  /start    — инструкция
  /practice — смешанная тренировка по всему пройденному
  /drill    — только трансформации (вопрос/отрицание)
  /gaps     — только пропуски am/is/are
  /translate— только перевод с русского
  /vocab    — карточки со словами
  /lessons  — список уроков
  /stats    — прогресс
  /reset    — сбросить текущую тренировку
"""
import json
import logging
import os
import random
import time
from pathlib import Path

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from lessons import answers_match, parse_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_FILE = Path(os.environ.get("DATA_DIR", Path(__file__).parent)) / "data.json"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

TYPE_NAMES = {
    "drill": "трансформации",
    "gapfill": "пропуски am/is/are",
    "translate": "перевод с русского",
    "vocab": "словарь",
}


# ------------------------------------------------------------------ хранилище

def load() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.exception("data.json повреждён, начинаю с чистого")
    return {}


def save(data: dict) -> None:
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)


def get_user(data: dict, chat_id) -> dict:
    key = str(chat_id)
    if key not in data:
        data[key] = {"lessons": [], "items": [], "session": None}
    data[key].setdefault("items", [])
    data[key].setdefault("lessons", [])
    data[key].setdefault("session", None)
    return data[key]


def new_item(ex: dict, lesson_idx: int) -> dict:
    item = dict(ex)
    item["lesson"] = lesson_idx
    item["seen"] = 0
    item["correct"] = 0
    item["wrong"] = 0
    item["box"] = 1
    return item


# ------------------------------------------------------------------ /start

WELCOME = (
    "Привет! Я помогу закреплять то, что вы проходите на уроках.\n\n"
    "*Как пользоваться:* во время или после звонка просто перешли мне сообщения "
    "преподавателя — любые, как есть. Я сам пойму, что это за задание.\n\n"
    "Я понимаю:\n"
    "• дриллы — `I am happy – Am I happy? – I am not happy.`\n"
    "• пропуски — `1. I ... in the theatre.`\n"
    "• перевод — `1. Я в парке. 2. Мы в углу.`\n"
    "• слова — `Abroad`, `On holiday`, `In bed`\n\n"
    "*Тренировки:*\n"
    "/practice — всё вперемешку (главная команда)\n"
    "/drill /gaps /translate /vocab — по типам\n"
    "/stats — прогресс, /lessons — уроки, /reset — сбросить тренировку"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.MARKDOWN)


# ------------------------------------------------------------------ приём урока

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    data = load()
    user = get_user(data, chat_id)
    text = update.message.text or update.message.caption or ""

    # если идёт тренировка — это ответ на упражнение
    if user.get("session"):
        await check_answer(update, context, data, user, text)
        return

    kind, exercises = parse_message(text)
    if not exercises:
        await update.message.reply_text(
            "Не понял, что это за задание 🤔 Я умею разбирать дриллы "
            "(`I am happy – Am I happy? – ...`), упражнения с пропусками "
            "(`1. I ... in the park.`), русские предложения на перевод "
            "и списки слов. Перешли сообщение целиком — или напиши /start.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lesson_idx = len(user["lessons"])
    user["lessons"].append({
        "kind": kind,
        "count": len(exercises),
        "created": time.time(),
        "preview": exercises[0]["prompt"][:60],
    })
    for ex in exercises:
        user["items"].append(new_item(ex, lesson_idx))
    save(data)

    unknown = sum(1 for e in exercises if e["type"] in ("translate", "vocab") and not e.get("answer"))
    note = ""
    if unknown:
        note = f"\n({unknown} без эталонного ответа — проверю их в режиме самопроверки)"

    await update.message.reply_text(
        f"Принял: *{TYPE_NAMES.get(kind, kind)}* — {len(exercises)} заданий ✅{note}\n\n"
        f"Всего в базе: {len(user['items'])} упражнений.\n"
        f"Тренироваться: /practice",
        parse_mode=ParseMode.MARKDOWN,
    )


# ------------------------------------------------------------------ тренировки

def pick_queue(user: dict, kind: str | None, size: int = 12):
    pool = [i for i, it in enumerate(user["items"]) if kind is None or it["type"] == kind]
    if not pool:
        return []
    weighted = []
    for idx in pool:
        weight = max(1, 5 - user["items"][idx].get("box", 1))
        weighted += [idx] * weight
    random.shuffle(weighted)
    seen, queue = set(), []
    for idx in weighted:
        if idx not in seen:
            seen.add(idx)
            queue.append(idx)
        if len(queue) >= size:
            break
    return queue


async def start_session(update: Update, context: ContextTypes.DEFAULT_TYPE, kind=None):
    chat_id = update.effective_chat.id
    data = load()
    user = get_user(data, chat_id)
    if not user["items"]:
        await update.message.reply_text(
            "Пока нечего повторять — перешли мне сообщения с урока, и я сделаю из них задания."
        )
        return
    queue = pick_queue(user, kind)
    if not queue:
        await update.message.reply_text(
            f"Заданий типа «{TYPE_NAMES.get(kind, kind)}» пока нет. Попробуй /practice."
        )
        return
    user["session"] = {"queue": queue, "index": 0, "step": 0, "score": 0, "asked": 0}
    save(data)
    await ask_current(update, context, data, user)


async def ask_current(update: Update, context: ContextTypes.DEFAULT_TYPE, data, user):
    sess = user["session"]
    if sess["index"] >= len(sess["queue"]):
        await finish_session(update, context, data, user)
        return
    item = user["items"][sess["queue"][sess["index"]]]
    n = sess["index"] + 1
    total = len(sess["queue"])
    head = f"*{n}/{total}*"

    if item["type"] == "drill":
        if sess["step"] == 0:
            text = (f"{head} Сделай *вопрос* из предложения:\n\n"
                    f"`{item['prompt']}`")
        else:
            text = (f"{head} Теперь *отрицание*:\n\n"
                    f"`{item['prompt']}`")
    elif item["type"] == "gapfill":
        text = (f"{head} Вставь нужную форму *to be*:\n\n"
                f"`{item['prompt']}`\n\n"
                f"_Напиши только am / is / are_")
    elif item["type"] == "translate":
        text = (f"{head} Переведи на английский:\n\n"
                f"*{item['prompt']}*")
    else:  # vocab
        if item.get("answer"):
            text = (f"{head} Как переводится?\n\n"
                    f"*{item['prompt']}*\n\n"
                    f"_Напиши перевод, или «?» чтобы посмотреть_")
        else:
            text = (f"{head} Составь предложение со словом:\n\n"
                    f"*{item['prompt']}*")

    await context.bot.send_message(update.effective_chat.id, text, parse_mode=ParseMode.MARKDOWN)


async def check_answer(update: Update, context: ContextTypes.DEFAULT_TYPE, data, user, text):
    sess = user["session"]
    item = user["items"][sess["queue"][sess["index"]]]
    text = text.strip()

    if text.lower() in ("/stop", "стоп", "хватит"):
        user["session"] = None
        save(data)
        await update.message.reply_text("Остановил тренировку. /practice — начать заново.")
        return

    advance = True
    item["seen"] += 1
    sess["asked"] += 1

    if item["type"] == "drill":
        expected = item["question"] if sess["step"] == 0 else item["negative"]
        ok = answers_match(text, expected)
        if ok:
            reply = f"✅ Верно!\n`{expected}`"
        else:
            reply = f"❌ Правильно так:\n`{expected}`"
        if sess["step"] == 0:
            sess["step"] = 1
            advance = False
        else:
            sess["step"] = 0
    elif item["type"] == "gapfill":
        ok = answers_match(text, item["answer"])
        reply = (f"✅ Верно!\n`{item['full']}`" if ok
                 else f"❌ Нужно *{item['answer']}*:\n`{item['full']}`")
    elif item["type"] == "translate":
        if item.get("answer"):
            ok = answers_match(text, item["answer"])
            reply = (f"✅ Верно!\n`{item['answer']}`" if ok
                     else f"❌ Эталон:\n`{item['answer']}`\nТвой вариант: _{text}_")
        else:
            ok = True
            reply = ("📝 Записал твой вариант — эталона для этого предложения у меня нет, "
                     f"сверь с преподавателем:\n_{text}_")
    else:  # vocab
        if item.get("answer"):
            if text == "?":
                ok = False
                reply = f"👀 *{item['prompt']}* — {item['answer']}"
            else:
                ok = answers_match(text, item["answer"]) or item["answer"].lower() in text.lower()
                reply = (f"✅ Верно! {item['prompt']} — {item['answer']}" if ok
                         else f"❌ Правильно: *{item['prompt']}* — {item['answer']}")
        else:
            ok = item["prompt"].split()[0].lower() in text.lower() and len(text.split()) >= 3
            reply = ("✅ Хорошее предложение! Покажи его преподавателю для проверки грамматики."
                     if ok else
                     f"Постарайся использовать «{item['prompt']}» в предложении из 3+ слов.")

    if ok:
        item["correct"] += 1
        item["box"] = min(5, item.get("box", 1) + 1)
        sess["score"] += 1
    else:
        item["wrong"] += 1
        item["box"] = 1

    if advance:
        sess["index"] += 1
    save(data)

    await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
    await ask_current(update, context, data, user)


async def finish_session(update: Update, context: ContextTypes.DEFAULT_TYPE, data, user):
    sess = user["session"]
    score, asked = sess["score"], max(1, sess["asked"])
    pct = round(score / asked * 100)
    user["session"] = None
    save(data)
    mood = "Отлично! 🔥" if pct >= 80 else ("Неплохо 👍" if pct >= 50 else "Есть над чем поработать 💪")
    await context.bot.send_message(
        update.effective_chat.id,
        f"Тренировка окончена. {mood}\nРезультат: *{score}/{asked}* ({pct}%)\n\n"
        f"Ещё раз: /practice · Прогресс: /stats",
        parse_mode=ParseMode.MARKDOWN,
    )


# ------------------------------------------------------------------ команды

async def practice(update, context):  await start_session(update, context, None)
async def drill(update, context):     await start_session(update, context, "drill")
async def gaps(update, context):      await start_session(update, context, "gapfill")
async def translate(update, context): await start_session(update, context, "translate")
async def vocab(update, context):     await start_session(update, context, "vocab")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load()
    user = get_user(data, update.effective_chat.id)
    user["session"] = None
    save(data)
    await update.message.reply_text("Тренировка сброшена. Присылай задания или жми /practice.")


async def lessons_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load()
    user = get_user(data, update.effective_chat.id)
    if not user["lessons"]:
        await update.message.reply_text("Уроков пока нет — перешли мне сообщения с занятия.")
        return
    lines = []
    for i, l in enumerate(user["lessons"], 1):
        when = time.strftime("%d.%m", time.localtime(l["created"]))
        lines.append(f"{i}. {TYPE_NAMES.get(l['kind'], l['kind'])} — {l['count']} заданий ({when})")
    await update.message.reply_text("\n".join(lines))


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load()
    user = get_user(data, update.effective_chat.id)
    items = user["items"]
    if not items:
        await update.message.reply_text("Пока нет данных — перешли задания с урока.")
        return
    by_type = {}
    for it in items:
        b = by_type.setdefault(it["type"], {"n": 0, "done": 0})
        b["n"] += 1
        if it.get("box", 1) >= 4:
            b["done"] += 1
    lines = [f"*Всего заданий:* {len(items)}", ""]
    for t, b in by_type.items():
        lines.append(f"• {TYPE_NAMES.get(t, t)}: {b['done']}/{b['n']} закреплено")
    weak = sorted([i for i in items if i["wrong"] > i["correct"] and i["seen"] > 0],
                  key=lambda x: x["wrong"] - x["correct"], reverse=True)[:10]
    if weak:
        lines.append("\n*Стоит повторить:*")
        for w in weak:
            lines.append(f"• {w['prompt'][:50]}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def on_error(update, context):
    log.exception("Ошибка при обработке апдейта", exc_info=context.error)


def main():
    if not BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("practice", practice))
    app.add_handler(CommandHandler("drill", drill))
    app.add_handler(CommandHandler("gaps", gaps))
    app.add_handler(CommandHandler("translate", translate))
    app.add_handler(CommandHandler("vocab", vocab))
    app.add_handler(CommandHandler("lessons", lessons_cmd))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    log.info("Bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
undefined
