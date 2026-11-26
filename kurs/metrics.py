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


# ---------------------------------------------------------------- таблица организаторов: строка на главу и этап
OFFICIAL = ["chapter", "stage", "nodes", "llm_tokens_in", "llm_tokens_out", "money_usd", "person_minutes", "mentor_minutes", "video_seconds", "manual_share"]
STAGES = ["layer", "routing", "course", "video", "review"]


def stage_of(scope: str, label: str) -> str:
    """Этап вызова модели по метке журнала."""
    if scope == "review" or label.startswith("review"):
        return "review"
    if label.startswith("routing"):
        return "routing"
    if label.startswith(("tasks", "visual")):
        return "course"
    if label.startswith(("video", "script")):
        return "video"
    return "layer"


def _stage_of_file(path: str) -> str:
    p = path.lower()
    for key, stage in (("routing", "routing"), ("video", "video"), ("passport", "video"), ("review", "review"), ("layer", "layer"), ("coverage", "layer"), ("node", "layer")):
        if key in p:
            return stage
    return "course"


def official_rows(cfg: Config, ids: list[str], edits: Path | None = None, mentor: Path | None = None, review_chapter: str = "vendelshtein_gl1") -> list[dict]:
    """Строки по шаблону организаторов: токены и деньги — из журнала вызовов, минуты людей — из manual_edits.csv,
    минуты ментора — из mentor_minutes.csv (chapter,stage,minutes), секунды видео — из videos.json."""
    use: dict[tuple[str, str], dict] = {}
    p = _config.BUILD / "llm_usage.jsonl"
    for line in p.read_text(encoding="utf-8").splitlines() if p.exists() else []:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("from_cache"):
            continue
        stage = stage_of(r.get("scope", ""), r.get("label", ""))
        u = use.setdefault((review_chapter if r.get("scope") == "review" else r.get("scope", ""), stage), {"in": 0, "out": 0, "usd": 0.0, "sec": 0.0})
        u["in"] += r.get("input", 0) + r.get("cache_write", 0) + r.get("cache_read", 0)
        u["out"] += r.get("output", 0)
        u["usd"] += float(r.get("cost_usd") or 0)
        u["sec"] += float(r.get("seconds") or 0)

    def minutes(path: Path | None, field: str, stage_from) -> dict[tuple[str, str], float]:
        out: dict[tuple[str, str], float] = {}
        if path and path.exists():
            with path.open(encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    k = (r["chapter"], stage_from(r))
                    out[k] = out.get(k, 0.0) + float(r.get(field) or 0)
        return out

    person = minutes(edits, "person_minutes", lambda r: _stage_of_file(r.get("file", "")))
    ment = minutes(mentor, "minutes", lambda r: r.get("stage", "layer"))
    rows = []
    for cid in ids:
        b = cfg.chapter(cid).build_dir
        layer = _read(b / "layer.json")
        vids = (_read(b / "videos.json") or {}).get("videos", [])
        for st in STAGES:
            u = use.get((cid, st))
            secs = round(sum(float(v.get("duration_s") or v.get("seconds") or 0) for v in vids)) if st == "video" else 0
            pm = person.get((cid, st), 0.0)
            if not u and not pm and not secs and not (cid, st) in ment and not (layer and st in ("layer", "course")):
                continue
            machine = (u["sec"] / 60) if u else 0.0
            rows.append({
                "chapter": cid, "stage": st, "nodes": len(layer["nodes"]) if layer else 0, "llm_tokens_in": u["in"] if u else 0, "llm_tokens_out": u["out"] if u else 0,
                "money_usd": round(u["usd"], 4) if u else 0, "person_minutes": round(pm, 1), "mentor_minutes": round(ment.get((cid, st), 0.0), 1),
                "video_seconds": secs, "manual_share": round(pm / (pm + machine), 3) if pm + machine > 0 else 0,
                "machine_minutes": round(machine, 2),
            })
    return rows


def economics(rows: list[dict]) -> list[dict]:
    """Сводка по главе из строк official_rows: деньги и человеко-минуты на узел и на минуту видео, доля ручного труда."""
    out: dict[str, dict] = {}
    for r in rows:
        e = out.setdefault(r["chapter"], {"chapter": r["chapter"], "nodes": 0, "usd": 0.0, "pm": 0.0, "mentor": 0.0, "machine": 0.0,
                                          "video_s": 0, "video_usd": 0.0, "video_pm": 0.0})
        e["nodes"] = max(e["nodes"], int(r["nodes"] or 0))
        e["usd"] += float(r["money_usd"] or 0)
        e["pm"] += float(r["person_minutes"] or 0)
        e["mentor"] += float(r["mentor_minutes"] or 0)
        e["machine"] += float(r.get("machine_minutes") or 0)
        if r["stage"] == "video":
            e["video_s"] += int(r["video_seconds"] or 0)
            e["video_usd"] += float(r["money_usd"] or 0)
            e["video_pm"] += float(r["person_minutes"] or 0)
    for e in out.values():
        n, vm = e["nodes"], e["video_s"] / 60
        e["usd_per_node"] = e["usd"] / n if n else None
        e["pm_per_node"] = e["pm"] / n if n else None
        e["usd_per_video_min"] = e["video_usd"] / vm if vm else None
        e["pm_per_video_min"] = e["video_pm"] / vm if vm else None
        e["manual_share"] = e["pm"] / (e["pm"] + e["machine"]) if e["pm"] + e["machine"] else 0.0
    return list(out.values())


def economics_section(rows: list[dict]) -> str:
    def f(v, d=3):
        return "—" if v is None else f"{v:.{d}f}"

    lines = [
        "| Глава | Узлов | $ всего | $ на узел | Чел.-мин | Чел.-мин на узел | Видео, мин | $ на минуту видео | Чел.-мин на минуту видео | Доля ручного труда | Минуты ментора |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for e in economics(rows):
        lines.append(
            f"| `{e['chapter']}` | {e['nodes']} | {e['usd']:.2f} | {f(e['usd_per_node'])} | {e['pm']:.0f} | {f(e['pm_per_node'], 1)} "
            f"| {e['video_s'] / 60:.1f} | {f(e['usd_per_video_min'])} | {f(e['pm_per_video_min'], 1)} | {e['manual_share']:.0%} | {e['mentor']:.0f} |"
        )
    return "\n".join(lines) + "\n"


def write_official(rows: list[dict], path: Path) -> Path:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OFFICIAL, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
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
