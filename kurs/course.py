"""Генератор автономного офлайн-курса (file://) из учебного слоя, иллюстраций, заданий и доменного пакета."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import jinja2
import jsonschema

from .config import ROOT, Chapter, Config
from .course_view import (
    DIFF_NAMES,
    Paragraphs,
    TermMarker,
    arc_map,
    chapter_parts,
    clean_tex,
    plain_note,
    reading_minutes,
    rich,
    split_notes,
    step_number,
    term_pattern,
)
from .datasets import load_volve_slice
from .illustrations import build_illustrations, load_styleguide, phone_file, volve_node_id
from .units import Unit, build_units, cut_chapter, load_units

_TYPE_RU = {
    "single": "Один ответ",
    "multiple": "Несколько ответов",
    "order": "Порядок",
    "gap": "Пропуск",
    "calc": "Расчёт",
}


def _load_glossary(domain_dir: Path) -> list[dict]:
    p = domain_dir / "glossary.md"
    if not p.exists():
        return []
    entries = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s.startswith("|") or s.startswith("|---") or "Термин (RU)" in s or "Искажение в md" in s:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 3 and cells[0]:
            entries.append(
                {
                    "term": cells[0],
                    "definition": cells[1],
                    "en": " · ".join(c for c in cells[2:] if c and c != "—"),
                }
            )
    return entries


def _load_errata(domain_dir: Path, chapter_id: str) -> dict[str, dict]:
    p = domain_dir / "errata" / f"{chapter_id}.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    by_key: dict[str, dict] = {}
    for e in data.get("entries", []):
        if e.get("unit_id"):
            by_key[e["unit_id"]] = e
        if e.get("task_number"):
            by_key[f"task:{e['task_number']}"] = e
    return by_key


class OcrFix:
    """Исправления распознавания для показа: индексы формул и пометки из domain/<домен>/errata. Цитаты не меняются."""

    def __init__(self, domain_dir: Path, chapter_id: str) -> None:
        p = domain_dir / "errata" / f"{chapter_id}.json"
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        self.subs = [(re.compile(x["find"]), x["to"]) for x in data.get("tex_subs", [])]
        self.notes = data.get("formula_notes", [])

    def tex(self, text: str) -> str:
        for pat, to in self.subs:
            text = pat.sub(lambda m, to=to: to, text or "")
        return text

    def note(self, page: int, tex: str) -> list[str]:
        return [x["note"] for x in self.notes if x["page_pdf"] == page and x["contains"] in (tex or "")]


def _parse_markdown_table(text: str) -> dict | None:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    caption = ""
    tbl_lines = []
    for l in lines:
        if l.startswith("|"):
            tbl_lines.append(l)
        elif not tbl_lines:
            caption = l.strip("*")
    if len(tbl_lines) < 2:
        return None
    headers = [c.strip() for c in tbl_lines[0].strip("|").split("|")]
    rows = []
    for l in tbl_lines[1:]:
        if re.match(r"^\|[\s:|\-]+\|$", l):
            continue
        cells = [c.strip() for c in l.strip("|").split("|")]
        while len(cells) < len(headers):
            cells.append("")
        cells = cells[: len(headers)]
        # строка таблицы, разрезанная границей страницы: «порового про-» / «странства…»
        if rows and rows[-1][0].endswith("-") and cells[0][:1].islower() and not any(cells[1:]):
            rows[-1][0] = rows[-1][0][:-1] + cells[0]
            continue
        rows.append(cells)
    if not rows:
        return None
    return {"caption": caption, "headers": headers, "rows": rows}


def _copy_local_assets(out_dir: Path) -> None:
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    static_dir = ROOT / "kurs" / "static"
    for fname in ("course.css", "course.js"):
        shutil.copy2(static_dir / fname, assets_dir / fname)

    node_modules = ROOT / "video" / "node_modules"
    sortable = node_modules / "sortablejs" / "Sortable.min.js"
    if not sortable.exists():
        raise FileNotFoundError(f"Не найден локальный SortableJS: {sortable}")
    shutil.copy2(sortable, assets_dir / "Sortable.min.js")

    katex_dist = node_modules / "katex" / "dist"
    if not (katex_dist / "katex.min.js").exists():
        raise FileNotFoundError(f"Не найден локальный KaTeX: {katex_dist}")
    shutil.copy2(katex_dist / "katex.min.js", assets_dir / "katex.min.js")

    # В CSS оставляем только woff2, чтобы не тащить устаревшие ttf/woff и не бить 404.
    css_raw = (katex_dist / "katex.min.css").read_text(encoding="utf-8")
    css_woff2 = re.sub(r",url\(fonts/[^)]+\.(?:woff|ttf)\)\s*format\([^)]+\)", "", css_raw)
    (assets_dir / "katex.min.css").write_text(css_woff2, encoding="utf-8")

    fonts_out = assets_dir / "fonts"
    fonts_out.mkdir(exist_ok=True)
    for wf in (katex_dist / "fonts").glob("*.woff2"):
        shutil.copy2(wf, fonts_out / wf.name)


def _resolve_task_dependencies(
    bt: dict,
    units: list[Unit],
    errata: dict[str, dict],
) -> list[dict]:
    deps: list[dict] = []
    seen: set[str] = set()

    def add(ref: str, u: Unit, text: str | None = None) -> None:
        if ref not in seen:
            seen.add(ref)
            deps.append({"ref": ref, "text": text or errata.get(u.id, {}).get("corrected_text") or u.text, "unit": u})

    search_blob = f"{bt.get('text', '')} {bt.get('figure') or ''}"
    # Ссылки на рис. N
    for fig_num in re.findall(r"рис\.?\s*(\d+(?:\.\d+)?)", search_blob, re.I):
        for u in units:
            if u.kind == "figure" and not u.model_description and re.search(rf"рис\.?\s*{re.escape(fig_num)}\b", u.text, re.I):
                add(f"рис. {fig_num}, стр. {u.page_pdf}", u)
                break
    # Ссылки на табл. N
    for tbl_num in re.findall(r"табл\.?\s*(\d+(?:\.\d+)?)", search_blob, re.I):
        for u in units:
            if u.kind == "table" and re.search(rf"таблица\s*{re.escape(tbl_num)}\b", u.text, re.I):
                add(f"табл. {tbl_num}, стр. {u.page_pdf}", u)
                break
    # Ссылка на предыдущую задачу (напр., задача 101 -> задача 100)
    if re.search(r"предыдущ\w+\s+задач", search_blob, re.I) and str(bt.get("number", "")).isdigit():
        prev_num = str(int(bt["number"]) - 1)
        for u in units:
            if u.kind == "task" and u.task_number == prev_num:
                txt = errata.get(f"task:{prev_num}", {}).get("corrected_text") or u.text
                add(f"задача {prev_num}, стр. {u.page_pdf}", u, txt)
                break
    return deps


def _node_terms(node: dict, glossary: list[dict]) -> list[dict]:
    """Термины шага с определениями: из карточки, затем из глоссария домена. Без определения термин не показываем."""
    own = {g["term"].lower(): g for g in node.get("glossary") or [] if g.get("definition")}
    out = list(own.values())
    for t in node.get("terms_introduced") or []:
        if t.lower() in own:
            continue
        pat = term_pattern(t)
        hit = next((g for g in glossary if g["term"].lower() == t.lower()), None)
        hit = hit or next((g for g in glossary if pat and pat.search(g["term"]) and len(g["term"].split()) <= len(t.split()) + 1), None)
        if hit:
            out.append({"term": t, "definition": hit["definition"], "en": hit["en"]})
    return [{"term": g["term"], "definition": g["definition"], "en": "" if g.get("en") in (None, "—") else g["en"]} for g in out]


def _node_formulas(node: dict, units: list[Unit], pages: list[int], ocr: OcrFix) -> list[dict]:
    if "formulas" in node:
        out = []
        for f in node["formulas"] or []:
            raw = clean_tex(f.get("latex") or "")
            tex = ocr.tex(clean_tex(f.get("latex_fixed") or "") or raw)
            if not tex:
                continue
            meaning, notes = split_notes(f.get("meaning") or "")
            notes = notes + ocr.note(f["page_pdf"], f.get("latex") or "")
            out.append({"page": f["page_pdf"], "tex": tex, "raw": raw if _tex_key(raw) != _tex_key(tex) else "", "meaning": rich(meaning), "notes": [plain_note(x) for x in notes]})
        return out
    return [
        {"page": u.page_pdf, "tex": ocr.tex(clean_tex(u.text)), "raw": "", "meaning": "", "notes": []}
        for u in units
        if u.kind == "formula" and not u.model_description and u.page_pdf in pages
    ]


def _tex_key(tex: str) -> str:
    return re.sub(r"\\(?:quad|qquad|,|;|!| )|\s|\([\d.,]+\)$", "", tex)


def _node_tables(node: dict, units: list[Unit], pages: list[int], ocr: OcrFix) -> list[dict]:
    own = set(node.get("unit_ids") or [])
    out = []
    for u in units:
        if u.kind != "table" or u.model_description or (u.id not in own if own else u.page_pdf not in pages):
            continue
        tb = _table_view(u, ocr)
        if tb:
            out.append(tb)
    return out


def _table_view(u: Unit, ocr: OcrFix) -> dict | None:
    parsed = _parse_markdown_table(ocr.tex(u.text))
    if not parsed:
        return None
    return {
        "page": u.page_pdf,
        "caption": rich(parsed["caption"]),
        "headers": [rich(x) for x in parsed["headers"]],
        "rows": [[rich(x) for x in r] for r in parsed["rows"]],
    }


def _dependency_view(d: dict, ocr: OcrFix) -> dict:
    table = _table_view(d["unit"], ocr) if d["unit"].kind == "table" else None
    return {"ref": d["ref"], "html": "" if table else rich(ocr.tex(d["text"])), "table": table}


def _load_videos(ch: Chapter, out_dir: Path) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Готовые ролики главы (build/<глава>/videos.json) → ролики по шагам и субтитры для офлайн-плеера."""
    p = ch.build_dir / "videos.json"
    if not p.exists():
        return {}, {}
    by_node: dict[str, list[dict]] = {}
    subs: dict[str, list[dict]] = {}
    for v in json.loads(p.read_text(encoding="utf-8")).get("videos", []):
        src = ch.build_dir / v["file"]
        if not src.exists():
            continue
        dst = out_dir / "assets" / "video" / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        poster = ""
        if v.get("poster") and (ch.build_dir / v["poster"]).exists():
            shutil.copy2(ch.build_dir / v["poster"], dst.parent / Path(v["poster"]).name)
            poster = f"assets/video/{Path(v['poster']).name}"
        subs[v["id"]] = v.get("cues") or []
        for nid in v.get("node_ids") or [v.get("node_id")]:
            by_node.setdefault(nid, []).append({"id": v["id"], "title": v["title"], "file": f"assets/video/{src.name}", "poster": poster, "vertical": bool(v.get("vertical"))})
    return by_node, subs


