"""Иллюстрации классов A и B к шагам курса: SVG из модели, данных и спецификаций, без нейросетевой графики."""
from __future__ import annotations

import html
import json
import math
import re
import sys
from pathlib import Path

import jsonschema

from . import visual
from .config import Chapter, Config
from .datasets import load_volve_slice
from .visual import SpecError, readable, render, render_frames, text_w


def load_styleguide(cfg: Config) -> dict:
    sg_domain = cfg.domain_dir / "styleguide.json"
    if sg_domain.exists():
        return json.loads(sg_domain.read_text(encoding="utf-8"))
    sg_input = cfg.input_dir / "пакет_вводных/03_стайлгайд/styleguide.json"
    return json.loads(sg_input.read_text(encoding="utf-8"))


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def render_volve_well_log_svg(dataset: dict, sg: dict, out_path: Path) -> None:
    """Класс A: планшет по данным Volve 15/9-F-1 B, три интервала друг под другом, глубина вниз."""
    w = 560
    left, top, track_w, gap = 92.0, 112.0, 138.0, 14.0
    ih, igap = 178.0, 56.0
    bg, panel, txt, muted = sg["background"], sg["panel"], sg["text"], "#AEB9C5"
    col = {"GR": sg["curves"]["GR"]["color"], "RT": sg["curves"]["RES"]["color"], "SW": readable(sg["fluids"]["water"]["color"])}
    ivs = dataset["intervals"]
    h = top + len(ivs) * (ih + igap) + 66
    out: list[str] = [
        f'<rect x="18" y="14" width="{text_w("реальные данные", 16, True) + 24:.0f}" height="28" rx="14" fill="{sg["provenance"]["реальные данные"]}"/>',
        f'<text x="{18 + (text_w("реальные данные", 16, True) + 24) / 2:.0f}" y="33" font-size="16" font-weight="700" fill="#FFFFFF" text-anchor="middle">реальные данные</text>',
        f'<text x="18" y="70" font-size="19" font-weight="700" fill="{txt}">Volve 15/9-F-1 B: три интервала</text>',
    ]
    lg = (math.log10(0.2), math.log10(200.0))
    tracks = [
        ("GR", "GR, API 0–100", lambda p: None if p["GR"] is None else min(1.0, max(0.0, p["GR"] / 100.0))),
        ("RT", "RT 0,2–200", lambda p: None if p["RT"] is None else min(1.0, max(0.0, (math.log10(max(0.2, p["RT"])) - lg[0]) / (lg[1] - lg[0])))),
        ("SW", "SW 0–1", lambda p: None if p["SW"] is None else min(1.0, max(0.0, p["SW"]))),
    ]
    for ti, (key, head, _) in enumerate(tracks):
        x = left + ti * (track_w + gap)
        out.append(f'<text x="{x + track_w / 2:.0f}" y="102" font-size="16" font-weight="700" fill="{col[key]}" text-anchor="middle">{head}</text>')
    verdict = {"A": "коллектор с нефтью", "B": "плотный неколлектор", "C": "водоносный песок"}
    for k, iv in enumerate(ivs):
        y = top + k * (ih + igap)
        pts = iv["points"]
        d0, d1 = pts[0]["depth_m"], pts[-1]["depth_m"]
        out.append(f'<text x="{left - 10:.0f}" y="{y + 16:.0f}" font-size="16" fill="{muted}" text-anchor="end">{_num(d0)}</text>')
        out.append(f'<text x="{left - 10:.0f}" y="{y + ih:.0f}" font-size="16" fill="{muted}" text-anchor="end">{_num(d1)}</text>')
        out.append(f'<text x="34" y="{y + ih / 2 + 10:.0f}" font-size="30" font-weight="700" fill="{txt}" text-anchor="middle">{_esc(iv["id"])}</text>')
        for ti, (key, _, fn) in enumerate(tracks):
            x = left + ti * (track_w + gap)
            out.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{track_w:.0f}" height="{ih:.0f}" fill="{panel}" stroke="#3A4754"/>')
            poly = []
            for pt in pts:
                v = fn(pt)
                if v is not None:
                    poly.append(f"{x + 4 + v * (track_w - 8):.1f},{y + (pt['depth_m'] - d0) / (d1 - d0) * ih:.1f}")
            dash = {"GR": "", "RT": "", "SW": ' stroke-dasharray="7,4"'}[key]
            out.append(f'<polyline fill="none" stroke="{col[key]}" stroke-width="2.4"{dash} points="{" ".join(poly)}"/>')
        s = iv["summary"]
        out.append(
            f'<text x="{left:.0f}" y="{y + ih + 24:.0f}" font-size="16" fill="{txt}"><tspan font-weight="700">{verdict.get(iv["id"], "")}</tspan>: '
            f'PHIF {_num(s["median_PHIF"])}, RT {_num(s["median_RT"])}, SW {_num(s["median_SW"])}</text>'
        )
    y = top + len(ivs) * (ih + igap) + 6
    out.append(f'<text x="18" y="{y:.0f}" font-size="16" fill="{txt}">Глубина по стволу в метрах растёт вниз. Числа — медианы.</text>')
    out.append(f'<text x="18" y="{y + 24:.0f}" font-size="16" fill="{muted}">Equinor Volve open dataset, CC BY 4.0</text>')
    out_path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h:.0f}" width="{w}" height="{h:.0f}" role="img" aria-label="Каротаж Volve 15/9-F-1 B" '
        f'font-family="\'Segoe UI\',\'Noto Sans\',system-ui,-apple-system,Roboto,sans-serif"><title>Каротаж Volve 15/9-F-1 B</title>'
        f'<rect width="{w}" height="{h:.0f}" rx="10" fill="{bg}"/>\n' + "\n".join(out) + "\n</svg>\n",
        encoding="utf-8",
    )


