"""Дополнение готового слоя недорогими запросами: заблуждение, «как это делают сейчас», линза неэффективности,
единицы, отданные узлу, но не сказанные в нём.

    python -m kurs enrich <глава> --max-usd 0.1

Карточки не переделываются: пустые поля заполняются, остальное не меняется. Каждое дополнение проверяет код (числа есть
в исходных единицах, цитата дословно на своей странице, в «сейчас» чисел нет), затем судья; узел, в котором после
дополнения появилась ошибка проверки, возвращается к прежнему виду.
"""
from __future__ import annotations

import copy
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import Chapter, Config
from .layer_gen import INEFF_FIELDS, S, arr, load_chapter_units, load_layer, load_misconceptions, obj, prompt, write_generated
from .llm import LLM, LLMError
from .verify import Source, check_coverage, check_layer, check_quantities_against_source, norm, taught_units

PLACES = ["mechanism", "practice", "nuances", "deeper"]
KINDS = ["observation", "inference", "assumption"]
_SCHEMA = obj(items=arr(obj(
    node=S, misconception_ref=S, misconception_text=S, now_practice=S, inefficiency=arr(S),
    taught=arr(obj(unit_id=S, place={"type": "string", "enum": PLACES}, paragraph=S, claim=S, claim_kind={"type": "string", "enum": KINDS}, quote=S)),
)))
_JUDGE = obj(rulings=arr(obj(node=S, field=S, why=S, keep={"type": "boolean"}, fix=S)))
_SOURCE_KIND = {"paragraph": "text", "heading": "text", "worked_example": "text", "formula": "formula", "table": "table", "task": "task", "figure": "figure_caption"}
_NOW = re.compile(r"сейчас|сегодня|теперь|ныне|в наши дни|современн|today|nowadays|currently|modern", re.I)
_OUTDATED = re.compile(r"устар|историч|вытесн|соврем|ныне|цифров", re.I)


def outdated_notes(cfg: Config, ch: Chapter, limit: int = 4500) -> str:
    """Строки справочного разбора книги про устаревшее. Разбора нет (тестовая глава) — пустая строка."""
    d = cfg.input_dir / "analysis books"
    book = Path(ch.book).name if getattr(ch, "book", None) else ""
    key = re.sub(r"^(train|ctrl)_", "", book).split("_")[0][:6].lower()
    if not d.exists() or not key:
        return ""
    names = {"latysh": "латыш", "basnie": "басни", "kuznet": "кузне", "vendel": "венде", "barenb": "барен", "barabo": "бараб"}
    stem = names.get(key, key)
    for p in sorted(d.glob("*.md")):
        if p.name.lower().startswith(stem):
            lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if _OUTDATED.search(ln) and len(ln) < 600]
            text = "\n".join(dict.fromkeys(lines))[:limit]
            return f"Справка: что в этой книге устарело (вторичный разбор, для now_practice):\n{text}\n" if text else ""
    return ""


def untaught(layer: dict, cov: dict, units: dict) -> dict[str, list[str]]:
    taught = {n["id"]: taught_units(n) for n in layer["nodes"]}
    out: dict[str, list[str]] = {}
    for r in cov["rows"]:
        u = units.get(r["unit_id"])
        owners = [nid for nid in r.get("node_ids", []) if nid in taught]
        if u and owners and u.kind != "heading" and not u.model_description and not any(u.id in taught[nid] for nid in owners):
            out.setdefault(owners[0], []).append(u.id)
    return out


_LINK = re.compile(r"^(?:или|откуда|отсюда|тогда|следовательно|таким образом|т\.\s*е\.|то есть|где|получим|имеем|or|whence|hence|then|thus|where)\b", re.I)


