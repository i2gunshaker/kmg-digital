"""Привязка паспорта ролика к утверждениям слоя: у каждой фразы и источника — настоящий id утверждения.

Паспорт может быть составлен до слоя, тогда номера утверждений в нём условные. Источник с цитатой находит в слое утверждение
с той же страницы и наибольшей общей частью цитаты (сначала в своём узле, затем в главе), фразы закадра получают его id.
Чему пары нет, возвращается списком: такую фразу надо либо опереть на утверждение, либо переписать.
"""
from __future__ import annotations

import re

from .claimcheck import content_stems, overlap
from .verify import norm

SAME = 0.6  # доля общих смысловых слов цитат, с которой это одно и то же место книги


def _pages(text: str) -> set[int]:
    out: set[int] = set()
    for a, b in re.findall(r"(\d+)(?:\s*[–—-]\s*(\d+))?", text.split("(")[0]):
        out |= set(range(int(a), int(b) + 1)) if b and int(b) - int(a) < 40 else {int(a)}
    return out


def _score(quote: str, claim: dict) -> float:
    a, b = norm(quote), norm(claim.get("quote") or "")
    if a and b and (a in b or b in a):
        return 1.0
    return max(overlap(quote, claim.get("quote") or ""), overlap(claim.get("quote") or "", quote) if len(content_stems(claim.get("quote") or "")) >= 4 else 0.0)


def is_data(source: dict) -> bool:
    return str(source.get("page_or_dataset", "")).startswith("data.")


def bind(passport: dict, layer: dict) -> list[str]:
    """Меняет id утверждений в паспорте на настоящие. Возвращает, что осталось без пары."""
    claims = [(n["id"], c) for n in layer["nodes"] for c in n.get("claims", [])]
    own = (passport.get("node_card") or {}).get("node_id") or ""
    left: list[str] = []
    new_id: dict[str, str] = {}
    for s in passport.get("sources", []):
        if is_data(s):
            new_id[s["claim_id"]] = s["page_or_dataset"].split(" ")[0]  # набор данных: свой id, не номер утверждения
            continue
        pages = _pages(s.get("page_or_dataset", ""))
        cands = [(nid, c) for nid, c in claims if c["page_pdf"] in pages] or claims
        best = max(cands, key=lambda x: (_score(s.get("quote", ""), x[1]) + (0.05 if x[0] == own else 0), -len(x[1].get("quote") or "")))
        if _score(s.get("quote", ""), best[1]) >= SAME:
            new_id[s["claim_id"]] = best[1]["id"]
        else:
            left.append(f"{s['claim_id']}: в слое нет утверждения с этой цитатой ({s.get('page_or_dataset')}: «{s.get('quote', '')[:70]}»)")
    for s in passport.get("sources", []):
        s["claim_id"] = new_id.get(s["claim_id"], s["claim_id"])
    for ln in passport.get("script", []):
        ln["claim_id"] = new_id.get(ln.get("claim_id"), ln.get("claim_id"))
    return left


def foreign_claims(passport: dict, layer: dict) -> list[str]:
    """Фразы, чей id не найден среди утверждений слоя."""
    known = {c["id"] for n in layer["nodes"] for c in n.get("claims", [])}
    data = {s["claim_id"] for s in passport.get("sources", []) if is_data(s)}
    return [f"t={ln['t_start']}: {ln.get('claim_id')}" for ln in passport.get("script", []) if ln.get("claim_id") not in known | data]
