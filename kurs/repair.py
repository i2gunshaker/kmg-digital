"""Починка карточек кодом: всё, что можно исправить по исходному тексту без модели, исправляется бесплатно.

Правило одно: код ничего не сочиняет. Он привязывает цитату к тексту, расширяет обрывок до предложения автора,
убирает фразу с неподтверждённым числом, берёт определение из доменного пакета или из главы, делит длинную фразу
по союзу. Если так починить нельзя, замечание остаётся модели, а с final=True — закрывается дословной фразой автора.
"""
from __future__ import annotations

import re
from pathlib import Path

from rapidfuzz import fuzz

from .quotes import (
    MAX_SENTENCE_WORDS,
    PARA_FIELDS,
    _find_span,
    is_substantive,
    referenced_units,
    sentences,
    snap_quote,
)
from .units import Unit
from .verify import check_quantities_against_source, numbers

MIN_QUOTE_WORDS = 5
MAX_QUOTE_WORDS = 60
PROSE = {"paragraph", "worked_example", "task", "figure"}
SOURCE_KIND = {"formula": "formula", "table": "table", "task": "task", "figure": "figure_caption"}

_END = re.compile(r"[.!?…][»\")\]]*(?=\s+[A-ZА-ЯЁ«\"(\d$])|\n\s*\n")
_ABBR_TAIL = re.compile(
    r"(?:\b(?:рис|табл|стр|гл|см|др|пр|им|напр|т|д|п|е|к|г|гг|в|вв|fig|eq|vs|с|ф-л|[A-ZА-ЯЁ])|e\.g|i\.e)\.$", re.I
)


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Границы предложений в тексте; сокращения и инициалы предложение не заканчивают."""
    out, start = [], 0
    for m in _END.finditer(text):
        head = text[start : m.end()].rstrip()
        if m.group(0)[0] == "." and _ABBR_TAIL.search(head):
            continue
        if head.count("$") % 2:
            continue
        if head:
            out.append((start, m.end()))
        start = m.end()
    if text[start:].strip():
        out.append((start, len(text)))
    res = []
    for a, b in out:
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if b > a:
            res.append((a, b))
    return res


def split_text(text: str) -> list[str]:
    return [text[a:b] for a, b in sentence_spans(text)]


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text or "")


# ---------------------------------------------------------------- цитаты
def widen_quote(quote: str, unit: Unit, pages: dict[int, str], need: set[str] = frozenset()) -> str | None:
    """Цитата, расширенная до целых предложений автора; need — числа, которые должны в неё войти."""
    span = _find_span(quote, unit.text)
    if not span:
        return None
    spans = sentence_spans(unit.text)
    a = next((i for i, (_, e) in enumerate(spans) if e > span[0]), None)
    b = next((i for i, (_, e) in enumerate(spans) if e >= span[1]), None)
    if a is None or b is None:
        return None
    for lo, hi in ((a, b), (a, b + 1), (a - 1, b)):
        if lo < 0 or hi >= len(spans):
            continue
        text = unit.text[spans[lo][0] : spans[hi][1]]
        if len(words(text)) > MAX_QUOTE_WORDS or not need <= numbers(text):
            continue
        hit = snap_quote(text, unit, pages)
        if hit and len(words(hit[0])) >= min(MIN_QUOTE_WORDS, len(words(text))) and need <= numbers(hit[0]):
            return hit[0]
    return None


def best_sentence(text: str, cands: list[Unit], pages: dict[int, str]) -> tuple[Unit, str] | None:
    """Предложение автора, которое говорит то же, что утверждение."""
    need = numbers(text)
    best: tuple[float, Unit, str] | None = None
    for u in cands:
        if u.kind not in PROSE:
            continue
        for s in split_text(u.text):
            n = len(words(s))
            if not MIN_QUOTE_WORDS <= n <= MAX_QUOTE_WORDS or not need <= numbers(s):
                continue
            score = fuzz.token_set_ratio(text.lower(), s.lower())
            if score >= 72 and (best is None or score > best[0]):
                hit = snap_quote(s, u, pages)
                if hit and len(words(hit[0])) >= MIN_QUOTE_WORDS:
                    best = (score, u, hit[0])
    return (best[1], best[2]) if best else None


def repair_claims(card: dict, own: list[str], units: dict[str, Unit], pages: dict[int, str], log: list[str]) -> None:
    mine = [units[x] for x in own if x in units and not units[x].model_description]
    keep: list[dict] = []
    for c in card["claims"]:
        u = units.get(c["unit_id"])
        if u is None or u.model_description or u.id not in own:
            u = None
        hit = snap_quote(c["quote"], u, pages) if u else None
        if hit is None:
            # Цитата верная, а единица указана не та.
            for v in mine:
                hit = snap_quote(c["quote"], v, pages) if v is not u else None
                if hit:
                    u = v
                    log.append(f"цитата привязана к {v.id}")
                    break
        if hit is None:
            found = best_sentence(c["text"], [u] if u else mine, pages) or best_sentence(c["text"], mine, pages)
            if found is None:
                log.append(f"утверждение без цитаты убрано: «{c['text'][:60]}»")
                continue
            u, quote = found
            log.append(f"цитата найдена в {u.id}")
        else:
            quote = hit[0]
        need = numbers(c["text"])
        if u.kind in PROSE:
            whole = len("".join(quote.split())) >= 0.8 * len("".join(u.text.split()))
            short = len(words(quote)) < MIN_QUOTE_WORDS and not whole
            if short or (need and not need <= numbers(quote) and need <= numbers(u.text)):
                wide = widen_quote(quote, u, pages, need & numbers(u.text)) or (widen_quote(quote, u, pages) if short else None)
                if wide:
                    quote = wide
                    log.append(f"цитата расширена до предложения ({u.id})")
        text, kind = c["text"], c["kind"]
        if check_quantities_against_source(text, [u.text]):
            # Число не подтверждено единицей: вместо пересказа ставится сама фраза автора.
            spoken = widen_quote(quote, u, pages) if u.kind in PROSE and not u.ocr_garbled else None
            if spoken is None or check_quantities_against_source(spoken, [u.text]):
                log.append(f"утверждение с неподтверждённым числом убрано: «{text[:60]}»")
                continue
            text, quote, kind = spoken, spoken, "observation"
            log.append(f"утверждение заменено фразой автора ({u.id})")
        same = {"text": text, "kind": kind, "unit_id": u.id, "quote": quote}
        if u.id != c["unit_id"]:
            same["source_kind"] = SOURCE_KIND.get(u.kind, "text")
        keep.append({**c, **same})
    card["claims"] = keep


# ---------------------------------------------------------------- числа
def drop_unsupported(text: str, sources: list[str]) -> tuple[str, int]:
    """Убирает фразы, числа которых не подтверждены. Возвращает текст и сколько фраз убрано."""
    if not any(ch.isdigit() for ch in text) or not check_quantities_against_source(text, sources):
        return text, 0
    parts = split_text(text)
    good = [s for s in parts if not check_quantities_against_source(s, sources)]
    return " ".join(good), len(parts) - len(good)


def repair_numbers(card: dict, sources: list[str], log: list[str], final: bool) -> None:
    for f in PARA_FIELDS:
        keep = []
        for p in card[f]:
            text, n = drop_unsupported(p["text"], sources)
            if n and (text or final):
                log.append(f"{f}: убрано фраз с неподтверждённым числом: {n}")
                p = {**p, "text": text}
            if p["text"].strip():
                keep.append(p)
        card[f] = keep
    text, n = drop_unsupported(card["main"], sources)
    if n and text and (final or len(sentences(text)) >= 2):
        card["main"] = text
        log.append("main: убрана фраза с неподтверждённым числом")
    dm = card["data_meaning"]
    text, n = drop_unsupported(dm["from_chapter"], sources)
    if n and text:
        dm["from_chapter"] = text
        log.append("data_from_chapter: убрана фраза с неподтверждённым числом")
    if final:
        good = []
        for w in card["worked_examples"]:
            body = " ".join([w["condition"], *w["steps"], w["result"]])
            if check_quantities_against_source(body, sources):
                log.append("пример с неподтверждённым числом убран")
            else:
                good.append(w)
        card["worked_examples"] = good


# ---------------------------------------------------------------- фразы
_JOINT = re.compile(
    r"(;)\s+|,\s+(?=(?:а|но|однако|поэтому|причём|причем|при этом|зато|следовательно|тогда как|то есть)\s)", re.I
)


def _count(text: str) -> int:
    return len(re.findall(r"[\w%°]+(?:[-–][\w]+)*", re.sub(r"\$\$.*?\$\$|\$[^$]+\$", "Ф", text)))


def split_long(text: str, limit: int = MAX_SENTENCE_WORDS) -> str:
    """Делит длинные фразы по точке с запятой или союзу; смысл и слова не меняются."""

    def one(s: str) -> str:
        if _count(s) <= limit:
            return s
        best: tuple[int, int, int] | None = None
        for m in _JOINT.finditer(s):
            head, tail = s[: m.start()], s[m.end() :]
            if head.count("$") % 2 or head.count("(") != head.count(")") or (m.group(1) and ":" in head):
                continue
            a, b = _count(head), _count(tail)
            if min(a, b) >= 4 and (best is None or abs(a - b) < best[0]):
                best = (abs(a - b), m.start(), m.end())
        if best is None:
            return s
        head, tail = s[: best[1]].rstrip(" ,;") + ".", s[best[2] :]
        return one(head) + " " + one(tail[:1].upper() + tail[1:])

    return " ".join(one(s) for s in split_text(text)) if text else text


def repair_phrases(card: dict, log: list[str]) -> None:
    for f in PARA_FIELDS:
        for p in card[f]:
            new = split_long(p["text"])
            if new != p["text"] and len(new) >= len(p["text"]) - 4 * len(split_text(p["text"])):
                p["text"] = new
    main = split_long(card["main"])
    if len(sentences(main)) > 3:  # деление не должно вытеснять мысли из главного
        main = card["main"]
    parts = split_text(main)
    while len(sentences(" ".join(parts))) > 3 and len(parts) > 2:
        parts.pop()
        log.append("main: оставлены первые фразы")
    main = " ".join(parts)
    if len(sentences(main)) < 2:
        extra = next((s for p in card["mechanism"] for s in split_text(p["text"]) if s not in main), None)
        if extra and _count(extra) <= MAX_SENTENCE_WORDS:
            main = f"{main} {extra}".strip()
            log.append("main: добавлена фраза из «Как работает»")
    card["main"] = main


# ---------------------------------------------------------------- термины
def domain_definitions(path: Path) -> dict[str, tuple[str, str]]:
    """Таблица доменного словаря: термин → (определение, английский аналог)."""
    out: dict[str, tuple[str, str]] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and not set(cells[0]) <= {"-", ":", " "} and not cells[0].startswith("Термин"):
            out.setdefault(cells[0].lower().replace("ё", "е"), (cells[1], cells[2].split("/")[0].strip()))
    return out


def author_definition(term: str, units: list[Unit]) -> str | None:
    """Фраза автора, которая вводит термин: «X — это…», «называется X», «под X понимают»."""
    stems = [re.escape(w[: max(3, len(w) - 2)].lower().replace("ё", "е")) + r"\w*" for w in re.findall(r"\w+", term)]
    if not stems:
        return None
    t = r"[\s\-–]+".join(stems)
    rx = re.compile(rf"(?<!\w){t}\s*(?:—|–|-|это|есть)\s|(?:назыв\w+|поним\w+|имену\w+)[^.]{{0,60}}(?<!\w){t}|(?<!\w){t}[^.]{{0,20}}назыв\w+")
    for u in units:
        if u.kind not in PROSE or u.ocr_garbled:
            continue
        for s in split_text(u.text):
            if rx.search(s.lower().replace("ё", "е")) and not any(ch.isdigit() for ch in s) and 5 <= len(words(s)) <= 35:
                return s
    return None


def repair_glossary(card: dict, node: dict, mine: list[Unit], defs: dict[str, tuple[str, str]], log: list[str]) -> None:
    keep = []
    for g in card["glossary"]:
        d = g["definition"]
        if any(ch.isdigit() for ch in d):
            d = re.sub(r"\s*\([^()]*\d[^()]*\)", "", d)
            d = " ".join(s for s in split_text(d) if not any(ch.isdigit() for ch in s))
            log.append(f"glossary: из определения «{g['term']}» убраны числа")
        if d.strip():
            keep.append({**g, "definition": d.strip()})
    card["glossary"] = keep
    have = {g["term"].lower().strip() for g in keep}
    for term in node["terms_introduced"]:
        if term.lower() in have:
            continue
        d, en = defs.get(term.lower().replace("ё", "е"), ("", ""))
        d = d or author_definition(term, mine) or ""
        if d and not any(ch.isdigit() for ch in d):
            card["glossary"].append({"term": term, "definition": d, "en": en})
            log.append(f"glossary: «{term}» взят из словаря или текста главы")


# ---------------------------------------------------------------- полнота
def cover_rest(card: dict, node: dict, units: dict[str, Unit], pages: dict[int, str], log: list[str]) -> None:
    """Последняя страховка полноты: непокрытая единица попадает в карточку дословной фразой автора."""
    ref = referenced_units(card)
    for uid in node["unit_ids"]:
        u = units[uid]
        if u.kind == "task" or uid in ref or not is_substantive(u):
            continue
        if u.kind == "formula":
            card["formulas"].append({"unit_id": uid, "meaning": "Запись автора; смысл смотри в соседнем абзаце.", "latex_fixed": ""})
            log.append(f"{uid}: формула добавлена как есть")
            continue
        added = 0
        spans = split_text(u.text) if u.kind in PROSE else [ln.strip() for ln in u.text.splitlines() if len(words(ln)) >= 2][:1]
        if u.kind in PROSE and all(len(words(s)) > MAX_QUOTE_WORDS for s in spans):
            # Перечень в одну фразу: берётся его начало до запятой.
            head = " ".join(u.text.split()[:40])
            spans = [head[: head.rfind(",")] if "," in head else head]
        for s in spans:
            n = len(words(s))
            if added >= 3 or n < (4 if u.kind in PROSE else 2) or n > MAX_QUOTE_WORDS:
                continue
            hit = snap_quote(s, u, pages)
            if not hit:
                continue
            text = re.sub(r"\s+", " ", hit[0]).strip()
            if u.kind == "table":
                text = "Автор сводит данные в таблицу: " + re.sub(r"\s*\|\s*", "; ", text.strip("| ")).strip("; ")
            if check_quantities_against_source(text, [u.text]):
                continue
            card["claims"].append(
                {"text": text, "kind": "observation", "unit_id": uid, "quote": hit[0], "source_kind": SOURCE_KIND.get(u.kind, "text")}
            )
            added += 1
        if added:
            log.append(f"{uid}: добавлено дословных фраз {added}")


def repair_card(
    card: dict,
    node: dict,
    units: dict[str, Unit],
    pages: dict[int, str],
    defs: dict[str, tuple[str, str]] | None = None,
    *,
    final: bool = False,
) -> list[str]:
    """Чинит карточку на месте. Возвращает список сделанного; повторный вызов ничего не меняет."""
    log: list[str] = []
    own = [x for x in node["unit_ids"] if x in units]
    mine = [units[x] for x in own if not units[x].model_description]
    sources = [u.text for u in mine]
    repair_claims(card, own, units, pages, log)
    repair_numbers(card, sources, log, final)
    repair_glossary(card, node, mine, defs or {}, log)
    repair_phrases(card, log)
    if final or not card["claims"]:
        cover_rest(card, node, units, pages, log)
    return log
