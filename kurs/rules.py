"""Маршрутизация и задания по правилам, без модели: запасной путь, когда денег или связи нет.

Всё берётся из готового слоя: тексты — утверждения, шаги, признаки и определения узла, неверные варианты —
заблуждения доменного пакета и утверждения других узлов. Код ничего не сочиняет.
"""
from __future__ import annotations

import hashlib
import re

from rapidfuzz import fuzz

from .config import Chapter
from .quotes import sentences

# ---------------------------------------------------------------- маршрутизация
_TIME = r"времен|во времени|со временем|постепенн|стади|период|эволюц|этап|медленн|быстр|восстан|нарастан|паден|осадконакоплен"
_HIDDEN = r"\bпор[аыуе]?\b|поров|пустот|каверн|трещин|флюид|фильтр|давлен|проникновен|насыщ|пласт|недр|залеж"
_SMALL = r"микро|зерн|\bпор[аыуе]?\b|поров|каверн|тонк|прослой|прослоев|мм\b|молекул"
_LARGE = r"пласт|залеж|массив|бассейн|скважин|риф\b|рифа|платформ|\bкм\b|месторожден"
_CAUSE = r"поэтому|потому|из-за|вызыва|приводит|вследстви|следовательно|зависит|обуслов|благодаря|в результате|так как"

_NAME = {
    "М": "механизм", "В": "эволюция во времени", "И": "косвенное измерение", "Ф": "формула или поле", "П": "пространство",
    "Пр": "процедура", "Н": "неопределённость", "Р": "распознавание",
}
_TEMPLATE = {
    "М": "process-section", "В": "time-evolution", "И": "wellbore-log-sync", "Ф": "field-diffusion-2d", "П": "geometry-section",
    "Пр": "procedure-flow", "Н": "scenario-range", "Р": "feature-recognition",
}
_SCORE_NAME = {
    "time": "изменение во времени", "invisible": "невидимое становится видимым", "scale": "скачок масштаба",
    "counterintuitive": "контринтуитивность", "cause_effect": "причина и следствие",
}
VIDEO_FROM = 6


def _hits(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, re.I))


def video_scores(node: dict, text: str) -> dict[str, int]:
    kinds = {node["difficulty"]["primary"], node["difficulty"]["secondary"]}

    def level(n: int, one: int, two: int) -> int:
        return 2 if n >= two else 1 if n >= one else 0

    small, large = _hits(_SMALL, text), _hits(_LARGE, text)
    misc = node.get("misconception")
    deep = bool(kinds & {"М", "Ф"})
    return {
        "time": max(level(_hits(_TIME, text), 3, 8), 2 if "В" in kinds else 0),
        # Слова о порах и пластах есть в каждом узле, поэтому полный балл — только механизму и полю.
        "invisible": min(level(_hits(_HIDDEN, text), 2, 8), 2 if deep else 1),
        "scale": 2 if min(small, large) >= 6 and kinds & {"М", "П", "Ф"} else 1 if min(small, large) >= 3 else 0,
        "counterintuitive": (2 if misc.get("ref") and node["threshold"] else 1) if misc else 0,
        "cause_effect": max(level(_hits(_CAUSE, text), 2, 5), 1 if "М" in kinds else 0),
    }


def route_by_rules(ch: Chapter, layer: dict, templates: dict[str, str], text_of) -> dict:
    """Формат узла — по типу трудности и сумме критериев видеоёмкости."""
    routes = []
    for n in layer["nodes"]:
        p = n["difficulty"]["primary"]
        scores = video_scores(n, text_of(n))
        total = sum(scores.values())
        # Ролик делается только там, где тип трудности его просит и хотя бы два критерия сработали полностью.
        video = total >= VIDEO_FROM and sum(v == 2 for v in scores.values()) >= 2
        second: str | None = None
        if p in ("М", "В"):
            fmt = "длинное видео" if video else "интерактив"
            second = "карточки"
        elif p == "И":
            fmt, second = "практикум на данных" if n["decision_on_data"] else "интерактив", "карточки"
        elif p == "Ф":
            fmt, second = "интерактив", "шортс" if video and n["threshold"] else "текст"
        elif p == "П":
            fmt, second = "интерактив", "карточки"
        elif p == "Пр":
            fmt, second = "практикум на данных", "интерактив"
        elif p == "Н":
            fmt, second = "интерактив", "практикум на данных" if n["decision_on_data"] else "текст"
        else:
            fmt, second = ("практикум на данных", "карточки") if n["decision_on_data"] else ("карточки", "интерактив")
        if total <= 2 and fmt in ("интерактив", "практикум на данных") and not n["decision_on_data"] and not n["steps"] and not n["formulas"]:
            fmt, second = "текст", "карточки"
        tpl = _TEMPLATE.get(p) if fmt not in ("карточки", "текст") else None
        strong = [_SCORE_NAME[k] for k, v in scores.items() if v == 2]
        why = f"Тип трудности {p} ({_NAME.get(p, p)}). "
        why += f"Полностью сработали критерии: {', '.join(strong)}; " if strong else "Ни один критерий видео не сработал полностью; "
        why += f"сумма {total}, " + ("видео оправдано." if fmt in ("шортс", "длинное видео") else f"ведущий формат — {fmt}.")
        routes.append(
            {
                "node_id": n["id"], "format": fmt, "secondary_format": second, "video_scores": scores, "hook_short": False,
                "template": tpl if tpl in templates else None, "why": why,
            }
        )
    return {"chapter": ch.id, "routes": routes}


