"""Генератор автономного офлайн-курса (file://) из учебного слоя, иллюстраций, заданий и доменного пакета."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import jinja2
import jsonschema
from markupsafe import escape

from .config import ROOT, Chapter, Config
from .datasets import load_volve_slice
from .illustrations import build_illustrations, load_styleguide
from .units import Unit, build_units, cut_chapter, load_units

_TYPE_RU = {
    "single": "Один ответ",
    "multiple": "Несколько ответов",
    "order": "Порядок",
    "gap": "Пропуск",
    "calc": "Расчёт",
}

_KIND_RU = {
    "observation": "Наблюдение",
    "inference": "Вывод",
    "assumption": "Допущение",
}

_DIFF_NAMES = {
    "М": "Механизм",
    "В": "Время",
    "И": "Измерение",
    "Ф": "Формализм",
    "П": "Пространство",
    "Пр": "Процедура",
    "Н": "Неопределённость",
    "Р": "Распознавание",
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
        rows.append(cells[: len(headers)])
    if not rows:
        return None
    return {"caption": caption, "headers": headers, "rows": rows}


def _highlight_terms(text: str, terms: list[str]) -> str:
    safe = str(escape(text))
    for t in sorted({x.strip() for x in terms if len(x.strip()) >= 4}, key=len, reverse=True):
        pat = re.compile(rf"(?<![А-Яа-яЁёA-Za-z])({re.escape(t)})(?![А-Яа-яЁёA-Za-z])", re.I)
        safe = pat.sub(r'<mark class="term-hl">\1</mark>', safe, count=1)
    return safe


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


def _demo_tasks_for_layer(layer: dict) -> dict:
    """Формирует демонстрационный набор заданий всех 5 типов по утверждениям слоя, если tasks.json не передан."""
    nodes = layer.get("nodes", [])
    n0 = nodes[0]
    cid0 = n0["claims"][0]["id"]
    sid0 = n0["id"]
    tasks = [
        {
            "id": f"{sid0}.t1",
            "type": "single",
            "step_id": sid0,
            "claim_ids": [cid0],
            "prompt": f"Какой основной вывод следует из шага «{n0['title']}»?",
            "options": [
                {"id": "a", "text": n0["claims"][0]["text"], "misconception": None},
                {"id": "b", "text": "Достаточно одного визуального признака на кривой без расчётов.", "misconception": "misc.log_is_rock"},
                {"id": "c", "text": "Тип породы и флюида не влияет на отклик приборов.", "misconception": "misc.high_res_oil"},
            ],
            "answer": "a",
            "explanation": f"Утверждение {cid0} (стр. {n0['claims'][0]['page_pdf']}) фиксирует необходимость количественной интерпретации.",
            "kind": "understanding",
        },
        {
            "id": f"{sid0}.t2",
            "type": "multiple",
            "step_id": sid0,
            "claim_ids": [cid0],
            "prompt": "Какие сведения необходимы для выделения коллектора в сложном разрезе?",
            "options": [
                {"id": "a", "text": "Количественная интерпретация геофизических данных", "misconception": None},
                {"id": "b", "text": "Геологические сведения о характере разреза", "misconception": None},
                {"id": "c", "text": "Только один параметр без учёта типа породы", "misconception": "misc.high_res_oil"},
            ],
            "answer": ["a", "b"],
            "explanation": "По тексту шага нужны одновременно количественная интерпретация и геологические сведения.",
            "kind": "recall",
        },
        {
            "id": f"{sid0}.t3",
            "type": "order",
            "step_id": sid0,
            "claim_ids": [cid0],
            "prompt": "Расположите этапы работы интерпретатора от данных к выводу:",
            "options": [
                {"id": "s2", "text": "Сопоставление откликов с геологической моделью"},
                {"id": "s1", "text": "Сбор комплекса геофизических кривых"},
                {"id": "s3", "text": "Выделение коллектора и оценка насыщения"},
            ],
            "answer": ["s1", "s2", "s3"],
            "explanation": "Сначала получают измерения комплекса, затем интерпретируют с геологией и выделяют коллектор.",
            "kind": "understanding",
        },
        {
            "id": f"{sid0}.t4",
            "type": "gap",
            "step_id": sid0,
            "claim_ids": [cid0],
            "prompt": "По данным Volve 15/9-F-1 B интервал B имеет высокое сопротивление, но это плотный ____ (неколлектор / коллектор).",
            "answer": ["неколлектор"],
            "explanation": "При PHIF=0.02 и SAND_FLAG=0 высокое сопротивление создаёт плотная порода, а не нефть.",
            "kind": "data",
        },
        {
            "id": f"{sid0}.t5",
            "type": "calc",
            "step_id": sid0,
            "claim_ids": [cid0],
            "prompt": "По данным Volve 15/9-F-1 B: в интервале A медиана PHIF = 0.228, а в интервале B равна 0.020. Во сколько раз пористость в A выше, чем в B?",
            "answer": 11.4,
            "tolerance": 0.3,
            "unit": "раз",
            "explanation": "0.228 / 0.020 = 11.4 — поровое пространство в плотном прослое B почти отсутствует.",
            "kind": "data",
        },
    ]
    return {"chapter": layer["chapter"], "tasks": tasks}


def _build_map_svg(steps: list[dict]) -> str:
    if not steps:
        return ""
    w = max(620, len(steps) * 190 + 40)
    h = 180
    pos = {}
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Карта связей шагов">',
        '  <defs><marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="#4DC4BF"/></marker></defs>',
    ]
    for i, s in enumerate(steps):
        x = 24 + i * 185
        y = 55 if i % 2 == 0 else 80
        pos[s["id"]] = (x + 80, y + 26)
    for s in steps:
        x2, y2 = pos[s["id"]]
        for pid in s.get("prereq_ids", []):
            if pid in pos:
                x1, y1 = pos[pid]
                lines.append(f'  <line x1="{x1 + 78}" y1="{y1}" x2="{x2 - 80}" y2="{y2}" stroke="#4DC4BF" stroke-width="2" marker-end="url(#arr)"/>')
    for s in steps:
        cx, cy = pos[s["id"]]
        x, y = cx - 80, cy - 26
        lbl = s["short_title"][:24] + ("…" if len(s["short_title"]) > 24 else "")
        lines.append(
            f'  <a href="#step-{escape(s["id"])}" data-goto-step="{escape(s["id"])}">'
            f'<rect x="{x}" y="{y}" width="160" height="52" rx="8" fill="#1A2129" stroke="#4DC4BF" stroke-width="1.5"/>'
            f'<text x="{cx}" y="{y + 22}" text-anchor="middle" fill="#ECEFF4" font-size="11.5" font-weight="700">{s["order"]}. {escape(lbl)}</text>'
            f'<text x="{cx}" y="{y + 40}" text-anchor="middle" fill="#9BA7B4" font-size="10">{escape(s["difficulty_label"])}</text>'
            f'</a>'
        )
    lines.append("</svg>")
    return "\n".join(lines)


def _resolve_task_dependencies(
    bt: dict,
    units: list[Unit],
    errata: dict[str, dict],
) -> list[dict]:
    deps: list[dict] = []
    search_blob = f"{bt.get('text', '')} {bt.get('figure') or ''}"
    # Ссылки на рис. N
    for fig_num in re.findall(r"рис\.?\s*(\d+(?:\.\d+)?)", search_blob, re.I):
        for u in units:
            if u.kind == "figure" and not u.model_description and re.search(rf"рис\.?\s*{re.escape(fig_num)}\b", u.text, re.I):
                deps.append({"ref": f"рис. {fig_num}, стр. {u.page_pdf}", "text": u.text})
                break
    # Ссылки на табл. N
    for tbl_num in re.findall(r"табл\.?\s*(\d+(?:\.\d+)?)", search_blob, re.I):
        for u in units:
            if u.kind == "table" and re.search(rf"таблица\s*{re.escape(tbl_num)}\b", u.text, re.I):
                deps.append({"ref": f"табл. {tbl_num}, стр. {u.page_pdf}", "text": u.text[:400]})
                break
    # Ссылка на предыдущую задачу (напр., задача 101 -> задача 100)
    if re.search(r"предыдущ\w+\s+задач", search_blob, re.I) and str(bt.get("number", "")).isdigit():
        prev_num = str(int(bt["number"]) - 1)
        for u in units:
            if u.kind == "task" and u.task_number == prev_num:
                txt = errata.get(f"task:{prev_num}", {}).get("corrected_text") or u.text
                deps.append({"ref": f"задача {prev_num}, стр. {u.page_pdf}", "text": txt})
                break
    return deps


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
    glossary_map = {g["term"].lower(): g for g in glossary}
    errata = _load_errata(cfg.domain_dir, ch.id)
    volve = load_volve_slice(cfg.domain_dir)

    out_dir = out_dir or (ch.build_dir / "course")
    out_dir.mkdir(parents=True, exist_ok=True)
    _copy_local_assets(out_dir)

    ill_doc = build_illustrations(cfg, ch, layer, out_dir)
    ills_by_step: dict[str, list[dict]] = {}
    for ill in ill_doc.get("illustrations", []):
        ills_by_step.setdefault(ill["step_id"], []).append(ill)

    if tasks_doc is None:
        tp = ch.build_dir / "tasks.json"
        tasks_doc = json.loads(tp.read_text(encoding="utf-8")) if tp.exists() else _demo_tasks_for_layer(layer)
    task_schema = json.loads((cfg.schemas_dir / "task.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(tasks_doc, task_schema)
    (ch.build_dir / "tasks.json").write_text(json.dumps(tasks_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    routes_by_node = {}
    if routing_doc:
        for r in routing_doc.get("routes", []):
            routes_by_node[r["node_id"]] = r

    tasks_by_step: dict[str, list[dict]] = {}
    tasks_by_id: dict[str, dict] = {}
    for t in tasks_doc.get("tasks", []):
        t_view = {**t, "type_ru": _TYPE_RU.get(t["type"], t["type"])}
        tasks_by_step.setdefault(t["step_id"], []).append(t_view)
        tasks_by_id[t["id"]] = t_view

    nodes = sorted(layer.get("nodes", []), key=lambda x: x.get("order", 0))
    title_by_id = {n["id"]: n["title"] for n in nodes}

    steps: list[dict] = []
    cards: list[dict] = []
    has_explicit_decision = any(n.get("decision_on_data") for n in nodes)

    for idx, n in enumerate(nodes):
        nid = n["id"]
        node_pages = sorted({*n.get("pages", []), *(c["page_pdf"] for c in n.get("claims", []))})
        terms_intro = n.get("terms_introduced", [])
        all_terms = [*terms_intro, *n.get("terms_used", [])]
        claims_view = []
        for c in n.get("claims", []):
            claims_view.append(
                {
                    **c,
                    "kind_ru": _KIND_RU.get(c["kind"], c["kind"]),
                    "text_html": _highlight_terms(c["text"], all_terms),
                }
            )
        # Таблицы и формулы из единиц на страницах узла
        tables = []
        formulas = []
        for u in units:
            if u.model_description or u.page_pdf not in node_pages:
                continue
            if u.kind == "table":
                parsed = _parse_markdown_table(u.text)
                if parsed:
                    tables.append({"page_pdf": u.page_pdf, **parsed})
            elif u.kind == "formula":
                tex = u.text.strip().strip("$").strip()
                if tex.startswith(r"\[") and tex.endswith(r"\]"):
                    tex = tex[2:-2].strip()
                formulas.append({"page_pdf": u.page_pdf, "tex": tex, "raw": u.text, "note": ""})

        # Глоссарий терминов шага
        gterms = []
        for t in terms_intro:
            hit = glossary_map.get(t.lower()) or next((v for k, v in glossary_map.items() if t.lower() in k or k in t.lower()), None)
            if hit:
                gterms.append(hit)
            else:
                gterms.append({"term": t, "definition": "Термин вводится в тексте данного шага.", "en": "—"})

        # Задачи книги + errata + решаемость
        bt_views = []
        for bt in n.get("book_tasks") or []:
            num = str(bt.get("number", ""))
            err = errata.get(f"task:{num}")
            text = err["corrected_text"] if err else bt["text"]
            bt_fixed = {**bt, "text": text}
            bt_views.append(
                {
                    **bt_fixed,
                    "errata_note": err["reason"] if err else None,
                    "dependencies": _resolve_task_dependencies(bt_fixed, units, errata),
                }
            )

        diff = n.get("difficulty") or {}
        d_prim = diff.get("primary", "И")
        d_sec = diff.get("secondary")
        diff_label = _DIFF_NAMES.get(d_prim, d_prim) + (f" · {_DIFF_NAMES.get(d_sec, d_sec)}" if d_sec else "")

        prereq_ids = [l["target"] for l in n.get("links", []) if l["type"] == "prerequisite"]
        route = routes_by_node.get(nid, {})
        layers = n.get("layers", {})
        short_title = (layers.get("topic") or {}).get("text") or n["title"]
        if len(short_title.split()) > 8:
            short_title = n["title"]

        show_volve = bool(n.get("decision_on_data")) or (not has_explicit_decision and idx == len(nodes) - 1)
        steps.append(
            {
                "id": nid,
                "order": n.get("order", idx + 1),
                "short_title": short_title,
                "book_title": n["title"],
                "difficulty_label": diff_label,
                "route_format": route.get("format", "текст + задания"),
                "threshold": bool(n.get("threshold")),
                "pages": node_pages,
                "minutes": max(4, 3 + len(claims_view) + len(bt_views)),
                "main_text": (layers.get("main") or {}).get("text", ""),
                "why_here": (n.get("why_here") or {}).get("text", ""),
                "mechanism_html": _highlight_terms((layers.get("mechanism") or {}).get("text", ""), all_terms),
                "practice": (layers.get("practice") or {}).get("text", ""),
                "nuances": (layers.get("nuances") or {}).get("text", ""),
                "deeper": layers.get("deeper") or [],
                "now_practice": (n.get("now_practice") or {}).get("text") if n.get("now_practice") else None,
                "inefficiency_lens": n.get("inefficiency_lens"),
                "worked_examples": n.get("worked_examples") or [],
                "claims": claims_view,
                "glossary_terms": gterms,
                "formulas": formulas,
                "tables": tables,
                "misconception": n.get("misconception"),
                "data_meaning": n.get("data_meaning"),
                "it_analogy": n.get("it_analogy"),
                "book_tasks": bt_views,
                "illustrations": ills_by_step.get(nid, []),
                "tasks": tasks_by_step.get(nid, []),
                "prereq_ids": prereq_ids,
                "prereq_titles": [title_by_id.get(p, p) for p in prereq_ids],
                "show_volve": show_volve,
            }
        )
        cards.append(
            {
                "node_id": nid,
                "front": short_title,
                "back": (layers.get("main") or {}).get("text", ""),
                "pages": node_pages,
            }
        )

    payload = {
        "chapter": ch.id,
        "steps": [{"id": s["id"], "short_title": s["short_title"]} for s in steps],
        "tasksById": tasks_by_id,
        "cards": cards,
        "volve": volve,
        "subtitles": {},
    }

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(ROOT / "kurs" / "templates")),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
    )
    tpl = env.get_template("course.html.j2")
    rendered = tpl.render(
        lang=ch.language or "ru",
        chapter_id=ch.id,
        chapter_title=ch.title,
        steps=steps,
        cards=cards,
        volve=volve,
        styleguide=sg,
        map_svg=_build_map_svg(steps),
        payload=payload,
    )
    (out_dir / "index.html").write_text(rendered, encoding="utf-8")
    return out_dir


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
    for fname, sname in (("illustrations.json", "illustration.schema.json"), ("tasks.json", "task.schema.json")):
        fp = ch.build_dir / fname
        if fp.exists():
            schema = json.loads((cfg.schemas_dir / sname).read_text(encoding="utf-8"))
            jsonschema.validate(json.loads(fp.read_text(encoding="utf-8")), schema)
        else:
            errors.append(f"Отсутствует {fp}")
    return errors
