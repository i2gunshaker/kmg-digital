"""Приёмка рисунков зрением: модель видит сам рисунок (PNG) рядом с карточкой шага и чек-листом физики.

    python -m kurs vision <глава> [...] --max-usd 0.1 [--apply]

Два прохода: первый ищет ошибку, второй (другая роль) пробует её снять. Остаётся подтверждённое обоими.
Итог — build/<глава>/vision.json. С --apply описание рисунка, собранное моделью и признанное неверным, снимается:
шаг получает простую схему из карточки. Описания, проверенные человеком, код не трогает, только называет.
"""
from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import ROOT, Chapter, Config
from .layer_gen import prompt, write_generated
from .llm import LLM, LLMError
from .verify import norm
from .visual_gen import node_card

SEVERITIES = ["critical", "significant", "minor"]
ISSUES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["issues"],
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "seen", "truth", "evidence"],
                "properties": {
                    "severity": {"type": "string", "enum": SEVERITIES},
                    "seen": {"type": "string"},
                    "truth": {"type": "string"},
                    "evidence": {"type": "string"},
                },
            },
        }
    },
}
RULING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rulings"],
    "properties": {
        "rulings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["n", "why", "real"],
                "properties": {"n": {"type": "integer"}, "why": {"type": "string"}, "real": {"type": "boolean"}},
            },
        }
    },
}
_DEFENDER = (
    "Другой проверяющий нашёл на рисунке ошибки. Ты защитник рисунка: для каждой посмотри на рисунок и карточку и реши, есть ли она на самом деле. "
    "real=false, если на рисунке этого нет, если это упрощение или если карточка такому не противоречит. real=true — только если ошибку видно и она "
    "расходится с карточкой или физикой. why — одна фраза.\n\n"
)


def frames_of(svg: Path) -> list[Path]:
    """Все кадры рисунка по порядку: node_02_1.svg, node_02_2.svg … Рисунок без кадров — он сам."""
    m = re.fullmatch(r"(node_\w+?)_(\d+)", svg.stem)
    if not m:
        return [svg]
    got = [(int(x.stem.rsplit("_", 1)[1]), x) for x in svg.parent.glob(m.group(1) + "_*.svg") if re.fullmatch(re.escape(m.group(1)) + r"_\d+", x.stem)]
    return [x for _, x in sorted(got)] or [svg]


def pictures(ch: Chapter) -> list[dict]:
    """Рисунки собранного курса: {node_id, file, frames}."""
    p = ch.build_dir / "illustrations.json"
    doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    rows = doc if isinstance(doc, list) else doc.get("illustrations", [])
    out = []
    for r in rows:
        f = ch.build_dir / "course" / r["file"]
        if f.suffix == ".svg" and f.exists():
            out.append({"node_id": r["node_id"], "file": r["file"], "path": f, "frames": frames_of(f), "caption": r.get("caption", ""), "label": r.get("label", "")})
    return out


def shoot(rows: list[dict], out: Path, log=print) -> None:
    """SVG → PNG тем же браузером, что и приёмка курса."""
    out.mkdir(parents=True, exist_ok=True)
    jobs = []
    for r in rows:
        r["pngs"] = []
        for f in r["frames"]:
            m = re.search(r'viewBox="[\d.\-]+ [\d.\-]+ ([\d.]+) ([\d.]+)"', f.read_text(encoding="utf-8"))
            w, h = (round(float(m.group(1))), round(float(m.group(2)))) if m else (960, 600)
            html, png = out / (f.stem + ".html"), out / (f.stem + ".png")
            html.write_text(
                f'<!doctype html><meta charset="utf-8"><style>body{{margin:0;background:#0F141A}}img{{display:block;width:{w}px}}</style>'
                f'<body data-ready="1"><img src="{f.resolve().as_uri()}">', encoding="utf-8")
            r["pngs"].append(png)
            if not png.exists() or png.stat().st_mtime < f.stat().st_mtime:
                jobs.append({"html": str(html), "png": str(png), "width": w, "height": h})
    if jobs:
        jp = out / "jobs.json"
        jp.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
        subprocess.run(["node", str(ROOT / "video" / "render_scenes.mjs"), str(jp)], check=True, capture_output=True, cwd=ROOT)
    log(f"  снимков рисунков: {len(jobs)} новых, рисунков {len(rows)}")