def scrap_reason(text: str, repeats: int = 1) -> str | None:
    """Обрывок без мысли автора: буква панели, связка между формулами, повтор подписи внутри рисунка, заголовок пункта."""
    body = re.sub(r"\s+", " ", re.sub(r"^\d{1,4}\s*\n", "", text.strip())).strip()
    words_ = re.findall(r"[A-Za-zА-Яа-яЁё]{2,}", re.sub(r"\$[^$]*\$|\\\(.*?\\\)", " ", body))
    if len(body) <= 3 or not words_:
        return "page_furniture: служебный обрывок — буква панели рисунка или номер страницы"
    if repeats >= 2 and len(words_) <= 4:
        return "page_furniture: служебная надпись внутри рисунка, повторяется на его панелях"
    if len(words_) <= 5 and _LINK.match(body) and not re.search(r"[–—=]", body):
        return "page_furniture: служебная связка между формулами"
    if len(words_) <= 7 and body.endswith(":") and re.search(r"следующ\w*\W*$|as follows\W*$|имеют вид\W*$", body, re.I):
        return "page_furniture: служебная связка перед формулами"
    verb = re.search(r"(?:ется|ются|ится|ятся|ёт|ают|яют|ает|яет|ует|уют|ят|ит|ет|ют|был\w*|есть|is|are|was|were)(?![\w])", body)
    if 5 <= len(words_) <= 8 and body.endswith(".") and not verb and not re.search(r"\d|[–—;]", body) and body[:1].isupper():
        return "heading_only: заголовок пункта или таблицы, мысль раскрыта в следующих единицах"
    return None


def release_scraps(layer: dict, cov: dict, units: dict) -> int:
    """Обрывки, отданные узлам, но не несущие мысли, исключаются с причиной из закрытого списка."""
    lost = {u for us in untaught(layer, cov, units).values() for u in us}
    seen: dict[str, int] = {}
    for u in units.values():
        seen[norm(u.text)] = seen.get(norm(u.text), 0) + 1
    done = set()
    for r in cov["rows"]:
        if r["unit_id"] in lost:
            why = scrap_reason(units[r["unit_id"]].text, seen[norm(units[r["unit_id"]].text)])
            if why:
                r["node_ids"], r["reason"] = [], why
                done.add(r["unit_id"])
    for n in layer["nodes"]:
        n["unit_ids"] = [u for u in n["unit_ids"] if u not in done]
    return len(done)


def _skip(n: dict) -> list[str]:
    return [k for k, f in (("misconception", "misconception"), ("now_practice", "now_practice"), ("inefficiency", "inefficiency_lens")) if n.get(f)]


def node_view(n: dict, lost: list[str], units: dict) -> dict:
    lay = n["layers"]
    v = {
        "id": n["id"], "title": n["title"], "main": lay["main"]["text"], "mechanism": lay["mechanism"]["text"],
        "practice": (lay.get("practice") or {}).get("text", ""), "nuances": (lay.get("nuances") or {}).get("text", ""),
        "conditions": [c["text"] for c in n.get("conditions", [])], "skip": _skip(n),
    }
    if lost:
        v["untaught"] = [{"unit_id": u, "kind": units[u].kind, "page": units[u].page_pdf, "text": units[u].text[:1800]} for u in lost]
    return v


def _numbers_ok(text: str, n: dict, units: dict, extra: list[str] = ()) -> bool:
    src = [units[u].text for u in list(n["unit_ids"]) + list(extra) if u in units]
    return not check_quantities_against_source(text, src) if src else not re.search(r"\d", text)