def _num(v: float) -> str:
    return f"{v:g}".replace(".", ",")


# ---------------------------------------------------------------- выбор рисунка для шага


_RANGE = re.compile(r"(?<![\d.,])(\d+(?:,\d+)?)\s*(?:[–—-]|до)\s*(\d+(?:,\d+)?)\s*(%|°C|МПа|км|см|мм|м|сут|лет|Ом·м|мкм|мкм²)(?![А-Яа-яA-Za-z])")


def _first_sentence(text: str, limit: int = 130) -> str:
    """Первая фраза без обрыва на полуслове; слишком длинная режется по запятой или слову."""
    text = re.sub(r"\s+", " ", text or "").strip()
    sent = re.split(r"(?<=[.!?])\s+(?=[А-ЯA-Z«])", text)[0]
    if len(sent) <= limit:
        return sent.rstrip(".")
    cut = sent[:limit]
    for sep in ("; ", ": ", ", "):
        if sep in cut[40:]:
            return cut[: cut.rindex(sep)]
    return cut[: cut.rindex(" ")] + "…" if " " in cut else cut


def _pages_note(node: dict) -> str:
    pages = sorted(set(node.get("pages") or []) | {c["page_pdf"] for c in node.get("claims", [])})
    if not pages:
        return "по тексту главы"
    return f"по тексту главы, стр. {pages[0]}" + (f"–{pages[-1]}" if pages[-1] != pages[0] else "")


def _auto_bars(node: dict) -> dict | None:
    """Диапазоны одной величины из утверждений автора: строка — утверждение, полоса — числа из него."""
    by_unit: dict[str, list[dict]] = {}
    for c in node.get("claims", []):
        m = _RANGE.search(c.get("text", ""))
        if not m or len(_RANGE.findall(c["text"])) != 1:
            continue
        lo, hi = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
        if hi <= lo:
            continue
        by_unit.setdefault(m.group(3), []).append({"label": _first_sentence(c["text"], 120), "lo": lo, "hi": hi})
    best = max(by_unit.items(), key=lambda kv: len(kv[1]), default=None)
    if not best or len(best[1]) < 2:
        return None
    rows = best[1][:5]
    top = max(r["hi"] for r in rows)
    if min(r["hi"] for r in rows) * 40 < top:  # разные порядки на одной шкале не сравнить
        return None
    return {"template": "bars", "params": {"rows": rows, "unit": best[0], "axis_label": best[0]}}


