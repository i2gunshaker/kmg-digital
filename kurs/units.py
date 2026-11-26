"""Вырезка главы и разбиение на единицы (абзац, рисунок, формула, таблица, задача, заголовок).

Детерминированно, без LLM. Единица получает устойчивый id вида p095.03 — третья единица на стр. 95.
Блоки описаний рисунков, сгенерированные моделью при извлечении (`----` / `━━━ FORMAT …` / `----`),
не считаются текстом автора: они выделяются в единицы kind=figure с флагом model_description
и исключаются из корпуса для проверки цитат и чисел.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import Chapter, Config

PAGE_RE = re.compile(r"<!--\s*(?:page|стр\.?)\s*(\d+)\s*-->", re.I)
COMMENT_RE = re.compile(r"<!--.*?-->")
TASK_RE = re.compile(
    r"^\s*[*_]{0,2}(?:(?:Задача|Упражнение|Вопрос|Problem|Exercise|Question)\s*№?\s*)?(\d{1,3}(?:\.\d{1,3})?)[.)][*_]{0,2}\s+\S", re.I
)
FIG_RE = re.compile(r"^\s*(рис\.|рисунок|fig\.|figure)\s*\d", re.I)
TABLE_CAPTION_RE = re.compile(r"^\s*(таблица|table)\s*\d", re.I)
EXAMPLE_RE = re.compile(r"^\s*(пример|example|решение|solution)\b", re.I)
TASK_HEADING_RE = re.compile(
    r"^(?:задачи|задача|упражнения|контрольные\s+вопросы(?:\s+и\s+задачи)?|вопросы\s+и\s+задачи|"
    r"exercises|problems|practice\s+problems|review\s+questions|questions\s+and\s+problems)\b",
    re.I,
)

_EN_STOPWORDS = {
    "the", "of", "and", "in", "to", "is", "for", "with", "that", "from", "by", "on", "are", "as",
    "this", "which", "be", "or", "an", "at", "can", "into", "between", "when", "where", "rock",
    "porosity", "permeability", "reservoir", "fluid", "pressure", "well", "log",
}
_MODEL_BLOCK_RE = re.compile(r"----\s*\n\s*━━━.*?(?:\n\s*----|\nKEYWORDS:[^\n]*)", re.S)
_MATH_RE = re.compile(r"\$\$.*?\$\$|\\\[.*?\\\]|\\\(.*?\\\)|\$[^$\n]+\$", re.S)


def _strip_markup_for_lang(text: str) -> str:
    s = _MODEL_BLOCK_RE.sub(" ", text or "")
    s = COMMENT_RE.sub(" ", s)
    s = _MATH_RE.sub(" ", s)
    return s


def detect_language(text: str) -> str:
    """Различает русский (в т.ч. с OCR-латинизацией) и английский тексты."""
    clean = _strip_markup_for_lang(text)
    cyr = len(re.findall(r"[А-Яа-яЁё]", clean))
    lat = len(re.findall(r"[A-Za-z]", clean))
    words = re.findall(r"[A-Za-z]{2,}", clean.lower())
    en_hits = sum(1 for w in words if w in _EN_STOPWORDS)
    if lat > cyr and en_hits >= 3:
        return "en"
    if cyr == 0 and lat > 0 and en_hits >= 1:
        return "en"
    return "ru"


def is_ocr_garbled(text: str) -> bool:
    """Детектор испорченного русского OCR с заменой кириллицы латиницей (пример: OchoBbAcb)."""
    clean = _strip_markup_for_lang(text)
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", clean)
    if not tokens:
        return False
    mixed_case = 0
    mixed_script = 0
    lat_tokens = 0
    en_hits = 0
    for tok in tokens:
        has_cyr = bool(re.search(r"[А-Яа-яЁё]", tok))
        has_lat = bool(re.search(r"[A-Za-z]", tok))
        if has_cyr and has_lat:
            mixed_script += 1
        if has_lat:
            lat_tokens += 1
            if tok.lower() in _EN_STOPWORDS:
                en_hits += 1
            # Внутренний скачок регистра после строчной буквы (OchoBbAcb, TeppHReHHOM, BbIeJIHTb).
            if re.search(r"[a-zа-яё][A-ZА-ЯЁ]", tok):
                mixed_case += 1
    if mixed_script >= 2 or mixed_case >= 3:
        return True
    if lat_tokens >= 8 and en_hits == 0 and mixed_case >= 1:
        return True
    return False


@dataclass
class Unit:
    id: str
    kind: str  # paragraph|figure|formula|table|worked_example|task|heading
    page_pdf: int
    text: str
    pages: list[int] = field(default_factory=list)  # все страницы, по которым идёт единица
    line: int = 0
    model_description: bool = False
    task_number: str | None = None
    section: str = ""
    ocr_garbled: bool = False

    @property
    def excerpt(self) -> str:
        words = re.sub(r"\s+", " ", self.text).strip().split(" ")
        return " ".join(words[:12])


# ---------------------------------------------------------------- вырезка
def cut_chapter(cfg: Config, ch: Chapter) -> Path:
    """Создаёт build/<id>/<id>.md: фрагмент книги с метками страниц."""
    if ch.file:
        src = cfg.resolve(ch.file)
        text = src.read_text(encoding="utf-8")
        ch.md_path.write_text(text, encoding="utf-8")
        return ch.md_path
    book = cfg.resolve(ch.book)
    lines = book.read_text(encoding="utf-8").splitlines()
    a, b = ch.lines
    # Страница, на которой начинается фрагмент: последняя метка до начала.
    page = None
    for ln in lines[: a - 1]:
        m = PAGE_RE.search(ln)
        if m:
            page = int(m.group(1))
    body = lines[a - 1 : b]
    head = [f"<!-- фрагмент: {book.name}, строки исходного md {a}–{b} -->"]
    if page is not None and not (body and PAGE_RE.search(body[0])):
        head.append(f"<!-- page {page} -->")
    ch.md_path.write_text("\n".join(head + body) + "\n", encoding="utf-8")
    return ch.md_path


# ---------------------------------------------------------------- разбор
def _clean_leading_emph(s: str) -> str:
    return re.sub(r"^[*_]+\s*", "", s.strip())


def _task_num(s: str) -> tuple[int, ...]:
    """«86» → (86,), «3.1» → (3, 1): номера задач сравниваются по порядку, в том числе составные."""
    return tuple(int(x) for x in s.split("."))


def _classify(block: list[str], in_tasks: bool, last_task_num: tuple[int, ...] | None) -> str:
    first = block[0].strip()
    first_clean = _clean_leading_emph(first)
    if first.startswith("#"):
        htext = _clean_leading_emph(first.lstrip("#"))
        if FIG_RE.match(htext):
            return "figure"
        if TABLE_CAPTION_RE.match(htext):
            return "table"
        if EXAMPLE_RE.match(htext):
            return "worked_example"
        return "heading"
    if all(l.strip().startswith("|") for l in block if l.strip()):
        return "table"
    if first.startswith("$$") and block[-1].strip().endswith("$$"):
        return "formula"
    if first.startswith(r"\[") and block[-1].strip().endswith(r"\]"):
        return "formula"
    if FIG_RE.match(first_clean):
        return "figure"
    if TABLE_CAPTION_RE.match(first_clean):
        return "table"
    if in_tasks:
        m = TASK_RE.match(first)
        if m:
            num = _task_num(m.group(1))
            if last_task_num is not None and num <= last_task_num:
                return "paragraph"
            return "task"
    if EXAMPLE_RE.match(first_clean):
        return "worked_example"
    return "paragraph"


def _initial_page(lines: list[str]) -> int:
    for ln in lines:
        m = PAGE_RE.search(ln)
        if m:
            return max(1, int(m.group(1)) - 1)
    return 1


def _starts_model_block(lines: list[str], i: int) -> bool:
    ln = lines[i]
    if ln.lstrip().startswith("━━━"):
        return True
    if ln.strip() == "----":
        j = i + 1
        while j < len(lines):
            s = lines[j].strip()
            if not s or PAGE_RE.search(lines[j]) or COMMENT_RE.fullmatch(s):
                j += 1
                continue
            return lines[j].lstrip().startswith("━━━")
    return False


def parse_units(md: str) -> tuple[list[Unit], dict[int, str]]:
    """Возвращает единицы и текст автора по страницам (без описаний модели)."""
    lines = md.splitlines()
    units: list[Unit] = []
    page_text: dict[int, list[str]] = {}
    page = _initial_page(lines)
    counters: dict[int, int] = {}
    section = ""
    in_tasks = False
    last_task_num: tuple[int, ...] | None = None
    block: list[str] = []
    block_pages: list[int] = []
    block_line = 0
    i = 0

    def new_id(p: int) -> str:
        counters[p] = counters.get(p, 0) + 1
        return f"p{p:03d}.{counters[p]:02d}"

    def flush():
        nonlocal block, block_pages, section, in_tasks, last_task_num
        if not any(l.strip() for l in block):
            block, block_pages = [], []
            return
        kind = _classify(block, in_tasks, last_task_num)
        text = "\n".join(block).strip()
        if kind == "heading":
            section = text.lstrip("#").strip()
            in_tasks = bool(TASK_HEADING_RE.match(section))
            last_task_num = None
        elif kind == "paragraph" and in_tasks:
            m_reset = TASK_RE.match(block[0].strip())
            if m_reset and last_task_num is not None and _task_num(m_reset.group(1)) <= last_task_num:
                # Нумерованный список (1., 2., 3.) после задач — это уже не задачи.
                in_tasks = False
                last_task_num = None
            elif (
                units
                and units[-1].kind == "task"
                and text
                and text[0].islower()
                and not units[-1].text.rstrip().endswith((".", "?", "!", ":", ";", "»", ")"))
            ):
                # Продолжение задачи, разорванной пустой строкой или меткой страницы.
                units[-1].text = units[-1].text.rstrip() + "\n" + text
                units[-1].pages = sorted(set(units[-1].pages + block_pages))
                units[-1].ocr_garbled = is_ocr_garbled(units[-1].text)
                block, block_pages = [], []
                return

        if kind == "task" and in_tasks:
            parts = re.split(r"\n(?=\s*\d{1,3}(?:\.\d{1,3})?[.)]\s+\S)", text)
            for part in parts:
                part_s = part.strip()
                m = TASK_RE.match(part_s)
                if m:
                    last_task_num = _task_num(m.group(1))
                u = Unit(
                    new_id(block_pages[0]),
                    "task",
                    block_pages[0],
                    part_s,
                    sorted(set(block_pages)),
                    block_line,
                    section=section,
                    task_number=m.group(1) if m else None,
                    ocr_garbled=is_ocr_garbled(part_s),
                )
                units.append(u)
        else:
            units.append(
                Unit(
                    new_id(block_pages[0]),
                    kind,
                    block_pages[0],
                    text,
                    sorted(set(block_pages)),
                    block_line,
                    section=section,
                    ocr_garbled=is_ocr_garbled(text),
                )
            )
        block, block_pages = [], []

    while i < len(lines):
        ln = lines[i]
        m = PAGE_RE.search(ln)
        if m:
            page = int(m.group(1))
            page_text.setdefault(page, [])
            i += 1
            continue
        if COMMENT_RE.fullmatch(ln.strip() or "x"):
            i += 1
            continue
        # Блок описания рисунка от модели (с закрывающим `----` или без него после `KEYWORDS:`).
        if _starts_model_block(lines, i):
            flush()
            desc_lines: list[str] = []
            model_pages: list[int] = []
            seen_fmt = False
            start_line = i + 1
            k = i
            while k < len(lines):
                cur = lines[k]
                pm = PAGE_RE.search(cur)
                if pm:
                    page = int(pm.group(1))
                    page_text.setdefault(page, [])
                    model_pages.append(page)
                    k += 1
                    continue
                if COMMENT_RE.fullmatch(cur.strip() or "x"):
                    k += 1
                    continue
                if cur.strip() == "----":
                    if not seen_fmt:
                        k += 1
                        continue
                    k += 1
                    break
                if cur.lstrip().startswith("━━━"):
                    seen_fmt = True
                    if not desc_lines:
                        start_line = k + 1
                if not model_pages:
                    model_pages.append(page)
                desc_lines.append(cur)
                if cur.lstrip().startswith("KEYWORDS:"):
                    t = k + 1
                    while t < len(lines):
                        ts = lines[t].strip()
                        if not ts or PAGE_RE.search(lines[t]) or COMMENT_RE.fullmatch(ts):
                            t += 1
                            continue
                        break
                    if t >= len(lines) or (lines[t].strip() != "----" and not lines[t].lstrip().startswith("━━━")):
                        k += 1
                        break
                k += 1
            desc = "\n".join(desc_lines).strip()
            mp0 = model_pages[0] if model_pages else page
            units.append(
                Unit(
                    new_id(mp0),
                    "figure",
                    mp0,
                    desc,
                    sorted(set(model_pages or [mp0])),
                    start_line,
                    model_description=True,
                    section=section,
                )
            )
            i = k
            continue
        if not ln.strip():
            flush()
            i += 1
            continue
        # Новая структурная единица (заголовок, рисунок, таблица, пример, задача) без пустой строки — отделяем.
        ln_clean = _clean_leading_emph(ln)
        if block and (
            ln.strip().startswith("#")
            or FIG_RE.match(ln_clean)
            or TABLE_CAPTION_RE.match(ln_clean)
            or EXAMPLE_RE.match(ln_clean)
            or (in_tasks and TASK_RE.match(ln))
        ):
            flush()
        if not block:
            block_line = i + 1
            block_pages = [page]
        # Таблица отделяется от абзаца даже без пустой строки.
        starts_table = ln.strip().startswith("|")
        if block and starts_table != block[-1].strip().startswith("|"):
            flush()
            block_line = i + 1
            block_pages = [page]
        if page not in block_pages:
            block_pages.append(page)
        block.append(ln)
        page_text.setdefault(page, []).append(ln)
        i += 1
    flush()
    return units, {p: "\n".join(t) for p, t in page_text.items()}


def build_units(cfg: Config, ch: Chapter) -> list[Unit]:
    if not ch.md_path.exists():
        cut_chapter(cfg, ch)
    md_text = ch.md_path.read_text(encoding="utf-8")
    if not ch.language:
        ch.language = detect_language(md_text)
    units, pages = parse_units(md_text)
    out = ch.build_dir / "units.json"
    out.write_text(json.dumps([asdict(u) for u in units], ensure_ascii=False, indent=1), encoding="utf-8")
    (ch.build_dir / "pages.json").write_text(
        json.dumps({str(k): v for k, v in pages.items()}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return units


def load_units(ch: Chapter) -> list[Unit]:
    data = json.loads((ch.build_dir / "units.json").read_text(encoding="utf-8"))
    return [Unit(**d) for d in data]


def load_pages(ch: Chapter) -> dict[int, str]:
    data = json.loads((ch.build_dir / "pages.json").read_text(encoding="utf-8"))
    return {int(k): v for k, v in data.items()}
