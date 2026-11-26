"""Генератор учебного слоя: глава → граф узлов и покрытие → карточки → исправление дефектов по проверке.

Страницы, id, порядок, покрытие, задачи книги и связи считает код. Модель пишет тексты и выбирает цитаты,
а цитата затем привязывается к исходной единице посимвольно.
"""
from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rapidfuzz import fuzz

from .config import ROOT, Chapter, Config
from .judge import apply_edits, issue_lines, judge_card, numbered_card, patch_card, weak_quotes
from .llm import LLM, LLMError
from .pdfref import load_pdf_pages, reference_block
from .quotes import PARA_FIELDS, is_substantive, long_sentences, referenced_units, sentences, snap_quote, strict_norm
from .repair import domain_definitions, repair_card
from .units import Unit, build_units, cut_chapter, load_pages, load_units
from .verify import Issue, check_coverage, check_layer, coded_reason

PROMPTS = ROOT / "kurs" / "prompts"
DIFFICULTY = ["М", "В", "И", "Ф", "П", "Пр", "Н", "Р"]
SOURCE_KINDS = ["text", "formula", "table", "task", "figure_caption"]
NODE_MAX_CHARS = 6500

S = {"type": "string"}
B = {"type": "boolean"}
I = {"type": "integer"}


def arr(item: dict) -> dict:
    return {"type": "array", "items": item}


def obj(**props: dict) -> dict:
    return {"type": "object", "properties": props}


OUTLINE_SCHEMA = obj(
    nodes=arr(
        obj(
            slug=S,
            title=S,
            title_book=S,
            gist=S,
            why_here=S,
            primary={"type": "string", "enum": DIFFICULTY},
            secondary={"type": "string", "enum": DIFFICULTY + [""]},
            threshold=B,
            decision_on_data=B,
            prerequisites=arr(S),
            terms_introduced=arr(S),
            unit_ids=arr(S),
        )
    ),
    excluded=arr(obj(unit_id=S, reason=S)),
)

INEFF_FIELDS = ("inputs", "manual_steps", "judgment_points", "time", "typical_errors")

# Плоская форма карточки для модели: большую вложенную схему API не компилирует.
CARD_SCHEMA = obj(
    topic=S,
    main=S,
    paragraphs=arr(obj(block={"type": "string", "enum": list(PARA_FIELDS)}, text=S, unit_ids=arr(S))),
    claims=arr(
        obj(
            text=S,
            kind={"type": "string", "enum": ["observation", "inference", "assumption"]},
            unit_id=S,
            quote=S,
            source_kind={"type": "string", "enum": SOURCE_KINDS},
        )
    ),
    formulas=arr(obj(unit_id=S, meaning=S, latex_fixed=S)),
    variables=arr(obj(symbol=S, meaning=S, unit=S)),
    worked_examples=arr(obj(unit_ids=arr(S), condition=S, steps=arr(S), result=S, figure=S)),
    glossary=arr(obj(term=S, definition=S, en=S)),
    misconception_ref=S,
    misconception_text=S,
    data_from_chapter=S,
    data_breaks=S,
    data_hypotheses=arr(S),
    analogy_text=S,
    analogy_breaks_where=S,
    now_practice=S,
    inefficiency=arr(S),
    decision_features=arr(S),
    decision_rule=S,
    decision_conclusion=S,
    decision_counterexample=S,
    to_data=S,
    needs_scan=arr(obj(page=I, what=S)),
)


def from_wire(w: dict) -> dict:
    """Плоский ответ модели → карточка узла."""
    card = {k: w[k] for k in ("topic", "main", "claims", "formulas", "variables", "worked_examples", "glossary", "now_practice", "to_data", "needs_scan")}
    for f in PARA_FIELDS:
        card[f] = [{"text": p["text"], "unit_ids": list(p["unit_ids"])} for p in w["paragraphs"] if p["block"] == f]
    card["misconception"] = {"ref": w["misconception_ref"], "text": w["misconception_text"]}
    card["data_meaning"] = {"from_chapter": w["data_from_chapter"], "breaks": w["data_breaks"], "hypotheses": w["data_hypotheses"]}
    card["it_analogy"] = {"text": w["analogy_text"], "breaks_where": w["analogy_breaks_where"]}
    ineff = list(w["inefficiency"])
    present = len(ineff) >= len(INEFF_FIELDS) and any(x.strip() for x in ineff)
    card["inefficiency"] = {"present": present, **{k: (ineff[i] if present else "") for i, k in enumerate(INEFF_FIELDS)}}
    card["decision"] = {
        "present": bool(w["decision_features"]) and bool(w["decision_rule"].strip()),
        "features": w["decision_features"],
        "rule": w["decision_rule"],
        "conclusion": w["decision_conclusion"],
        "counterexample": w["decision_counterexample"],
    }
    return card


def to_wire(card: dict) -> dict:
    w = {k: card[k] for k in ("topic", "main", "claims", "formulas", "variables", "worked_examples", "glossary", "now_practice", "to_data", "needs_scan")}
    w["paragraphs"] = [{"block": f, **p} for f in PARA_FIELDS for p in card[f]]
    w["misconception_ref"], w["misconception_text"] = card["misconception"]["ref"], card["misconception"]["text"]
    dm = card["data_meaning"]
    w["data_from_chapter"], w["data_breaks"], w["data_hypotheses"] = dm["from_chapter"], dm["breaks"], dm["hypotheses"]
    w["analogy_text"], w["analogy_breaks_where"] = card["it_analogy"]["text"], card["it_analogy"]["breaks_where"]
    ineff = card["inefficiency"]
    w["inefficiency"] = [ineff[k] for k in INEFF_FIELDS] if ineff["present"] else []
    d = card["decision"]
    w["decision_features"] = d["features"] if d["present"] else []
    for k in ("rule", "conclusion", "counterexample"):
        w[f"decision_{k}"] = d[k] if d["present"] else ""
    return w