def auto_spec(node: dict) -> dict:
    """Рисунок по содержанию карточки, когда готовой спецификации нет: для незнакомых глав."""
    base = {"label": "схема", "class": "B", "origin": "code_from_model", "title": node["title"], "source": _pages_note(node)}
    steps = [s.get("text", "") for s in node.get("steps") or []]
    if 3 <= len(steps) <= 7:
        items = [{"text": _first_sentence(s)} for s in steps]
        return {**base, "template": "flow", "caption": f"Порядок действий: {node['title']}.", "params": {"items": items}}
    bars = _auto_bars(node)
    if bars:
        return {**base, **bars, "class": "A", "origin": "code_from_data", "caption": f"Числа главы на одной шкале: {node['title']}.", "source": _pages_note(node).replace("по тексту", "числа из текста")}
    dec = node.get("decision_on_data")
    if dec:
        items = [{"head": "Признаки", "text": _first_sentence("; ".join(dec.get("features") or []), 150), "kind": "in"}]
        for key, head, kind in (("rule", "Правило", "rule"), ("conclusion", "Вывод", "out"), ("counterexample", "Где ломается", "warn")):
            if dec.get(key):
                items.append({"head": head, "text": _first_sentence(dec[key], 150), "kind": kind})
        return {**base, "template": "flow", "caption": f"От признаков к выводу: {node['title']}.", "params": {"numbered": False, "items": items}}
    mis = (node.get("misconception") or {}).get("text")
    main = ((node.get("layers") or {}).get("main") or {}).get("text", "")
    if mis and main:
        wrong = re.split(r"(?i)\bна самом деле\b[:,]?|\bв действительности\b[:,]?|\bно\b", mis)[0].strip(" .,;")
        wrong = re.sub(r"(?i)^кажется,?\s*(?:что\s+)?", "", wrong)
        wrong = wrong[:1].upper() + wrong[1:]
        cols = [
            {"title": "Кажется", "lines": [_first_sentence(wrong, 140)], "color": "bad"},
            {"title": "На самом деле", "lines": [_first_sentence(s, 140) for s in re.split(r"(?<=[.!?])\s+", main)[:2]], "color": "ok"},
        ]
        return {**base, "template": "compare", "caption": f"Частая ошибка и вывод автора: {node['title']}.", "params": {"stack": True, "cols": cols}}
    kids = [{"title": g["term"], "lines": [_first_sentence(g["definition"], 110)]} for g in (node.get("glossary") or [])[:5] if g.get("definition")]
    if len(kids) < 2:
        kids = [{"title": _first_sentence(c["text"], 110), "lines": [f"стр. {c['page_pdf']}"]} for c in node.get("claims", [])[:4]]
    if not kids:
        kids = [{"title": _first_sentence(main or node["title"], 120)}]
    return {**base, "template": "tree", "caption": f"Основные понятия шага: {node['title']}.", "params": {"root": node["title"], "children": kids}}


def reviewed_specs(cfg: Config, ch: Chapter) -> dict[str, dict | list]:
    """Описания, проверенные человеком: лежат в доменном пакете."""
    p = cfg.domain_dir / "visual" / f"{ch.id}.json"
    return json.loads(p.read_text(encoding="utf-8")).get("nodes", {}) if p.exists() else {}


def generated_specs(ch: Chapter) -> dict[str, dict | list]:
    """Описания, собранные конвейером."""
    p = ch.build_dir / "visual.json"
    return json.loads(p.read_text(encoding="utf-8")).get("nodes", {}) if p.exists() else {}


def load_visual_specs(cfg: Config, ch: Chapter) -> dict[str, dict | list]:
    """Описания рисунков главы: собранные конвейером, поверх них проверенные человеком.

    Если у шага есть и то и другое, конвейер рисовал его потому, что проверенная схема статична,
    а маршрут требует кадров (ролик или интерактив): тогда идут обе, проверенная первой.
    """
    made, human = generated_specs(ch), reviewed_specs(cfg, ch)
    out: dict[str, dict | list] = {**made, **human}
    rp = ch.build_dir / "routing.json"
    need = needs_frames(json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else None)
    for k in made.keys() & human.keys() & need.keys():
        if not animated(human[k], need[k]) and animated(made[k], need[k]):
            out[k] = _as_list(human[k]) + _as_list(made[k])
    return out