def _task_view(t: dict, claim_pages: dict[str, int]) -> dict:
    pages = sorted({claim_pages[c] for c in t.get("claim_ids", []) if c in claim_pages})
    parts = [rich(t["prompt"])]
    gaps = 0
    if t["type"] == "gap":
        chunks = re.split(r"\{\{\d+\}\}|_{3,}", t["prompt"])
        gaps = len(chunks) - 1
        parts = [rich(c) for c in chunks]
    return {**t, "type_ru": _TYPE_RU.get(t["type"], t["type"]), "pages": pages, "prompt_parts": parts, "gaps": gaps}


def build_course(
    cfg: Config,
    ch: Chapter,
    layer: dict,
    *,
    tasks_doc: dict | None = None,
    routing_doc: dict | None = None,
    out_dir: Path | None = None,
) -> Path:
    """Собирает автономный курс в out_dir (по умолчанию build/<chapter>/course)."""
    if not ch.md_path.exists() or not (ch.build_dir / "units.json").exists():
        cut_chapter(cfg, ch)
        build_units(cfg, ch)
    units = load_units(ch)
    sg = load_styleguide(cfg)
    glossary = _load_glossary(cfg.domain_dir)
    errata = _load_errata(cfg.domain_dir, ch.id)
    ocr = OcrFix(cfg.domain_dir, ch.id)
    volve = load_volve_slice(cfg.domain_dir)

    default_out = ch.build_dir / "course"
    out_dir = out_dir or default_out
    json_dir = ch.build_dir if out_dir == default_out else out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    _copy_local_assets(out_dir)

    if routing_doc is None and (ch.build_dir / "routing.json").exists():
        routing_doc = json.loads((ch.build_dir / "routing.json").read_text(encoding="utf-8"))
    interactive = frozenset(
        r["node_id"] for r in (routing_doc or {}).get("routes", []) if "интерактив" in (r.get("format"), r.get("secondary_format"))
    )
    ill_doc = build_illustrations(cfg, ch, layer, out_dir, json_dir=json_dir, interactive=interactive)
    ills_by_step: dict[str, list[dict]] = {}
    for ill in ill_doc["illustrations"]:
        phone = phone_file(ill["file"])
        view = {**ill, "frames": ill_doc["frames"].get(ill["id"]), "phone": phone if (out_dir / phone).exists() else None}
        ills_by_step.setdefault(ill["step_id"], []).append(view)
    volve_id = volve_node_id(layer) if volve else None
    videos_by_node, subtitles = _load_videos(ch, out_dir)

    if tasks_doc is None and (ch.build_dir / "tasks.json").exists():
        tasks_doc = json.loads((ch.build_dir / "tasks.json").read_text(encoding="utf-8"))
    nodes = sorted(layer.get("nodes", []), key=lambda x: x.get("order", 0))
    node_ids = {n["id"] for n in nodes}
    tasks = [t for t in (tasks_doc or {}).get("tasks", []) if t["step_id"] in node_ids]
    if tasks:
        schema = json.loads((cfg.schemas_dir / "task.schema.json").read_text(encoding="utf-8"))
        jsonschema.validate(tasks_doc, schema)
        # Готовый файл главы не переписываем: иначе генератор примет новую запись за ручную правку.
        if json_dir != ch.build_dir or not (json_dir / "tasks.json").exists():
            (json_dir / "tasks.json").write_text(json.dumps(tasks_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    routes = {r["node_id"]: r for r in (routing_doc or {}).get("routes", [])}

    claim_pages = {c["id"]: c["page_pdf"] for n in nodes for c in n.get("claims", [])}
    tasks_by_step: dict[str, list[dict]] = {}
    tasks_by_id: dict[str, dict] = {}
    for t in tasks:
        tasks_by_step.setdefault(t["step_id"], []).append(_task_view(t, claim_pages))
        tasks_by_id[t["id"]] = t

    title_by_id = {n["id"]: n["title"] for n in nodes}
    marker = TermMarker()
    steps: list[dict] = []
    cards: list[dict] = []

    for idx, n in enumerate(nodes):
        nid = n["id"]
        layers = n.get("layers", {})
        pages = sorted({*n.get("pages", []), *(c["page_pdf"] for c in n.get("claims", []))})
        terms = _node_terms(n, glossary)
        main = layers.get("main") or {}
        main_text, main_notes = split_notes(main.get("text", ""))
        main_html = marker.html(main_text or main.get("text", ""), terms)

        paras = Paragraphs(n, marker, terms)
        paras.add_layer("mechanism", layers.get("mechanism"), pages)
        for st in n.get("steps") or []:
            paras.add("steps", step_number(st.get("text", "")), st.get("pages") or pages, bool(st.get("by_model", True)), st.get("unit_ids") or ())
        paras.add_layer("practice", layers.get("practice"), pages)
        for cnd in n.get("conditions") or []:
            paras.add("conditions", cnd.get("text", ""), cnd.get("pages") or pages, bool(cnd.get("by_model", True)), cnd.get("unit_ids") or ())
        paras.add_layer("nuances", layers.get("nuances"), pages)
        for dp in layers.get("deeper") or []:
            paras.add("deeper", dp.get("text", ""), dp.get("pages") or pages, bool(dp.get("by_model", True)), dp.get("unit_ids") or ())
        paras.attach(n.get("claims", []))

        bt_views = []
        for bt in n.get("book_tasks") or []:
            err = errata.get(f"task:{bt.get('number', '')}")
            fixed = {**bt, "text": err["corrected_text"] if err else bt["text"]}
            bt_views.append(
                {
                    "number": bt.get("number", ""),
                    "page": bt["page_pdf"],
                    "figure": bt.get("figure"),
                    "html": rich(ocr.tex(re.sub(rf"^\s*{re.escape(str(bt.get('number', '')))}\.\s*", "", fixed["text"]))),
                    "errata_note": err["reason"] if err else None,
                    "dependencies": [_dependency_view(d, ocr) for d in _resolve_task_dependencies(fixed, units, errata)],
                }
            )

        diff = n.get("difficulty") or {}
        names = [DIFF_NAMES.get(d, d) for d in (diff.get("primary"), diff.get("secondary")) if d]
        why, why_notes = split_notes((n.get("why_here") or {}).get("text", ""))
        dm = n.get("data_meaning")
        dec = n.get("decision_on_data")
        lens = n.get("inefficiency_lens")
        scan_notes = [f"стр. {q['page']}: {plain_note(q['what'])}" for q in n.get("needs_scan") or []]
        seen: set[str] = set()
        notes = [x for x in scan_notes if not (x in seen or seen.add(x))] + [plain_note(x) for x in (*why_notes, *(main_notes if main_text else []))]
        prereq = [l["target"] for l in n.get("links", []) if l["type"] == "prerequisite" and l["target"] in title_by_id]
        step_tasks = tasks_by_step.get(nid, [])
        mech, steps_l = paras.block("mechanism"), paras.block("steps")
        steps.append(
            {
                "id": nid,
                "order": n.get("order", idx + 1),
                "title": n["title"],
                "title_book": n.get("title_book") or ch.title,
                "difficulty": ", ".join(names),
                "route": (routes.get(nid) or {}).get("format", ""),
                "threshold": bool(n.get("threshold")),
                "pages": pages,
                "pages_label": _pages_label(pages),
                "minutes": reading_minutes([main.get("text"), *(q["text"] for q in paras.items)], len(step_tasks)),
                "main_html": main_html,
                "main_text": main_text or main.get("text", ""),
                "main_pages": _pages_label(main.get("pages") or pages),
                "why_here": why,
                "mechanism": mech,
                "steps_list": steps_l,
                "practice": paras.block("practice"),
                "conditions": paras.block("conditions") + paras.block("nuances"),
                "deeper": paras.block("deeper"),
                "now_practice": (n.get("now_practice") or {}).get("text") if n.get("now_practice") else None,
                "lens": lens,
                "examples": [
                    {"page": w["page_pdf"], "condition": rich(w["condition"]), "steps": [rich(x) for x in w["steps"]], "result": rich(w["result"]), "figure": w.get("figure")}
                    for w in n.get("worked_examples") or []
                ],
                "terms": terms,
                "formulas": _node_formulas(n, units, pages, ocr),
                "variables": [{"tex": ocr.tex(clean_tex(v["symbol"])), "meaning": re.sub(r"(?<=\w)_(?=\w)", "", v["meaning"]), "unit": v.get("unit") or ""} for v in n.get("variables") or []],
                "tables": _node_tables(n, units, pages, ocr),
                "misconception": rich(split_notes(n["misconception"]["text"])[0] or n["misconception"]["text"]) if n.get("misconception") else None,
                "data_from": rich(((dm or {}).get("from_chapter") or {}).get("text", "")),
                "data_breaks": rich(((dm or {}).get("breaks") or {}).get("text", "")),
                "hypotheses": [rich(h["text"] if isinstance(h, dict) else h) for h in (dm or {}).get("hypotheses") or []],
                "it_analogy": n.get("it_analogy"),
                "decision": {
                    "features": dec.get("features") or [],
                    "rule": rich(split_notes(dec.get("rule", ""))[0]),
                    "conclusion": rich(split_notes(dec.get("conclusion", ""))[0]),
                    "counterexample": rich(split_notes(dec.get("counterexample") or "")[0]),
                }
                if dec
                else None,
                "book_tasks": bt_views,
                "illustrations": ills_by_step.get(nid, []),
                "videos": videos_by_node.get(nid, []),
                "tasks": step_tasks,
                "prereq_ids": prereq,
                "prereq": [{"id": p, "title": title_by_id[p]} for p in prereq],
                "notes": notes,
                "show_volve": nid == volve_id,
            }
        )
        cards.append({"id": nid, "front": n["title"], "back": main_text or main.get("text", ""), "pages": _pages_label(pages)})

    for s, n in zip(steps, nodes):
        for g in s["terms"]:
            cards.append({"id": f"term:{g['term']}", "front": g["term"], "back": g["definition"], "pages": s["pages_label"]})

    parts = chapter_parts(nodes, units)
    step_by_id = {s["id"]: s for s in steps}
    for p in parts:
        p["steps"] = [step_by_id[i] for i in p["ids"]]
        for s in p["steps"]:
            s["part_label"] = f"Часть {p['n']}" + (f". {p['title']}" if p["title"] else "")
    payload = {
        "chapter": ch.id,
        "steps": [{"id": s["id"], "title": s["title"]} for s in steps],
        "tasksById": tasks_by_id,
        "claimPages": claim_pages,
        "cards": cards,
        "volve": volve if volve_id else None,
        "subtitles": subtitles,
    }
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(ROOT / "kurs" / "templates")),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    rendered = env.get_template("course.html.j2").render(
        lang=ch.language or "ru",
        chapter_id=ch.id,
        chapter_title=ch.title,
        steps=steps,
        parts=parts,
        cards=cards,
        volve=volve,
        styleguide=sg,
        arc=arc_map(steps),
        payload=payload,
        total_minutes=sum(s["minutes"] for s in steps),
    )
    (out_dir / "index.html").write_text(rendered, encoding="utf-8")
    return out_dir


def _pages_label(pages: list[int]) -> str:
    """90, 91, 92, 97 → «90–92, 97»."""
    ps = sorted(set(pages))
    out: list[str] = []
    k = 0
    while k < len(ps):
        j = k
        while j + 1 < len(ps) and ps[j + 1] == ps[j] + 1:
            j += 1
        out.append(str(ps[k]) if j == k else f"{ps[k]}–{ps[j]}")
        k = j + 1
    return ", ".join(out)


def verify_course_dir(cfg: Config, ch: Chapter, out_dir: Path) -> list[str]:
    """Приёмка собранного офлайн-курса: индекс, размер < 30 МБ, отсутствие сетевых ссылок, локальные файлы."""
    errors: list[str] = []
    idx = out_dir / "index.html"
    if not idx.exists():
        return [f"Отсутствует {idx}"]
    html_text = idx.read_text(encoding="utf-8")

    # Размер без видео < 30_000_000 байт
    total_bytes = sum(
        p.stat().st_size
        for p in out_dir.rglob("*")
        if p.is_file() and p.suffix.lower() not in {".mp4", ".webm"}
    )
    if total_bytes >= 30_000_000:
        errors.append(f"Размер курса без видео {total_bytes} байт >= 30 000 000")

    # Запрет внешних URL в ресурсах
    for m in re.finditer(r'(?:src|href)=["\'](https?://[^"\']+)["\']', html_text, re.I):
        errors.append(f"Внешняя ссылка в ресурсах: {m.group(1)}")

    # Проверка существования всех локальных ./assets/...
    for m in re.finditer(r'(?:src|href)=["\']\./([^"\']+)["\']', html_text):
        rel = m.group(1)
        if not (out_dir / rel).exists():
            errors.append(f"Отсутствует локальный ассет: {rel}")

    # Схемы иллюстраций и заданий
    json_dir = ch.build_dir if out_dir == ch.build_dir / "course" else out_dir
    for fname, sname in (("illustrations.json", "illustration.schema.json"), ("tasks.json", "task.schema.json")):
        fp = json_dir / fname
        if fp.exists():
            schema = json.loads((cfg.schemas_dir / sname).read_text(encoding="utf-8"))
            jsonschema.validate(json.loads(fp.read_text(encoding="utf-8")), schema)
        else:
            errors.append(f"Отсутствует {fp}")
    return errors