def proposals(it: dict, n: dict, lost: list[str], units: dict, src: Source, misc_ids: set[str]) -> dict[str, dict]:
    """Дополнения, прошедшие проверку кодом: поле → значение."""
    out: dict[str, dict] = {}
    skip = _skip(n)
    text = it["misconception_text"].strip()
    if text and "misconception" not in skip and 25 <= len(text) <= 600 and _numbers_ok(text, n, units):
        ref = it["misconception_ref"].strip()
        out["misconception"] = {"ref": ref if ref in misc_ids else None, "text": text, "by_model": True}
    text = it["now_practice"].strip()
    if text and "now_practice" not in skip and 25 <= len(text) <= 500 and _NOW.search(text) and not re.search(r"\d", text) and not re.search(r"\b(всегда|никогда)\b", text, re.I):
        out["now_practice"] = {"text": text, "by_model": True, "hypothesis": True}
    ineff = [x.strip() for x in it["inefficiency"]]
    if len(ineff) == len(INEFF_FIELDS) and all(ineff) and "inefficiency" not in skip and _numbers_ok(" ".join(ineff), n, units):
        out["inefficiency"] = {**dict(zip(INEFF_FIELDS, ineff)), "by_model": True}
    for t in it["taught"]:
        u = units.get(t["unit_id"])
        if not u or u.id not in lost or not t["paragraph"].strip() or not t["claim"].strip() or len(t["quote"].split()) < 3:
            continue
        if norm(t["quote"]) not in norm(u.text) or not src.find_quote(t["quote"], u.page_pdf)[0]:
            continue
        if check_quantities_against_source(t["paragraph"], [u.text]) or check_quantities_against_source(t["claim"], [u.text]):
            continue
        out[f"taught:{u.id}"] = {**t, "unit": u}
    return out


def _as_text(field: str, v: dict) -> str:
    if field.startswith("taught:"):
        return f"{v['paragraph']} | утверждение: {v['claim']}"
    if field == "inefficiency":
        return " | ".join(f"{k}: {v[k]}" for k in INEFF_FIELDS)
    return v["text"]


def apply(n: dict, props: dict[str, dict], off: int | None) -> None:
    for field, v in props.items():
        if field == "misconception":
            n["misconception"] = v
            if v["ref"] and not any(ln["type"] == "refutes_misconception" and ln["target"] == v["ref"] for ln in n["links"]):
                n["links"].append({"type": "refutes_misconception", "target": v["ref"]})
        elif field == "now_practice":
            n["now_practice"] = v
        elif field == "inefficiency":
            n["inefficiency_lens"] = v
        else:
            u = v["unit"]
            rec = {"text": v["paragraph"].strip(), "by_model": True, "unit_ids": [u.id], "pages": [u.page_pdf]}
            if v["place"] == "deeper":
                n["layers"]["deeper"].append(rec)
            else:
                f = n["layers"][v["place"]]
                f.setdefault("paragraphs", []).append(rec)
                f["text"] = "\n\n".join(p["text"] for p in f["paragraphs"])
                f.pop("hypothesis", None)
            nums = [int(m.group(1)) for c in n["claims"] if (m := re.search(r"#c(\d+)$", c["id"]))]
            n["claims"].append({
                "id": f"{n['id']}#c{max(nums, default=0) + 1}", "text": v["claim"].strip(), "kind": v["claim_kind"], "page_pdf": u.page_pdf,
                "page_print": u.page_pdf + off if off is not None else None, "quote": v["quote"].strip(), "source_kind": _SOURCE_KIND.get(u.kind, "text"),
                "by_model": True, "needs_scan": False, "unit_id": u.id,
            })
            if u.page_pdf not in n["pages"]:
                n["pages"] = sorted(n["pages"] + [u.page_pdf])