def needs_frames(routing: dict | None) -> dict[str, str]:
    """Шаги, рисунку которых нужны кадры: «video» — отправлен в ролик, «interactive» — в интерактив."""
    out: dict[str, str] = {}
    for r in (routing or {}).get("routes", []):
        fmt = r.get("format") or ""
        if "видео" in fmt or "шортс" in fmt:
            out[r["node_id"]] = "video"
        elif "интерактив" in (fmt, r.get("secondary_format")):
            out[r["node_id"]] = "interactive"
    return out


def animated(spec: dict | list | None, kind: str) -> bool:
    """Есть ли у шага рисунок с кадрами; интерактиву хватает и раскрытия по частям."""
    items = _as_list(spec)
    if kind == "video":
        return any(len(sp.get("frames") or []) > 1 for sp in items)
    return any(len(with_reveal(sp).get("frames") or []) > 1 for sp in items)


_FIG = re.compile(r"рис(?:\.|унок)?\s*(\d+(?:\.\d+)?)", re.I)


def _as_list(spec: dict | list | None) -> list[dict]:
    return [] if spec is None else (list(spec) if isinstance(spec, list) else [spec])


def _node_specs(node: dict, specs: dict[str, dict | list]) -> list[dict]:
    """Рисунки шага; к примеру, который опирается на рисунок книги, добавляется его перерисовка с другого шага."""
    own = _as_list(specs.get(node["id"]))
    drawn = {m for sp in own for m in _FIG.findall(sp.get("source") or "")}
    for ex in node.get("worked_examples") or []:
        for num in _FIG.findall(ex.get("figure") or ""):
            if num in drawn:
                continue
            hit = next((sp for other in specs.values() for sp in _as_list(other) if num in _FIG.findall(sp.get("source") or "")), None)
            if hit:
                own.append(hit)
                drawn.add(num)
    return own


def volve_node_id(layer: dict) -> str | None:
    """Шаг, к содержанию которого относится реальный каротаж: различение пластов по сопротивлению и пористости."""
    best: tuple[int, str] | None = None
    for n in layer.get("nodes", []):
        dec = n.get("decision_on_data") or {}
        blob = " ".join([*(dec.get("features") or []), dec.get("rule") or "", dec.get("conclusion") or ""]).lower()
        if not blob or "сопротивлен" not in blob and "ρп" not in blob:
            continue
        score = 2 * ("порист" in blob or "kп" in blob) + ("нефт" in blob) + ("коллектор" in blob)
        if score >= 3 and (best is None or score > best[0]):
            best = (score, n["id"])
    return best[1] if best else None


_REVEAL = {"flow": "items", "tree": "children", "compare": "cols", "bars": "rows"}


def with_reveal(spec: dict) -> dict:
    """Интерактивный шаг без своих кадров: рисунок открывается по одной части, сначала виден целиком."""
    key = _REVEAL.get(spec.get("template", ""))
    parts = (spec.get("params") or {}).get(key) if key else None
    if spec.get("frames") or not parts or len(parts) < 2:
        return spec
    top = max((float(r.get("hi", r.get("value", 0))) for r in parts), default=0) * 1.12 if key == "rows" else 0
    extra = {"max": spec["params"].get("max") or top} if key == "rows" else {}
    frames = [{"label": "всё", "params": {}}] + [{"label": f"1–{k}" if k > 1 else "1", "params": {key: parts[:k], **extra}} for k in range(1, len(parts))]
    return {**spec, "frames_label": "Показать части по порядку", "frames_ui": "buttons", "frames": frames}


def phone_file(file: str) -> str:
    return re.sub(r"\.svg$", "_m.svg", file)


def _phone_variant(spec: dict, sg: dict) -> list[tuple[str, str]]:
    """Тот же рисунок на узком полотне: шрифт на экране телефона остаётся крупным."""
    with visual.canvas_width(visual.phone_width(spec)):
        return render_frames(spec, sg) or [("", render(spec, sg))]


