"""
Разбор сообщений от преподавателя английского.

Поддерживаемые форматы (определяются автоматически):

1. DRILL — трансформационные дриллы:
     I am happy – Am I happy? – I am not happy.
   → упражнение: дано утверждение, нужно построить вопрос и отрицание.

2. GAPFILL — упражнение с пропусками:
     1. I ... in the theatre. 2. We ... in the yard. ... 8. ... I on the floor?
   → упражнение: подставить нужную форму to be (am/is/are).

3. TRANSLATE — русские предложения на перевод:
     1. Я в парке. 2. Мы в углу. 3. Ты в саду.
   → упражнение: перевести на английский, с автопроверкой.

4. VOCAB — отдельные слова/фразы:
     Abroad / On holiday / In bed
   → карточки; перевод берётся из встроенного словаря или спрашивается.

5. PAIRS — классический формат "word - перевод" (оставлен как запасной).
"""

import re
import unicodedata

DASHES = "-–—"
DASH_SPLIT_RE = re.compile(r"\s+[–—]\s+")
NUM_ITEM_RE = re.compile(r"(?:^|\s)(\d{1,2})\s*\.\s*")
PAIR_RE = re.compile(r"^(.+?)\s*[-–—:=]\s*(.+)$")
EXAMPLE_RE = re.compile(r"^(.*?)\s*\(([^)]+)\)\s*$")
CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")


# ---------------------------------------------------------------- to be forms

BE_BY_SUBJECT = {
    "i": "am",
    "he": "is", "she": "is", "it": "is",
    "we": "are", "you": "are", "they": "are",
}


def be_form(subject: str) -> str:
    return BE_BY_SUBJECT.get(subject.strip().lower(), "is")


# ---------------------------------------------------------------- нормализация

def normalize(text: str) -> str:
    """Приводит ответ к сравнимому виду: нижний регистр, без пунктуации,
    контракции раскрыты, лишние пробелы убраны."""
    t = unicodedata.normalize("NFKC", text).strip().lower()
    contractions = {
        "i'm": "i am", "you're": "you are", "we're": "we are",
        "they're": "they are", "he's": "he is", "she's": "she is",
        "it's": "it is", "isn't": "is not", "aren't": "are not",
        "amn't": "am not", "i´m": "i am", "i’m": "i am",
    }
    for k, v in contractions.items():
        t = t.replace(k, v)
    t = re.sub(r"[.,!?;:]+", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def answers_match(user: str, reference: str) -> bool:
    return normalize(user) == normalize(reference)


# ---------------------------------------------------------------- разбиение на пункты

def split_numbered(text: str):
    """Разбивает '1. Foo. 2. Bar.' на ['Foo.', 'Bar.'].
    Работает и когда всё в одну строку, и когда пункты на разных строках."""
    flat = re.sub(r"\s*\n\s*", " ", text).strip()
    positions = [(m.start(), m.end(), int(m.group(1))) for m in NUM_ITEM_RE.finditer(flat)]
    if len(positions) < 2:
        return []
    items = []
    for idx, (start, end, num) in enumerate(positions):
        stop = positions[idx + 1][0] if idx + 1 < len(positions) else len(flat)
        chunk = flat[end:stop].strip()
        if chunk:
            items.append(chunk)
    return items


# ---------------------------------------------------------------- 1. DRILL

def parse_drills(text: str):
    """Строки вида 'I am happy – Am I happy? – I am not happy.'"""
    drills = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in DASH_SPLIT_RE.split(line) if p.strip()]
        if len(parts) != 3:
            continue
        affirmative, question, negative = parts
        if "?" not in question:
            continue
        if " not " not in negative.lower():
            continue
        drills.append({
            "type": "drill",
            "prompt": affirmative.rstrip("."),
            "question": question,
            "negative": negative,
        })
    return drills


# ---------------------------------------------------------------- 2. GAPFILL

GAP_RE = re.compile(r"\.\.\.|…|_{2,}")


