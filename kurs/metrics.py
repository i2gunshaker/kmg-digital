"""Метрики достоверности и процесса по главам → build/metrics.csv и раздел для MANIFEST.md.

Всё считается детерминированно из файлов сборки, без модели:
- покрытие единиц (ledger coverage.json), mapped_but_untaught;
- quote validity — доля утверждений, чья цитата найдена дословно на указанной странице;
- numeric traceability — доля утверждений без ошибок проверки чисел;
- смысловые замечания (отрицание, условие, допущение, направление, обобщение);
- задания: число, типы, виды; рисунки и ролики;
- фаззинг валидатора: доля пойманных внесённых ошибок и фон ложных тревог;
- деньги, вызовы и кэш модели по build/llm_usage.jsonl; время прогона.
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

from . import config as _config
from .config import Chapter, Config

FIELDS = [
    "chapter", "set", "language", "units", "units_in_nodes", "units_excluded", "unit_coverage", "untaught_units",
    "nodes", "claims", "quote_validity", "numeric_traceability", "semantic_flags", "check_errors", "check_warnings",
    "tasks", "task_types", "task_kinds", "illustrations", "videos", "fuzz_injected", "fuzz_recall", "fuzz_baseline_rate",
    "llm_cost_usd", "llm_calls", "llm_cache_hits", "routing_by", "tasks_by", "seconds", "measured_at",
]


def _read(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _usage(ch: Chapter) -> tuple[float, int, int]:
    p = _config.BUILD / "llm_usage.jsonl"
    cost, calls, hits = 0.0, 0, 0
    if not p.exists():
        return cost, calls, hits
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("scope") != ch.id:
            continue
        if r.get("from_cache"):
            hits += 1
        else:
            calls += 1
            cost += float(r.get("cost_usd") or 0)
    return round(cost, 4), calls, hits


def chapter_metrics(cfg: Config, ch: Chapter, seconds: float | None = None) -> dict:
    from .verify import Source, check_coverage, check_layer

    b = ch.build_dir
    layer, cov = _read(b / "layer.json"), _read(b / "coverage.json")
    tasks, ills, vids = _read(b / "tasks.json"), _read(b / "illustrations.json"), _read(b / "videos.json")
    fuzz, rep = _read(b / "fuzz_report.json"), _read(b / "gen_report.json") or {}
    row: dict = {k: "" for k in FIELDS}
    row.update(chapter=ch.id, set=ch.set, language=ch.language or "ru", measured_at=time.strftime("%Y-%m-%dT%H:%M"))
    if seconds is not None:
        row["seconds"] = round(seconds)
    if layer:
        claims = [c for n in layer["nodes"] for c in n.get("claims", [])]
        issues = check_layer(cfg, ch, layer, coverage=cov, strict=False)
        num_bad = {i.where.split("claim ", 1)[-1] for i in issues if "числа не подтверждены исходной" in i.msg}
        src = Source(ch)
        valid = sum(src.find_quote(c["quote"], c["page_pdf"])[0] for c in claims)
        row.update(
            nodes=len(layer["nodes"]), claims=len(claims),
            quote_validity=round(valid / len(claims), 4) if claims else "",
            numeric_traceability=round(1 - len(num_bad) / len(claims), 4) if claims else "",
            semantic_flags=sum(1 for i in issues if i.level == "warn" and "/claim " in i.where),
            check_errors=sum(i.level == "error" for i in issues), check_warnings=sum(i.level == "warn" for i in issues),
        )
        if cov:
            rows = cov.get("rows", [])
            cov_issues = check_coverage(cfg, ch, cov, layer, strict=False)
            units = len(src.units)
            row.update(
                units=units, units_in_nodes=sum(bool(r.get("node_ids")) for r in rows),
                units_excluded=sum(not r.get("node_ids") for r in rows),
                unit_coverage=round(len({r["unit_id"] for r in rows} & set(src.unit_by_id)) / units, 4) if units else "",
                untaught_units=sum("mapped_but_untaught" in i.msg for i in cov_issues),
            )
            row["check_errors"] += sum(i.level == "error" for i in cov_issues)
    if tasks:
        ts = tasks.get("tasks", [])
        row.update(tasks=len(ts), task_types=len({t["type"] for t in ts}), task_kinds=len({t.get("kind") for t in ts}))
    if ills:
        row["illustrations"] = len(ills.get("illustrations", []))
    if vids:
        row["videos"] = len(vids.get("videos", []))
    if fuzz:
        row.update(fuzz_injected=fuzz["injected"], fuzz_recall=fuzz["recall"], fuzz_baseline_rate=fuzz["baseline_rate"])
    cost, calls, hits = _usage(ch)
    row.update(llm_cost_usd=cost, llm_calls=calls, llm_cache_hits=hits, routing_by=rep.get("routing_by", ""), tasks_by=rep.get("tasks_by", ""))
    return row


def write_metrics(cfg: Config, rows: list[dict], path: Path | None = None) -> Path:
    """Дописывает или обновляет строки глав в metrics.csv (одна строка на главу — последняя сборка)."""
    path = path or _config.BUILD / "metrics.csv"
    old: dict[str, dict] = {}
    if path.exists():
        with path.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                old[r["chapter"]] = r
    for r in rows:
        prev = old.get(r["chapter"], {})
        if r.get("seconds") in ("", None) and prev.get("seconds"):
            r = {**r, "seconds": prev["seconds"]}
        old[r["chapter"]] = r
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in old.values():
            w.writerow({k: r.get(k, "") for k in FIELDS})
    return path


def manifest_section(rows: list[dict]) -> str:
    """Таблица метрик для MANIFEST.md: цели взяты из архитектурного документа."""
    def pct(v):
        return f"{float(v):.1%}" if v not in ("", None) else "—"

    lines = [
        "## Метрики достоверности",
        "",
        "| Глава | Покрытие единиц (цель 100%) | Не преподано | Цитаты на своей странице (цель 100%) | Числа прослеживаются (цель 100%) "
        "| Смысловых замечаний | Заданий / типов | Фаззинг: поймано | Фон тревог | $ | Время, с |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['chapter']} | {pct(r['unit_coverage'])} | {r['untaught_units'] or 0} | {pct(r['quote_validity'])} | {pct(r['numeric_traceability'])} "
            f"| {r['semantic_flags'] or 0} | {r['tasks'] or 0} / {r['task_types'] or 0} | {pct(r['fuzz_recall'])} | {pct(r['fuzz_baseline_rate'])} "
            f"| {r['llm_cost_usd'] or 0} | {r['seconds'] or '—'} |"
        )
    lines += [
        "",
        "Как считается: покрытие — по таблице coverage.json (каждая единица главы отдана узлу или исключена с причиной из закрытого списка); "
        "цитаты — дословный поиск на указанной странице md; числа — значение, знак, единица и неравенство сверяются с исходной единицей; "
        "фаззинг — `kurs fuzz`: в верные утверждения вносятся ошибки (число, единица, отрицание, направление, условие, допущение, страница, "
        "чужая цитата), доля пойманных проверкой; фон — доля исходных утверждений, которые проверка отметила.",
    ]
    return "\n".join(lines) + "\n"
