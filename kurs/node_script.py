"""Закадровый текст ролика по любому узлу слоя.

Модель пишет реплики, код проверяет форму и числа, модель-судья сверяет смысл с карточкой. Не прошло или денег нет —
сценарий по правилам: в нём звучат только проверенные утверждения узла. Готовый текст лежит в build/<глава>/scripts/
и хранит хэш узла: после правки факта он собирается заново (без сети — по правилам).
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re

from .claimcheck import content_stems, overlap, sentences
from .config import Chapter, Config
from .layer_gen import prompt
from .llm import LLM, LLMError
from .verify import check_quantities_against_source
from .visual_gen import node_card

KINDS = ["hook", "context", "mechanism", "why", "data", "limit", "now", "analogy", "predict", "answer", "transfer"]
ASKS = {"hook", "predict", "transfer"}
FORMATS = {
    "9:16": {"ru": "шортс 9:16, 40–75 секунд: 9–11 строк, всего 85–125 слов, один механизм", "lines": (8, 13), "words": (70, 140)},
    "16:9": {"ru": "разбор 16:9, 95–170 секунд: 16–21 строка, всего 190–280 слов; в конце решение специалиста на данных", "lines": (13, 24), "words": (160, 310)},
}
WPS = 2.15  # слов в секунду у диктора: 130 в минуту
MAX_WORDS = 18
_FORMULA = re.compile(r"[$\\^_=<>≤≥→←±√∫∑∂Δα-ωΑ-Ω{}\[\]()]")
_LETTER = re.compile(r"(?<![A-Za-z0-9])[A-Za-z](?![A-Za-z0-9])")
# обозначения из формул: «V0», «dt», «pк», «Q1» — диктор их не читает; аббревиатуры (GR, ECD, ГДИС) можно
_SYMBOL = re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9])(?:[IVXL]+\.\d+|[A-Za-z]+\d+[A-Za-z]*|[a-z]{1,2}|[A-Za-z][а-яё]{1,2}|[а-яё]{1,2}[A-Za-z])(?![A-Za-zА-Яа-яЁё0-9])")
_META = re.compile(r"карточк|сценари|в главе не", re.I)
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9,.\-–%°/]*")

LINE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "text", "on_screen", "claim", "frame"],
    "properties": {
        "kind": {"type": "string", "enum": KINDS},
        "text": {"type": "string"},
        "on_screen": {"type": "string"},
        "claim": {"type": "string"},
        "frame": {"type": "integer"},
    },
}
SCRIPT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lines", "prediction_question", "transfer_question", "data_bridge", "checklist_note"],
    "properties": {
        "lines": {"type": "array", "items": LINE},
        **{k: {"type": "string"} for k in ("prediction_question", "transfer_question", "data_bridge", "checklist_note")},
    },
}
JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["issues"],
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["n", "problem", "fix"],
                "properties": {"n": {"type": "integer"}, "problem": {"type": "string"}, "fix": {"type": "string"}},
            },
        }
    },
}


def count_words(text: str) -> int:
    return len(_WORD.findall(text or ""))


def node_hash(node: dict, fmt: str) -> str:
    body = {k: v for k, v in node.items() if k != "links"}
    return hashlib.sha256((fmt + json.dumps(body, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()[:16]


def card(node: dict) -> str:
    """Карточка шага со всем, на что можно опереться в озвучке."""
    out = [node_card(node, False)]
    mis = node.get("misconception") or {}
    if mis.get("text"):
        out.append("Заблуждение, которое шаг исправляет: " + mis["text"])
    dm = node.get("data_meaning") or {}
    for key, ru in (("from_chapter", "Что это значит для данных"), ("breaks", "Что здесь ломается")):
        if (dm.get(key) or {}).get("text"):
            out.append(f"{ru}: {dm[key]['text']}")
    it = node.get("it_analogy") or {}
    if it.get("text"):
        out.append(f"ИТ-аналогия: {it['text']} Где ломается: {it.get('breaks_where', '')}")
    if (node.get("now_practice") or {}).get("text"):
        out.append("Как делают сейчас: " + node["now_practice"]["text"])
    terms = [f"{g['term']} — {g['definition']}" for g in node.get("glossary") or []]
    if terms:
        out.append("Термины шага: " + "; ".join(terms))
    return "\n".join(out)


def claim_map(node: dict) -> dict[str, dict]:
    return {c["id"].split("#")[-1]: c for c in node.get("claims") or []}


def line_problems(ln: dict, claims: dict[str, dict], source: str, n_frames: int) -> list[str]:
    """Форма реплики и числа; смысл проверяет судья."""
    out = []
    text, scr = ln["text"].strip(), ln["on_screen"].strip()
    n = count_words(text)
    if n > MAX_WORDS:
        out.append(f"фраза {n} слов, можно до {MAX_WORDS}")
    if n < 3:
        out.append("фраза слишком короткая")
    if m := _FORMULA.search(text):
        out.append(f"в фразе знак «{m.group(0)}»: формулы, греческие буквы и скобки диктор не читает")
    if m := _LETTER.search(text) or _LETTER.search(scr) or _SYMBOL.search(text) or _SYMBOL.search(scr):
        out.append(f"обозначение «{m.group(0)}»: назови величину словом")
    if _META.search(text):
        out.append("фраза говорит о карточке или сценарии, а не о предмете")
    if not 1 <= len(scr.split()) <= 7:
        out.append("на экране должно быть от 1 до 7 слов")
    if m := _FORMULA.search(scr):
        out.append(f"на экране знак «{m.group(0)}»")
    if ln["claim"] not in claims:
        out.append(f"нет утверждения {ln['claim']}")
    if not 1 <= int(ln["frame"]) <= max(1, n_frames):
        out.append(f"нет кадра {ln['frame']}")
    bad = check_quantities_against_source(text, [source])
    bad += [x for x in check_quantities_against_source(scr, [source]) if x not in bad]
    out += [f"число {b}" for b in bad]
    return out


def script_problems(doc: dict, node: dict, fmt: str, n_frames: int) -> list[str]:
    lim = FORMATS[fmt]
    claims = claim_map(node)
    source = card(node) + "\n" + "\n".join(c.get("quote", "") for c in claims.values())
    lines = doc["lines"]
    out = []
    kinds = [ln["kind"] for ln in lines]
    if not lim["lines"][0] <= len(lines) <= lim["lines"][1]:
        out.append(f"строк {len(lines)}, нужно {lim['lines'][0]}–{lim['lines'][1]}")
    total = sum(count_words(ln["text"]) for ln in lines)
    if not lim["words"][0] <= total <= lim["words"][1]:
        out.append(f"всего слов {total}, нужно {lim['words'][0]}–{lim['words'][1]}")
    if kinds[:1] != ["hook"]:
        out.append("первая строка должна быть hook")
    elif count_words(lines[0]["text"]) > 14:
        out.append("hook длиннее 14 слов")
    if kinds[-1:] != ["transfer"] or not lines[-1]["text"].rstrip().endswith("?"):
        out.append("последняя строка — transfer, вопрос")
    if kinds.count("predict") != 1:
        out.append("нужна ровно одна строка predict")
    else:
        i = kinds.index("predict")
        if i + 1 >= len(kinds) or kinds[i + 1] != "answer":
            out.append("сразу после predict должна идти answer")
        if not lines[i]["text"].rstrip().endswith("?"):
            out.append("predict должен быть вопросом")
        if i < 3:
            out.append("predict слишком рано: сначала механизм")
        if i + 1 < len(kinds) and any(overlap(lines[i + 1]["text"], x["text"]) >= 0.75 for x in lines[1:i] if len(content_stems(lines[i + 1]["text"])) >= 3):
            out.append("ответ прозвучал до вопроса predict: спроси о том, чего ещё не сказано")
    if sum(k in ("mechanism", "why", "context") for k in kinds) < 3:
        out.append("мало механизма: нужно не меньше трёх строк mechanism, why или context")
    if "limit" not in kinds:
        out.append("нет строки limit: когда правило не работает")
    if "data" not in kinds:
        out.append("нет строки data: что это значит для данных")
    for i, ln in enumerate(lines, 1):
        out += [f"строка {i}: {p}" for p in line_problems(ln, claims, source, n_frames)]
    return out


def support(line: dict, node_text: str) -> float:
    """Доля смысловых слов фразы, которые есть в карточке."""
    return overlap(line["text"], node_text) if len(content_stems(line["text"])) >= 3 else 1.0


# ---------------------------------------------------------------- по правилам
def _pieces(text: str) -> list[str]:
    """Утверждение → фразы до 18 слов. Не делится по смыслу — не берём."""
    t = text.replace("—", "–")
    if "$" in t:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.!?;])\s+", t) if p.strip()]
    parts = [p.rstrip(";") + ("" if p.endswith((".", "!", "?")) else ".") for p in parts]
    parts = [p[:1].upper() + p[1:] for p in parts]
    return parts if parts and all(3 <= count_words(p) <= MAX_WORDS and not _FORMULA.search(p) and not _LETTER.search(p) and not _SYMBOL.search(p) and not _META.search(p) for p in parts) else []


def _good(text: str) -> list[str]:
    """Пояснение карточки (не утверждение) → те его фразы, которые диктор может прочесть: без формул и букв-обозначений."""
    if "$" in (text or ""):
        text = re.sub(r"\$[^$]*\$", " $ ", text)
    out = []
    for p in re.split(r"(?<=[.!?;])\s+", (text or "").replace("—", "–")):
        p = p.strip().rstrip(";")
        if not p:
            continue
        p = p[:1].upper() + p[1:] + ("" if p.endswith((".", "!", "?")) else ".")
        if 3 <= count_words(p) <= MAX_WORDS and not _FORMULA.search(p) and not _LETTER.search(p) and not _SYMBOL.search(p) and not _META.search(p):
            out.append(p)
    return out


def _screen(text: str) -> str:
    """Надпись на экране до 7 слов: фраза целиком, иначе её первая часть до запятой, иначе начало с многоточием."""
    def clean(s: str) -> list[str]:
        return re.sub(r"(?<!\d)[.,;:!?«»]|[.,;:!?«»](?!\d)", "", _FORMULA.sub("", s)).split()

    def cap(s: str) -> str:
        return s[:1].upper() + s[1:]

    whole = clean(text)
    if len(whole) <= 7:
        return cap(" ".join(whole))
    head = clean(re.split(r"(?<!\d),(?!\d)|\s[–—-]\s|:", text)[0])
    if 4 <= len(head) <= 7:
        return cap(" ".join(head))
    w = whole[:6]
    while w and len(w[-1]) <= 3 and len(w) > 2:
        w = w[:-1]
    return cap(" ".join(w)) + "…"


_LIMIT = re.compile(r"\b(?:только|если|при условии|не (?:всегда|годится|работает|применя\w+)|огранич\w+|допуска\w+|предполага\w+|обычно|лишь)\b", re.I)


def _nearest(text: str, claims: dict[str, dict]) -> str:
    return max(claims, key=lambda k: overlap(text, claims[k]["text"] + " " + claims[k].get("quote", "")))


def script_by_rules(node: dict, fmt: str, n_frames: int) -> dict:
    """Запасной сценарий: звучат утверждения узла и вывод для данных из карточки; вопросы общие и ничего не утверждают."""
    claims = claim_map(node)
    source = card(node) + "\n" + "\n".join(c.get("quote", "") for c in claims.values())
    lo, hi = FORMATS[fmt]["words"]

    def ok(text: str) -> bool:
        return not check_quantities_against_source(text, [source])

    pool = [(k, c, p) for k, c in claims.items() if (p := _pieces(c["text"])) and all(ok(x) for x in p)]
    limits = [x for x in pool if _LIMIT.search(x[1]["text"])]
    answer = limits[-1] if limits else pool[-1] if pool else None
    spare = limits[-2] if len(limits) > 1 else None
    taken = {x[0] for x in (answer, spare) if x}
    dm = ((node.get("data_meaning") or {}).get("from_chapter") or {}).get("text", "")
    dec = node.get("decision_on_data") or {}
    data = [p for p in _good(dm) if ok(p)][:2] or [p for p in _good(" ".join([dec.get("rule", ""), dec.get("conclusion", "")])) if ok(p)][:2]
    body, words = [], 0
    for k, _, parts in pool:
        if k in taken:
            continue
        for p in parts:
            if words + count_words(p) <= hi - 60:
                body.append({"kind": "mechanism", "text": p, "on_screen": _screen(p), "claim": k, "frame": 1})
                words += count_words(p)
        if fmt == "9:16" and len(body) >= 5:
            break
    first = next(iter(claims), "c1")
    title = _FORMULA.sub("", node["title"]).strip()
    hook = f"{title}: где здесь легко ошибиться?"
    if count_words(hook) > 14 or _LETTER.search(title) or _SYMBOL.search(title):
        hook, title = "Где в этом шаге легко ошибиться?", "Где легко ошибиться?"
    lines = [{"kind": "hook", "text": hook, "on_screen": _screen(title), "claim": first, "frame": 1}]
    for i, ln in enumerate(body):
        ln["frame"] = 1 + (i * max(1, n_frames)) // max(1, len(body))
    last = max(1, n_frames)

    def add(kind: str, parts: list[str], claim: str) -> list[dict]:
        return [{"kind": kind, "text": p, "on_screen": _screen(p), "claim": claim, "frame": last} for p in parts]

    cut = max(3, len(body) * 2 // 3)
    # есть утверждение-граница — спрашиваем об условии; нет — о следствии, ответ — последнее утверждение шага
    ask = ({"kind": "predict", "text": "Предскажите: верно ли это всегда, или нужно условие?", "on_screen": "Верно ли это всегда?"} if limits else
           {"kind": "predict", "text": "Предскажите: к чему это приводит?", "on_screen": "К чему это приводит?"})
    ask.update(claim=answer[0] if answer else first, frame=last)
    tail = add("answer", answer[2][:1], answer[0]) + add("limit", answer[2][1:], answer[0]) if answer else []
    if spare:
        tail += add("limit", spare[2], spare[0])
    tail += [x for p in data for x in add("data", [p], _nearest(p, claims))]

    # Текст карточки (суть, механизм, нюансы, практика) — когда утверждений мало для формата или нет строки limit.
    said = [x["text"] for x in [*lines, *body, *tail]]
    ans_text = tail[0]["text"] if tail else ""
    words = sum(count_words(t) for t in said) + 20

    def extra(kind: str, text: str, *, room: int, before_ask: bool = False) -> list[dict]:
        nonlocal words
        got = []
        for p in _good(text):
            if not ok(p) or any(overlap(p, s) >= 0.6 for s in said) or (before_ask and ans_text and overlap(p, ans_text) >= 0.5):
                continue
            if words + count_words(p) > room:
                break
            got.append({"kind": kind, "text": p, "on_screen": _screen(p), "claim": _nearest(p, claims), "frame": last if not before_ask else 1})
            said.append(p)
            words += count_words(p)
        return got

    la = node.get("layers") or {}
    room = lo + 40 if fmt == "9:16" else hi - 30
    nuance = (la.get("nuances") or {}).get("text", "")
    breaks = ((node.get("data_meaning") or {}).get("breaks") or {}).get("text", "")
    limit_lines = [] if any(x["kind"] == "limit" for x in tail) else (extra("limit", nuance, room=hi - 15)[:2] or extra("limit", breaks, room=hi - 15)[:1])
    if not limit_lines and not any(x["kind"] == "limit" for x in tail):
        # нюанс уже звучит утверждением после вопроса — эта строка и есть граница применимости
        hit = [x for x in body[cut:] if any(overlap(x["text"], p) >= 0.6 for p in _pieces(nuance))] or [x for x in body[cut:] if _LIMIT.search(x["text"])]
        if hit:
            hit[0]["kind"] = "limit"
    data_more = [] if any(x["kind"] == "data" for x in tail) else extra("data", breaks, room=hi - 10)[:1]  # что ломается в данных
    thin = len(body[:cut]) < 3  # до вопроса меньше трёх строк механизма — объяснение из сути и механизма карточки
    head = extra("context", (la.get("main") or {}).get("text", ""), room=room, before_ask=True) if words < lo or thin else []
    why = extra("why", (la.get("mechanism") or {}).get("text", ""), room=room, before_ask=True) if words < lo or thin or fmt == "16:9" else []
    late = []
    if fmt == "16:9":
        late = extra("now", (node.get("now_practice") or {}).get("text", ""), room=room)
        late += extra("analogy", (node.get("it_analogy") or {}).get("text", ""), room=room)
    end = {"kind": "transfer", "text": "По каким данным вы проверите это на своей скважине?", "on_screen": "Что покажут ваши данные?", "claim": first, "frame": last}
    out = [*lines, *head, *body[:cut], *why, ask, *tail, *limit_lines, *data_more, *body[cut:], *late, end]
    top = FORMATS[fmt]["lines"][1]
    while len(out) > top:  # лишние строки механизма — с конца; три до вопроса остаются
        i = next((j for j in range(len(out) - 1, 0, -1) if out[j]["kind"] in ("mechanism", "now", "analogy") and (j > out.index(ask) or sum(x["kind"] == "mechanism" for x in out[:out.index(ask)]) > 3)), None)
        if i is None:
            break
        out.pop(i)
    return {
        "lines": out,
        "prediction_question": ask["text"], "transfer_question": end["text"],
        "data_bridge": dm or node["title"], "checklist_note": "", "by": "rules",
    }


# ---------------------------------------------------------------- модель
def write_script(llm: LLM, node: dict, fmt: str, pictures: list[str], caption: str, *, log=print) -> dict | None:
    """Сценарий моделью с проверкой кодом и судьёй. None — не получилось: возьмите script_by_rules."""
    n_frames = max(1, len(pictures))
    pic = f"{caption}. Кадры: " + "; ".join(f"{i}) {p}" for i, p in enumerate(pictures, 1)) if len(pictures) > 1 else f"{caption}. Кадр один."
    system = [prompt("node_script", format=FORMATS[fmt]["ru"], picture=pic)]
    jsys = [prompt("node_script_judge")]
    body = card(node)
    label = f"script:{node['id'].split('.')[-1]}:{fmt.replace(':', 'x')}"
    ask, doc = body, None
    for attempt in range(3):
        try:
            doc = llm.json("senior", system, ask, SCRIPT_SCHEMA, label=f"{label}:{attempt}", effort="medium", max_tokens=16000)
        except LLMError as e:
            log(f"  сценарий {node['id']}: {str(e)[:120]}")
            return None
        for ln in doc["lines"]:
            ln["text"] = re.sub(r"\s+", " ", ln["text"]).strip().replace("—", "–")
            ln["claim"] = ln["claim"].split("#")[-1]
            ln["frame"] = min(max(1, int(ln["frame"])), n_frames)
        probs = script_problems(doc, node, fmt, n_frames)
        if not probs:
            view = "\n".join(f"{i}. [{x['kind']}, {x['claim']}] {x['text']}" for i, x in enumerate(doc["lines"], 1))
            try:
                issues = llm.json("senior", jsys, f"{body}\n\nСтроки:\n{view}", JUDGE_SCHEMA, label=f"{label}:judge{attempt}", effort="medium", max_tokens=6000)["issues"]
            except LLMError as e:
                log(f"  сценарий {node['id']}: судья не ответил ({str(e)[:80]})")
                return None
            issues = [x for x in issues if 1 <= x["n"] <= len(doc["lines"])]
            if not issues:
                doc["by"] = "model"
                return doc
            # правки судьи принимаем, если они проходят те же проверки; второй круг судит уже исправленный текст
            trial = json.loads(json.dumps(doc))
            for x in issues:
                if x["fix"].strip():
                    trial["lines"][x["n"] - 1]["text"] = re.sub(r"\s+", " ", x["fix"]).strip().replace("—", "–")
            if not script_problems(trial, node, fmt, n_frames):
                view = "\n".join(f"{i}. [{x['kind']}, {x['claim']}] {x['text']}" for i, x in enumerate(trial["lines"], 1))
                try:
                    left = llm.json("senior", jsys, f"{body}\n\nСтроки:\n{view}", JUDGE_SCHEMA, label=f"{label}:rejudge{attempt}", effort="medium", max_tokens=6000)["issues"]
                except LLMError:
                    left = issues
                if not left:
                    trial["by"] = "model"
                    trial["judge_fixed"] = len(issues)
                    return trial
                issues = left
                doc = trial
            probs = [f"строка {x['n']}: {x['problem']}" for x in issues]
        ask = (body + "\n\nПрошлый вариант не прошёл проверку. Исправь всё перечисленное, остальное сохрани.\n" + "\n".join(f"— {p}" for p in probs[:14])
               + "\n\nПрошлый вариант:\n" + json.dumps({k: doc[k] for k in SCRIPT_SCHEMA["required"]}, ensure_ascii=False))
    log(f"  сценарий {node['id']}: после трёх попыток замечания остались ({probs[0][:120]}), беру сценарий по правилам")
    return None


# ---------------------------------------------------------------- правка факта без модели
def snapshot(node: dict) -> dict:
    return {"claims": {k: c["text"] for k, c in claim_map(node).items()}, "card": sentences(card(node))}


def _skeleton(text: str) -> str:
    return re.sub(r"\d+(?:[.,]\d+)?", "#", text)


def number_swaps(old: str, new: str) -> dict[str, str]:
    """Если между версиями поменялись только числа: {старое: новое}."""
    if _skeleton(old) != _skeleton(new):
        return {}
    a, b = re.findall(r"\d+(?:[.,]\d+)?", old), re.findall(r"\d+(?:[.,]\d+)?", new)
    return {x: y for x, y in zip(a, b) if x != y}


def _swap(text: str, swaps: dict[str, str]) -> str:
    for x, y in swaps.items():
        alt = {x, x.replace(".", ","), x.replace(",", ".")}
        text = re.sub(r"(?<![\d.,])(?:" + "|".join(map(re.escape, sorted(alt))) + r")(?![\d]|[.,]\d)", y, text)
    return text


def patch_script(doc: dict, node: dict, n_frames: int) -> tuple[dict, list[str]]:
    """Переносит правку слоя в готовый сценарий: меняет числа, изменённое утверждение произносит новой формулировкой."""
    old = doc.get("snapshot") or {}
    new = snapshot(node)
    claims = claim_map(node)
    doc = json.loads(json.dumps(doc))
    log: list[str] = []
    pairs = [(old["claims"][k], new["claims"][k], k) for k in old.get("claims", {}) if k in new["claims"] and old["claims"][k] != new["claims"][k]]
    sm = difflib.SequenceMatcher(a=old.get("card", []), b=new["card"], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace":
            pairs += [(x, y, None) for x, y in zip(old["card"][i1:i2], new["card"][j1:j2])]
    source = card(node) + "\n" + "\n".join(c.get("quote", "") for c in claims.values())
    keep = []
    for ln in doc["lines"]:
        before = ln["text"]
        if ln["claim"] not in claims:
            if ln["kind"] not in ("hook", "predict", "answer", "transfer"):
                log.append(f"реплика снята (утверждения {ln['claim']} больше нет): {before}")
                continue
            ln["claim"] = _nearest(ln["text"], claims)
        for o, n, k in pairs:
            swaps = number_swaps(o, n)
            if swaps and (k == ln["claim"] or overlap(ln["text"], o) >= 0.3):
                ln["text"], ln["on_screen"] = _swap(ln["text"], swaps), _swap(ln["on_screen"], swaps)
            elif k == ln["claim"] and not swaps and ln["kind"] not in ASKS | {"analogy"} and len(p := _pieces(n)) == 1:
                cand = {**ln, "text": p[0], "on_screen": _screen(p[0])}
                if not line_problems(cand, claims, source, n_frames):
                    ln.update(cand)
        if ln["text"] != before:
            log.append(f"{before} → {ln['text']}")
        keep.append(ln)
    doc["lines"] = keep
    for key in ("prediction_question", "transfer_question", "data_bridge"):
        for o, n, _ in pairs:
            if (swaps := number_swaps(o, n)) and overlap(doc.get(key, ""), o) >= 0.3:
                doc[key] = _swap(doc[key], swaps)
    return doc, log


def get_script(cfg: Config, ch: Chapter, node: dict, fmt: str, pictures: list[str], caption: str, *, llm: LLM | None = None, force: bool = False, log=print) -> dict:
    """Сценарий узла: сохранённый (узел не менялся), с перенесённой правкой, новый от модели или по правилам."""
    d = ch.build_dir / "scripts"
    p = d / f"{node['id'].replace('.', '_')}_{fmt.replace(':', 'x')}.json"
    h, n = node_hash(node, fmt), max(1, len(pictures))
    old = json.loads(p.read_text(encoding="utf-8")) if p.exists() and not force else None
    if old and old.get("node_hash") == h and not (old.get("by") == "rules" and llm is not None):
        return old
    doc = None
    if old and old.get("by", "").startswith("model") and old.get("node_hash") != h:
        doc, changes = patch_script(old, node, n)
        for c in changes:
            log(f"  сценарий {node['id']}: {c}")
        doc["by"] = "model+edit"
    if doc is None and llm is not None:
        doc = write_script(llm, node, fmt, pictures, caption, log=log)
    if doc is None:
        doc = script_by_rules(node, fmt, n)
    doc = {**doc, "node_id": node["id"], "format": fmt, "node_hash": h, "snapshot": snapshot(node)}
    d.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def timeline(doc: dict) -> list[dict]:
    """Реплики с плановыми таймкодами; после вопроса-предсказания пауза 1,5 с."""
    out, t = [], 0.0
    for ln in doc["lines"]:
        dur = max(1.6, count_words(ln["text"]) / WPS)
        scr = ln.get("on_screen", "").strip()
        out.append({**ln, "on_screen": scr[:1].upper() + scr[1:], "t_start": round(t, 2), "t_end": round(t + dur, 2)})
        t += dur + (1.5 if ln["kind"] == "predict" else 0.25)
    return out