def parse_gapfill(text: str):
    """Пункты с пропуском: 'I ... in the theatre.' / '... I on the floor?'"""
    items = split_numbered(text)
    if not items:
        items = [l.strip() for l in text.splitlines() if GAP_RE.search(l)]
    exercises = []
    for item in items:
        if not GAP_RE.search(item):
            continue
        gap = GAP_RE.search(item)
        before = item[:gap.start()].strip()
        after = item[gap.end():].strip()

        if before:
            # утвердительная или отрицательная форма: подлежащее перед пропуском
            subject = before.split()[-1]
            answer = be_form(subject)
        else:
            # вопрос: подлежащее сразу после пропуска
            words = after.split()
            if not words:
                continue
            subject = words[0]
            answer = be_form(subject).capitalize()

        filled = item[:gap.start()] + answer + item[gap.end():]
        filled = re.sub(r"\s+", " ", filled).strip()
        exercises.append({
            "type": "gapfill",
            "prompt": item,
            "answer": answer,
            "full": filled,
        })
    return exercises


# ---------------------------------------------------------------- 3. TRANSLATE

SUBJECTS = {
    "я": ("I", "am"),
    "мы": ("We", "are"),
    "ты": ("You", "are"),
    "вы": ("You", "are"),
    "они": ("They", "are"),
    "он": ("He", "is"),
    "она": ("She", "is"),
    "оно": ("It", "is"),
    "это": ("It", "is"),
}

# косвенные формы для конструкций «Мне жарко», «Ему тепло»
DATIVE_SUBJECTS = {
    "мне": ("I", "am"),
    "нам": ("We", "are"),
    "тебе": ("You", "are"),
    "вам": ("You", "are"),
    "им": ("They", "are"),
    "ему": ("He", "is"),
    "ей": ("She", "is"),
}

PLACES = {
    "в парке": "in the park",
    "в углу": "in the corner",
    "в саду": "in the garden",
    "за компьютером": "at the computer",
    "на занятии": "in class",
    "в отеле": "at the hotel",
    "в отпуске": "on holiday",
    "в ванной": "in the bathroom",
    "в спортзале": "at the gym",
    "в машине": "in the car",
    "в кафе": "at the cafe",
    "за границей": "abroad",
    "на пляже": "on the beach",
    "в кровати": "in bed",
    "на ковре": "on the carpet",
    "в такси": "in the taxi",
    "за столом": "at the table",
    "дома": "at home",
    "на работе": "at work",
    "в театре": "in the theatre",
    "во дворе": "in the yard",
    "в ресторане": "in the restaurant",
    "в аэропорту": "in the airport",
    "в спальне": "in the bedroom",
    "за городом": "in the countryside",
    "на концерте": "at the concert",
    "на полу": "on the floor",
    "в лесу": "in the forest",
    "на встрече": "at the meeting",
    "в офисе": "in the office",
    "в самолёте": "on the plane",
    "в самолете": "on the plane",
    "у окна": "at the window",
    "в душе": "in the shower",
    "на вечеринке": "at the party",
    "в кино": "in the cinema",
    "на конференции": "at the conference",
    "в музее": "in the museum",
    "в гостиной": "in the sitting-room",
    "в кухне": "in the kitchen",
    "на кухне": "in the kitchen",
    "в пабе": "in the pub",
    "в классе": "in class",
    "онлайн": "online",
}

