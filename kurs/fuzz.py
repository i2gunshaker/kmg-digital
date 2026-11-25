"""Фаззинг валидатора: портим верные утверждения слоя и считаем, сколько ошибок проверка поймала.

«Внесли 200, поймано N, вот что пропущено» — главная метрика достоверности проверки.
Заодно меряется фон: сколько исходных, неиспорченных утверждений проверка считает ошибочными.
Это ложные тревоги, а для модуля «Закладки» их должно быть не больше двух на пакет.

Работает без модели и без сети: слой, единицы главы и правила из verify.py и claimcheck.py.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from .claimcheck import CAUGHT_BY, claim_findings, mutations
from .config import Chapter, Config
from .verify import Source


def detect(text: str, quote: str, page: int, src: Source, unit_texts: list[str], context: str = "") -> set[str]:
    """Коды всех замечаний проверки к утверждению. Пустое множество — проверка считает утверждение верным."""
    codes = {f.code for f in claim_findings(text, quote, unit_texts or [quote], context)}
    ok, _ = src.find_quote(quote, page)
    if not ok:
        codes.add("quote_not_on_page")
    return codes


def _units_of(c: dict, src: Source) -> list[str]:
    u = src.unit_by_id.get(c.get("unit_id", ""))
    if u and not u.model_description:
        return [u.text]
    return [x.text for x in src.find_quote_units(c.get("quote", ""), c.get("page_pdf", -1))]


def _pick(items: list, limit: int, seed: str) -> list:
    if len(items) <= limit:
        return items
    return sorted(items, key=lambda x: hashlib.sha256((seed + json.dumps(x, ensure_ascii=False, sort_keys=True)).encode()).hexdigest())[:limit]


def run_fuzz(ch: Chapter, layer: dict, limit: int = 400) -> dict:
    """Отчёт: по каждому виду ошибки — внесено, поймано, примеры пропусков; плюс фон ложных тревог."""
    src = Source(ch)
    claims = [(n, c) for n in layer.get("nodes", []) for c in n.get("claims", []) if c.get("quote")]
    baseline_flags: Counter = Counter()
    false_alarms: list[dict] = []
    clean: list[tuple[dict, dict, list[str], str]] = []
    for n, c in claims:
        units = _units_of(c, src)
        context = " ".join(p.get("text", "") for p in n.get("conditions") or [])
        codes = detect(c["text"], c["quote"], c["page_pdf"], src, units, context)
        if c.get("kind") == "assumption":
            codes.discard("assumption_as_fact")
        if codes:
            baseline_flags.update(codes)
            false_alarms.append({"claim": c["id"], "codes": sorted(codes), "text": c["text"][:160]})
        else:
            clean.append((n, c, units, context))

    cases: list[dict] = []
    for n, c, units, context in clean:
        for m in mutations(c["text"], seed=c["id"]):
            cases.append({"kind": m.kind, "claim": c["id"], "text": m.text, "quote": c["quote"], "page": c["page_pdf"], "_u": units, "_ctx": context})
        cases.append({"kind": "page_shift", "claim": c["id"], "text": c["text"], "quote": c["quote"], "page": c["page_pdf"] + 2, "_u": units, "_ctx": context})
    others = [(n["id"], c) for n, c, _, _ in clean]
    for i, (n, c, units, context) in enumerate(clean):
        far = [o for nid, o in others if nid != n["id"]]
        if far:
            o = far[int(hashlib.sha256(c["id"].encode()).hexdigest(), 16) % len(far)]
            cases.append({"kind": "quote_swap", "claim": c["id"], "text": c["text"], "quote": o["quote"], "page": o["page_pdf"], "_u": _units_of(o, src), "_ctx": context})

    by_kind: dict[str, list[dict]] = {}
    for case in cases:
        by_kind.setdefault(case["kind"], []).append(case)
    per_kind = max(1, limit // max(len(by_kind), 1))
    stats: dict[str, dict] = {}
    for kind, items in sorted(by_kind.items()):
        chosen = _pick([{k: v for k, v in x.items() if not k.startswith("_")} | {"_i": i} for i, x in enumerate(items)], per_kind, kind)
        caught, missed = 0, []
        for x in chosen:
            full = items[x["_i"]]
            codes = detect(full["text"], full["quote"], full["page"], src, full["_u"], full["_ctx"])
            if codes & CAUGHT_BY.get(kind, set()) or (kind not in CAUGHT_BY and codes):
                caught += 1
            else:
                missed.append({"claim": full["claim"], "text": full["text"][:200], "codes": sorted(codes)})
        stats[kind] = {"injected": len(chosen), "caught": caught, "recall": round(caught / len(chosen), 3) if chosen else None, "missed": missed[:5]}
    injected = sum(s["injected"] for s in stats.values())
    caught = sum(s["caught"] for s in stats.values())
    return {
        "chapter": ch.id,
        "claims": len(claims),
        "clean_claims": len(clean),
        "baseline_flagged": len(false_alarms),
        "baseline_rate": round(len(false_alarms) / len(claims), 3) if claims else None,
        "baseline_codes": dict(baseline_flags.most_common()),
        "baseline_examples": false_alarms[:10],
        "injected": injected,
        "caught": caught,
        "recall": round(caught / injected, 3) if injected else None,
        "by_kind": stats,
    }


def report_md(rep: dict) -> str:
    lines = [
        f"# Фаззинг валидатора: {rep['chapter']}",
        "",
        f"Внесено ошибок: **{rep['injected']}**, поймано: **{rep['caught']}** ({(rep['recall'] or 0):.0%}).",
        f"Фон: из {rep['claims']} исходных утверждений проверка отметила {rep['baseline_flagged']} ({(rep['baseline_rate'] or 0):.0%}).",
        "",
        "| Вид ошибки | Внесено | Поймано | Доля |",
        "|---|---|---|---|",
    ]
    for kind, s in rep["by_kind"].items():
        lines.append(f"| {kind} | {s['injected']} | {s['caught']} | {(s['recall'] or 0):.0%} |")
    lines += ["", "## Пропущено (примеры)"]
    for kind, s in rep["by_kind"].items():
        for m in s["missed"][:3]:
            lines.append(f"- {kind} `{m['claim']}`: {m['text']}")
    if rep["baseline_examples"]:
        lines += ["", "## Фон: исходные утверждения с замечаниями"]
        for b in rep["baseline_examples"]:
            lines.append(f"- `{b['claim']}` {', '.join(b['codes'])}: {b['text']}")
    return "\n".join(lines) + "\n"


def fuzz_chapter(cfg: Config, ch: Chapter, layer_path: Path | None = None, limit: int = 400) -> tuple[dict, Path]:
    layer = json.loads((layer_path or ch.build_dir / "layer.json").read_text(encoding="utf-8"))
    rep = run_fuzz(ch, layer, limit)
    (ch.build_dir / "fuzz_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ch.build_dir / "fuzz_report.md"
    md.write_text(report_md(rep), encoding="utf-8")
    return rep, md
