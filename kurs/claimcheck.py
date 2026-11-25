"""Смысловые проверки утверждения против цитаты автора — без модели, только правила.

Числа (значение, знак, единица, неравенство) проверяет `verify.check_quantities_against_source`.
Здесь — то, что число не ловит:

- negation_dropped / negation_added — у автора «не X», в утверждении просто «X», и наоборот;
- direction_flip      — «растёт» вместо «падает», «выше» вместо «ниже»;
- assumption_as_fact  — у автора «как правило», «может», «вероятно», в утверждении это убрано;
- condition_dropped   — у автора «если…», «только при…», а в утверждении условия нет;
- overgeneralization  — в утверждении «всегда», «любой», «никогда», а у автора нет;
- quote_unsupported   — у утверждения и цитаты почти нет общих смысловых слов.

Каждая проверка смотрит на ту часть цитаты, которая ближе всего к утверждению, а не на всю цитату:
иначе «не» из соседнего придаточного давало бы ложную тревогу.

Здесь же — мутации: те же ошибки, внесённые нарочно. Ими пользуются фаззинг валидатора
(сколько внесённых ошибок поймано) и задания (неверный вариант — правдоподобная ошибка того же шага).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .verify import check_quantities_against_source, extract_quantities

# ---------------------------------------------------------------- слова и основы
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+(?:-[A-Za-zА-Яа-яЁё]+)?")
_ENDINGS = re.compile(
    r"(иями|ями|ами|иях|иям|ях|ах|ией|ием|ого|его|ому|ему|ыми|ими|ых|их|ой|ей|ий|ый|ая|яя|ое|ее|ые|ие|ым|им|ом|ем|ам|ям|ую|юю|ов|ев|ью|ию|ия"
    r"|ется|ются|ится|ятся|ает|яет|ует|ют|ут|ят|ат|ит|ет|ться|ть|ти|ся|сь|а|я|о|е|ы|и|у|ю|ь|й|s|es|ed|ing)$"
)
_STOP = set(
    "и в во на по из за для что как это при от до с со к ко о об у а но или же то его ее их так где чем также если между через под над "
    "the a an of in on at to for by with and or as is are be was were it its this that from".split()
)


def words(text: str) -> list[str]:
    return [w.lower().replace("ё", "е") for w in _WORD.findall(text or "")]


def stem(word: str) -> str:
    w = word.lower().replace("ё", "е")
    if len(w) <= 4:
        return w
    cut = _ENDINGS.sub("", w)
    return cut if len(cut) >= 3 else w


def content_stems(text: str) -> set[str]:
    return {stem(w) for w in words(text) if w not in _STOP and w not in NEG_WORDS and len(w) > 2}


def overlap(a: str, b: str) -> float:
    """Доля смысловых слов a, которые есть в b."""
    sa = content_stems(a)
    return len(sa & content_stems(b)) / len(sa) if sa else 1.0


def clauses(text: str) -> list[str]:
    """Части предложения: по точке, точке с запятой, двоеточию, запятой и тире."""
    parts = re.split(r"(?<=[.!?;:])\s+|,\s+|\s+[—–]\s+|\n+", text or "")
    return [p.strip() for p in parts if p and p.strip()]


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n+", text or "") if s.strip()]


def best_window(claim: str, quote: str) -> str:
    """Предложение цитаты (или пара соседних), где больше всего смысловых слов утверждения."""
    sents = sentences(quote)
    if len(sents) <= 1:
        return quote
    cands = sents + [a + " " + b for a, b in zip(sents, sents[1:])]
    return max(cands, key=lambda s: (overlap(claim, s), -len(s)))


# ---------------------------------------------------------------- словари маркеров
NEG_WORDS = {
    "не", "нет", "ни", "без", "нельзя", "никогда", "отсутствие", "отсутствует", "отсутствуют", "отсутствуя", "отсутствовать",
    "not", "no", "never", "without", "none", "cannot", "nor",
}
_HEDGE = re.compile(
    r"(?<!\w)(?:может|могут|мог(?:ла|ли|ло)?|вероятно|возможно|предположительно|как\s+правило|обычно|часто|чаще\s+всего|иногда|"
    r"в\s+большинстве(?:\s+случаев)?|в\s+основном|по-видимому|видимо|примерно|приблизительно|около|порядка|нередко|"
    r"may|might|likely|probably|usually|typically|often|generally|approximately|sometimes|tend(?:s)?\s+to)(?!\w)",
    re.I,
)
_COND = re.compile(
    r"(?<!\w)(?:если|при\s+условии|только|лишь|когда|в\s+случае|пока|при\s+(?:\w+\s+)?(?:давлени|температур|насыщени|содержани|значени)\w*|"
    r"if|only|unless|when|provided|in\s+case)(?!\w)",
    re.I,
)
_ABSOLUTE = re.compile(r"(?<!\w)(?:всегда|никогда|любо(?:й|е|го|му|м|я|ую|ых|ые)|всяк\w+|исключительно|неизбежно|always|never|every|any|invariably)(?!\w)", re.I)

# Пары противоположных направлений: основы слов, без окончаний.
_UP = (
    "увелич", "возраст", "раст", "рост", "повыш", "выш", "больш", "боле", "усил", "ускор", "расшир", "нараст",
    "increas", "higher", "greater", "more", "rise", "grow",
)
_DOWN = (
    "уменьш", "сниж", "пада", "паден", "пониж", "ниж", "меньш", "мене", "ослаб", "замедл", "сужа", "убыва",
    "decreas", "lower", "less", "fall", "declin", "drop",
)
_FLIP_WORDS = {
    "увеличивается": "уменьшается", "уменьшается": "увеличивается", "увеличиваются": "уменьшаются", "уменьшаются": "увеличиваются",
    "возрастает": "убывает", "убывает": "возрастает", "возрастают": "убывают", "убывают": "возрастают",
    "растёт": "падает", "растет": "падает", "падает": "растёт", "растут": "падают", "падают": "растут",
    "повышается": "понижается", "понижается": "повышается", "повышаются": "понижаются", "понижаются": "повышаются",
    "снижается": "повышается", "снижаются": "повышаются", "повышает": "снижает", "снижает": "повышает",
    "увеличивает": "уменьшает", "уменьшает": "увеличивает", "усиливается": "ослабевает", "ослабевает": "усиливается",
    "выше": "ниже", "ниже": "выше", "больше": "меньше", "меньше": "больше", "более": "менее", "менее": "более",
    "увеличение": "уменьшение", "уменьшение": "увеличение", "рост": "снижение", "снижение": "рост", "повышение": "понижение",
    "понижение": "повышение", "increases": "decreases", "decreases": "increases", "higher": "lower", "lower": "higher",
    "more": "less", "less": "more", "rises": "falls", "falls": "rises",
}
_COMPARE_FLIP = [
    (re.compile(r"(?<!\w)не\s+более(?!\w)", re.I), "не менее"), (re.compile(r"(?<!\w)не\s+менее(?!\w)", re.I), "не более"),
    (re.compile(r"(?<!\w)не\s+выше(?!\w)", re.I), "не ниже"), (re.compile(r"(?<!\w)не\s+ниже(?!\w)", re.I), "не выше"),
    (re.compile(r"(?<!\w)свыше(?!\w)", re.I), "до"), (re.compile(r"≤"), "≥"), (re.compile(r"≥"), "≤"),
    (re.compile(r"(?<![<>=])<(?![<>=])"), ">"), (re.compile(r"(?<![<>=])>(?![<>=])"), "<"),
]
_UNIT_SWAP = [
    ("МПа", "кПа"), ("кПа", "МПа"), ("атм", "МПа"), ("мм", "см"), ("см", "мм"), ("км", "м"), ("мкм", "мм"), ("мД", "Д"),
    ("Ом·м", "мВ"), ("мВ", "Ом·м"), ("г/см³", "кг/м³"), ("м³/сут", "т/сут"), ("т/сут", "м³/сут"), ("°С", "К"), ("%", "‰"),
]


_PRED = re.compile(r"(?:ется|ются|ится|ятся|ает|яет|ают|яют|ывает|ивает|ет|ёт|ут|ют|ит|ят|ес|ase|ases|ises|ise|lls|ops|ines)$")


def _direction(text: str, predicates_only: bool = False) -> set[str]:
    """Направления изменения в тексте. predicates_only — только у сказуемых: «С увеличением X Y уменьшается» → down."""
    out = set()
    for w in words(text):
        if predicates_only and not _PRED.search(w):
            continue
        s = stem(w)
        if any(s.startswith(p) or w.startswith(p) for p in _UP):
            out.add("up")
        if any(s.startswith(p) or w.startswith(p) for p in _DOWN):
            out.add("down")
    return out


def negated_heads(text: str) -> tuple[set[str], set[str]]:
    """(основы слов, которые в тексте встречаются только под отрицанием, основы всех слов).

    «не всегда» — под отрицанием «всегда». Если слово встречается и с отрицанием, и без
    («отдаёт нефть, но не отдаёт воду»), утверждение без отрицания может пересказывать другую часть — это не ошибка."""
    ws = words(text)
    neg_pos: set[int] = set()
    for i, w in enumerate(ws):
        if w in NEG_WORDS:
            # «отсутствие проникновения»: под отрицанием следующее смысловое слово; «не очень высокая» — первое не служебное
            for j in range(i + 1, min(i + 4, len(ws))):
                if ws[j] in _STOP or ws[j] in NEG_WORDS:
                    continue
                neg_pos.add(j)
                break
    heads = {stem(ws[j]) for j in neg_pos}
    plain = {stem(w) for j, w in enumerate(ws) if j not in neg_pos and w not in NEG_WORDS}
    return heads - plain, {stem(w) for w in ws}


# ---------------------------------------------------------------- проверка
@dataclass(frozen=True)
class Finding:
    code: str
    msg: str
    evidence: str = ""


def claim_findings(text: str, quote: str, source_texts: list[str] | None = None, context: str = "") -> list[Finding]:
    """Всё, что можно сказать про утверждение по его цитате, без модели.

    source_texts — исходные единицы для чисел (по умолчанию сама цитата),
    context — тексты узла, где автор мог перенести условие (блок «Цифры и условия»)."""
    out: list[Finding] = []
    if not (text or "").strip() or not (quote or "").strip():
        return out
    win = best_window(text, quote)

    probs = check_quantities_against_source(text, source_texts or [quote])
    for p in probs:
        code = "condition_flipped" if "перевёрнуто условие" in p else "unit_mismatch" if "(в источнике:" in p else "number_not_in_source"
        out.append(Finding(code, f"число {p}", win))

    sup = overlap(text, quote)
    if sup < 0.25 and len(content_stems(text)) >= 4:
        out.append(Finding("quote_unsupported", f"у утверждения и цитаты общих смысловых слов {sup:.0%}", win))

    q_heads, q_all = negated_heads(win)
    c_heads, c_all = negated_heads(text)
    dropped = sorted(h for h in q_heads if h in c_all and h not in c_heads and len(h) > 2)
    added = sorted(h for h in c_heads if h in q_all and h not in q_heads and len(h) > 2)
    if dropped:
        out.append(Finding("negation_dropped", f"у автора под отрицанием «{dropped[0]}…», в утверждении — без отрицания", win))
    if added:
        out.append(Finding("negation_added", f"в утверждении отрицание при «{added[0]}…», у автора его нет", win))

    dc, dq = _direction(text, True), _direction(win, True)
    if not (dc and dq):
        dc, dq = _direction(text), _direction(win)
    if dc and dq and not (dc & dq) and overlap(text, win) >= 0.4:
        out.append(Finding("direction_flip", f"направление в утверждении {'/'.join(sorted(dc))}, у автора {'/'.join(sorted(dq))}", win))

    if _HEDGE.search(win) and not _HEDGE.search(text):
        hedged = [s for s in sentences(win) if _HEDGE.search(s)]
        if any(overlap(text, s) >= 0.6 for s in hedged):
            w = _HEDGE.search(win).group(0)
            out.append(Finding("assumption_as_fact", f"у автора «{w}», в утверждении сказано как факт", win))

    if _COND.search(win) and not _COND.search(text) and overlap(text, win) >= 0.5:
        for c in clauses(win):
            m = _COND.search(c)
            if not m:
                continue
            tail = c[m.end():]
            if content_stems(tail) and overlap(tail, text + " " + context) < 0.3:
                out.append(Finding("condition_dropped", f"у автора условие «{c[m.start():][:80]}», в утверждении его нет", win))
                break

    m = _ABSOLUTE.search(text)
    if m and not _ABSOLUTE.search(quote):
        out.append(Finding("overgeneralization", f"в утверждении «{m.group(0)}», у автора такого обобщения нет", win))
    return out


# ---------------------------------------------------------------- мутации
@dataclass(frozen=True)
class Mutation:
    kind: str
    text: str


def _seeded(seed: str, n: int) -> int:
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % max(n, 1)


def _other_number(value: str) -> str:
    v = float(value)
    if v == int(v):
        iv = int(v)
        new = iv * 2 if abs(iv) < 50 else iv // 2
        if new == iv:
            new = iv + 7
        return str(new)
    s = value.replace("-", "")
    digits = len(s.split(".")[1])
    return f"{v * 3:.{digits}f}".replace(".", ",")


def mutate_number(text: str, seed: str = "") -> str | None:
    qs = [q for q in extract_quantities(text) if q.raw and q.value not in ("0",)]
    if not qs:
        return None
    q = qs[_seeded(seed, len(qs))]
    raw_num = re.search(r"\d+(?:[.,]\d+)?", q.raw)
    if not raw_num:
        return None
    new = _other_number(q.value.lstrip("-"))
    pos = text.find(raw_num.group(0))
    return text[:pos] + new + text[pos + len(raw_num.group(0)) :] if pos >= 0 else None


def mutate_unit(text: str, seed: str = "") -> str | None:
    hits = [(a, b) for a, b in _UNIT_SWAP if re.search(rf"\d\s*{re.escape(a)}(?![\wа-яА-Я])", text)]
    if not hits:
        return None
    a, b = hits[_seeded(seed, len(hits))]
    return re.sub(rf"(\d\s*){re.escape(a)}(?![\wа-яА-Я])", lambda m: m.group(1) + b, text, count=1)


def mutate_compare(text: str, seed: str = "") -> str | None:
    for rx, repl in _COMPARE_FLIP:
        if rx.search(text):
            return rx.sub(repl, text, count=1)
    return None


# Глагол настоящего времени; минералы на -ит (аргиллит, доломит, ангидрит) глаголами не считаются.
_VERB = re.compile(
    r"(?<!\w)(?!не\s)((?:[а-яё]{2,}(?:ется|ются|ится|ятся|ает|яет|ует|ают|яют|уют|ет|ют|ят))|(?:[а-яё]{2,}(?<![лрмнбцзтгф])ит)|"
    r"является|являются|есть|is|are|was|were|does|do|can|has|have)(?!\w)",
    re.I,
)


def mutate_negation(text: str, seed: str = "") -> str | None:
    """«не X» → «X» или «X» → «не X» — у глагола или краткого причастия."""
    m = re.search(r"(?<!\w)(не|not)\s+(?=\w)", text, re.I)
    if m and not re.match(r"\s*(?:более|менее|выше|ниже|больше|меньше|только)\b", text[m.end():], re.I):
        return text[: m.start()] + text[m.end() :]
    verbs = [v for v in _VERB.finditer(text) if v.group(0).lower() not in _STOP and not re.search(r"(?<!\w)не\s*$", text[: v.start()])]
    if not verbs:
        return None
    v = verbs[_seeded(seed, len(verbs))]
    neg = "not " if re.match(r"[A-Za-z]", v.group(0)) else "не "
    return text[: v.start()] + neg + text[v.start() :]


def mutate_direction(text: str, seed: str = "") -> str | None:
    ws = [m for m in _WORD.finditer(text) if m.group(0).lower().replace("ё", "е") in {k.replace("ё", "е") for k in _FLIP_WORDS}]
    if not ws:
        return None
    m = ws[_seeded(seed, len(ws))]
    w = m.group(0)
    key = next(k for k in _FLIP_WORDS if k.replace("ё", "е") == w.lower().replace("ё", "е"))
    new = _FLIP_WORDS[key]
    new = new[:1].upper() + new[1:] if w[:1].isupper() else new
    return text[: m.start()] + new + text[m.end() :]


def mutate_hedge(text: str, seed: str = "") -> str | None:
    """Допущение автора → факт: убрать «как правило», «обычно», «вероятно»."""
    m = re.search(
        r"(,\s*)?(?<!\w)(?:как\s+правило|обычно|вероятно|возможно|предположительно|по-видимому|в\s+большинстве\s+случаев|в\s+основном|часто|"
        r"usually|typically|probably|likely|generally|often)(?!\w)(,?\s*)",
        text,
        re.I,
    )
    if not m:
        return None
    # «А, как правило, B» → «А B»; «Как правило, B» → «B»
    joint = " " if m.start() and m.end() < len(text) else ""
    out = (text[: m.start()] + joint + text[m.end() :]).strip()
    out = re.sub(r"\s{2,}", " ", out)
    return out[:1].upper() + out[1:]


def mutate_condition(text: str, seed: str = "") -> str | None:
    """Убрать условие: «если …,» / «только при …» / «при условии …»."""
    m = re.search(r",?\s*(?<!\w)(?:если|при\s+условии|только\s+при|лишь\s+при|в\s+случае|if|only\s+when|unless)(?!\w)[^,.;]*[,]?", text, re.I)
    if not m or len(text) - len(m.group(0)) < 25:
        return None
    out = (text[: m.start()] + (" " if m.start() and text[m.end() - 1 : m.end()] == "," else "") + text[m.end() :]).strip()
    out = re.sub(r"\s{2,}", " ", out)
    return out[:1].upper() + out[1:]


def mutate_absolute(text: str, seed: str = "") -> str | None:
    if _ABSOLUTE.search(text):
        return None
    verbs = list(_VERB.finditer(text))
    if not verbs:
        return None
    v = verbs[0]
    word = "always " if re.match(r"[A-Za-z]", v.group(0)) else "всегда "
    return text[: v.start()] + word + text[v.start() :]


MUTATORS = {
    "number_not_in_source": mutate_number,
    "unit_mismatch": mutate_unit,
    "condition_flipped": mutate_compare,
    "negation": mutate_negation,
    "direction_flip": mutate_direction,
    "assumption_as_fact": mutate_hedge,
    "condition_dropped": mutate_condition,
    "overgeneralization": mutate_absolute,
}

# Какие коды проверки считаются поимкой мутации данного вида.
CAUGHT_BY = {
    "number_not_in_source": {"number_not_in_source", "unit_mismatch", "condition_flipped"},
    "unit_mismatch": {"unit_mismatch", "number_not_in_source"},
    "condition_flipped": {"condition_flipped", "number_not_in_source", "direction_flip", "negation_added", "negation_dropped"},
    "negation": {"negation_added", "negation_dropped"},
    "direction_flip": {"direction_flip"},
    "assumption_as_fact": {"assumption_as_fact"},
    "condition_dropped": {"condition_dropped"},
    "overgeneralization": {"overgeneralization"},
    "page_shift": {"quote_not_on_page"},
    "quote_swap": {"quote_unsupported", "number_not_in_source", "unit_mismatch"},
}


def mutations(text: str, seed: str = "") -> list[Mutation]:
    """Все применимые к тексту мутации; результат отличается от исходного текста."""
    out = []
    for kind, fn in MUTATORS.items():
        new = fn(text, seed + kind)
        if new and new.strip() != text.strip():
            out.append(Mutation(kind, new))
    return out