def enrich_layer(cfg: Config, ch: Chapter, llm: LLM, *, force: bool = False, size: int = 5, log=print) -> dict:
    layer = load_layer(ch)
    cov = json.loads((ch.build_dir / "coverage.json").read_text(encoding="utf-8"))
    units = {u.id: u for u in load_chapter_units(cfg, ch)}
    src = Source(ch)
    misc = load_misconceptions(cfg)
    misc_ids = {m["id"] for m in misc}
    for n in layer["nodes"]:  # «как сейчас» без слова о нынешнем — пересказ книги, в этом поле ему не место
        np_ = n.get("now_practice")
        if np_ and np_.get("by_model") and not _NOW.search(np_["text"]):
            n["now_practice"] = None
    lost = untaught(layer, cov, units)
    total_lost = sum(map(len, lost.values()))
    freed = release_scraps(layer, cov, units)
    lost = untaught(layer, cov, units)
    todo = [n for n in layer["nodes"] if lost.get(n["id"]) or len(_skip(n)) < 3]
    rep = {"nodes": len(todo), "untaught_before": total_lost, "freed": freed, "added": {}, "dropped": 0, "reverted": []}
    if not todo and not freed:
        return rep
    system = [prompt("common")]
    head = dict(outdated=outdated_notes(cfg, ch), misconceptions="\n".join(f"{m['id']}: {m['text']}" for m in misc))
    groups = [todo[k : k + size] for k in range(0, len(todo), size)]

    def ask(g: list[dict]) -> list[dict]:
        view = [node_view(n, lost.get(n["id"], []), units) for n in g]
        data = llm.json("fast", system, prompt("enrich", nodes=json.dumps(view, ensure_ascii=False), **head), _SCHEMA, label=f"enrich:{g[0]['id'].split('.')[-1]}",
                        max_tokens=24000)
        return data["items"]

    by_id = {n["id"]: n for n in layer["nodes"]}

    def work(g: list[dict]) -> dict[str, dict[str, dict]]:
        props: dict[str, dict[str, dict]] = {}
        for it in ask(g):
            n = by_id.get(it["node"])
            if n is not None and n in g:
                props[n["id"]] = proposals(it, n, lost.get(n["id"], []), units, src, misc_ids)
        items = [{"node": node_view(by_id[nid], [f.split(":", 1)[1] for f in p if f.startswith("taught:")], units),
                  "additions": {f: _as_text(f, v) for f, v in p.items()}} for nid, p in props.items() if p]
        if not items:
            return props
        data = llm.json("senior", system, prompt("enrich_judge", items=json.dumps(items, ensure_ascii=False)), _JUDGE, label=f"enrich-judge:{g[0]['id'].split('.')[-1]}",
                        max_tokens=16000)
        seen: dict[tuple[str, str], dict] = {(r["node"], r["field"]): r for r in data["rulings"]}
        for nid, p in props.items():
            for f in list(p):
                r = seen.get((nid, f))
                if r is None or r["keep"]:
                    continue
                fix = r["fix"].strip()
                n = by_id[nid]
                if fix and f in ("misconception", "now_practice") and _numbers_ok(fix, n, units) and len(fix) <= 600 and not (f == "now_practice" and (re.search(r"\d", fix) or not _NOW.search(fix))):
                    p[f] = {**p[f], "text": fix}
                else:
                    del p[f]
                    rep["dropped"] += 1
        return props

    merged: dict[str, dict[str, dict]] = {}

    def safe(g: list[dict]) -> dict:
        try:
            return work(g)
        except LLMError as e:
            log(f"  дополнение пропущено ({g[0]['id']}…): {str(e)[:160]}")
            return {}

    with ThreadPoolExecutor(max_workers=int(cfg.llm.get("workers", 4))) as pool:
        for r in pool.map(safe, groups):
            merged.update(r)

    def errors(doc: dict) -> set[str]:
        iss = check_layer(cfg, ch, doc, coverage=cov) + check_coverage(cfg, ch, cov, doc)
        return {f"{i.where}|{i.msg}" for i in iss if i.level == "error"}

    before = errors(layer)
    off = ch.page_print_offset
    for nid, p in merged.items():
        if not p:
            continue
        keep = copy.deepcopy(by_id[nid])
        apply(by_id[nid], p, off)
        if errors(layer) - before:
            idx = next(i for i, n in enumerate(layer["nodes"]) if n["id"] == nid)
            layer["nodes"][idx] = keep
            by_id[nid] = keep
            rep["reverted"].append(nid)
            continue
        for f in p:
            k = f.split(":")[0]
            rep["added"][k] = rep["added"].get(k, 0) + 1
    rep["untaught_after"] = sum(map(len, untaught(layer, cov, units).values()))
    if json.dumps(layer, ensure_ascii=False) != json.dumps(load_layer(ch), ensure_ascii=False):
        write_generated(ch, "layer.json", layer, force=force)
    if freed:
        write_generated(ch, "coverage.json", cov, force=force)
    return rep