def grounded(card: str, issue: dict) -> bool:
    """Цитата-доказательство должна быть в карточке дословно; у нечитаемой подписи и у законов физики цитаты нет."""
    ev = norm(issue.get("evidence") or "").strip(" .«»\"")
    return not ev or ev in norm(card)


def check(cfg: Config, ch: Chapter, llm: LLM, layer: dict, *, log=print) -> dict:
    rows = pictures(ch)
    shoot(rows, ch.build_dir / "vision", log)
    nodes = {n["id"]: n for n in layer["nodes"]}
    rows = [r for r in rows if r["node_id"] in nodes]
    pc = cfg.domain_dir / "physics_checklist.json"
    items = json.loads(pc.read_text(encoding="utf-8")) if pc.exists() else []
    system = [prompt("vision", checklist="\n".join(f"— {x['item']}" for x in items) or "— общие законы физики")]
    effort = "low"

    def look(r: dict) -> dict:
        card = node_card(nodes[r["node_id"]], False)
        base = f"{card}\n\nПодпись под рисунком: {r['caption']} ({r['label']})"
        if len(r["pngs"]) > 1:
            base += (f"\nУ рисунка {len(r['pngs'])} кадра(ов), они даны по порядку: студент двигает ползунок и видит их один за другим. "
                     "Суди рисунок целиком: то, чего нет на одном кадре, может быть на другом.")
        out = {"node_id": r["node_id"], "file": r["file"], "issues": [], "dropped": 0, "error": ""}
        try:
            got = llm.json("senior", system, base, ISSUES_SCHEMA, label=f"vision:{r['path'].stem}", effort=effort, max_tokens=4000, images=r["pngs"])["issues"]
            got = [x for x in got if x["seen"].strip() and x["truth"].strip() and grounded(card, x)]
            if got:
                ask = base + "\n\n" + _DEFENDER + "\n".join(f"{i + 1}. [{x['severity']}] видно: {x['seen']}; верно: {x['truth']}" for i, x in enumerate(got))
                rul = llm.json("senior", system, ask, RULING_SCHEMA, label=f"vision-judge:{r['path'].stem}", effort="medium", max_tokens=4000,
                               images=r["pngs"])["rulings"]
                real = {x["n"]: x["why"] for x in rul if x["real"]}
                out["issues"] = [{**x, "why": real[i + 1]} for i, x in enumerate(got) if i + 1 in real]
                out["dropped"] = len(got) - len(out["issues"])
        except LLMError as e:
            out["error"] = str(e)[:200]
        return out

    with ThreadPoolExecutor(max_workers=max(1, int(cfg.llm.get("workers", 4)))) as pool:
        res = list(pool.map(look, rows))
    doc = {"chapter": ch.id, "pictures": len(res), "failed": sum(bool(r["error"]) for r in res),
           "with_issues": [r for r in res if r["issues"]], "dropped": sum(r["dropped"] for r in res)}
    (ch.build_dir / "vision.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def apply(ch: Chapter, doc: dict) -> list[str]:
    """Снимает собранные моделью описания с подтверждённой ошибкой. Возвращает шаги, у которых рисунок заменится простой схемой."""
    p = ch.build_dir / "visual.json"
    if not p.exists():
        return []
    vis = json.loads(p.read_text(encoding="utf-8"))
    gone = []
    for r in doc["with_issues"]:
        worst = [x for x in r["issues"] if x["severity"] in ("critical", "significant")]
        if worst and r["node_id"] in vis.get("nodes", {}):
            vis["nodes"].pop(r["node_id"])
            vis.setdefault("rejected", {})[r["node_id"]] = [f"зрение: {x['seen']} → {x['truth']}" for x in worst]
            gone.append(r["node_id"])
    if gone:
        write_generated(ch, "visual.json", vis)
    return gone
