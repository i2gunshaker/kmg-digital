"""Подбор рисунков к шагам новой главы: модель выбирает шаблон и параметры, код проверяет и отбирает.

Порядок: черновик пачками → проверка кодом → исправление брака → сверка со смыслом шага → исправление.
Что не прошло, в visual.json не попадает: такой шаг получает простую схему из карточки (auto_spec).
"""
from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import yaml

from .config import Chapter, Config
from .illustrations import animated, auto_spec, load_styleguide, needs_frames, reviewed_specs
from .layer_gen import S, arr, load_errata, obj, prompt, write_generated
from .llm import LLM, LLMError
from .tasks_gen import note_source
from .units import Unit
from .visual import SpecError, _grid, compile_expr, render_frames
from .visual_check import PARAMS, RICH, check_spec, prune, schema_issues

BATCH = 4
JUDGE_BATCH = 5
_NO_ERROR = re.compile(r"ошиб\w+ (?:по существу )?(?:нет|не найден|не обнаруж)|исправлени\w+ не треб|вс[её] верно", re.I)
VISUAL_SCHEMA = obj(
    specs=arr(
        obj(
            node_id=S,
            template={"type": "string", "enum": list(PARAMS)},
            title=S,
            caption=S,
            note=S,
            scale=S,
            claim_ids=arr(S),
            formula_unit=S,
            params=S,
            frames_label=S,
            frames=S,
        )
    )
)
JUDGE_SCHEMA = obj(issues=arr(obj(node_id=S, wrong=S, truth=S, fix=S)))


# ---------------------------------------------------------------- карточка шага для запроса
def node_card(n: dict, interactive: bool) -> str:
    """Всё, из чего можно составить рисунок шага: мысль, порядок, числа, формулы, утверждения."""
    la = n["layers"]
    out = [f'<step id="{n["id"]}" title="{n["title"]}" difficulty="{n["difficulty"]["primary"]}"{" interactive" if interactive else ""}>']
    out.append("Суть: " + la["main"]["text"])
    out.append("Как работает: " + la["mechanism"]["text"])
    if n.get("steps"):
        out.append("По шагам: " + " | ".join(p["text"] for p in n["steps"]))
    if n.get("conditions"):
        out.append("Цифры и условия: " + " | ".join(p["text"] for p in n["conditions"]))
    if la.get("nuances", {}).get("text"):
        out.append("Нюансы: " + la["nuances"]["text"])
    for f in n.get("formulas") or []:
        out.append(f"Формула [{f['unit_id']}]: {f.get('latex_fixed') or f['latex']} — {f['meaning']}")
    if n.get("variables"):
        out.append("Обозначения: " + "; ".join(f"{v['symbol']} — {v['meaning']}" + (f", {v['unit']}" if v.get("unit") else "") for v in n["variables"]))
    dec = n.get("decision_on_data")
    if dec:
        out.append("Решение на данных: " + " | ".join([*dec["features"], dec["rule"], dec["conclusion"]]))
    out.append("Утверждения:\n" + "\n".join(f"{c['id'].split('#')[-1]} (стр. {c['page_pdf']}): {c['text']}" for c in n["claims"]))
    out.append("</step>")
    return "\n".join(out)


def node_sources(n: dict, units: dict[str, Unit], errata: dict[str, str]) -> list[str]:
    """Тексты, в которых должно стоять каждое число рисунка."""
    ids = dict.fromkeys([*n["unit_ids"], *(c["unit_id"] for c in n["claims"] if c.get("unit_id"))])
    out = [units[i].text for i in ids if i in units and not units[i].model_description]
    out += [errata[i] for i in ids if i in errata]
    return out + [f"{c['text']} {c['quote']}" for c in n["claims"]]


# ---------------------------------------------------------------- ответ модели → описание
def parse_flow(text: str) -> Any:
    """Параметры приходят строкой YAML или JSON; числа вида 1e-3 YAML оставляет строками."""
    body = text.strip().strip("`").strip()
    try:
        doc = yaml.safe_load(body)
    except yaml.YAMLError:
        doc = json.loads(body)
    return _numbers(doc)


