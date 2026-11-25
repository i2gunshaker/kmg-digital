"""Запуск одной командой и пересборка после правки факта — без вызовов модели.

Слой знаний (layer.json) — единственный источник истины. Курс, задания, рисунки и ролики выводятся из него.
После каждой сборки в build/<глава>/manifest.json пишутся хэши узлов и утверждений и то, какие задания и ролики
на них опираются. `kurs plan` сравнивает текущий слой с манифестом и показывает, что устарело,
`kurs rebuild` пересобирает только это и проверяет результат. Модель при этом не вызывается
(KURS_LLM_OFFLINE=1): правка факта человеком не должна стоить денег и менять остальной курс.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from .config import Chapter, Config

MANIFEST = "manifest.json"


def _h(obj) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _read(p: Path) -> dict | None:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def config_hash(cfg: Config) -> str:
    """Хэш того, что влияет на сборку: config.yaml, промпты, доменный пакет."""
    from .config import ROOT

    parts = [(ROOT / "config.yaml").read_bytes()]
    for d in (ROOT / "kurs" / "prompts", cfg.domain_dir):
        if d.exists():
            parts += [p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()]
    return "sha256:" + hashlib.sha256(b"\0".join(parts)).hexdigest()[:16]


def snapshot(cfg: Config, ch: Chapter, layer: dict) -> dict:
    """Хэши слоя и зависимости производных файлов от узлов и утверждений."""
    tasks = _read(ch.build_dir / "tasks.json") or {"tasks": []}
    videos = _read(ch.build_dir / "videos.json") or {"videos": []}
    nodes, claims = {}, {}
    for n in layer.get("nodes", []):
        body = {k: v for k, v in n.items() if k != "claims"}
        nodes[n["id"]] = _h(body)
        for c in n.get("claims", []):
            claims[c["id"]] = _h(c)
    return {
        "chapter": ch.id,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config_hash": config_hash(cfg),
        "layer_hash": _h(layer),
        "nodes": nodes,
        "claims": claims,
        "artifacts": {
            "tasks": {t["id"]: {"step_id": t["step_id"], "claim_ids": t.get("claim_ids", [])} for t in tasks.get("tasks", [])},
            "videos": {v["id"]: {"node_ids": v.get("node_ids") or [v.get("node_id")], "claim_ids": v.get("claim_ids", [])} for v in videos.get("videos", [])},
        },
    }


def write_manifest(cfg: Config, ch: Chapter, layer: dict) -> Path:
    p = ch.build_dir / MANIFEST
    p.write_text(json.dumps(snapshot(cfg, ch, layer), ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def plan(cfg: Config, ch: Chapter, layer: dict, only_node: str | None = None) -> dict:
    """Что устарело после правки слоя: аналог `aida plan` — diff до применения."""
    old = _read(ch.build_dir / MANIFEST)
    new = snapshot(cfg, ch, layer)
    if old is None:
        return {"chapter": ch.id, "no_manifest": True, "changed_nodes": sorted(new["nodes"]), "changed_claims": [], "stale_tasks": [],
                "stale_videos": [], "course": True, "config_changed": False}
    changed_claims = sorted(
        c for c in set(old["claims"]) | set(new["claims"]) if old["claims"].get(c) != new["claims"].get(c)
    )
    changed_nodes = sorted(
        n for n in set(old["nodes"]) | set(new["nodes"]) if old["nodes"].get(n) != new["nodes"].get(n)
    )
    touched_nodes = set(changed_nodes) | {c.split("#", 1)[0] for c in changed_claims}
    if only_node:
        touched_nodes &= {only_node}
        changed_claims = [c for c in changed_claims if c.split("#", 1)[0] == only_node]
        changed_nodes = [n for n in changed_nodes if n == only_node]
    arts = old.get("artifacts", {})
    stale_tasks = sorted(
        tid for tid, a in arts.get("tasks", {}).items() if a["step_id"] in touched_nodes or set(a["claim_ids"]) & set(changed_claims)
    )
    stale_videos = sorted(
        vid for vid, a in arts.get("videos", {}).items() if set(a["node_ids"]) & touched_nodes or set(a["claim_ids"]) & set(changed_claims)
    )
    return {
        "chapter": ch.id,
        "changed_nodes": changed_nodes,
        "changed_claims": changed_claims,
        "touched_nodes": sorted(touched_nodes),
        "stale_tasks": stale_tasks,
        "stale_videos": stale_videos,
        "course": bool(touched_nodes) or old.get("config_hash") != new["config_hash"],
        "config_changed": old.get("config_hash") != new["config_hash"],
    }


def plan_text(p: dict) -> str:
    if p.get("no_manifest"):
        return f"{p['chapter']}: манифеста нет — соберётся всё ({len(p['changed_nodes'])} узлов)"
    lines = [f"{p['chapter']}: изменено узлов {len(p['changed_nodes'])}, утверждений {len(p['changed_claims'])}"]
    for c in p["changed_claims"]:
        lines.append(f"  ~ {c}")
    for n in p["changed_nodes"]:
        lines.append(f"  ~ узел {n}")
    lines.append(f"  задания к пересмотру: {', '.join(p['stale_tasks']) or 'нет'}")
    lines.append(f"  ролики к пересборке: {', '.join(p['stale_videos']) or 'нет'}")
    lines.append(f"  курс: {'пересобрать' if p['course'] else 'без изменений'}" + (" (изменились настройки или доменный пакет)" if p["config_changed"] else ""))
    return "\n".join(lines)


def _fix_tasks(cfg: Config, ch: Chapter, layer: dict, stale: list[str], log) -> dict:
    """Устаревшие задания перепроверяются; сломанные заменяются заданиями по правилам того же шага или убираются."""
    from .layer_gen import ManualEdits, load_chapter_units, load_misconceptions, term_regex, write_generated
    from .rules import tasks_by_rules
    from .tasks_gen import shape_task, shuffle_options, task_problems

    path = ch.build_dir / "tasks.json"
    doc = _read(path)
    if not doc or not stale:
        return {"checked": 0, "replaced": [], "removed": []}
    units = {u.id: u for u in load_chapter_units(cfg, ch)}
    misc = load_misconceptions(cfg)
    misc_ids = {m["id"] for m in misc}
    node_ids = {n["id"] for n in layer["nodes"]}
    replaced, removed, out = [], [], []
    spare_pool = tasks_by_rules(layer, misc, term_regex, 200)
    used_prompts = {t["prompt"] for t in doc["tasks"]}
    for t in doc["tasks"]:
        if t["id"] not in stale:
            out.append(t)
            continue
        probs = task_problems(t, layer, units, misc_ids) if t["step_id"] in node_ids else [f"нет шага {t['step_id']}"]
        if not probs:
            out.append(t)
            continue
        cand = None
        for r in sorted(spare_pool, key=lambda r: (r["step_id"] != t["step_id"], r["type"] != t["type"])):
            if r["step_id"] != t["step_id"] or r["prompt"] in used_prompts:
                continue
            new = shuffle_options(shape_task(r, t["id"]))
            if not task_problems(new, layer, units, misc_ids):
                cand = new
                break
        if cand:
            used_prompts.add(cand["prompt"])
            out.append(cand)
            replaced.append((t["id"], probs[0]))
        else:
            removed.append((t["id"], probs[0]))
        log(f"  задание {t['id']}: {probs[0]} → {'заменено по правилам' if cand else 'убрано'}")
    if replaced or removed:
        try:
            write_generated(ch, "tasks.json", {**doc, "tasks": out}, force=False)
        except ManualEdits:
            # tasks.json правил человек: перезаписываем только с согласия, а пока — отчёт
            log("  tasks.json изменён вручную, замены не записаны (kurs rebuild --force)")
            return {"checked": len(stale), "replaced": [], "removed": [], "manual": True, "would_replace": replaced, "would_remove": removed}
    return {"checked": len(stale), "replaced": replaced, "removed": removed}


def rebuild(cfg: Config, ch: Chapter, only_node: str | None = None, *, video: bool = False, force: bool = False, log=print) -> int:
    """Пересборка после ручной правки слоя: проверка, задания, курс, ролики, манифест. Сеть модели запрещена."""
    from .course import build_course, verify_course_dir
    from .verify import check_coverage, check_layer, report

    os.environ["KURS_LLM_OFFLINE"] = "1"
    t0 = time.time()
    layer = json.loads((ch.build_dir / "layer.json").read_text(encoding="utf-8"))
    p = plan(cfg, ch, layer, only_node)
    log(plan_text(p))
    cov = _read(ch.build_dir / "coverage.json")
    issues = check_layer(cfg, ch, layer, coverage=cov) + (check_coverage(cfg, ch, cov, layer) if cov else [])
    errors = [i for i in issues if i.level == "error"]
    if errors:
        log(report(errors))
        log("слой с ошибками проверки: курс не пересобираю — исправьте факт так, чтобы он сходился с цитатой")
        return 1
    if force:
        # --force: заменить сломанные задания даже в tasks.json, который правил человек
        state = ch.build_dir / ".generated.json"
        if state.exists():
            st = json.loads(state.read_text(encoding="utf-8"))
            st.pop("tasks.json", None)
            state.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    t = _fix_tasks(cfg, ch, layer, p["stale_tasks"], log)
    rc = 0
    if p["stale_videos"] and video:
        from .video import render_videos

        render_videos(cfg, ch, layer, only=p["stale_videos"], log=log)
    elif p["stale_videos"]:
        log(f"  ролики устарели: {', '.join(p['stale_videos'])} — пересобрать: kurs video {ch.id} --only <id> (или rebuild --video)")
    if p["course"] or t["replaced"] or t["removed"]:
        built = build_course(cfg, ch, layer)
        errs = verify_course_dir(cfg, ch, built)
        if errs:
            log("Ошибки приёмки курса:\n" + "\n".join(errs))
            rc = 1
        else:
            log(f"курс пересобран: {built / 'index.html'}")
    write_manifest(cfg, ch, layer)
    log(f"{ch.id}: пересборка за {time.time() - t0:.1f} с, вызовов модели 0")
    return rc


def run(cfg: Config, chapter: str, *, max_usd: float | None, profile: str | None, video: bool, log=print) -> int:
    """Полный прогон главы (в т.ч. незнакомой .md на русском или английском) одной командой."""
    from .__main__ import _gen
    from .course import build_course, verify_course_dir
    from .units import build_units, cut_chapter
    from .verify import check_coverage, check_layer, report

    t0 = time.time()
    ch = cfg.chapter(chapter)
    cut_chapter(cfg, ch)
    units = build_units(cfg, ch)
    log(f"{ch.id}: {len(units)} единиц")

    class A:  # аргументы _gen
        pass

    a = A()
    a.chapter, a.only, a.force, a.redo, a.max_usd, a.profile = [ch.id], None, False, False, max_usd, profile
    rc = _gen(cfg, a)
    lp = ch.build_dir / "layer.json"
    if not lp.exists():
        log(f"{ch.id}: слоя нет — без --max-usd модель не вызывается, а в кэше ответа нет")
        return 1
    layer = json.loads(lp.read_text(encoding="utf-8"))
    cov = _read(ch.build_dir / "coverage.json")
    issues = check_layer(cfg, ch, layer, coverage=cov) + (check_coverage(cfg, ch, cov, layer) if cov else [])
    log(report(issues))
    if video:
        from .video import render_videos

        render_videos(cfg, ch, layer, log=log)
    built = build_course(cfg, ch, layer)
    errs = verify_course_dir(cfg, ch, built)
    for e in errs:
        log("  приёмка: " + e)
    write_manifest(cfg, ch, layer)
    from .metrics import chapter_metrics, write_metrics

    row = chapter_metrics(cfg, ch, seconds=time.time() - t0)
    write_metrics(cfg, [row])
    log(f"{ch.id}: готово за {time.time() - t0:.0f} с → {built / 'index.html'}")
    return rc | (1 if errs or any(i.level == "error" for i in issues) else 0)