def build_illustrations(
    cfg: Config, ch: Chapter, layer: dict, out_dir: Path, *, json_dir: Path | None = None, interactive: frozenset[str] = frozenset()
) -> dict:
    """Рисунки A/B ко всем шагам слоя по содержанию шага; проверка по illustration.schema.json."""
    sg = load_styleguide(cfg)
    img_dir = out_dir / "assets" / "img"
    if img_dir.exists():
        for old in img_dir.glob("*.svg"):
            old.unlink()
    img_dir.mkdir(parents=True, exist_ok=True)
    specs = load_visual_specs(cfg, ch)
    items: list[dict] = []
    frames: dict[str, dict] = {}

    for idx, n in enumerate(layer.get("nodes", []), 1):
        nid = n["id"]
        drawn_specs: list[tuple[dict, list[tuple[str, str]]]] = []
        for raw_spec in _node_specs(n, specs):
            spec = {"label": "схема", "class": "B", "origin": "code_from_model", "title": n["title"], "source": _pages_note(n), **raw_spec}
            if spec["source"] in ("по тексту главы", "числа из текста главы"):
                spec["source"] += _pages_note(n).removeprefix("по тексту главы")
            if nid in interactive and not drawn_specs:
                spec = with_reveal(spec)
            try:
                drawn_specs.append((spec, render_frames(spec, sg) or [("", render(spec, sg))]))
            except (SpecError, KeyError, ValueError, TypeError, IndexError) as e:
                print(f"рисунок {nid}: {type(e).__name__} {e}; пропускаю спецификацию", file=sys.stderr)
        if not drawn_specs:
            spec = with_reveal(auto_spec(n)) if nid in interactive else auto_spec(n)
            drawn_specs.append((spec, render_frames(spec, sg) or [("", render(spec, sg))]))
        for k, (spec, drawn) in enumerate(drawn_specs):
            suffix = chr(ord("a") + k) if k else ""
            files = []
            phone = _phone_variant(spec, sg)
            for j, (_, svg) in enumerate(drawn):
                stem = f"node_{idx:02d}{suffix}" + (f"_{j + 1}" if len(drawn) > 1 else "")
                (img_dir / f"{stem}.svg").write_text(svg, encoding="utf-8")
                (img_dir / f"{stem}_m.svg").write_text(phone[j][1], encoding="utf-8")
                files.append(f"assets/img/{stem}.svg")
            ill_id = f"ill.{idx:02d}" + (f".{k + 1}" if k else "")
            caption = spec.get("caption") or f"{spec['title']}."
            items.append(
                {
                    "id": ill_id,
                    "step_id": nid,
                    "node_id": nid,
                    "file": files[0],
                    "frame_class": spec["class"],
                    "origin": spec["origin"],
                    "label": spec["label"],
                    "caption": caption,
                    "alt": spec.get("alt") or caption,
                    "source": spec.get("source") or "",
                    "claim_ids": spec.get("claim_ids") or [c["id"] for c in n.get("claims", [])],
                }
            )
            if len(drawn) > 1:
                frames[ill_id] = {"label": spec.get("frames_label") or "Кадр", "ui": spec.get("frames_ui") or "slider", "items": [{"label": lab, "file": f, "phone": phone_file(f)} for (lab, _), f in zip(drawn, files)]}

    volve = load_volve_slice(cfg.domain_dir)
    vid = volve_node_id(layer) if volve else None
    if vid:
        idx = next(i for i, n in enumerate(layer["nodes"], 1) if n["id"] == vid)
        render_volve_well_log_svg(volve, sg, img_dir / "volve_15_9_f1b.svg")
        items.append(
            {
                "id": f"ill.{idx:02d}.volve",
                "step_id": vid,
                "node_id": vid,
                "file": "assets/img/volve_15_9_f1b.svg",
                "frame_class": "A",
                "origin": "code_from_data",
                "label": "реальные данные",
                "caption": "Три интервала скважины Volve 15/9-F-1 B: гамма, сопротивление и водонасыщенность.",
                "alt": "Каротаж Volve 15/9-F-1 B: нефтеносный интервал A, плотный прослой B и водоносный песок C.",
                "source": "Equinor Volve open dataset, скв. 15/9-F-1 B (CC BY 4.0)",
                "claim_ids": [c["id"] for c in layer["nodes"][idx - 1].get("claims", [])],
            }
        )

    doc = {"chapter": ch.id, "illustrations": items}
    schema = json.loads((cfg.schemas_dir / "illustration.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(doc, schema)
    (json_dir or ch.build_dir).mkdir(parents=True, exist_ok=True)
    ((json_dir or ch.build_dir) / "illustrations.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**doc, "frames": frames}