ADJECTIVES = {
    "счастлив": "happy", "счастлива": "happy", "счастливы": "happy", "счастливый": "happy",
    "грустный": "sad", "грустная": "sad", "грустные": "sad", "грустно": "sad",
    "уставший": "tired", "уставшая": "tired", "уставшие": "tired", "устал": "tired",
    "занят": "busy", "занята": "busy", "заняты": "busy",
    "готов": "ready", "готова": "ready", "готово": "ready", "готовы": "ready",
    "уверен": "sure", "уверена": "sure", "уверены": "sure",
    "свободен": "free", "свободна": "free", "свободны": "free",
    "боюсь": "afraid", "боишься": "afraid", "боитесь": "afraid", "боится": "afraid", "боятся": "afraid",
    "взволнован": "worried", "взволнована": "worried", "взволнованы": "worried",
    "скучно": "bored", "скучаю": "bored",
    "сонный": "sleepy", "сонная": "sleepy", "сонные": "sleepy",
    "болею": "ill", "болеет": "ill", "болен": "ill", "больна": "ill",
    "здоров": "well", "здорова": "well", "здоровы": "well",
    "тепло": "warm", "жарко": "hot", "прохладно": "cool", "холодно": "cold",
    "один": "alone", "одна": "alone", "одни": "alone",
    "невезучий": "unlucky", "невезучая": "unlucky", "невезучие": "unlucky",
    "голоден": "hungry", "голодна": "hungry", "голодны": "hungry",
    "хочу пить": "thirsty",
    "удобно": "comfortable", "неудобно": "uncomfortable",
    "нервничаю": "nervous", "нервничаем": "nervous", "нервничаете": "nervous",
    "злой": "angry", "злая": "angry", "злые": "angry", "зол": "angry",
    "женат": "married", "замужем": "married",
    "удивлён": "surprised", "удивлена": "surprised", "удивлены": "surprised",
    "несчастлив": "unhappy", "несчастна": "unhappy",
    "спит": "asleep", "сплю": "asleep",
    "безопасно": "safe",
    "холост": "single", "не замужем": "single",
}

NEGATION_WORDS = {"не", "ни"}


def _lookup_predicate(rest: str):
    """Возвращает английский предикат для остатка русского предложения."""
    r = rest.strip().strip(".?!").lower()
    r = re.sub(r"\s+", " ", r)
    if r in PLACES:
        return PLACES[r]
    if r in ADJECTIVES:
        return ADJECTIVES[r]
    # пробуем убрать вспомогательные слова
    for prefix in ("ли ",):
        if r.startswith(prefix):
            r2 = r[len(prefix):].strip()
            if r2 in PLACES:
                return PLACES[r2]
            if r2 in ADJECTIVES:
                return ADJECTIVES[r2]
    return None


def translate_ru_sentence(sentence: str):
    """Пытается перевести простое предложение с to be. Возвращает строку или None."""
    s = sentence.strip()
    if not s:
        return None
    is_question = s.endswith("?")
    body = s.rstrip(".?!").strip()
    words = body.split()
    if not words:
        return None

    lowered = [w.lower() for w in words]

    # находим подлежащее
    subj_idx = None
    subj_en = subj_be = None
    for i, w in enumerate(lowered):
        clean = w.strip(",")
        if clean in SUBJECTS:
            subj_en, subj_be = SUBJECTS[clean]
            subj_idx = i
            break
        if clean in DATIVE_SUBJECTS:
            subj_en, subj_be = DATIVE_SUBJECTS[clean]
            subj_idx = i
            break
    if subj_idx is None:
        return None

    rest_words = lowered[:subj_idx] + lowered[subj_idx + 1:]
    negated = any(w.strip(",") in NEGATION_WORDS for w in rest_words)
    rest_words = [w for w in rest_words if w.strip(",") not in NEGATION_WORDS and w.strip(",") != "ли"]

    predicate = _lookup_predicate(" ".join(rest_words))
    if predicate is None:
        return None

    if is_question:
        head = f"{subj_be.capitalize()} {subj_en.lower() if subj_en != 'I' else 'I'}"
        if negated:
            return f"{head} not {predicate}?"
        return f"{head} {predicate}?"

    if negated:
        return f"{subj_en} {subj_be} not {predicate}."
    return f"{subj_en} {subj_be} {predicate}."