def prompt(name: str, **kw: str) -> str:
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    for k, v in kw.items():
        text = text.replace(f"@@{k.upper()}@@", str(v))
    left = re.findall(r"@@[A-Z_]+@@", text)
    if left:
        raise ValueError(f"в промпте {name} не заполнены поля: {left}")
    return text


# ---------------------------------------------------------------- доменный пакет и глава
def load_misconceptions(cfg: Config) -> list[dict]:
    p = cfg.domain_dir / "misconceptions.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def load_errata(cfg: Config, ch: Chapter) -> dict[str, str]:
    p = cfg.domain_dir / "errata" / f"{ch.id}.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {
        e["unit_id"]: e["corrected_text"]
        for e in data.get("entries", [])
        if e.get("unit_id") and e.get("corrected_text")
    }


def domain_block(cfg: Config) -> str:
    parts = ["<domain_pack>"]
    misc = load_misconceptions(cfg)
    if misc:
        parts.append("Заблуждения (id и текст):\n" + "\n".join(f"{m['id']}: {m['text']}" for m in misc))
    for name in ("it_bridges.md", "glossary.md"):
        p = cfg.domain_dir / name
        if p.exists():
            parts.append(p.read_text(encoding="utf-8").strip())
    parts.append("</domain_pack>")
    return "\n\n".join(parts)


