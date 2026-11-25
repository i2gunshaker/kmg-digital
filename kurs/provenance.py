"""Происхождение каждого абзаца курса: кто это утверждает.

Пять значений из архитектурного документа вместо двух «автор / модель»:

- stated      — из книги: слова автора или пересказ утверждения автора с дословной цитатой;
- inferred    — вывод из текста автора (утверждение вида inference, посылки — в цитате);
- explanatory — пояснение модели по указанным страницам главы;
- external    — знание вне главы (аналогия из ИТ, современная практика);
- hypothesis  — нет опоры в главе, нужна проверка специалистом.

Вторая ось — что за утверждение у автора (assertion): observation / inference / assumption.
Схема слоя организаторов не меняется: происхождение выводится из полей by_model, hypothesis, kind и unit_ids.
"""
from __future__ import annotations

LABELS = {
    "stated": ("из книги", "Слова автора книги или пересказ его утверждения; цитата — рядом"),
    "inferred": ("вывод", "Вывод из текста автора; на чём он держится — в цитате"),
    "explanatory": ("пояснение ИИ", "Пояснение составлено моделью по этим страницам главы"),
    "external": ("внешнее", "Знание вне главы: проверено не по книге"),
    "hypothesis": ("гипотеза", "В главе опоры нет: нужна проверка специалистом"),
}
ASSERTION_RU = {"observation": "наблюдение", "inference": "вывод автора", "assumption": "допущение автора"}


def of_claim(c: dict) -> str:
    """Утверждение слоя всегда с цитатой: из книги, а вывод модели из книги — inferred."""
    if c.get("hypothesis"):
        return "hypothesis"
    return "inferred" if c.get("kind") == "inference" else "stated"


def of_part(part: dict | None, default_by_model: bool = True) -> str:
    """Абзац слоя: пояснение модели с опорой на единицы главы, гипотеза без опоры, или текст автора."""
    if not part:
        return "explanatory"
    if part.get("hypothesis"):
        return "hypothesis"
    if part.get("external"):
        return "external"
    return "explanatory" if part.get("by_model", default_by_model) else "stated"


def normalize(value) -> str:
    """Шаблон получает либо код, либо старое by_model (True/False)."""
    if isinstance(value, str) and value in LABELS:
        return value
    return "explanatory" if value else "stated"


def label(value) -> str:
    return LABELS[normalize(value)][0]


def title(value) -> str:
    return LABELS[normalize(value)][1]
