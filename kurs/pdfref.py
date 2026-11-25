"""Текстовый слой PDF как справка для чтения индексов и знаков.

Цитаты и числа берутся только из md: PDF лишь показывает, как на самом деле напечатан индекс,
который распознавание в md заменило. Нет PDF, pdftotext или текстового слоя — справка пустая.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from .config import Chapter, Config

MIN_OVERLAP = 0.55
_WORD = re.compile(r"[А-Яа-яЁёA-Za-z]{5,}")


def _words(text: str) -> set[str]:
    text = re.sub(r"-\s*\n\s*", "", text or "")
    return {w.lower().replace("ё", "е") for w in _WORD.findall(text)}


def page_text(pdf: Path, page: int) -> str:
    try:
        out = subprocess.run(
            ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), "-"],
            capture_output=True, timeout=30, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.decode("utf-8", "replace")


def overlap(md: str, pdf: str) -> float:
    """Доля слов страницы md, которые есть на странице PDF."""
    a, b = _words(md), _words(pdf)
    return len(a & b) / len(a) if len(a) >= 15 else 0.0


def candidates(cfg: Config, ch: Chapter) -> list[Path]:
    if ch.extra.get("pdf"):
        pat = ch.extra["pdf"]
        return sorted(cfg.input_dir.glob(pat)) if not Path(pat).is_absolute() else [Path(pat)]
    dirs = [cfg.input_dir / "книги"]
    if ch.file:
        dirs.append(cfg.resolve(ch.file).parent)
    seen: dict[Path, None] = {}
    for d in dirs:
        if d.is_dir():
            for p in sorted(d.glob("*.pdf")):
                seen.setdefault(p)
    return list(seen)


def load_pdf_pages(cfg: Config, ch: Chapter, pages: dict[int, str]) -> dict[int, str]:
    """Страница → текст PDF, только для страниц, где PDF совпал с md."""
    cache = ch.build_dir / "pdf_pages.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
        if set(map(int, data["checked"])) == set(pages):
            return {int(k): v for k, v in data["pages"].items()}
    if not shutil.which("pdftotext"):
        return {}
    probe = sorted(pages, key=lambda p: -len(_words(pages[p])))[:3]
    best, best_score = None, 0.0
    for pdf in candidates(cfg, ch):
        score = sum(overlap(pages[p], page_text(pdf, p)) for p in probe) / max(1, len(probe))
        if score > best_score:
            best, best_score = pdf, score
    out: dict[int, str] = {}
    if best and best_score >= MIN_OVERLAP:
        for p in sorted(pages):
            t = page_text(best, p)
            if overlap(pages[p], t) >= MIN_OVERLAP:
                out[p] = re.sub(r"[ \t]{3,}", "  ", re.sub(r"\n{3,}", "\n\n", t)).strip()
    cache.write_text(
        json.dumps({"pdf": best.name if out and best else None, "checked": sorted(pages), "pages": out}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return out


def reference_block(pdf_pages: dict[int, str], pages: list[int]) -> str:
    have = [p for p in pages if pdf_pages.get(p)]
    if not have:
        return "<pdf_pages>Текста PDF нет: индексы читай по таблице замен и по физике.</pdf_pages>"
    body = "\n".join(f'<pdf page="{p}">\n{pdf_pages[p]}\n</pdf>' for p in have)
    return f"<pdf_pages>\n{body}\n</pdf_pages>"