_SCI = re.compile(r"[-+]?\d+(?:\.\d*)?[eE][-+]?\d+")
_TEXT_KEYS = {"label", "title", "text", "head", "name", "expr", "unit", "axis_label", "inv_label", "frames_label", "outer", "inner", "fill_label", "root"}
_TEXT_LISTS = {"lines", "cells", "cols", "sides"}


def _numbers(v: Any, key: str = "") -> Any:
    if isinstance(v, str):
        return float(v) if _SCI.fullmatch(v) and key not in _TEXT_KEYS else v
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and key in _TEXT_KEYS | _TEXT_LISTS:
        return str(v)  # «label: 1» — подпись, а не величина
    if isinstance(v, dict):
        return {str(k): _numbers(x, str(k)) for k, x in v.items() if x is not None and x != []}
    if isinstance(v, list):
        return [_numbers(x, key if key in _TEXT_LISTS else "") for x in v]
    return v


_SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
_INDEX = re.compile(r"(?<![A-Za-zА-Яа-яЁёα-ωΑ-Ω0-9])([A-Za-zα-ωΔ]{1,2})_?([0-9])(?![0-9A-Za-z]|[.,]\d)")
_UNDER = re.compile(r"(?<=[A-Za-zА-Яа-яα-ωΔ])_\{?([A-Za-zА-Яа-я]{1,4})\}?")


def _pretty(v: Any, key: str = "") -> Any:
    """Индексы у обозначений: f0 → f₀. Формулы и служебные значения не трогает."""
    if isinstance(v, str):
        if key in ("expr", "color", "kind", "lith", "fluid", "template"):
            return v
        return _UNDER.sub(r"\1", _INDEX.sub(lambda m: m.group(1) + m.group(2).translate(_SUB), v))
    if isinstance(v, dict):
        return {k: x if k in ("vars", "names") else _pretty(x, k) for k, x in v.items()}
    return [_pretty(x, key) for x in v] if isinstance(v, list) else v


