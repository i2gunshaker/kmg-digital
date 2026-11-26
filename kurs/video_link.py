"""Ролики Remotion в курсе: регистрация готового ролика в build/<глава>/videos.json и пересборка после правки слоя.

    python -m kurs video <глава> --node <узел> --format 9:16|16:9 [--max-usd 0.05] [--backend auto|elevenlabs|edge_tts|mock]
    python -m kurs video <глава> --import build/videos/<id>   — положить в курс ролик, собранный на другой машине

Курс берёт ролики из videos.json и ставит их на свои шаги. rebuild --video пересобирает устаревшие ролики:
по узлу — из сохранённого сценария с перенесённой правкой (без сети), обучающий — из спецификации с заменой изменённых чисел.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from .config import BUILD, Chapter, Config

TITLE = {"9:16": "Шортс", "16:9": "Разбор"}


def _videos(ch: Chapter) -> dict:
    p = ch.build_dir / "videos.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"chapter": ch.id, "videos": []}


def _claim_texts(layer: dict) -> dict[str, str]:
    return {c["id"]: c["text"] for n in layer.get("nodes", []) for c in n.get("claims", [])}


def register(cfg: Config, ch: Chapter, out_dir: Path, *, kind: str, layer: dict, args: dict | None = None) -> dict:
    """Ролик из папки рендера (mp4, passport.json, timings.json, frames/) → videos.json главы."""
    passport = json.loads((out_dir / "passport.json").read_text(encoding="utf-8"))
    vid = passport["video_id"]
    mp4 = out_dir / f"{vid}.mp4"
    if not mp4.exists():
        raise FileNotFoundError(mp4)
    dst = ch.build_dir / "video" / vid
    dst.mkdir(parents=True, exist_ok=True)
    if mp4.resolve() != (dst / mp4.name).resolve():
        shutil.copy2(mp4, dst / mp4.name)
    frames = sorted((out_dir / "frames").glob("*.png"))
    poster = ""
    if frames:
        shutil.copy2(frames[min(1, len(frames) - 1)], dst / "poster.png")
        poster = str((dst / "poster.png").relative_to(ch.build_dir))
    tp = out_dir / "timings.json"
    tdoc = json.loads(tp.read_text(encoding="utf-8")) if tp.exists() else {}
    segs = tdoc.get("segments", [])
    seconds = float(tdoc.get("total_duration_sec") or passport["node_card"].get("duration_sec") or (segs[-1]["t_end"] if segs else 0))
    cues = [{"t_start": c["start"], "t_end": c["end"], "text": c["text"]} for s in segs for c in s.get("captions") or []] or [
        {"t_start": s["t_start"], "t_end": s["t_end"], "text": re.sub(r"[+́]", "", s["spoken_ref"])} for s in segs] or [
        {"t_start": ln["t_start"], "t_end": ln["t_end"], "text": ln["text"]} for ln in passport["script"]]
    texts = _claim_texts(layer)
    claim_ids = sorted({ln["claim_id"] for ln in passport["script"] if ln.get("claim_id")})
    fmt = "9:16" if passport["node_card"]["format"] == "шортс" else "16:9"
    entry = {
        "id": vid,
        "title": f"{TITLE[fmt]}: {passport['node_card']['title']}",
        "file": str((dst / mp4.name).relative_to(ch.build_dir)),
        # хэш файла: новый MP4 под тем же id (другой голос, новые подписи) меняет videos.json, и rebuild пересобирает курс
        "mp4_sha256": hashlib.sha256((dst / mp4.name).read_bytes()).hexdigest()[:16],
        "poster": poster,
        "node_ids": [passport["node_id"]],
        "claim_ids": claim_ids,
        "vertical": fmt == "9:16",
        "duration_s": round(seconds, 1),
        "cues": cues,
        "engine": "remotion",
        "kind": kind,
        "format": fmt,
        "audio": {k: passport.get("audio_check", {}).get(k) for k in ("backend", "measured_lufs", "wer", "max_sync_drift_sec", "status")},
        "args": args or {},
        "snapshot": {c: texts[c] for c in claim_ids if c in texts},
    }
    shutil.copy2(out_dir / "passport.json", dst / "passport.json")
    doc = _videos(ch)
    doc["videos"] = [v for v in doc["videos"] if v["id"] != vid] + [entry]
    (ch.build_dir / "videos.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return entry


def render_node(cfg: Config, ch: Chapter, layer: dict, node_id: str, fmt: str, *, llm: Any = None, backend: str = "auto",
                skip_asr: bool = False, log=print) -> dict:
    from .video_render import render_node_video

    res = render_node_video(cfg, ch.id, node_id, fmt=fmt, backend=backend, skip_asr=skip_asr, llm=llm)
    if res.get("source_issues"):
        log("  паспорт: " + "; ".join(res["source_issues"][:4]))
    entry = register(cfg, ch, Path(res["mp4"]).parent, kind="node", layer=layer, args={"backend": backend, "skip_asr": skip_asr})
    a = entry["audio"]
    log(f"  ролик {entry['id']}: {res['probe']['duration_sec']:.0f} с, голос {a['backend']}, {a['measured_lufs']} LUFS, WER {a['wer']}, в курсе на шаге {node_id}")
    if a.get("status") != "ok":
        note = json.loads((ch.build_dir / "video" / entry["id"] / "passport.json").read_text(encoding="utf-8")).get("audio_check", {}).get("asr_note", "")
        log(f"  ролик {entry['id']}: проверка звука «{a.get('status')}» {note}".rstrip())
    return entry


def _patch_training(passport_path: Path, old: dict[str, str], layer: dict, log=print) -> int:
    """Правка числа в утверждении слоя → то же число в репликах и надписях обучающего ролика."""
    from .node_script import _swap, number_swaps

    new = _claim_texts(layer)
    p = json.loads(passport_path.read_text(encoding="utf-8"))
    n = 0
    for ln in p["script"]:
        o, t = old.get(ln.get("claim_id") or ""), new.get(ln.get("claim_id") or "")
        if o and t and o != t and (swaps := number_swaps(o, t)):
            before = ln["text"]
            ln["text"], ln["on_screen"] = _swap(ln["text"], swaps), _swap(ln.get("on_screen", ""), swaps)
            if ln["text"] != before:
                n += 1
                log(f"  {p['video_id']}: {before} → {ln['text']}")
        elif o and t and o != t:
            log(f"  {p['video_id']}: утверждение {ln['claim_id']} изменено не только в числах — реплику проверьте вручную: {ln['text']}")
    passport_path.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    return n


def rerender(cfg: Config, ch: Chapter, layer: dict, ids: list[str], *, backend: str | None = None, log=print) -> list[dict]:
    """Пересобирает устаревшие ролики Remotion. Сеть модели не нужна: сценарии сохранены, голос — из кэша по фразам."""
    from .video_render import render_from_spec
    from .video_spec import build_training_specs

    doc = _videos(ch)
    out = []
    for v in doc["videos"]:
        if v["id"] not in ids or v.get("engine") != "remotion":
            continue
        be = backend or v.get("args", {}).get("backend", "auto")
        skip = bool(v.get("args", {}).get("skip_asr"))
        if v.get("kind") == "node":
            out.append(render_node(cfg, ch, layer, v["node_ids"][0], v["format"], backend=be, skip_asr=skip, log=log))
            continue
        specs = build_training_specs(cfg)
        key = next((k[: -len("_passport")] for k, p in specs.items() if k.endswith("_passport") and json.loads(Path(p).read_text(encoding="utf-8"))["video_id"] == v["id"]), None)
        if not key:
            log(f"  {v['id']}: спецификации нет, ролик не пересобран")
            continue
        _patch_training(Path(specs[f"{key}_passport"]), v.get("snapshot", {}), layer, log)
        res = render_from_spec(cfg, Path(specs[f"{key}_passport"]), Path(specs[f"{key}_scene"]), backend=be, skip_asr=skip)
        out.append(register(cfg, ch, Path(res["mp4"]).parent, kind="training", layer=layer, args={"backend": be, "skip_asr": skip}))
    return out


def import_dir(cfg: Config, ch: Chapter, layer: dict, folder: Path, kind: str = "training") -> dict:
    """Ролик, собранный на другой машине (папка build/videos/<id>), — в курс этой главы."""
    return register(cfg, ch, folder, kind=kind, layer=layer)


_DYNAMIC = {"В": "процесс во времени"}  # трудности, которым нужна смена состояний, а не части одного рисунка


def state_model(spec: dict, n_frames: int) -> tuple[str, str]:
    """Модель состояния ролика по рисунку узла: (вид, описание). Вид: расчёт, состояния, части, статичный."""
    frames = spec.get("frames") or []
    labels = ", ".join(str(f.get("label", "")) for f in frames if f.get("label"))[:120]
    if frames and any(f.get("vars") for f in frames):
        names = sorted({k for f in frames for k in (f.get("vars") or {})})
        return "расчёт", f"кадры считаются по {', '.join(names)}: {labels} ({len(frames)} кадр.)"
    if frames and len(frames) > 1:
        return "состояния", f"смена состояний схемы: {labels} ({len(frames)} кадр.)"
    if spec.get("_reveal") and n_frames > 1:
        return "части", f"рисунок раскрывается по частям: {n_frames} шаг."
    return "статичный", "один кадр: смена реплик, карта шагов и пауза предсказания поверх рисунка"


def node_story_row(cfg: Config, ch: Chapter, n: dict, rev: dict, gen: dict) -> dict:
    """Чем закрыт ролик по узлу: рисунок, модель состояния, сценарий; «нужен новый», если закрыть нечем."""
    from .node_script import FORMATS, script_by_rules, script_problems
    from .video_spec import _load_node_visual

    diff = (n.get("difficulty") or {}).get("primary", "")
    row: dict[str, Any] = {"node_id": n["id"], "title": n["title"], "difficulty": diff}
    try:
        spec, frames = _load_node_visual(cfg, ch, n)
        kind, desc = state_model(spec, len(frames))
        row.update(template=f"NodeStory · {spec.get('template', '')}", picture_template=spec.get("template", ""), frames=len(frames),
                   label=spec.get("label", "схема"), state_kind=kind, state_model=desc,
                   picture="из доменного пакета" if n["id"] in rev else "описание модели" if n["id"] in gen else "по карточке узла")
    except ValueError:
        row.update(template="", picture_template="", frames=0, label="", state_kind="", state_model="", picture="нет")
    for fmt in FORMATS:
        sp = ch.build_dir / "scripts" / f"{n['id'].replace('.', '_')}_{fmt.replace(':', 'x')}.json"
        saved = json.loads(sp.read_text(encoding="utf-8")).get("by") if sp.exists() else None
        rules_ok = not script_problems(script_by_rules(n, fmt, max(1, row["frames"])), n, fmt, max(1, row["frames"]))
        row[fmt] = {"script": saved or "нет", "rules_pass": rules_ok}
    if not row["frames"]:
        row["covered"], row["status"] = False, "нужен новый: рисунок узла не строится"
    elif diff in _DYNAMIC and row["state_kind"] not in ("расчёт", "состояния"):
        row["covered"], row["status"] = False, f"нужен новый: {_DYNAMIC[diff]}, а рисунок не меняет состояние"
    else:
        row["covered"] = True
        row["status"] = "закрыт; сценарий без модели" if row["9:16"]["rules_pass"] else "закрыт; сценарий моделью (≈ $0,01)"
    return row


def node_story_coverage(cfg: Config, ch: Chapter, layer: dict) -> dict:
    """Отчёт о покрытии узлов шаблоном NodeStory: какой рисунок и сколько кадров будет в ролике по узлу,
    есть ли готовый сценарий и пройдёт ли сценарий по правилам стандарт формата без модели."""
    from .illustrations import generated_specs, reviewed_specs

    rev, gen = reviewed_specs(cfg, ch), generated_specs(ch)
    rows = [node_story_row(cfg, ch, n, rev, gen) for n in layer.get("nodes", [])]
    total = len(rows) or 1
    return {
        "chapter": ch.id, "template": "NodeStory", "nodes": len(rows),
        "covered": sum(r["covered"] for r in rows) / total,
        "with_picture": sum(r["frames"] > 0 for r in rows) / total,
        "multi_frame": sum(r["frames"] > 1 for r in rows) / total,
        "rules_ready_9x16": sum(r["9:16"]["rules_pass"] for r in rows) / total,
        "rules_ready_16x9": sum(r["16:9"]["rules_pass"] for r in rows) / total,
        "rows": rows,
    }


def training_into_courses(cfg: Config, log=print) -> list[dict]:
    """Оба обучающих ролика из build/videos/ — в videos.json своих глав."""
    out = []
    for cid in ("latyshova_gl5_par17-20", "basniev_gl23-24"):
        ch = cfg.chapter(cid)
        lp = ch.build_dir / "layer.json"
        if not lp.exists():
            continue
        layer = json.loads(lp.read_text(encoding="utf-8"))
        for d in sorted((BUILD / "videos").glob("*")):
            pp = d / "passport.json"
            if pp.exists() and json.loads(pp.read_text(encoding="utf-8"))["node_card"]["chapter"] == cid and (d / f"{d.name}.mp4").exists():
                out.append(register(cfg, ch, d, kind="training", layer=layer))
                log(f"  {d.name} → курс {cid}")
    return out