# ---------------------------------------------------------------- задания
def _pick(seed: str, items: list, k: int) -> list:
    """Детерминированный выбор: зависит только от текста задания."""
    return sorted(items, key=lambda x: hashlib.sha256((seed + str(x)).encode("utf-8")).hexdigest())[:k]


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _short(text: str, limit: int = 170) -> bool:
    return 15 <= len(text) <= limit and "$" not in text and "\\" not in text


def _one(text: str) -> str:
    s = sentences(text)
    return (s[0] if s else text).rstrip(".;") + "."


def _raw(type_: str, node: dict, claims: list[str], prompt: str, explanation: str, kind: str, **kw) -> dict:
    base = dict(
        type=type_, step_id=node["id"], claim_ids=claims, prompt=prompt, options=[], answer_list=[], expression="", tolerance=0,
        unit="", explanation=_one(explanation), kind=kind,
    )
    return {**base, **kw}


def _opts(texts: list[tuple[str, str]]) -> list[dict]:
    return [{"id": chr(ord("a") + i), "text": t, "misconception": m} for i, (t, m) in enumerate(texts)]


def gap_task(node: dict, terms: list[str], term_rx) -> dict | None:
    """Утверждение с пропущенным термином."""
    for c in node["claims"]:
        text = _plain(c["text"])
        if not _short(text, 220):
            continue
        for t in sorted(terms, key=len, reverse=True):
            m = list(term_rx(t).finditer(text))
            if len(m) != 1 or len(m[0].group(0).split()) > 2 or len(text) - len(m[0].group(0)) < 30:
                continue
            word = m[0].group(0)
            return _raw(
                "gap", node, [c["id"]], text[: m[0].start()] + "{{1}}" + text[m[0].end() :],
                f"В шаге здесь стоит «{word}»", "recall", answer_list=[word],
            )
    return None


_ORDINAL = re.compile(r"^(?:сначала|вначале|прежде всего|затем|далее|потом|после этого|наконец|в конце|теперь)[,\s]+", re.I)
_ACTION = re.compile(r"^(?:не\s+)?[а-яё]+(?:ть|ти|чь|ться|ют|ут|ят|ат|ем|им|ют\w*)\b", re.I)


def order_task(node: dict) -> dict | None:
    """Порядок действий из блока «По шагам». Слова «сначала», «затем» убираются: они выдают ответ."""
    said = [_one(p["text"]) for p in node["steps"] if not p.get("hypothesis")]
    if sum(bool(_ORDINAL.match(s) or _ACTION.match(s)) for s in said) < 0.6 * len(said):
        return None  # в блоке не действия, а рассуждения: порядок не однозначен
    steps = [(t := _ORDINAL.sub("", s))[:1].upper() + t[1:] for s in said]
    steps = [s for s in dict.fromkeys(steps) if _short(s, 200)][:6]
    if len(steps) < 3:
        return None
    opts = _opts([(s, "") for s in reversed(steps)])
    uids = {u for p in node["steps"] for u in p["unit_ids"]}
    claims = [c["id"] for c in node["claims"] if c.get("unit_id") in uids][:3] or [node["claims"][0]["id"]]
    return _raw(
        "order", node, claims, f"Расставьте действия по порядку: {node['title'][:1].lower() + node['title'][1:]}.",
        "Порядок задан в блоке «По шагам» этого узла", "understanding", options=opts, answer_list=[o["id"] for o in reversed(opts)],
    )