def parse_translation(text: str):
    """Русские пронумерованные предложения → задания на перевод."""
    items = split_numbered(text)
    if not items:
        items = [l.strip() for l in text.splitlines() if CYRILLIC_RE.search(l)]
    exercises = []
    for item in items:
        if not CYRILLIC_RE.search(item):
            continue
        reference = translate_ru_sentence(item)
        exercises.append({
            "type": "translate",
            "prompt": item,
            "answer": reference,   # может быть None — тогда самопроверка
        })
    return exercises


# ---------------------------------------------------------------- 4. VOCAB

VOCAB_DICT = {
    "abroad": "за границей",
    "bathroom": "ванная",
    "in class": "на занятии",
    "in bed": "в кровати",
    "in the corner": "в углу",
    "on holiday": "в отпуске",
    "on the beach": "на пляже",
    "on the carpet": "на ковре",
    "bored": "скучающий, скучно",
    "i am busy with english": "я занят английским",
    "to be": "быть (глагол-связка)",
    "at home": "дома",
    "at work": "на работе",
    "at the table": "за столом",
    "in the kitchen": "на кухне",
    "in the garden": "в саду",
    "in the park": "в парке",
    "in the office": "в офисе",
    "online": "онлайн, в сети",
    "in the shower": "в душе",
    "in the sitting-room": "в гостиной",
    "at the gym": "в спортзале",
    "in the pub": "в пабе",
    "in the restaurant": "в ресторане",
    "happy": "счастливый",
    "sad": "грустный",
    "tired": "уставший",
    "busy": "занятый",
    "hungry": "голодный",
    "thirsty": "испытывающий жажду",
    "comfortable": "удобный",
    "uncomfortable": "неудобный",
    "afraid": "испуганный, боящийся",
    "worried": "взволнованный",
    "unhappy": "несчастливый",
    "surprised": "удивлённый",
    "single": "холост, не замужем",
    "safe": "безопасный",
    "asleep": "спящий",
    "nervous": "нервный",
    "alone": "один, в одиночестве",
    "angry": "сердитый",
    "married": "женат, замужем",
    "ready": "готовый",
}


def parse_vocab(text: str):
    """Короткие английские фразы без перевода → словарные карточки."""
    exercises = []
    for line in text.splitlines():
        line = line.strip().rstrip(".")
        if not line or CYRILLIC_RE.search(line):
            continue
        if len(line.split()) > 5:
            continue
        if GAP_RE.search(line) or NUM_ITEM_RE.search(line):
            continue
        ru = VOCAB_DICT.get(line.lower())
        exercises.append({
            "type": "vocab",
            "prompt": line,
            "answer": ru,
        })
    return exercises


# ---------------------------------------------------------------- 5. PAIRS

def parse_pairs(text: str):
    exercises = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = PAIR_RE.match(line)
        if not m:
            continue
        en, rest = m.group(1).strip(), m.group(2).strip()
        ex = EXAMPLE_RE.match(rest)
        ru, example = rest, ""
        if ex:
            ru, example = ex.group(1).strip(), ex.group(2).strip()
        if en and ru and CYRILLIC_RE.search(ru) and not CYRILLIC_RE.search(en):
            exercises.append({"type": "vocab", "prompt": en, "answer": ru, "example": example})
    return exercises


# ---------------------------------------------------------------- диспетчер

def parse_message(text: str):
    """Определяет тип сообщения и возвращает (тип, список упражнений)."""
    text = text.strip()
    if not text:
        return None, []

    drills = parse_drills(text)
    if len(drills) >= 2:
        return "drill", drills

    if GAP_RE.search(text):
        gaps = parse_gapfill(text)
        if gaps:
            return "gapfill", gaps

    if CYRILLIC_RE.search(text):
        pairs = parse_pairs(text)
        if len(pairs) >= 2:
            return "vocab", pairs
        translations = parse_translation(text)
        if translations:
            return "translate", translations

    vocab = parse_vocab(text)
    if vocab:
        return "vocab", vocab

    return None, []