def _fit_axis(params: dict, frames: list[dict]) -> None:
    """Ось без чисел растягивается так, чтобы кривые всех кадров помещались."""
    y, x = params.get("y"), params.get("x")
    if not (isinstance(x, dict) and isinstance(y, dict) and y.get("qualitative") and not y.get("log")):
        return
    vals: list[float] = []
    try:
        for over in [{}] + [{**({"vars": f["vars"]} if f.get("vars") else {}), **(f.get("params") or {})} for f in frames]:
            p = {**params, **over}
            for s in p["series"]:
                if s.get("points"):
                    vals += [float(q[1]) for q in s["points"]]
                    continue
                fn, env = compile_expr(str(s["expr"])), {**(p.get("vars") or {}), **(s.get("vars") or {})}
                got = sorted(fn({**env, "x": v}) for v in _grid(*(s.get("domain") or [x["min"], x["max"]]), bool(x.get("log")), 80))
                got = [v for v in got if math.isfinite(v)]
                vals += got[int(0.04 * len(got)) : max(int(0.96 * len(got)), 1)] if got and (got[-1] > 4 * abs(got[len(got) // 2] or 1)) else got
    except (SpecError, KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        return
    if not vals:
        return
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or abs(hi) or 1.0
    y["min"] = 0.0 if 0 <= lo < 0.5 * span else lo - 0.08 * span
    y["max"] = hi + 0.1 * span


# как модель называет части рифа → как их называет шаблон
_ZONES = {
    "dome": {"core": "core", "inner": "core", "flank": "flank", "flank_l": "flank", "flank_r": "flank", "outer": "flank"},
    "flat": {"core": "core", "inner": "core", "flank": "flank", "flank_l": "flank", "flank_r": "flank", "outer": "flank"},
    "atoll": {"rim": "rim", "rim_l": "rim", "rim_r": "rim", "outer": "rim", "lagoon": "lagoon", "inner": "lagoon", "flank": "flank", "flank_l": "flank", "flank_r": "flank"},
    "system": {"front": "front", "crest": "crest", "back": "back", "outer": "front", "inner": "back"},
}


def _unique_frames(spec: dict, sg: dict) -> None:
    """Кадры, которые выглядят одинаково, остаются в одном экземпляре; один кадр — не кадры."""
    seen: set[str] = set()
    keep = []
    for f, (_, svg) in zip(spec.get("frames") or [], render_frames(spec, sg)):
        if svg not in seen:
            seen.add(svg)
            keep.append(f)
    if len(keep) >= 2:
        spec["frames"] = keep
    else:
        for k in ("frames", "frames_label", "frames_ui"):
            spec.pop(k, None)


def _tidy(template: str, params: dict, frames: list[dict]) -> None:
    """Что код делает лучше модели: диапазон оси, тени кадров, место подписей, плавные границы."""
    if template == "curves":
        params["auto_labels"] = True
        for s in params.get("series") or []:
            if isinstance(s, dict):
                s.pop("label_side", None)
        _fit_axis(params, frames)
        seen = [f["vars"] for f in frames if f.get("vars") and not f.get("params")]
        if len(seen) > 1 and len(seen) == len(frames):
            params["ghosts"] = seen
    if template in ("reef", "plan"):
        kind = params.get("kind")
        for p in (params, *(f["params"] for f in frames if f.get("params"))):
            if isinstance(p.get("highlight"), list):
                zones = _ZONES.get(p.get("kind") or kind, {})
                p["highlight"] = list(dict.fromkeys(zones[h] for h in map(str, p["highlight"]) if h in zones))
    if template == "section":
        for p in (params, *(f["params"] for f in frames if f.get("params"))):
            for part in (*(p.get("bodies") or []), *(p.get("lines") or [])):
                if isinstance(part, dict) and len(part.get("points") or []) >= 6 and part.get("kind") not in ("fault", "contact"):
                    part.setdefault("smooth", True)


def _pages(pages: list[int]) -> str:
    ps = sorted(set(pages))
    if not ps:
        return ""
    if len(ps) > 1 and ps == list(range(ps[0], ps[-1] + 1)):
        return f"стр. {ps[0]}–{ps[-1]}"
    return "стр. " + ", ".join(map(str, ps))


_EQ = re.compile(r"\\tag\{\s*\(?([\dIVXLC]+[.,]?\d*)\)?\s*\}|\(\s*(\d+[.,]\d+)\s*\)")
_OFFSCALE = {"layers", "section", "reef", "plan", "pores"}


def _formula(fid: str, n: dict, units: dict[str, Unit]) -> tuple[int, str] | None:
    """Страница и запись формулы: из карточки или из единицы этого шага."""
    for f in n.get("formulas") or []:
        if f["unit_id"] == fid:
            return f["page_pdf"], f.get("latex_fixed") or f["latex"]
    u = units.get(fid)
    if u is not None and fid in n["unit_ids"] and not u.model_description and "$" in u.text:
        return u.page_pdf, u.text
    return None


def shape(raw: dict, n: dict, units: dict[str, Unit]) -> tuple[dict | None, list[str]]:
    """Описание рисунка из ответа модели. Плашку, происхождение, страницы и масштаб ставит код."""
    issues: list[str] = []
    claims = {c["id"].split("#")[-1]: c for c in n["claims"]}
    used = [claims[k] for k in (x.split("#")[-1].strip() for x in raw["claim_ids"]) if k in claims]
    if not used:
        issues.append("claim_ids: нужно хотя бы одно утверждение карточки, например c1")
    try:
        params = parse_flow(raw["params"])
        frames = parse_flow(raw["frames"]) if raw["frames"].strip() not in ("", "[]", "''", '""') else None
    except (yaml.YAMLError, json.JSONDecodeError) as e:
        return None, [f"params или frames не разобраны как YAML: {str(e)[:120]}"]
    if not isinstance(params, dict) or (frames is not None and not isinstance(frames, list)):
        return None, ["params должен быть объектом, frames — списком"]

    params = prune(params, PARAMS[raw["template"]])
    frames = [
        {"label": str(f.get("label", "")), **{k: f[k] for k in ("params", "vars") if isinstance(f.get(k), dict)}}
        for f in frames or []
        if isinstance(f, dict)
    ]
    for f in frames:
        if "params" in f:
            f["params"] = prune(f["params"], PARAMS[raw["template"]])

    _tidy(raw["template"], params, frames)
    params, frames = _pretty(params), _pretty(frames)

    formula = _formula(raw["formula_unit"].strip().strip("[]"), n, units)
    by_formula = raw["template"] == "curves" and formula is not None and any(isinstance(s, dict) and s.get("expr") for s in params.get("series", []))
    pages = _pages([c["page_pdf"] for c in used] + ([formula[0]] if by_formula else []))
    if by_formula:
        num = _EQ.search(formula[1])
        source = f"расчёт по формуле ({num.group(1) or num.group(2)}), {pages}" if num else f"расчёт по формуле главы, {pages}"
    elif raw["template"] == "bars":
        source = f"числа из текста главы, {pages}"
    else:
        source = f"по тексту главы, {pages}"
    scale = raw["scale"].strip().rstrip(".")
    scale = scale[:1].lower() + scale[1:]
    if raw["template"] not in _OFFSCALE | {"curves"}:
        scale = ""
    if raw["template"] in _OFFSCALE and "масштаб" not in scale.lower():
        scale = f"{scale}, не в масштабе" if scale and len(scale) < 36 else "не в масштабе"
    if raw["template"] == "curves":
        both = all(isinstance(params.get(k), dict) and params[k].get("qualitative") for k in "xy")
        scale = "безразмерные оси" if both and by_formula else "качественный вид, оси без чисел" if both else scale
    data = raw["template"] == "bars"
    spec: dict[str, Any] = {
        "template": raw["template"],
        "label": "симуляция" if by_formula else "схема",
        "class": "A" if data else "B",
        "origin": "code_from_data" if data else "code_from_model",
        "title": _pretty(raw["title"].strip().rstrip(".")),
        "caption": _pretty(raw["caption"].strip()),
        "source": source,
        "params": params,
        "claim_ids": [c["id"] for c in used],
        "by": "model",
    }
    if raw["note"].strip():
        spec["note"] = _pretty(raw["note"].strip())
    if scale:
        spec["scale"] = scale
    if frames and raw["template"] in RICH:  # текст в рамках открывается по частям сам (with_reveal)
        spec["frames_label"] = raw["frames_label"].strip() or "Кадр"
        spec["frames"] = frames
        spec["frames_ui"] = "buttons"
    return spec, issues


# ---------------------------------------------------------------- оркестровка
def interactive_nodes(routing: dict | None) -> frozenset[str]:
    """Шаги, которым нужен рисунок с кадрами: интерактив и ролик."""
    return frozenset(needs_frames(routing))


def generate_visuals(
    cfg: Config, ch: Chapter, llm: LLM, layer: dict, units: list[Unit], *, routing: dict | None = None, everything: bool = False,
    retry_rejected: bool = False, force: bool = False, log=print
) -> dict:
    """visual.json главы. everything — заново и те шаги, у которых уже есть проверенное описание.

    retry_rejected — по готовому visual.json: принятые рисунки остаются, отклонённые шаги идут к модели
    сразу с замечаниями прошлой проверки (иначе одинаковый запрос вернул бы из кэша тот же брак).
    """
    sg = load_styleguide(cfg)
    by_id = {u.id: u for u in units}
    errata = load_errata(cfg, ch)
    inter = interactive_nodes(routing)
    need = needs_frames(routing)
    # проверенная статичная схема не закрывает шаг, которому маршрут велит кадры: к ней рисуется ещё и анимированный
    ready = {} if everything else {k: v for k, v in reviewed_specs(cfg, ch).items() if k not in need or animated(v, need[k])}
    nodes = {n["id"]: n for n in layer["nodes"] if n["id"] not in ready}
    vp = ch.build_dir / "visual.json"
    prev = json.loads(vp.read_text(encoding="utf-8")) if retry_rejected and vp.exists() else None
    kept: dict[str, dict] = {k: v for k, v in (prev or {}).get("nodes", {}).items() if k in nodes}
    if prev is not None:
        nodes = {k: n for k, n in nodes.items() if k not in kept}
    role = str(cfg.llm.get("visual", "fast"))
    judge = str(cfg.llm.get("visual_judge", "senior"))
    effort, j_effort = str(cfg.llm.get("visual_effort", "medium")), str((cfg.llm.get("judge_effort") or ["high"])[0])
    system = [prompt("visual")]
    cards = {k: node_card(n, k in inter) for k, n in nodes.items()}
    sources = {k: node_sources(n, by_id, errata) for k, n in nodes.items()}

    specs: dict[str, dict] = {}
    bad: dict[str, tuple[dict | None, list[str]]] = {}  # шаг → последний ответ и замечания
    notes: dict[str, Any] = {"first_try": 0, "fixed": 0, "judged": False, "judged_bad": 0}
    error = ""
    for k, why in ((prev or {}).get("rejected") or {}).items():
        if k in nodes:
            bad[k] = (None, list(why))

    def accept(raws: list[dict], want: set[str]) -> int:
        """Разбирает ответ: годные — в specs, остальные — в bad."""
        got = 0
        for raw in raws:
            nid = raw["node_id"]
            if nid not in want:
                continue
            want = want - {nid}
            spec, issues = shape(raw, nodes[nid], by_id)
            if spec:
                if spec.get("frames") and not schema_issues(spec):
                    try:
                        _unique_frames(spec, sg)
                    except (SpecError, KeyError, ValueError, TypeError, IndexError, OverflowError, ZeroDivisionError):
                        pass  # причину назовёт check_spec
                issues = issues + check_spec(spec, sg, source=sources[nid], interactive=nid in inter)
            if spec and not issues:
                specs[nid] = spec
                bad.pop(nid, None)
                got += 1
            else:
                specs.pop(nid, None)
                bad[nid] = (raw, issues)
        for nid in want:
            bad.setdefault(nid, (None, ["модель не вернула описание"]))
        return got

    def ask(ids: list[str], label: str) -> tuple[list[str], list[dict], str]:
        body = "\n\n".join(cards[k] for k in ids)
        user = f"Составь по одному рисунку на каждый шаг, всего {len(ids)}.\n\n{body}"
        fixes = [(k, *bad[k]) for k in ids if k in bad]
        if fixes:
            user += "\n\nПрошлые описания не прошли проверку. Исправь всё перечисленное, остальное сохрани; не получается — возьми шаблон проще.\n" + "\n".join(
                f"{k}: {'; '.join(i[:6])}" + (f"\nПрошлое описание: {json.dumps(r, ensure_ascii=False)}" if r else "") for k, r, i in fixes
            )
        try:
            return ids, llm.json(role, system, user, VISUAL_SCHEMA, label=label, effort=effort, max_tokens=20000)["specs"], ""
        except LLMError as e:
            return ids, [], str(e)

    def run(ids: list[str], label: str) -> int:
        nonlocal error
        packs = [ids[i : i + BATCH] for i in range(0, len(ids), BATCH)]
        got = 0
        with ThreadPoolExecutor(max_workers=max(1, int(cfg.llm.get("workers", 4)))) as pool:
            for pack, raws, err in pool.map(lambda p: ask(p[1], f"{label}-{p[0] + 1}"), enumerate(packs)):
                error = error or err
                got += accept(raws, set(pack))
        return got

    def judge_round(ids: list[str], label: str) -> dict[str, str]:
        nonlocal error
        pc = cfg.domain_dir / "physics_checklist.json"
        items = json.loads(pc.read_text(encoding="utf-8")) if pc.exists() else []
        jsys = [prompt("visual_judge", checklist="\n".join(f"— {x['item']}" for x in items) or "— общие законы физики")]
        found: dict[str, str] = {}
        for i in range(0, len(ids), JUDGE_BATCH):
            pack = ids[i : i + JUDGE_BATCH]
            body = "\n\n".join(
                f"{cards[k]}\nРисунок: {json.dumps({q: specs[k][q] for q in ('template', 'title', 'caption', 'note', 'params', 'frames_label', 'frames') if q in specs[k]}, ensure_ascii=False)}"
                for k in pack
            )
            try:
                for x in llm.json(judge, jsys, body, JUDGE_SCHEMA, label=f"{label}-{i // JUDGE_BATCH + 1}", effort=j_effort, max_tokens=12000)["issues"]:
                    if x["node_id"] in pack and x["wrong"].strip() and x["truth"].strip() and not _NO_ERROR.search(x["fix"] + x["truth"]):
                        found[x["node_id"]] = f"«{x['wrong'].strip()}» неверно: {x['truth'].strip()}. Исправить: {x['fix'].strip()}"
            except LLMError as e:
                error = error or str(e)
        return found

    if nodes:
        notes["first_try"] = run(list(nodes), "visual")
        if bad and not error:
            log(f"  рисунки: {len(bad)} из {len(nodes)} на исправление")
            notes["fixed"] = run(list(bad), "visual-fix")
        if specs and judge != "off" and not error:
            found = judge_round(list(specs), "visual-judge")
            notes.update(judged=not error, judged_bad=len(found))
            if found:
                log(f"  рисунки: сверка со смыслом вернула {len(found)}")
                for nid, why in found.items():
                    bad[nid] = ({"node_id": nid, **{k: specs[nid][k] for k in ("template", "title", "params", "frames") if k in specs[nid]}}, [why])
                    specs.pop(nid)
                again = list(found)
                run(again, "visual-redo")
                still = [k for k in again if k in specs]
                for nid, why in (judge_round(still, "visual-judge2") if still and not error else {}).items():
                    specs.pop(nid)
                    bad[nid] = (None, [why])
    if error:
        log(f"  рисунки: часть без модели: {error[:140]}")

    specs = {**kept, **specs}
    order = [n["id"] for n in layer["nodes"]]
    nodes = {k: next(n for n in layer["nodes"] if n["id"] == k) for k in order if k in nodes or k in kept}
    rich = sum(specs[k]["template"] in RICH for k in specs)
    doc = {
        "chapter": ch.id,
        "nodes": {k: specs[k] for k in nodes if k in specs},
        "rejected": {k: bad[k][1][:6] for k in nodes if k not in specs and k in bad},
    }
    if specs:
        write_generated(ch, "visual.json", doc, force)
        note_source(ch, "visual_by", "model" if len(specs) == len(nodes) else "model+rules")
    elif nodes:
        note_source(ch, "visual_by", "rules")
    if specs:
        note_source(ch, "visual_stats", {**notes, "nodes": len(nodes), "accepted": len(specs), "rich": rich, "rejected": len(nodes) - len(specs)})
    return {"nodes": len(nodes), "accepted": len(specs), "rich": rich, "rejected": doc["rejected"], "reviewed": len(ready), "error": error}


def survey(cfg: Config, ch: Chapter, layer: dict, units: list[Unit], specs: dict, routing: dict | None, generated: set[str]) -> list[dict]:
    """Что нарисовано к каждому шагу и какие есть замечания. Бесплатно."""
    sg = load_styleguide(cfg)
    by_id = {u.id: u for u in units}
    errata = load_errata(cfg, ch)
    inter = interactive_nodes(routing)
    rows = []
    for n in layer["nodes"]:
        own = specs.get(n["id"])
        items = own if isinstance(own, list) else [own] if own else []
        if not items:
            rows.append({"node": n["id"], "template": auto_spec(n)["template"], "by": "правила", "issues": []})
        for sp in items:
            full = {"label": "схема", "class": "B", "title": n["title"], **sp}
            model = n["id"] in generated
            issues = check_spec(full, sg, source=node_sources(n, by_id, errata) if model else None, interactive=model and n["id"] in inter)
            rows.append({"node": n["id"], "template": sp["template"], "by": "модель" if model else "проверено", "issues": issues})
    return rows