def single_task(node: dict, misc: dict[str, str]) -> dict | None:
    """Верное утверждение узла против распространённых заблуждений."""
    ref = (node.get("misconception") or {}).get("ref")
    good = next((c for c in node["claims"] if _short(_plain(c["text"]), 200) and c["kind"] != "assumption"), None)
    if good is None or not ref or ref not in misc:
        return None
    others = _pick(node["id"], [m for m in misc if m != ref], 2)
    texts = [(_plain(good["text"]), "")] + [(misc[m], m) for m in (ref, *others)]
    return _raw(
        "single", node, [good["id"]], f"Какое утверждение верно? Тема шага: {node['title'].lower() if not node['title'].isupper() else node['title']}.",
        "Остальные варианты — распространённые заблуждения", "understanding", options=_opts(texts), answer_list=["a"],
    )


def term_task(node: dict, nodes: list[dict]) -> dict | None:
    """Определение термина среди определений других узлов."""
    for g in node["glossary"]:
        d = _plain(g["definition"])
        if not _short(d, 200):
            continue
        pool = [
            _plain(x["definition"]) for n in nodes if n["id"] != node["id"] for x in n["glossary"]
            if _short(_plain(x["definition"]), 200) and x["term"].lower() not in d.lower()
        ]
        wrong = _pick(g["term"], sorted(set(pool) - {d}), 3)
        stem = g["term"].lower()[: max(4, len(g["term"]) - 3)]
        claim = next((c for c in node["claims"] if stem in c["text"].lower()), node["claims"][0])
        if len(wrong) == 3:
            return _raw(
                "single", node, [claim["id"]], f"Что означает термин «{g['term']}»?", f"{g['term']}: {d}", "recall",
                options=_opts([(d, "")] + [(w, "") for w in wrong]), answer_list=["a"],
            )
    return None


def multiple_task(node: dict, nodes: list[dict]) -> dict | None:
    """Признаки решения на данных этого узла среди признаков других узлов, иначе — утверждения узла среди чужих."""
    dec = node.get("decision_on_data")
    mine = [f for f in dict.fromkeys(_plain(f) for f in dec["features"]) if _short(f, 160) or 8 <= len(f) <= 160] if dec else []
    if len(mine) >= 2:
        pool = [
            _plain(f) for n in nodes if n["id"] != node["id"] and n.get("decision_on_data") for f in n["decision_on_data"]["features"]
            if 8 <= len(_plain(f)) <= 160
        ]
        # Чужой признак не годится, если он похож на свой или упомянут в этом шаге.
        here = " ".join([*mine, *(c["text"] for c in node["claims"]), node["layers"]["mechanism"]["text"]]).lower()
        pool = [p for p in pool if fuzz.partial_token_set_ratio(p.lower(), here) < 100 and max(fuzz.token_set_ratio(p, m) for m in mine) < 55]
        wrong = _pick(node["id"], sorted(set(pool)), 2)
        good = mine[:3]
        if len(wrong) == 2:
            return _raw(
                "multiple", node, [c["id"] for c in node["claims"][:3]],
                f"Какие признаки нужны для решения в этом шаге? Тема: {node['title']}.",
                "Эти признаки названы в блоке «Решение на данных»", "data",
                options=_opts([(g, "") for g in good] + [(w, "") for w in wrong]), answer_list=[chr(97 + i) for i in range(len(good))],
            )
    own = [c for c in node["claims"] if _short(_plain(c["text"]))][:3]
    far = [
        _plain(c["text"]) for n in nodes if abs(n["order"] - node["order"]) >= 4 for c in n["claims"][:3] if _short(_plain(c["text"]))
    ]
    wrong = _pick(node["id"], sorted(set(far)), 2)
    if len(own) < 2 or len(wrong) < 2:
        return None
    return _raw(
        "multiple", node, [c["id"] for c in own], f"Что из этого сказано в шаге «{node['title']}»?",
        "Остальные утверждения относятся к другим шагам", "recall",
        options=_opts([(_plain(c["text"]), "") for c in own] + [(w, "") for w in wrong]), answer_list=[chr(97 + i) for i in range(len(own))],
    )


_EQ = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*([·*×/:+])\s*(\d+(?:[.,]\d+)?)\s*=\s*(\d+(?:[.,]\d+)?)(?![\d.,]*\s*[·*×/^\\])")


