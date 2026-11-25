"""Данные для страниц курса: абзацы со страницами, цитаты под абзацем, термины, части главы, карта связей."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

from markupsafe import Markup, escape

from . import provenance
from .units import Unit
from .verify import numbers

KIND_RU = {"observation": "Наблюдение", "inference": "Вывод", "assumption": "Допущение"}

DIFF_NAMES = {
    "М": "механизм",
    "В": "время",
    "И": "измерение",
    "Ф": "формулы",
    "П": "пространство",
    "Пр": "порядок действий",
    "Н": "неопределённость",
    "Р": "распознавание",
}

_ABBR = r"(?<!рис\.)(?<!табл\.)(?<!стр\.)(?<!см\.)(?<!гл\.)(?<!др\.)(?<!т\.е\.)(?<!т\.д\.)(?<!скв\.)(?<!п\.)(?<!напр\.)"
_SENT = re.compile(_ABBR + r"(?<=[.!?])\s+(?=[А-ЯЁA-Z«(0-9–—])")
# Пометки о состоянии текста книги нужны методисту, а не читателю: показываем отдельной строкой.
_NOTE = re.compile(
    r"опечатк|ошибк\w* распозн|искаж\w+ распозн|в распознанном тексте|разночтен|нуж\w+ сверк|нужен скан|не передан|сверить по|надо сверить|напечатан|\bв md\b|отсутствует в md|фраза повреждена",
    re.I,
)
_MATH = re.compile(
    r"\\\((.+?)\\\)|\\\[(.+?)\\\]|\$\$(.+?)\$\$|\$([^$\n]+?)\$"
    r"|(?<![\w\\])((?:[A-Za-zΔα-ω]{1,2}|\\[a-z]+)_\{(?:[^{}]|\{[^{}]*\}){1,24}\}(?:\s*[=<>≤≥]\s*(?:[A-Za-zΔα-ω]{1,2}|\\[a-z]+)_\{(?:[^{}]|\{[^{}]*\}){1,24}\})?)",
    re.S,
)
_ENDINGS = re.compile(
    r"(иями|ями|ами|иях|иям|ях|ах|ией|ием|ого|его|ому|ему|ыми|ими|ой|ей|ий|ый|ая|яя|ое|ее|ые|ие|ым|им|ом|ем|ам|ям|ую|юю|ов|ев|ью|ию|ия|а|я|о|е|ы|и|у|ю|ь|й)$"
)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text or "") if s.strip()]


def split_notes(text: str) -> tuple[str, list[str]]:
    """Разделяет учебный текст и пометки о состоянии источника."""
    keep, notes = [], []
    for s in sentences(text):
        (notes if _NOTE.search(s) else keep).append(s)
    if not keep:  # весь абзац — пометка
        return "", notes
    return " ".join(keep), notes


_UNIT = r"p\d{3}\.\d{2}"


def plain_note(s: str) -> str:
    """Пометка к тексту книги без внутренних номеров единиц: «В p202.02 …» → «В тексте …»."""
    s = re.sub(rf"\s*\({_UNIT}(?:\s*(?:,|и)\s*{_UNIT})*\)", "", s)
    s = re.sub(rf"\s*\bв единиц(?:е|ах) {_UNIT}(?:\s*(?:,|и)\s*{_UNIT})*", "", s)
    s = re.sub(rf"^В md в {_UNIT}(?:\s*(?:,|и)\s*(?:{_UNIT}|\([\d.]+\)))*", "В файле главы", s)
    s = re.sub(rf"^В {_UNIT}(?:\s*(?:,|и)\s*{_UNIT})*", "В тексте", s)
    s = re.sub(rf"{_UNIT}(?:\s*(?:,|и)\s*{_UNIT})*", "текст", s)
    return re.sub(r"\b[Вв] md\b", lambda m: "В файле главы" if m.group(0)[0] == "В" else "в файле главы", s)


_NBSP = re.compile(r"(?<=\d) (?=(?:%|°|Ом|мкс|МПа|атм|сут|см|мм|км|кг|г/|м(?![а-яё]))|\d{3}(?!\d))")


def rich(text: str) -> Markup:
    """Экранирует текст и оставляет места под формулы."""
    out: list[str] = []
    pos = 0
    text = _NBSP.sub("\u00a0", text or "")
    for m in _MATH.finditer(text or ""):
        tex = next(g for g in m.groups() if g is not None).strip()
        out.append(str(escape(text[pos : m.start()])))
        out.append(f'<span class="math-tex" data-tex="{escape(tex)}">{escape(tex)}</span>')
        pos = m.end()
    out.append(str(escape((text or "")[pos:])))
    return Markup("".join(out))


def clean_tex(tex: str) -> str:
    t = (tex or "").strip()
    # выключная формула вместе с колонтитулом: берём только то, что между $$
    m = re.match(r"\$\$(.+?)(?:\$\$|\$|\Z)", t, re.S)
    if m:
        t = m.group(1)
    t = t.replace("$", " ").strip()
    t = re.sub(r"^\\\[|\\\]$", "", t).strip()
    return re.sub(r"[,;.]\s*$", "", t).strip()


# ---------------------------------------------------------------- термины


def stem(word: str) -> str:
    w = word.lower().replace("ё", "е")
    if len(w) <= 4:
        return w
    cut = _ENDINGS.sub("", w)
    return cut if len(cut) >= 3 else w


def term_pattern(term: str) -> re.Pattern[str] | None:
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", re.sub(r"\(.*?\)", "", term))
    if not words or sum(map(len, words)) < 3:
        return None
    parts = []
    for w in words:
        st = re.escape(stem(w)).replace("е", "[её]")
        parts.append(st + (r"[а-яё]{0,4}" if len(w) > 4 else ""))
    return re.compile(r"(?<![А-Яа-яЁёA-Za-z0-9])(" + r"[\s\-–]+".join(parts) + r")(?![А-Яа-яЁёA-Za-z0-9])", re.I)


class TermMarker:
    """Выделяет термин один раз за курс, в первом тексте шага, где он вводится."""

    def __init__(self) -> None:
        self.done: set[str] = set()

    def html(self, text: str, terms: list[dict]) -> Markup:
        base = text or ""
        marks: list[tuple[int, int, dict]] = []
        for t in sorted(terms, key=lambda x: -len(x["term"])):
            key = t["term"].lower()
            if key in self.done or not t.get("definition"):
                continue
            pat = term_pattern(t["term"])
            m = pat.search(base) if pat else None
            if not m or any(a < m.end() and m.start() < b for a, b, _ in marks):
                continue
            marks.append((m.start(), m.end(), t))
            self.done.add(key)
        out: list[str] = []
        pos = 0
        for a, b, t in sorted(marks, key=lambda q: q[0]):
            out.append(str(rich(base[pos:a])))
            out.append(f'<dfn class="term" tabindex="0" data-term="{escape(t["term"])}" data-def="{escape(t["definition"])}">{escape(base[a:b])}</dfn>')
            pos = b
        out.append(str(rich(base[pos:])))
        return Markup("".join(out))


# ---------------------------------------------------------------- утверждения


_STOP = set("и в на по из за для что как это при не от до с к о у а но или же то его её их так где чем также только если между через под над без".split())


def content_stems(text: str) -> set[str]:
    return {stem(w) for w in re.findall(r"[A-Za-zА-Яа-яЁё*]+", text or "") if w.lower() not in _STOP and len(w) > 2}


def covered(claim: str, text: str) -> bool:
    """Мысль утверждения уже есть в абзаце: те же числа и большинство смысловых слов."""
    if numbers(claim) - numbers(text):
        return False
    a = content_stems(claim)
    return not a or len(a & content_stems(text)) / len(a) >= 0.6


def claim_view(c: dict, marker: TermMarker, terms: list[dict]) -> dict:
    text, notes = split_notes(c["text"])
    return {
        "id": c["id"],
        "html": marker.html(text or c["text"], terms),
        "kind": KIND_RU.get(c["kind"], c["kind"]),
        "page": c["page_pdf"],
        "by_model": bool(c.get("by_model")),
        "prov": provenance.of_claim(c),
        "quote": rich(c["quote"]),
        "notes": [plain_note(s) for s in notes] if text else [],
    }


class Paragraphs:
    """Собирает абзацы шага и раздаёт утверждения по абзацам того же фрагмента книги."""

    def __init__(self, node: dict, marker: TermMarker, terms: list[dict]) -> None:
        self.node, self.marker, self.terms = node, marker, terms
        self.items: list[dict] = []

    def add(
        self, block: str, text: str, pages: Iterable[int] | None, by_model: bool, unit_ids: Iterable[str] = (), hypothesis: bool = False
    ) -> None:
        body, notes = split_notes(text)
        if not body and not notes:
            return
        self.items.append(
            {
                "block": block,
                "text": body,
                "html": self.marker.html(body, self.terms),
                "pages": sorted(set(pages or [])),
                "by_model": by_model,
                "prov": "hypothesis" if hypothesis else ("explanatory" if by_model else "stated"),
                "unit_ids": set(unit_ids or ()),
                "notes": [plain_note(s) for s in notes],
                "quotes": [],
                "extra": [],
            }
        )

    def add_layer(self, block: str, part: dict | None, default_pages: list[int]) -> None:
        if not part:
            return
        paras = part.get("paragraphs")
        if paras:
            for q in paras:
                self.add(block, q["text"], q.get("pages") or default_pages, bool(q.get("by_model")), q.get("unit_ids") or (), bool(q.get("hypothesis")))
            return
        for chunk in re.split(r"\n\s*\n", part.get("text") or ""):
            if chunk.strip():
                self.add(block, chunk.strip(), part.get("pages") or default_pages, bool(part.get("by_model", True)), (), bool(part.get("hypothesis")))

    def attach(self, claims: list[dict]) -> None:
        for c in claims:
            same_unit = [q for q in self.items if c.get("unit_id") and c["unit_id"] in q["unit_ids"]]
            pool = same_unit or [q for q in self.items if c["page_pdf"] in q["pages"]] or self.items
            view = claim_view(c, self.marker, self.terms)
            if not pool:
                self.items.append(
                    {"block": "mechanism", "text": c["text"], "html": view["html"], "pages": [c["page_pdf"]], "by_model": view["by_model"], "prov": view["prov"], "unit_ids": set(), "notes": view["notes"], "quotes": [view], "extra": []}
                )
                continue
            words = content_stems(c["text"])
            best = max(pool, key=lambda q: len(words & content_stems(q["text"])))
            (best["quotes"] if covered(c["text"], best["text"]) else best["extra"]).append(view)

    def block(self, name: str) -> list[dict]:
        return [q for q in self.items if q["block"] == name]


def step_number(text: str) -> str:
    return re.sub(r"^\s*Шаг\s+\d+[.:)]\s*", "", text or "")


# ---------------------------------------------------------------- части главы


_KEEP_UPPER = {"ГИС", "СП", "ПС", "ГК", "НГК", "КС", "БК", "ВНК", "ГНК", "ГВК", "ИИ", "ПССС", "СССР", "США"}
_NUMBERED = re.compile(r"^\s*(§\s*\d+|\d+\.\d+|\d+\.\s|Глава|ГЛАВА|Chapter|Section)")


def _sentence_case(s: str) -> str:
    s = re.sub(r"\s+", " ", s.strip(" #*")).strip()
    letters = [ch for ch in s if ch.isalpha()]
    if letters and sum(ch.isupper() for ch in letters) / len(letters) > 0.7:
        words = []
        for w in s.split(" "):
            core = re.sub(r"[^А-ЯЁA-Z]", "", w)
            words.append(w if core in _KEEP_UPPER or re.fullmatch(r"[IVXLC]+\.?", w) else w.lower())
        s = " ".join(words)
        m = re.search(r"[а-яёa-z]", s)
        if m:
            s = s[: m.start()] + s[m.start()].upper() + s[m.start() + 1 :]
    return s


def _short_heading(s: str, limit: int = 110) -> str:
    s = _sentence_case(s)
    if len(s) <= limit:
        return s.rstrip(".")
    head = re.match(r"^(?:§\s*\d+\.?|\d+(?:\.\d+)*\.?)\s*", s)
    body = s[head.end() :] if head else s
    cut = re.split(r"(?<=[а-яёa-z])\.\s", body)[0]
    if len(cut) > limit:
        cut = cut[: limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
        cut = re.sub(r"\s(?:и|в|на|по|из|за|с|к|о|у|а|для|их|от)…$", "…", cut)
    return ((head.group(0).strip() + " ") if head else "") + cut.rstrip(".")


def chapter_parts(nodes: list[dict], units: list[Unit]) -> list[dict]:
    """Части пути по нумерованным заголовкам книги; одиночные шаги примыкают к соседней части."""
    major: dict[str, str] = {}
    minor: dict[str, str] = {}
    cur, sub = "", ""
    for u in units:
        if u.kind == "heading":
            t = u.text.strip(" #*")
            if re.match(r"^\s*Глава\s+[IVXLC\d]+\.?\s*$", t, re.I):
                cur, sub = "", ""
            elif _NUMBERED.match(t):
                cur, sub = t, ""
            elif not re.match(r"^(Задач|Контрольн|Вопрос|Упражнен|Литератур|Exercise|Problem|Reference)", t, re.I) and len(t.split()) <= 4:
                sub = t
        major[u.id], minor[u.id] = cur, sub

    def vote(n: dict, table: dict[str, str]) -> str:
        c = Counter(table[i] for i in n.get("unit_ids") or [] if i in table)
        return c.most_common(1)[0][0] if c else ""

    groups: list[dict] = []
    for n in nodes:
        key = vote(n, major)
        if groups and groups[-1]["key"] == key:
            groups[-1]["ids"].append(n["id"])
        else:
            groups.append({"key": key, "ids": [n["id"]]})
    merged: list[dict] = []
    for g in groups:
        if merged and (len(g["ids"]) == 1 or len(merged[-1]["ids"]) == 1 or g["key"] == merged[-1]["key"]):
            if len(merged[-1]["ids"]) == 1 and not merged[-1]["key"]:
                merged[-1]["key"] = g["key"]
            merged[-1]["ids"] += g["ids"]
        else:
            merged.append(dict(g))
    out: list[dict] = []
    by_id = {n["id"]: n for n in nodes}
    for g in merged:
        title = _short_heading(g["key"]) if g["key"] else ""
        if len(g["ids"]) > 9:
            runs: list[dict] = []
            for i in g["ids"]:
                s = vote(by_id[i], minor)
                if runs and runs[-1]["sub"] == s:
                    runs[-1]["ids"].append(i)
                elif runs and not s:
                    runs[-1]["ids"].append(i)
                else:
                    runs.append({"sub": s, "ids": [i]})
            if len(runs) > 1 and all(len(r["ids"]) > 1 for r in runs):
                num = re.match(r"^(§\s*\d+|\d+(?:\.\d+)+)", title)
                for r in runs:
                    name = _sentence_case(r["sub"]) or title
                    out.append({"title": f"{num.group(0)}. {name}" if num and r["sub"] else name, "ids": r["ids"]})
                continue
        out.append({"title": title, "ids": g["ids"]})
    if len(out) == 1 or not any(p["title"] for p in out):
        size = 5
        ids = [n["id"] for n in nodes]
        chunks = [ids[k : k + size] for k in range(0, len(ids), size)]
        if len(chunks) > 1 and len(chunks[-1]) == 1:
            chunks[-2] += chunks.pop()
        title = out[0]["title"] if out else ""
        return [{"title": title if len(chunks) == 1 else "", "ids": c, "n": k + 1} for k, c in enumerate(chunks)]
    return [{**p, "n": k + 1} for k, p in enumerate(out)]


# ---------------------------------------------------------------- карта связей


def arc_map(steps: list[dict], row: int = 60, gutter: int = 92) -> dict:
    """Список шагов с дугами слева: опора → шаг. Текст остаётся обычным текстом страницы."""
    idx = {s["id"]: i for i, s in enumerate(steps)}
    arcs = []
    for s in steps:
        for p in s["prereq_ids"]:
            if p in idx and idx[p] < idx[s["id"]]:
                i, j = idx[p], idx[s["id"]]
                y1, y2 = i * row + row / 2, j * row + row / 2
                bulge = min(gutter - 14, 16 + 9 * (j - i))
                x = gutter - 8
                arcs.append({"from": p, "to": s["id"], "d": f"M{x} {y1:.0f} C{x - bulge} {y1:.0f} {x - bulge} {y2:.0f} {x} {y2:.0f}"})
    return {"arcs": arcs, "row": row, "gutter": gutter, "height": row * len(steps)}


def reading_minutes(texts: Iterable[Any], tasks: int) -> int:
    chars = sum(len(str(t)) for t in texts if t)
    return max(3, round(chars / 900 + tasks * 1.2 + 1))