def _furniture_key(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()[:50]


def furniture(units: list[Unit]) -> dict[str, int]:
    """Колонтитулы: короткий абзац, который почти дословно повторяется на трёх и более страницах.

    Возвращает {unit_id: число страниц}. Номера единиц не меняются, такие единицы просто не видит модель.
    """
    def short(u: Unit) -> bool:
        return u.kind == "paragraph" and not u.model_description and len(u.text.strip()) <= 200

    pages: dict[str, set[int]] = {}
    for u in units:
        if short(u):
            pages.setdefault(_furniture_key(u.text), set()).add(u.page_pdf)
    rep = {k: len(v) for k, v in pages.items() if len(v) >= 3 and len(k) >= 15}
    return {u.id: rep[_furniture_key(u.text)] for u in units if short(u) and _furniture_key(u.text) in rep}


def unit_block(units: list[Unit], errata: dict[str, str]) -> str:
    out = []
    skip = furniture(units)
    for u in units:
        if u.model_description or u.id in skip:
            continue
        pages = ",".join(map(str, u.pages or [u.page_pdf]))
        extra = f' task="{u.task_number}"' if u.task_number else ""
        out.append(f'<unit id="{u.id}" kind="{u.kind}" page="{pages}"{extra}>\n{u.text}\n</unit>')
        if u.id in errata:
            out.append(f'<errata unit="{u.id}">Исправленное чтение (для смысла, не для цитаты): {errata[u.id]}</errata>')
    return "\n".join(out)


def system_blocks(cfg: Config, ch: Chapter, units: list[Unit], errata: dict[str, str]) -> list[str]:
    lang = "английский" if (ch.language or "ru") == "en" else "русский"
    chapter = (
        f'<chapter id="{ch.id}" title="{ch.title}" language="{lang}">\n'
        "Описания рисунков, составленные при извлечении текста, убраны: это не текст автора.\n"
        f"{unit_block(units, errata)}\n</chapter>"
    )
    return [prompt("common"), domain_block(cfg), chapter]


# ---------------------------------------------------------------- проход 1: граф узлов
def content_units(units: list[Unit]) -> list[Unit]:
    skip = furniture(units)
    return [u for u in units if not u.model_description and u.id not in skip]


def check_outline(ol: dict, units: list[Unit]) -> list[str]:
    issues: list[str] = []
    by_id = {u.id: u for u in content_units(units)}
    seen: dict[str, int] = {}
    slugs: set[str] = set()
    terms: dict[str, str] = {}
    for n in ol.get("nodes", []):
        slug = n["slug"]
        if not re.fullmatch(r"[a-z0-9_]+", slug):
            issues.append(f"узел {slug}: slug только из латиницы, цифр и подчёркивания")
        if slug in slugs:
            issues.append(f"узел {slug}: slug повторяется")
        words = len(n["title"].split())
        if not 3 <= words <= 7:
            issues.append(f"узел {slug}: в заголовке {words} слов, нужно 3–7")
        for p in n["prerequisites"]:
            if p not in slugs:
                issues.append(f"узел {slug}: предпосылка {p} не стоит раньше")
        slugs.add(slug)
        size = 0
        for uid in n["unit_ids"]:
            if uid not in by_id:
                issues.append(f"узел {slug}: нет единицы {uid}")
                continue
            seen[uid] = seen.get(uid, 0) + 1
            if by_id[uid].kind != "table":
                size += len(by_id[uid].text)
        if not any(uid in by_id and is_substantive(by_id[uid]) for uid in n["unit_ids"]):
            issues.append(f"узел {slug}: нет ни одной содержательной единицы")
        if size > NODE_MAX_CHARS:
            issues.append(f"узел {slug}: {size} знаков текста, раздели на два узла")
        for t in n["terms_introduced"]:
            key = t.lower().strip()
            if key in terms:
                issues.append(f"термин «{t}» вводится дважды: {terms[key]} и {slug}")
            terms[key] = slug
    for e in ol.get("excluded", []):
        u = by_id.get(e["unit_id"])
        if u is None:
            issues.append(f"excluded: нет единицы {e['unit_id']}")
            continue
        seen[u.id] = seen.get(u.id, 0) + 1
        if is_substantive(u):
            issues.append(f"единица {u.id} содержательная, её нельзя исключить")
        if not e["reason"].strip():
            issues.append(f"единица {u.id}: нет причины исключения")
    missing = [uid for uid in by_id if uid not in seen]
    if missing:
        issues.append("не распределены единицы: " + ", ".join(missing))
    if not any(n["decision_on_data"] for n in ol.get("nodes", [])):
        issues.append("нет ни одного узла decision_on_data = true")
    return issues


def repair_outline(ol: dict, units: list[Unit]) -> list[str]:
    """Детерминированная починка того, что модель не исправила. Возвращает список сделанного."""
    log: list[str] = []
    by_id = {u.id: u for u in content_units(units)}
    used_slugs: set[str] = set()
    for n in ol["nodes"]:
        slug = re.sub(r"[^a-z0-9_]+", "_", n["slug"].lower()).strip("_") or "node"
        base, k = slug, 2
        while slug in used_slugs:
            slug, k = f"{base}_{k}", k + 1
        used_slugs.add(slug)
        n["slug"] = slug
        n["unit_ids"] = list(dict.fromkeys(x for x in n["unit_ids"] if x in by_id))
    earlier: set[str] = set()
    for n in ol["nodes"]:
        bad = [p for p in n["prerequisites"] if p not in earlier]
        if bad:
            log.append(f"{n['slug']}: убраны предпосылки {bad}")
        n["prerequisites"] = [p for p in n["prerequisites"] if p in earlier]
        earlier.add(n["slug"])
    seen_terms: set[str] = set()
    for n in ol["nodes"]:
        keep = []
        for t in n["terms_introduced"]:
            key = t.lower().strip()
            if key and key not in seen_terms:
                seen_terms.add(key)
                keep.append(t.strip())
        n["terms_introduced"] = keep
    owner: dict[str, list[dict]] = {}
    for n in ol["nodes"]:
        for uid in n["unit_ids"]:
            owner.setdefault(uid, []).append(n)
    ol["excluded"] = [
        e for e in ol["excluded"]
        if e["unit_id"] in by_id and e["unit_id"] not in owner and not is_substantive(by_id[e["unit_id"]])
        and e["reason"].strip()
    ]
    excluded = {e["unit_id"] for e in ol["excluded"]}
    last = ol["nodes"][0]
    for uid in by_id:
        if uid in owner:
            last = owner[uid][-1]
        elif uid not in excluded:
            last["unit_ids"].append(uid)
            log.append(f"{uid}: отнесена к узлу {last['slug']}")
    ol["nodes"] = [n for n in ol["nodes"] if n["unit_ids"]]
    order = {uid: i for i, uid in enumerate(by_id)}
    for n in ol["nodes"]:
        n["unit_ids"].sort(key=order.__getitem__)
    return log


def make_outline(llm: LLM, system: list[str], units: list[Unit]) -> tuple[dict, list[str]]:
    size = sum(len(u.text) for u in content_units(units) if u.kind != "table")
    lo, hi = max(5, size // 4500), max(8, size // 2400)
    user = prompt("outline", min=lo, max=hi)
    ol = llm.json("senior", system, user, OUTLINE_SCHEMA, label="outline", effort="medium")
    for attempt in (1, 2):
        issues = check_outline(ol, units)
        if not issues:
            break
        ol = llm.json(
            "senior",
            system,
            user
            + "\n\nТвой прошлый вариант:\n"
            + json.dumps(ol, ensure_ascii=False)
            + "\n\nЗамечания проверки:\n- "
            + "\n- ".join(issues)
            + "\n\nИсправь замечания и верни граф целиком, остальное не меняй.",
            OUTLINE_SCHEMA,
            label=f"outline-fix{attempt}",
            effort="medium",
        )
    return ol, repair_outline(ol, units)


# ---------------------------------------------------------------- проход 2: карточки
def node_brief(n: dict, order: int) -> str:
    return json.dumps(
        {
            "order": order,
            "title": n["title"],
            "title_book": n["title_book"],
            "gist": n["gist"],
            "primary": n["primary"],
            "secondary": n["secondary"],
            "decision_on_data": n["decision_on_data"],
            "terms_introduced": n["terms_introduced"],
        },
        ensure_ascii=False,
        indent=1,
    )


def outline_brief(ol: dict) -> str:
    return "\n".join(
        f"{i}. {n['title']} — {n['gist']}" + (f" [термины: {', '.join(n['terms_introduced'])}]" if n["terms_introduced"] else "")
        for i, n in enumerate(ol["nodes"], 1)
    )


def card_texts(card: dict) -> list[tuple[str, str]]:
    """Тексты карточки, в которых проверяется длина фраз."""
    out = [("main", card["main"])]
    for f in PARA_FIELDS:
        out += [(f"{f}[{i}]", p["text"]) for i, p in enumerate(card[f])]
    return out


def card_issues(card: dict, node: dict, units: dict[str, Unit], pages: dict[int, str]) -> list[str]:
    """Проверка карточки до сборки: то, чего нет в общей проверке слоя."""
    out: list[str] = []
    own = set(node["unit_ids"])
    for i, c in enumerate(card["claims"]):
        u = units.get(c["unit_id"])
        if u is None or u.model_description or c["unit_id"] not in own:
            out.append(f"claims[{i}]: единица {c['unit_id']} не принадлежит узлу")
        elif snap_quote(c["quote"], u, pages) is None:
            out.append(f"claims[{i}]: цитата не найдена в {c['unit_id']}: «{c['quote'][:90]}»")
    for f in PARA_FIELDS:
        for i, p in enumerate(card[f]):
            bad = [x for x in p["unit_ids"] if x not in own]
            if bad:
                out.append(f"{f}[{i}]: единицы {bad} не принадлежат узлу")
    ref = referenced_units(card)
    for uid in node["unit_ids"]:
        u = units[uid]
        if u.kind != "task" and is_substantive(u) and uid not in ref:
            out.append(f"единица {uid} ({u.kind}) не раскрыта: «{u.excerpt}»")
    want = {t.lower() for t in node["terms_introduced"]}
    have = {g["term"].lower().strip() for g in card["glossary"] if g["definition"].strip()}
    for t in sorted(want - have):
        out.append(f"glossary: нет определения термина «{t}»")
    for g in card["glossary"]:
        if any(ch.isdigit() for ch in g["definition"]):
            out.append(f"glossary: в определении «{g['term']}» не должно быть чисел")
    if node["decision_on_data"] and not card["decision"]["present"]:
        out.append("decision: узел decision_on_data = true, нужно заполнить decision_features, decision_rule, decision_conclusion")
    if not card["claims"]:
        out.append("claims: нет ни одного утверждения")
    n_main = len(sentences(card["main"]))
    if not 2 <= n_main <= 3:
        out.append(f"main: {n_main} фраз, нужно 2–3")
    for path, text in card_texts(card):
        for s in long_sentences(text):
            out.append(f"{path}: длинная фраза: «{s[:140]}»")
    return out


def is_soft(msg: str) -> bool:
    """Замечание о стиле: запроса к модели не стоит."""
    return "длинная фраза" in msg or msg.startswith("main:")


def sanitize_card(card: dict, node: dict, units: dict[str, Unit]) -> None:
    own = set(node["unit_ids"])
    for f in PARA_FIELDS:
        card[f] = [p for p in card[f] if p["text"].strip()]
        for p in card[f]:
            p["unit_ids"] = [x for x in dict.fromkeys(p["unit_ids"]) if x in own]
    card["formulas"] = [x for x in card["formulas"] if x["unit_id"] in own and units[x["unit_id"]].kind in ("formula", "paragraph")]
    for w in card["worked_examples"]:
        w["unit_ids"] = [x for x in dict.fromkeys(w["unit_ids"]) if x in own]
    card["worked_examples"] = [w for w in card["worked_examples"] if w["unit_ids"]]


# ---------------------------------------------------------------- сборка узла
def _pages_of(uids: list[str], units: dict[str, Unit]) -> list[int]:
    return sorted({p for u in uids for p in (units[u].pages or [units[u].page_pdf])})


def _paras(card_paras: list[dict], units: dict[str, Unit]) -> list[dict]:
    out = []
    for p in card_paras:
        rec = {"text": p["text"].strip(), "by_model": True, "unit_ids": p["unit_ids"], "pages": _pages_of(p["unit_ids"], units)}
        if not p["unit_ids"]:
            rec["hypothesis"] = True
        out.append(rec)
    return out


def _field(paras: list[dict]) -> dict:
    f = {"text": "\n\n".join(p["text"] for p in paras), "by_model": True, "paragraphs": paras}
    if paras and all(p.get("hypothesis") for p in paras):
        f["hypothesis"] = True
    return f


_FIG_REF_RE = re.compile(r"(?:рис(?:унк\w*)?|fig(?:ure|s)?)\.?\s*(\d+(?:[.,]\d+)*(?:\s*,\s*[а-яa-z]\b)?)", re.I)


def task_figure(text: str) -> str | None:
    nums = list(dict.fromkeys(m.group(1).strip() for m in _FIG_REF_RE.finditer(text)))
    return "рис. " + ", ".join(nums) if nums else None


def build_node(
    n: dict,
    card: dict,
    order: int,
    ch: Chapter,
    units: dict[str, Unit],
    pages: dict[int, str],
    errata: dict[str, str],
    misc_ids: set[str],
    off: int | None,
) -> tuple[dict, list[str]]:
    nid = f"{ch.prefix}.{n['slug']}"
    dropped: list[str] = []
    claims = []
    seen: set[tuple[str, str]] = set()
    for c in card["claims"]:
        u = units.get(c["unit_id"])
        hit = snap_quote(c["quote"], u, pages) if u and not u.model_description else None
        if not hit:
            dropped.append(f"{nid}: утверждение без цитаты отброшено: «{c['text'][:80]}»")
            continue
        quote, page = hit
        key = (strict_norm(quote), strict_norm(c["text"]))
        if key in seen:
            continue
        seen.add(key)
        claims.append(
            {
                "id": f"{nid}#c{len(claims) + 1}",
                "text": c["text"].strip(),
                "kind": c["kind"],
                "page_pdf": page,
                "page_print": page + off if off is not None else None,
                "quote": quote,
                "source_kind": c["source_kind"],
                "by_model": True,
                "needs_scan": bool(u.ocr_garbled),
                "unit_id": u.id,
            }
        )
    mech = _paras(card["mechanism"], units)
    node_pages = _pages_of(n["unit_ids"], units)
    links = [{"type": "prerequisite", "target": f"{ch.prefix}.{p}"} for p in n["prerequisites"]]
    ref = card["misconception"]["ref"].strip()
    if ref in misc_ids:
        links.append({"type": "refutes_misconception", "target": ref})
    else:
        ref = ""
    to_data = card["to_data"].strip()
    if re.fullmatch(r"data\.[a-z_]+", to_data):
        links.append({"type": "to_data", "target": to_data})

    book_tasks = []
    for uid in n["unit_ids"]:
        u = units[uid]
        if u.kind != "task":
            continue
        text = errata.get(uid, u.text)
        rec = {
            "number": u.task_number or uid,
            "page_pdf": u.page_pdf,
            "text": re.sub(r"^\s*\d{1,3}\.\s+", "", re.sub(r"\s+", " ", text)).strip(),
            "figure": task_figure(text),
            "unit_id": uid,
        }
        if uid in errata:
            rec["corrected"] = True
        book_tasks.append(rec)

    examples = []
    for w in card["worked_examples"]:
        examples.append(
            {
                "page_pdf": _pages_of(w["unit_ids"], units)[0],
                "condition": w["condition"].strip(),
                "steps": [s.strip() for s in w["steps"] if s.strip()],
                "result": w["result"].strip(),
                "figure": w["figure"].strip() or None,
                "unit_ids": w["unit_ids"],
            }
        )
    formulas = [
        {
            "unit_id": f["unit_id"],
            "page_pdf": units[f["unit_id"]].page_pdf,
            "latex": units[f["unit_id"]].text,
            "latex_fixed": f["latex_fixed"].strip() or None,
            "meaning": f["meaning"].strip(),
        }
        for f in card["formulas"]
    ]
    ineff = card["inefficiency"]
    dec = card["decision"]
    misc = card["misconception"]["text"].strip()
    node = {
        "id": nid,
        "order": order,
        "title": n["title"].strip(),
        "title_book": n["title_book"].strip(),
        "why_here": {"text": n["why_here"].strip(), "by_model": True},
        "difficulty": {"primary": n["primary"], "secondary": n["secondary"] or None},
        "threshold": bool(n["threshold"]),
        "pages": node_pages,
        "unit_ids": n["unit_ids"],
        "layers": {
            "topic": {"text": card["topic"].strip(), "by_model": True},
            "main": {"text": card["main"].strip(), "by_model": True, "pages": node_pages},
            "mechanism": _field(mech),
            "practice": _field(_paras(card["practice"], units)),
            "nuances": _field(_paras(card["nuances"], units)),
            "deeper": _paras(card["deeper"], units),
        },
        "steps": _paras(card["steps"], units),
        "conditions": _paras(card["conditions"], units),
        "claims": claims,
        "formulas": formulas,
        "variables": card["variables"] if formulas else [],
        "links": links,
        "misconception": {"ref": ref or None, "text": misc, "by_model": True} if misc else None,
        "data_meaning": {
            "from_chapter": {"text": card["data_meaning"]["from_chapter"].strip(), "by_model": True},
            "breaks": {"text": card["data_meaning"]["breaks"].strip(), "by_model": True},
            "hypotheses": [
                {"text": h.strip(), "by_model": True, "hypothesis": True}
                for h in card["data_meaning"]["hypotheses"] if h.strip()
            ],
        },
        "it_analogy": {**{k: v.strip() for k, v in card["it_analogy"].items()}, "by_model": True},
        "now_practice": {"text": card["now_practice"].strip(), "by_model": True, "hypothesis": True}
        if card["now_practice"].strip() else None,
        "inefficiency_lens": {k: ineff[k].strip() for k in INEFF_FIELDS}
        | {"by_model": True} if ineff["present"] else None,
        "glossary": [
            {"term": g["term"].strip(), "definition": g["definition"].strip(), "en": g["en"].strip()}
            for g in card["glossary"] if g["term"].strip() and g["definition"].strip()
        ],
        "terms_introduced": [],
        "terms_used": [],
        "needs_scan": [x for x in card["needs_scan"] if x["what"].strip() and x["page"] in node_pages],
        "decision_on_data": {
            "features": dec["features"], "rule": dec["rule"], "conclusion": dec["conclusion"],
            "counterexample": dec["counterexample"],
        } if dec["present"] and dec["features"] and dec["rule"].strip() else None,
        "worked_examples": examples,
        "book_tasks": book_tasks,
    }
    return node, dropped


# ---------------------------------------------------------------- термины: вводятся в самом раннем узле
def _stem(word: str) -> str:
    w = word.lower().replace("ё", "е")
    return w[: max(4, len(w) - 2)] if len(w) > 5 else w[: max(3, len(w) - 1)]


def term_regex(term: str) -> re.Pattern[str]:
    words = re.findall(r"[\w]+", term)
    if len(words) == 1 and (term.isupper() or len(term) <= 3):
        return re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")
    parts = [rf"{re.escape(_stem(w))}\w*" for w in words]
    return re.compile(r"(?<!\w)" + r"[\s\-–]+".join(parts), re.I)


def node_text(n: dict) -> str:
    parts = [n["title"], n["why_here"]["text"]]
    for k, v in n["layers"].items():
        parts += [x["text"] for x in v] if isinstance(v, list) else [v["text"]]
    for f in ("steps", "conditions"):
        parts += [p["text"] for p in n.get(f, [])]
    parts += [c["text"] for c in n["claims"]]
    for w in n["worked_examples"]:
        parts += [w["condition"], *w["steps"], w["result"]]
    dec = n.get("decision_on_data")
    if dec:
        parts += [*dec["features"], dec["rule"], dec["conclusion"]]
    if n.get("misconception"):
        parts.append(n["misconception"]["text"])
    dm = n["data_meaning"]
    parts += [dm["from_chapter"]["text"], dm["breaks"]["text"]]
    return "\n".join(p.replace("ё", "е") for p in parts if p)


def place_terms(nodes: list[dict]) -> None:
    """Термин и его определение переносятся в самый ранний узел, где термин встречается."""
    entries: dict[str, dict] = {}
    home: dict[str, int] = {}
    for i, n in enumerate(nodes):
        for g in n["glossary"]:
            key = g["term"].lower().replace("ё", "е")
            if key not in entries:
                entries[key], home[key] = g, i
        n["glossary"], n["terms_introduced"], n["terms_used"] = [], [], []
    texts = [node_text(n) for n in nodes]
    for key, g in entries.items():
        rx = term_regex(key)
        hits = [i for i, t in enumerate(texts) if rx.search(t)]
        first = min([home[key], *hits])
        nodes[first]["glossary"].append(g)
        nodes[first]["terms_introduced"].append(g["term"])
        for i in hits:
            if i > first:
                nodes[i]["terms_used"].append(g["term"])


# ---------------------------------------------------------------- покрытие
def build_coverage(ch: Chapter, ol: dict, units: list[Unit]) -> dict:
    owners: dict[str, list[str]] = {}
    for n in ol["nodes"]:
        for uid in n["unit_ids"]:
            owners.setdefault(uid, []).append(f"{ch.prefix}.{n['slug']}")
    reasons = {e["unit_id"]: e["reason"].strip() for e in ol["excluded"]}
    captions = [u for u in units if u.kind == "figure" and not u.model_description]
    skip = furniture(units)
    rows = []
    for u in units:
        node_ids = owners.get(u.id, [])
        reason = None
        if u.id in skip:
            node_ids = []
            reason = coded_reason(f"page_furniture: колонтитул, одна и та же строка на {skip[u.id]} страницах", u.kind)
        elif u.model_description:
            node_ids = []
            near = sorted(captions, key=lambda c: (abs(c.page_pdf - u.page_pdf), abs(c.line - u.line)))
            ref = f"; рисунок учтён по подписи {near[0].id}" if near and abs(near[0].page_pdf - u.page_pdf) <= 1 else ""
            reason = f"model_description: описание рисунка составлено при извлечении md, это не текст автора{ref}"
        elif not node_ids:
            reason = coded_reason(reasons.get(u.id) or "page_furniture: служебный текст без мысли автора", u.kind)
        rows.append(
            {
                "unit_id": u.id,
                "kind": u.kind,
                "page_pdf": u.page_pdf,
                "excerpt": u.excerpt,
                "node_ids": node_ids,
                "reason": reason,
            }
        )
    return {"chapter": ch.id, "rows": rows}


# ---------------------------------------------------------------- запись с защитой ручных правок
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dump(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


class ManualEdits(RuntimeError):
    pass


def write_generated(ch: Chapter, name: str, doc: dict, force: bool = False) -> Path:
    """Пишет файл слоя. Файл, который правил человек, без force не перезаписывается."""
    path = ch.build_dir / name
    state_path = ch.build_dir / ".generated.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    text = dump(doc)
    if path.exists() and not force:
        cur = path.read_text(encoding="utf-8")
        if name in state and _sha(cur) != state[name] and cur != text:
            raise ManualEdits(f"{path} изменён вручную; чтобы перезаписать, добавьте --force")
    path.write_text(text, encoding="utf-8")
    state[name] = _sha(text)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


# ---------------------------------------------------------------- оркестровка
# Смысловые предупреждения, которые правка карточки закрывает одним словом автора:
# вернуть «могут», «часто», условие или отрицание. Правка, от которой стало хуже, откатывается.
FIXABLE_WARN = {
    "assumption_as_fact:": "верните в текст утверждения оговорку автора",
    "condition_dropped:": "верните в текст утверждения условие автора",
    "negation_dropped:": "верните отрицание автора",
    "negation_added:": "уберите отрицание, которого у автора нет",
}


def _node_issue_map(layer: dict, issues: list[Issue]) -> dict[str, list[str]]:
    ids = [n["id"] for n in layer["nodes"]]
    claim_text = {c["id"]: c["text"] for n in layer["nodes"] for c in n["claims"]}
    out: dict[str, list[str]] = {}
    for it in issues:
        hint = next((h for code, h in FIXABLE_WARN.items() if it.msg.startswith(code)), None)
        if it.level != "error" and hint is None:
            continue
        nid = next((x for x in ids if it.where == x or it.where.startswith((x + "/", x + "#"))), None)
        if nid is None:
            m = re.match(r"schema:nodes/(\d+)", it.where)
            if m and int(m.group(1)) < len(ids):
                nid = ids[int(m.group(1))]
        if nid:
            # Номер утверждения в карточке не виден, поэтому показываем его текст.
            cid = re.search(r"\S+#c\d+", it.where)
            where = f"утверждение «{claim_text.get(cid.group(0), '')[:100]}»" if cid else it.where.replace(nid, "").strip("/ ") or "узел"
            out.setdefault(nid, []).append(f"{where}: {it.msg}" + (f" — {hint}" if hint else ""))
    return out


def generate_layer(cfg: Config, ch: Chapter, llm: LLM, *, rounds: int = 2, judge: bool = True, force: bool = False, log=print) -> dict:
    """Строит layer.json и coverage.json. Возвращает сводку."""
    cut_chapter(cfg, ch)
    units = build_units(cfg, ch)
    pages = load_pages(ch)
    by_id = {u.id: u for u in units}
    errata = load_errata(cfg, ch)
    system = system_blocks(cfg, ch, units, errata)
    misc_ids = {m["id"] for m in load_misconceptions(cfg)}

    ol, fixed = make_outline(llm, system, units)
    for line in fixed:
        log(f"  граф: {line}")
    (ch.build_dir / "outline.json").write_text(dump(ol), encoding="utf-8")
    coverage = build_coverage(ch, ol, units)
    log(f"{ch.id}: узлов {len(ol['nodes'])}, единиц {len(units)}, исключено {len(ol['excluded'])}")

    brief = outline_brief(ol)
    pdf = load_pdf_pages(cfg, ch, pages)
    log(f"  текст PDF для сверки индексов: страниц {len(pdf)} из {len(pages)}")

    def node_units(n: dict) -> str:
        return unit_block([by_id[u] for u in n["unit_ids"]], errata)

    def node_pdf(n: dict) -> str:
        return reference_block(pdf, _pages_of(n["unit_ids"], by_id))

    defs = domain_definitions(cfg.domain_dir / "glossary.md")
    repaired: dict[int, int] = {}

    def tidy(card: dict, i: int, final: bool = False) -> list[str]:
        """Бесплатная починка кодом после каждого ответа модели."""
        n = ol["nodes"][i]
        sanitize_card(card, n, by_id)
        done = repair_card(card, n, by_id, pages, defs, final=final)
        repaired[i] = repaired.get(i, 0) + len(done)
        return done

    def make_card(i: int) -> dict:
        n = ol["nodes"][i]
        card = from_wire(llm.json(
            "fast",
            system,
            prompt("card", node=node_brief(n, i + 1), outline=brief, units=node_units(n), pdf=node_pdf(n)),
            CARD_SCHEMA,
            label=f"card:{n['slug']}",
        ))
        tidy(card, i)
        return card

    def patch(job: tuple[int, dict, list[str], str]) -> dict:
        """Модель правит только указанные места; глава целиком ей не нужна."""
        i, card, issues, label = job
        n = ol["nodes"][i]
        edits = patch_card(
            llm, "fast", system[:2],
            prompt("patch", node=node_brief(n, i + 1), units=node_units(n), card=numbered_card(to_wire(card)), issues="- " + "\n- ".join(issues)),
            label=f"{label}:{n['slug']}",
        )
        wire, done, _ = apply_edits(to_wire(card), edits, set(n["unit_ids"]), by_id)
        if not done:
            return card
        new = from_wire(wire)
        tidy(new, i)
        return new

    # Первый запрос прогревает кэш общей части, остальные идут параллельно.
    cards: list[dict] = [make_card(0)]
    workers = int(cfg.llm.get("workers", 4))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        cards += list(pool.map(make_card, range(1, len(ol["nodes"]))))

    def assemble() -> tuple[dict, dict[int, list[str]], list[str]]:
        nodes, dropped = [], []
        for i, n in enumerate(ol["nodes"]):
            node, drop = build_node(n, cards[i], i + 1, ch, by_id, pages, errata, misc_ids, ch.page_print_offset)
            nodes.append(node)
            dropped += drop
        place_terms(nodes)
        all_pages = sorted({u.page_pdf for u in units})
        layer = {
            "chapter": ch.id,
            "source": f"{ch.id}.md",
            "pdf_pages": [f"{all_pages[0]}–{all_pages[-1]}"],
            "generated_by": llm.describe(),
            "nodes": nodes,
        }
        issues = check_layer(cfg, ch, layer, coverage=coverage)
        per_node = _node_issue_map(layer, issues)
        todo: dict[int, list[str]] = {}
        for i, n in enumerate(ol["nodes"]):
            msgs = card_issues(cards[i], n, by_id, pages) + per_node.get(f"{ch.prefix}.{n['slug']}", [])
            if msgs:
                todo[i] = msgs
        return layer, todo, dropped

    missed: list[str] = []

    def hard(i: int) -> list[str]:
        return [m for m in todo.get(i, []) if not is_soft(m)]

    def try_patch(job: tuple[int, dict, list[str], str]) -> dict:
        # Нет денег, связи или лимита — карточка остаётся прежней, а пропуск попадает в отчёт.
        try:
            return patch(job)
        except LLMError as e:
            missed.append(f"правка {ol['nodes'][job[0]]['slug']}: {str(e)[:160]}")
            return job[1]

    def run_patches(jobs: list[tuple[int, dict, list[str], str]]) -> None:
        """Правит карточки; правка, от которой замечаний стало больше, отменяется."""
        nonlocal layer, todo, dropped
        held, counts = list(cards), {i: len(hard(i)) for i, *_ in jobs}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for (i, *_), card in zip(jobs, pool.map(try_patch, jobs)):
                cards[i] = card
        layer, todo, dropped = assemble()
        worse = [i for i, *_ in jobs if len(hard(i)) > counts[i]]
        for i in worse:
            cards[i] = held[i]
        if worse:
            layer, todo, dropped = assemble()

    layer, todo, dropped = assemble()
    log(f"  код починил мест: {sum(repaired.values())}, осталось узлов с замечаниями: {sum(1 for i in todo if hard(i))}")
    asked: set[tuple[int, str]] = set()
    for rnd in range(1, rounds + 1):
        jobs = []
        for i in sorted(todo):
            msgs = [m for m in hard(i) if rnd == 1 or (i, m) not in asked]
            asked.update((i, m) for m in msgs)
            if msgs:
                jobs.append((i, cards[i], msgs, f"patch{rnd}"))
        if not jobs:
            break
        log(f"  правка {rnd}: узлов {len(jobs)}, замечаний {sum(len(j[2]) for j in jobs)}")
        run_patches(jobs)

    # ---- смысловая проверка: судья даёт правки, код их вносит, затем снова машинная проверка
    role = str(cfg.llm.get("judge", "senior"))
    judge_rounds = int(cfg.llm.get("judge_rounds", 2)) if judge and role != "off" else 0
    efforts = cfg.llm.get("judge_effort") or ["medium", "low"]
    efforts = [efforts] if isinstance(efforts, str) else list(efforts)
    judge_system = system[:2]
    doubts: dict[int, list[dict]] = {}
    judge_log: list[dict] = []
    unresolved: dict[int, list[str]] = {}

    def judge_one(args: tuple[int, int]) -> dict:
        try:
            return ask_judge(args)
        except LLMError as e:
            missed.append(f"судья {args[1]} {ol['nodes'][args[0]]['slug']}: {str(e)[:160]}")
            return {"issues": [], "source_doubts": []}

    def ask_judge(args: tuple[int, int]) -> dict:
        i, rnd = args
        n = ol["nodes"][i]
        return judge_card(
            llm, role, judge_system,
            prompt(
                "judge", node=node_brief(n, i + 1), units=node_units(n),
                pdf=node_pdf(n), card=numbered_card(to_wire(cards[i])),
            ),
            label=f"judge{rnd}:{n['slug']}",
            effort=efforts[min(rnd, len(efforts)) - 1],
        )

    queue = list(range(len(cards)))
    for rnd in range(1, judge_rounds + 1):
        if not queue:
            break
        # Первый запрос прогревает кэш общей части.
        verdicts = [judge_one((queue[0], rnd))]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            verdicts += list(pool.map(judge_one, [(i, rnd) for i in queue[1:]]))
        prev, before = list(cards), {i: len(hard(i)) for i in queue}
        touched: list[int] = []
        n_err = n_min = n_skip = 0
        for i, v in zip(queue, verdicts):
            n = ol["nodes"][i]
            doubts.setdefault(i, []).extend(v["source_doubts"])
            wire, done, skipped = apply_edits(to_wire(cards[i]), v["issues"], set(n["unit_ids"]), by_id)
            judge_log += [{"round": rnd, "node": n["slug"], "applied": x in done, **x} for x in v["issues"]]
            n_err += sum(x["severity"] == "error" for x in v["issues"])
            n_min += sum(x["severity"] == "minor" for x in v["issues"])
            n_skip += len(skipped)
            unresolved[i] = issue_lines([x for x in skipped if x["severity"] == "error"])
            if done:
                cards[i] = from_wire(wire)
                tidy(cards[i], i)
            if any(x["severity"] == "error" for x in done):
                touched.append(i)
        log(f"  судья {rnd}: узлов {len(queue)}, ошибок {n_err}, мелочей {n_min}, правки не подошли {n_skip}")
        layer, todo, dropped = assemble()
        # Остаток — то, что не смогли ни код, ни готовые правки судьи: одна правка моделью.
        jobs = []
        for i in queue:
            msgs = hard(i) + weak_quotes(cards[i], by_id) + unresolved[i]
            if msgs:
                jobs.append((i, cards[i], msgs, f"after{rnd}"))
        if jobs:
            log(f"    после правок: узлов {len(jobs)}, замечаний {sum(len(j[2]) for j in jobs)}")
            held = list(cards)
            run_patches(jobs)
            for i, *_ in jobs:
                if cards[i] is not held[i]:
                    unresolved[i] = []
                    if i not in touched:
                        touched.append(i)
        # Правка судьи не должна ломать машинную проверку: из узла убираются только те правки, что её ломают.
        back = [i for i in queue if len(hard(i)) > before[i]]
        verdict_of = dict(zip(queue, verdicts))
        for i in back:
            n, own, kept = ol["nodes"][i], set(ol["nodes"][i]["unit_ids"]), []
            for e in verdict_of[i]["issues"]:
                wire, done, _ = apply_edits(to_wire(prev[i]), [*kept, e], own, by_id)
                if len(done) <= len(kept):
                    continue
                cards[i] = from_wire(wire)
                tidy(cards[i], i)
                if len([m for m in assemble()[1].get(i, []) if not is_soft(m)]) <= before[i]:
                    kept.append(e)
            cards[i] = from_wire(apply_edits(to_wire(prev[i]), kept, own, by_id)[0])
            tidy(cards[i], i)
            lost = [e for e in verdict_of[i]["issues"] if e not in kept and e["severity"] == "error"]
            unresolved[i] = issue_lines(lost)
            log(f"    {n['slug']}: правок принято {len(kept)}, отброшено {len(verdict_of[i]['issues']) - len(kept)}")
            for rec in judge_log:
                if rec["round"] == rnd and rec["node"] == n["slug"]:
                    rec["applied"] = any(all(rec[k] == e[k] for k in ("path", "problem")) for e in kept)
        if back:
            layer, todo, dropped = assemble()
        queue = sorted(touched)

    # Что осталось и после модели, закрывает код: чужое число уходит, непокрытая единица встаёт словами автора.
    last = [line for i in sorted(todo) if hard(i) for line in tidy(cards[i], i, final=True)]
    if last:
        log(f"  закрыто кодом напоследок: {len(last)}")
        layer, todo, dropped = assemble()

    for i, ds in doubts.items():
        node = layer["nodes"][i]
        for d in ds:
            what = d["what"].strip()
            same = [x for x in node["needs_scan"] if x["page"] == d["page"] and fuzz.token_set_ratio(x["what"], what) >= 60]
            if what and d["page"] in node["pages"] and not same:
                node["needs_scan"].append({"page": d["page"], "what": what})

    cards_doc = {n["slug"]: to_wire(c) for n, c in zip(ol["nodes"], cards)}
    (ch.build_dir / "cards.json").write_text(dump(cards_doc), encoding="utf-8")
    if missed:
        log(f"  ВНИМАНИЕ: не выполнено запросов {len(missed)}, слой собран без них; первый: {missed[0]}")
    write_generated(ch, "layer.json", layer, force)
    write_generated(ch, "coverage.json", coverage, force)
    final = check_layer(cfg, ch, layer, coverage=coverage) + check_coverage(cfg, ch, coverage, layer)
    report = {
        "chapter": ch.id,
        "nodes": len(layer["nodes"]),
        "claims": sum(len(n["claims"]) for n in layer["nodes"]),
        "units": len(units),
        "excluded": [e for e in ol["excluded"]],
        "dropped_claims": dropped,
        "open_issues": {layer["nodes"][i]["id"]: m for i, m in sorted(todo.items())},
        "judge": {
            "rounds": judge_rounds,
            "pdf_pages": len(pdf),
            "log": judge_log,
            "unresolved": {layer["nodes"][i]["id"]: m for i, m in sorted(unresolved.items()) if m},
            "weak_quotes": {layer["nodes"][i]["id"]: w for i, c in enumerate(cards) if (w := weak_quotes(c, by_id))},
        },
        "repaired_by_code": sum(repaired.values()),
        "check_errors": [str(x) for x in final if x.level == "error"],
        "missed_calls": missed,
        "cost_usd": round(llm.spent, 3),
        "calls": llm.calls,
        "cache_hits": llm.cache_hits,
    }
    (ch.build_dir / "gen_report.json").write_text(dump(report), encoding="utf-8")
    return report


def load_layer(ch: Chapter) -> dict:
    return json.loads((ch.build_dir / "layer.json").read_text(encoding="utf-8"))


def load_chapter_units(cfg: Config, ch: Chapter) -> list[Unit]:
    if not (ch.build_dir / "units.json").exists():
        build_units(cfg, ch)
    return load_units(ch)