def calc_task(node: dict) -> dict | None:
    """Расчёт, который автор сделал сам: «a / b = c» в примере узла."""
    for w in node["worked_examples"]:
        for line in [*w["steps"], w["result"]]:
            m = _EQ.search(_plain(line))
            if not m:
                continue
            a, op, b, c = m.groups()
            op = {"·": "*", "×": "*", ":": "/"}.get(op, op)
            a, b, c = (x.replace(",", ".") for x in (a, b, c))
            val = {"*": lambda: float(a) * float(b), "/": lambda: float(a) / float(b) if float(b) else 0.0, "+": lambda: float(a) + float(b)}[op]()
            if not 0.01 <= abs(val) < 1e5 or abs(val - float(c)) > 0.02 * max(abs(float(c)), 1e-9):
                continue
            name = {"*": "произведение", "/": "частное", "+": "сумму"}[op]
            return _raw(
                "calc", node, [node["claims"][0]["id"]],
                f"Повторите расчёт автора. {_plain(w['condition'])[:200]} Найдите {name} {a.replace('.', ',')} и {b.replace('.', ',')}.",
                "Так считает автор в примере этого шага", "transfer", answer_list=[f"{val:.6g}"], expression=f"{a} {op} {b}",
                tolerance=round(max(abs(val) * 0.02, 0.005), 4),
            )
    return None


_RANGE = re.compile(
    r"(?<![\w.,–-])(?:от\s+)?(\d+(?:[.,]\d+)?)\s*(?:–|—|-|до)\s*(\d+(?:[.,]\d+)?)\s*(км|мкм|мм|см|м|МПа|атм|°С|сут|лет|Ом·м)(?![\w/²³])"
)
_LIMIT = re.compile(r"не\s+(?:выше|более|больше|ниже|менее|меньше)|\bдо\s+\d+[^.]*[–—-]\s*\d", re.I)  # «не выше 10–20» — не размах


def range_task(node: dict) -> dict | None:
    """Размах величины, для которой автор дал границы."""
    for c in node["claims"]:
        text = _plain(c["text"])
        found = _RANGE.findall(text)
        if len(found) != 1 or not _short(text, 200) or _LIMIT.search(text):
            continue
        a, b, unit = found[0]
        lo, hi = float(a.replace(",", ".")), float(b.replace(",", "."))
        if not 0.01 <= hi - lo < 1e5:
            continue
        return _raw(
            "calc", node, [c["id"]], f"В шаге сказано: «{text.rstrip('.')}». На сколько верхняя граница больше нижней?",
            f"Размах — разность границ, {b} и {a} {unit}", "data", answer_list=[f"{hi - lo:.6g}"],
            expression=f"{b.replace(',', '.')} - {a.replace(',', '.')}", tolerance=round(max((hi - lo) * 0.01, 0.005), 4), unit=unit,
        )
    return None


def tasks_by_rules(layer: dict, misc: list[dict], term_rx, want: int) -> list[dict]:
    """Кандидаты в задания в форме ответа модели; проверяет и отбирает их общий код."""
    nodes = [n for n in layer["nodes"] if n["claims"]]
    texts = {m["id"]: m["text"] for m in misc}
    by_type: dict[str, list[dict]] = {k: [] for k in ("order", "calc", "multiple", "single", "gap", "term")}
    for n in nodes:
        terms = [g["term"] for g in n["glossary"]] + list(n["terms_used"])
        for key, made in (
            ("order", order_task(n)), ("calc", calc_task(n) or range_task(n)), ("multiple", multiple_task(n, nodes)), ("single", single_task(n, texts)),
            ("gap", gap_task(n, terms, term_rx)), ("term", term_task(n, nodes)),
        ):
            if made:
                by_type[key].append(made)
    out: list[dict] = []
    per_step: dict[str, int] = {}
    # По кругу: типы чередуются, на шаг не больше двух заданий, узнавания не больше трети.
    while len(out) < want and any(by_type.values()):
        moved = False
        for key in ("order", "single", "multiple", "gap", "calc", "term"):
            pool = by_type[key]
            pool.sort(key=lambda t: (per_step.get(t["step_id"], 0)))
            while pool and len(out) < want:
                t = pool.pop(0)
                if per_step.get(t["step_id"], 0) >= 2:
                    continue
                out.append(t)
                per_step[t["step_id"]] = per_step.get(t["step_id"], 0) + 1
                moved = True
                break
        if not moved:
            break
    return out
