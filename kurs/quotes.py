"""Цитаты, фразы и единицы: привязка к исходному тексту посимвольно."""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from .units import Unit

PARA_FIELDS = ("mechanism", "steps", "practice", "nuances", "deeper", "conditions")
MAX_SENTENCE_WORDS = 20


# ---------------------------------------------------------------- цитаты
def strict_norm(s: str) -> str:
    """Правило организаторов: не учитываются только пробелы, переносы строк и регистр."""
    return re.sub(r"\s+", "", s or "").lower()


_SOFT = str.maketrans({
    "ё": "е", "«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "’": "'", "‘": "'",
    "—": "-", "–": "-", "−": "-", "‑": "-", "­": "-",
})


def _index_map(text: str) -> tuple[str, list[int]]:
    chars, idx = [], []
    for i, c in enumerate(text):
        if c.isspace():
            continue
        low = c.lower()
        chars.append(low if len(low) == 1 else c)
        idx.append(i)
    return "".join(chars), idx


def _find_span(quote: str, text: str) -> tuple[int, int] | None:
    """Границы цитаты в исходном тексте: точно, с мягкой заменой знаков, затем с опечатками."""
    src, idx = _index_map(text)
    q = strict_norm(quote)
    if not q or not src:
        return None
    for s, t in ((src, q), (src.translate(_SOFT), q.translate(_SOFT))):
        pos = s.find(t)
        if pos != -1:
            return idx[pos], idx[pos + len(t) - 1] + 1
    if len(q) < 16:
        return None
    al = fuzz.partial_ratio_alignment(q.translate(_SOFT), src.translate(_SOFT))
    if al is None or al.score < 90 or al.dest_end - al.dest_start < 8:
        return None
    a, b = idx[al.dest_start], idx[al.dest_end - 1] + 1
    while a > 0 and not text[a - 1].isspace():
        a -= 1
    while b < len(text) and not text[b].isspace():
        b += 1
    return a, b


def snap_quote(quote: str, unit: Unit, pages: dict[int, str]) -> tuple[str, int] | None:
    """Возвращает дословный кусок единицы и страницу, на которой он стоит целиком."""
    span = _find_span(quote, unit.text)
    if not span:
        return None
    # Слово, оборванное переносом на конце страницы, в цитату не входит.
    exact = re.sub(r"[\s,;:]*\S*[A-Za-zА-Яа-яЁё]-$", "", unit.text[span[0]:span[1]].strip())
    cand_pages = unit.pages or [unit.page_pdf]
    norm_pages = {p: strict_norm(pages.get(p, "")) for p in cand_pages}
    nq = strict_norm(exact)
    for p in cand_pages:
        if len(nq) >= 8 and nq in norm_pages[p]:
            return exact, p
    # Цитата перешла через границу страниц: берём самую длинную часть, которая стоит на одной странице.
    words = list(re.finditer(r"\S+", exact))
    best: tuple[int, str, int] | None = None
    for p in cand_pages:
        for k in range(len(words) - 1, 2, -1):
            for part in (exact[: words[k - 1].end()], exact[words[len(words) - k].start():]):
                n = strict_norm(part)
                if len(n) >= 8 and n in norm_pages[p] and (best is None or k > best[0]):
                    best = (k, part, p)
            if best and best[2] == p and best[0] == k:
                break
    return (best[1], best[2]) if best else None


# ---------------------------------------------------------------- единицы и фразы
def is_substantive(u: Unit) -> bool:
    """Единица, в которой есть мысль автора: её нельзя исключить и нужно раскрыть в карточке."""
    if u.model_description or u.kind == "heading":
        return False
    if u.kind in ("formula", "table", "worked_example", "task"):
        return True
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]{2,}", u.text)
    if u.kind == "figure":
        return len(letters) >= 4
    return len(letters) >= 8


_ABBR_RE = re.compile(r"\b(?:т\. ?[едкп]|рис|табл|стр|гл|см|др|пр|им|напр|fig|eq|e\.g|i\.e|vs)\.", re.I)
_MATH_INLINE_RE = re.compile(r"\$\$.*?\$\$|\$[^$]+\$", re.S)


def sentences(text: str) -> list[str]:
    s = _MATH_INLINE_RE.sub("Ф", text or "")
    s = _ABBR_RE.sub(lambda m: m.group(0).replace(".", ""), s)
    return [x.strip() for x in re.split(r"(?<=[.!?;])\s+|\n+", s) if x.strip()]


def long_sentences(text: str, limit: int = MAX_SENTENCE_WORDS) -> list[str]:
    return [x for x in sentences(text) if len(re.findall(r"[\w%°]+(?:[-–][\w]+)*", x)) > limit]


def referenced_units(card: dict) -> set[str]:
    ids: set[str] = set()
    for f in PARA_FIELDS:
        for p in card[f]:
            ids.update(p["unit_ids"])
    ids.update(c["unit_id"] for c in card["claims"])
    ids.update(f["unit_id"] for f in card["formulas"])
    for w in card["worked_examples"]:
        ids.update(w["unit_ids"])
    return ids
