"""Проверка описаний рисунков до сборки: форма, отрисовка, подписи, числа со страниц, правила физики.

Одни и те же правила идут к описаниям из доменного пакета и к собранным конвейером; для собранных
дополнительно каждое число на рисунке должно стоять в тексте шага, а подлинность нельзя приписать.
"""
from __future__ import annotations

import ast
import re
from typing import Any, Iterator

import jsonschema

from . import visual
from .verify import check_quantities_against_source, extract_quantities
from .visual import SpecError

# ---------------------------------------------------------------- форма параметров
LITH = ["sand", "shale", "limestone", "dolomite", "anhydrite", "salt", "dense", "reef"]
FLUID = ["oil", "gas", "water", "filtrate", "mud"]
CURVE = ["SP", "GR", "RES", "RES_SHALLOW", "NEUTRON", "DENSITY", "SONIC", "CALIPER", "PRESSURE"]
TONE = ["accent", "ok", "bad", "warn", "ink", "muted", "uncertainty", "measurement"]
PERMEABLE = {"sand", "limestone", "dolomite", "reef"}

_N = {"type": "number"}
_B = {"type": "boolean"}
_POS = {"type": "number", "exclusiveMinimum": 0}
_UNIT = {"type": "number", "minimum": -0.05, "maximum": 1.05}
_FLUID = {"enum": FLUID}
_COLOR = {"anyOf": [{"enum": LITH + FLUID + CURVE + TONE}, {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"}]}
_LITH = {"anyOf": [{"enum": LITH}, {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"}]}
_SIZE = {"type": "number", "minimum": 0}
_PAIR = {"type": "array", "items": _N, "minItems": 2, "maxItems": 2}
_XY = {"type": "array", "items": _UNIT, "minItems": 2, "maxItems": 2}
_RING = {"type": "array", "items": _UNIT, "minItems": 3, "maxItems": 3}
_PAIRS = {"type": "array", "items": _PAIR, "minItems": 2, "maxItems": 40}


def _t(n: int) -> dict:
    return {"type": "string", "maxLength": n}


def _o(req: str = "", **props: dict) -> dict:
    return {"type": "object", "properties": props, "required": req.split(), "additionalProperties": False}


def _a(item: dict, lo: int = 1, hi: int = 12) -> dict:
    return {"type": "array", "items": item, "minItems": lo, "maxItems": hi}


_AXIS = _o("min max label", min=_N, max=_N, label=_t(48), log=_B, qualitative=_B, down=_B, ticks={"type": "array", "items": _N, "maxItems": 12})
_VARS = {"type": "object", "additionalProperties": _N}
_SIDES = _a(_t(28), 2, 2)

PARAMS: dict[str, dict] = {
    "curves": _o(
        "x y series",
        x=_AXIS,
        y=_AXIS,
        vars=_VARS,
        ghosts={"type": "array", "items": _VARS, "maxItems": 6},
        auto_labels=_B,
        height={"type": "number", "minimum": 160, "maximum": 420},
        legend={"enum": ["br", "bl"]},
        series=_a(
            _o(expr=_t(220), points=_PAIRS, label=_t(40), label_at=_N, label_side={"enum": ["above", "below"]}, color=_COLOR, sw=_POS, dash=_t(12), vars=_VARS, domain=_PAIR, smooth=_B),
            1,
            6,
        ),
        bands=_a(_o("", x0=_N, x1=_N, color=_COLOR, label=_t(40), dy=_N), 0, 5),
        hbands=_a(_o("y0 y1", y0=_N, y1=_N, color=_COLOR, label=_t(40)), 0, 4),
        hlines=_a(_o("y", y=_N, label=_t(40), color=_COLOR, dy=_N, dash=_t(12)), 0, 4),
        vlines=_a(_o("x", x=_N, label=_t(40), color=_COLOR, dy=_N, dash=_t(12)), 0, 4),
        points=_a(_o("x y", x=_N, y=_N, label=_t(40), color=_COLOR, dy=_N), 0, 8),
        areas=_a(_o("upper lower", upper=_PAIRS, lower=_PAIRS, x0=_N, x1=_N, color=_COLOR, op=_N, label=_t(40), label_x=_N, label_y=_N), 0, 3),
    ),
    "layers": _o(
        "layers",
        layers=_a(
            _o("h lith", h=_POS, lith=_LITH, label=_t(44), fluid=_FLUID, op=_N, invasion=_SIZE, cake=_SIZE, caliper=_POS, inv_label=_t(30), fluids=_a(_o("to fluid", to=_N, fluid=_FLUID, **{"from": _N}), 1, 3)),
            1,
            16,
        ),
        well=_o("x", x=_UNIT, label=_t(28), tool=_N),
        tracks=_a(
            _o("name", name=_t(26), color=_COLOR, values=_a(_N, 0, 16), profile=_PAIRS, second=_a(_N, 0, 16), second_color=_COLOR, min=_N, max=_N, dash=_t(12), marks=_a(_o("v", v=_N, label=_t(22)), 0, 4)),
            0,
            3,
        ),
        contacts=_a(_o("at label", at=_UNIT, label=_t(30), dy=_N, color=_COLOR, dash=_t(12)), 0, 3),
        height={"type": "number", "minimum": 140, "maximum": 460},
        depth_label=_t(20),
    ),
    "section": _o(
        "bodies",
        view={"enum": ["depth", "plan", "none"]},
        bodies=_a(_o("", points=_a(_XY, 3, 24), circle=_RING, lith=_LITH, fluid=_FLUID, color=_COLOR, op=_N, pattern={"enum": ["dots", "dash", "brick", "hatch"]}, label=_t(40), label_at=_XY, smooth=_B, highlight=_B), 1, 10),
        lines=_a(_o("", points=_a(_XY, 2, 24), circle=_RING, kind={"enum": ["fault", "contact", "level", "border", "path"]}, label=_t(30), label_t=_UNIT, color=_COLOR, smooth=_B), 0, 8),
        arrows=_a(_o("from to", **{"from": _XY}, to=_XY, label=_t(30)), 0, 8),
        wells=_a(_o("", x=_UNIT, y=_UNIT, top=_UNIT, bottom=_UNIT, points=_a(_XY, 2, 12), label=_t(24), color=_COLOR), 0, 5),
        labels=_a(_o("at text", at=_XY, text=_t(40)), 0, 8),
        sides=_SIDES,
        depth_label=_t(20),
        height={"type": "number", "minimum": 140, "maximum": 420},
    ),
    "reef": _o(
        "kind",
        kind={"enum": ["dome", "flat", "atoll", "system"]},
        names={"type": "object", "additionalProperties": {"type": "string", "maxLength": 34}},
        highlight=_a(_t(12), 0, 6),
        wells=_a(_o("x", x=_UNIT, label=_t(24), bottom=_N, to_x=_UNIT), 0, 5),
        sides=_SIDES,
        sea=_B,
        seal=_B,
        seal_line=_PAIRS,
        fill_to=_UNIT,
        fill_label=_t(30),
        height=_POS,
    ),
    "plan": _o(
        "kind",
        kind={"enum": ["dome", "atoll", "system"]},
        outer=_t(34),
        inner=_t(34),
        highlight=_a(_t(12), 0, 4),
        wells=_a(_o("x y", x=_N, y=_N, label=_t(24)), 0, 8),
        height=_POS,
    ),
    "flow": _o("items", numbered=_B, items=_a(_o("text", text=_t(190), head=_t(60), kind={"enum": ["in", "step", "out", "warn", "bad", "rule"]}), 2, 8)),
    "bars": _o(
        "rows",
        rows=_a(_o("label", label=_t(60), lo=_N, hi=_N, value=_N, text=_t(40), color=_COLOR), 1, 8),
        unit=_t(20),
        axis_label=_t(60),
        axis=_B,
        min=_N,
        max=_N,
        log=_B,
    ),
    "compare": _o("cols", stack=_B, cols=_a(_o("title lines", title=_t(60), lines=_a(_t(170), 1, 6), color=_COLOR), 2, 4)),
    "tree": _o("root children", root=_t(90), children=_a(_o("title", title=_t(130), lines=_a(_t(170), 0, 5), color=_COLOR), 2, 7)),
    "pores": _o(
        "panels",
        panels=_a(_o("", head=_t(34), fluid=_FLUID, grain=_N, lines=_a(_t(60), 0, 4), lith=_COLOR, film=_N, film_color=_COLOR, vugs={"type": "array", "items": {"type": "array", "items": _N, "minItems": 4, "maxItems": 4}, "maxItems": 12}), 1, 3),
        height=_POS,
    ),
    "matrix": _o("cols rows", cols=_a(_t(34), 2, 6), rows=_a(_o("label cells", label=_t(60), lith=_COLOR, cells=_a(_t(80), 2, 6)), 2, 8), label_w=_N),
}
RICH = {"curves", "layers", "section", "reef", "plan", "pores"}  # рисунок по существу, а не текст в рамках
_FRAME = _o("label", label=_t(60), params={"type": "object"}, vars=_VARS)
SPEC = _o(
    "template label class title params",
    template={"enum": list(PARAMS)},
    label={"enum": ["схема", "симуляция", "реальные данные"]},
    **{"class": {"enum": ["A", "B"]}},
    title=_t(110),
    caption=_t(320),
    alt=_t(320),
    source=_t(220),
    origin={"enum": ["code_from_model", "code_from_data", "redrawn_from_book"]},
    params={"type": "object"},
    scale=_t(60),
    note=_t(260),
    frames_label=_t(60),
    frames_ui={"enum": ["slider", "buttons"]},
    frames={"type": "array", "items": _FRAME, "minItems": 2, "maxItems": 6},
    claim_ids={"type": "array", "items": {"type": "string"}},
    by={"type": "string"},
)


def _errors(doc: Any, schema: dict, where: str = "") -> list[str]:
    out = []
    for e in jsonschema.Draft202012Validator(schema).iter_errors(doc):
        path = ".".join(str(x) for x in e.absolute_path)
        msg = e.message if len(e.message) < 150 else e.message[:60] + " … " + e.message[-80:]
        out.append(f"{where}{path}: {msg}" if path else f"{where}{msg}")
    return out


def _relaxed(schema: Any) -> Any:
    """Для кадров: части показываются по одной, поэтому списки могут быть короче."""
    if isinstance(schema, dict):
        return {k: (min(v, 1) if k == "minItems" and schema.get("items") not in (_N, _UNIT) else _relaxed(v)) for k, v in schema.items()}
    return [_relaxed(x) for x in schema] if isinstance(schema, list) else schema


FRAME_PARAMS = {k: _relaxed(v) for k, v in PARAMS.items()}


def prune(v: Any, schema: dict) -> Any:
    """Убирает ключи, которых шаблон не знает: на рисунок они не влияют."""
    if isinstance(v, dict) and schema.get("type") == "object" and schema.get("additionalProperties") is False:
        return {k: prune(x, schema["properties"][k]) for k, x in v.items() if k in schema["properties"]}
    if schema is _B and isinstance(v, (str, int)) and not isinstance(v, bool):
        return str(v).strip().lower() not in ("", "0", "false", "no", "нет")  # «seal: покрышка» значит «есть»
    if isinstance(v, list) and schema.get("type") == "array":
        return [prune(x, schema.get("items") or {}) for x in v]
    return v


def frames_of(spec: dict) -> list[tuple[str, dict]]:
    """Рисунок и его кадры с уже слитыми параметрами."""
    base = spec["params"]
    out = [("рисунок", base)]
    for i, f in enumerate(spec.get("frames") or [], 1):
        over = dict(f.get("params") or {})
        if f.get("vars"):
            over["vars"] = f["vars"]
        out.append((f"кадр {i}", visual.merge_params(base, over)))
    return out


def schema_issues(spec: dict) -> list[str]:
    out = _errors(spec, SPEC)
    if out:
        return out
    for k, (where, p) in enumerate(frames_of(spec)):
        out += _errors(p, (FRAME_PARAMS if k else PARAMS)[spec["template"]], f"{where}: ")
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- видимый текст и числа
_HIDDEN = {"expr", "color", "second_color", "film_color", "dash", "kind", "lith", "fluid", "legend", "label_side", "highlight", "pattern", "view"}


def _strings(v: Any, key: str = "") -> Iterator[str]:
    if isinstance(v, str):
        if key not in _HIDDEN:
            yield v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield from _strings(x, k if k not in ("names",) else "")
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x, key)


def visible_texts(spec: dict) -> list[str]:
    head = [spec.get(k) or "" for k in ("title", "caption", "note", "scale", "frames_label")]
    body = [s for _, p in frames_of(spec) for s in _strings(p)]
    return list(dict.fromkeys(s for s in head + [f.get("label", "") for f in spec.get("frames") or []] + body if s))


# ссылки и порядковые номера — не величины
_REF = re.compile(
    r"\b(?:рис(?:\.|унок|унка|унке)?|табл(?:\.|ица|ице)?|стр\.?|с\.|гл\.|формул[аеуы]|уравнени[ея]|задач[аеиу]|пример[ае]?|fig\.?|table|eq\.?|p\.)\s*\(?\s*[\dIVXLC]+(?:[.,–—-]\s*\d+)*\)?"
    r"|§+\s*\d+"
    r"|\(\s*[\dIVXLC]+[.,]\d+\s*\)"
    r"|\b(?:скв(?:\.|ажин[аыеу])?|пласт[аеу]?|сло[йяюе]|зон[аыеу]|шаг[аеу]?|этап[аеу]?|стади[яиею]|фаз[аыеу]|тип[аеу]?|групп[аыеу]|случа[йяею]|вариант[аеу]?|кадр[аеу]?|интервал[аеу]?|кривая|кривой|№|N)\s*\d+"
    r"|№\s*\d+"
    r"|\b\d+-(?:й|я|е|го|му|м|х|ый|ая|ое)\b"
    r"|\b[12]\s*[:)]\s*(?=\D)",
    re.I,
)


def _values(texts: list[str]) -> set[str]:
    return {q.value.lstrip("-") for t in texts for q in extract_quantities(t)}


def _norm(v: float) -> str:
    return f"{abs(v):.10g}"


def _derived(spec: dict) -> list[float]:
    """Числа самой модели: значения параметра кадров, их отношения и значения кривой в подписанных точках."""
    if spec["template"] != "curves":
        return []
    out: list[float] = []
    sets = [p.get("vars") or {} for _, p in frames_of(spec)]
    for name in {k for s in sets for k in s}:
        vals = [s[name] for s in sets if name in s]
        low = min((abs(v) for v in vals if v), default=0)
        out += vals + [v / low for v in vals if low] + [v / vals[0] for v in vals if vals[0]]
    for _, p in frames_of(spec):
        for q in p.get("points", []):
            out += [y for y in _curve_values(p, q["x"])]
    return out


def _curve_values(p: dict, x: float) -> Iterator[float]:
    for s in p["series"]:
        if s.get("expr"):
            yield visual.compile_expr(str(s["expr"]))({**(p.get("vars") or {}), **(s.get("vars") or {}), "x": x})


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 0.006 * max(abs(a), abs(b), 1e-9)


_BARE = re.compile(r"\s*\d{1,2}\s*")  # номер скважины или точки


def text_number_issues(spec: dict, source: list[str]) -> list[str]:
    own = _derived(spec)
    out = []
    for t in visible_texts(spec):
        if _BARE.fullmatch(t):
            continue
        for p in check_quantities_against_source(_REF.sub(" ", t), source):
            num = re.match(r"-?[\d.]+", p)
            if num and "нет в исходных" in p and any(_close(abs(float(num.group(0))), abs(v)) for v in own):
                continue
            out.append(f"число {p} в подписи «{t[:60]}»")
    return list(dict.fromkeys(out))


_MATH = {"0", "1", "2", "3", "4", "0.5", "10", "100"}  # множители, которые стоят в самих формулах


def _constants(expr: str) -> Iterator[float]:
    for n in ast.walk(ast.parse(expr.replace("^", "**"), mode="eval")):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            yield float(n.value)


def _numeric(axis: dict) -> bool:
    return not axis.get("qualitative") and axis.get("ticks") != []


def plotted_number_issues(spec: dict, source: list[str]) -> list[str]:
    """Числа, которые читатель снимет с рисунка: точки, полосы, пороги и постоянные формул на осях с делениями."""
    known = _values(source) | _MATH
    out: list[str] = []

    def need(v: float, what: str) -> None:
        if _norm(v) not in known:
            out.append(f"{what}: числа {_norm(v)} нет в тексте шага")

    for where, p in frames_of(spec):
        if spec["template"] == "bars":
            for r in p["rows"]:
                for k in ("lo", "hi", "value"):
                    if k in r and "text" not in r:
                        need(r[k], f"{where}: полоса «{r['label'][:30]}»")
        if spec["template"] != "curves":
            continue
        nx, ny = _numeric(p["x"]), _numeric(p["y"])
        for s in p["series"]:
            if s.get("points"):
                for xv, yv in s["points"]:
                    if nx:
                        need(xv, f"{where}: точка кривой по x")
                    if ny:
                        need(yv, f"{where}: точка кривой по y")
            elif nx or ny:
                for v in [*_constants(s["expr"]), *(s.get("vars") or {}).values(), *(p.get("vars") or {}).values()]:
                    need(v, f"{where}: формула «{s['expr'][:40]}»")
        for q in p.get("points", []):
            if nx:
                need(q["x"], f"{where}: точка «{q.get('label', '')[:24]}»")
            if ny and not any(_close(q["y"], v) for v in _curve_values(p, q["x"])):
                need(q["y"], f"{where}: точка «{q.get('label', '')[:24]}»")
        for key, ax, on in (("vlines", "x", nx), ("hlines", "y", ny)):
            for q in p.get(key, []):
                if on:
                    need(q[ax], f"{where}: порог «{q.get('label', '')[:24]}»")
        for q in p.get("bands", []):
            for k in ("x0", "x1"):
                if nx and k in q and q[k] not in (p["x"]["min"], p["x"]["max"]):
                    need(q[k], f"{where}: зона «{q.get('label', '')[:24]}»")
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- смысл
_RANK = {"gas": 0, "oil": 1, "water": 2}


def _layer_rules(p: dict, where: str) -> Iterator[str]:
    prev = -1
    for l in p["layers"]:
        name = l.get("label") or l["lith"]
        if (l.get("invasion") or l.get("cake")) and l["lith"] not in PERMEABLE:
            yield f"{where}: проникновение или глинистая корка в непроницаемом пласте «{name[:30]}»"
        if l.get("cake") and l.get("caliper", 1) > 1.0:
            yield f"{where}: корка и увеличенный диаметр скважины в одном пласте «{name[:30]}»"
        if l["lith"] not in PERMEABLE:
            prev = -1  # покрышка разделяет ловушки
            continue
        parts = [f["fluid"] for f in l.get("fluids", [])] or ([l["fluid"]] if l.get("fluid") else [])
        for fl in parts:
            rank = _RANK.get(fl, -1)
            if rank < 0:
                prev = -1
                continue
            if rank < prev:
                yield f"{where}: {'газ' if rank == 0 else 'нефть'} под более тяжёлым флюидом в одном резервуаре «{name[:30]}»"
            prev = rank
    if p.get("well") and not 0.05 <= p["well"]["x"] <= 0.95:
        yield f"{where}: скважина у края разреза"
    n = len(p["layers"])
    for t in p.get("tracks", []):
        if not t.get("values") and not t.get("profile"):
            yield f"{where}: кривая «{t['name']}» без значений"
        for k in ("values", "second"):
            if t.get(k) and len(t[k]) != n:
                yield f"{where}: у кривой «{t['name']}» значений {len(t[k])}, а пластов {n}"


def _section_rules(p: dict, where: str) -> Iterator[str]:
    for part in (*p["bodies"], *p.get("lines", [])):
        if bool(part.get("points")) == bool(part.get("circle")):
            yield f"{where}: у тела и линии нужно ровно одно: points или circle"
            return
    if p.get("view", "depth") == "depth":
        with_fluid = [b for b in p["bodies"] if b.get("fluid") in _RANK and b.get("points")]
        for i, a in enumerate(with_fluid):
            for b in with_fluid[i + 1 :]:
                shared = {tuple(q) for q in a["points"]} & {tuple(q) for q in b["points"]}
                if len(shared) < 2 or a["fluid"] == b["fluid"]:
                    continue
                ya, yb = (sum(q[1] for q in z["points"]) / len(z["points"]) for z in (a, b))
                lo, hi = (a, b) if _RANK[a["fluid"]] < _RANK[b["fluid"]] else (b, a)
                if (ya if lo is a else yb) > (ya if hi is a else yb):
                    yield f"{where}: {lo['fluid']} ниже {hi['fluid']} в соседних телах"
    for w in p.get("wells", []):
        if p.get("view") == "plan" and not w.get("points"):
            if "x" not in w or not ("y" in w or "bottom" in w):
                yield f"{where}: в плане у скважины нужны x и y"
            continue
        if not w.get("points") and ("x" not in w or "bottom" not in w):
            yield f"{where}: у скважины нужны x и bottom или points"
        elif not w.get("points") and w["bottom"] <= w.get("top", 0):
            yield f"{where}: забой скважины выше устья"
    if not any(b.get("label") for b in p["bodies"]) and not p.get("labels"):
        yield f"{where}: тела без подписей"


def _curve_rules(p: dict, where: str) -> Iterator[str]:
    x, y = p["x"], p["y"]
    for ax, name in ((x, "x"), (y, "y")):
        if ax["max"] <= ax["min"]:
            yield f"{where}: ось {name}: min не меньше max"
            return
        if ax.get("log") and ax["min"] <= 0:
            yield f"{where}: логарифмическая ось {name} начинается не с положительного числа"
            return
    if re.match(r"\s*(глубина|depth)", y["label"], re.I) and not y.get("down"):
        yield f"{where}: глубина должна расти вниз: y.down = true"
    if len(p["series"]) > 1 and any("label" not in s for s in p["series"]):
        yield f"{where}: несколько кривых, но подписаны не все"
    for s in p["series"]:
        name = s.get("label") or s.get("expr", "")[:30]
        if bool(s.get("expr")) == bool(s.get("points")):
            yield f"{where}: у кривой «{name}» нужно ровно одно: expr или points"
            continue
        if s.get("points"):
            xs = [q[0] for q in s["points"]]
            if xs != sorted(xs) or len(set(xs)) < len(xs):
                yield f"{where}: точки кривой «{name}» должны идти по возрастанию x"
                continue
            ys = [q[1] for q in s["points"]]
        else:
            fn = visual.compile_expr(str(s["expr"]))
            a, b = s.get("domain") or [x["min"], x["max"]]
            env = {**(p.get("vars") or {}), **(s.get("vars") or {})}
            ys = [fn({**env, "x": v}) for v in visual._grid(a, b, bool(x.get("log")), 60)]
        span = y["max"] - y["min"]
        inside = sum(y["min"] - 0.02 * span <= v <= y["max"] + 0.02 * span for v in ys) / len(ys)
        if inside < 0.45:
            yield f"{where}: кривая «{name}» почти вся за пределами оси y"


def meaning_issues(spec: dict) -> list[str]:
    out: list[str] = []
    if spec["template"] in ("reef", "plan"):
        for where, p in frames_of(spec):
            if p["kind"] in ("dome", "flat") and re.search("лагун", " ".join(_strings(p)), re.I):
                out.append(f"{where}: у купола и плоской постройки нет лагуны")
    rules = {"layers": _layer_rules, "section": _section_rules, "curves": _curve_rules}.get(spec["template"])
    for where, p in frames_of(spec):
        if rules:
            out += rules(p, where)
        if spec["template"] == "bars":
            if where == "рисунок" and len(p["rows"]) == 1 and "value" in p["rows"][0]:
                out.append("одно число не сравнение: нужен диапазон lo и hi или вторая полоса")
            for r in p["rows"]:
                if "value" not in r and not ("lo" in r and "hi" in r):
                    out.append(f"{where}: у полосы «{r['label'][:30]}» нужны value или lo и hi")
                elif "lo" in r and r["lo"] > r["hi"]:
                    out.append(f"{where}: у полосы «{r['label'][:30]}» lo больше hi")
        if spec["template"] == "matrix" and any(len(r["cells"]) != len(p["cols"]) for r in p["rows"]):
            out.append(f"{where}: число ячеек не равно числу столбцов")
    for t in visible_texts(spec):
        if re.search(r"\\[a-zA-Z]+|\$|\{|\}|_\(", t):
            out.append(f"запись формулы в подписи: «{t[:50]}»; нужны обычные символы")
    return list(dict.fromkeys(out))


def frame_issues(spec: dict, sg: dict, interactive: bool) -> list[str]:
    """Интерактивный рисунок по существу меняется от кадра к кадру, а подписи кадров различны."""
    frames = spec.get("frames") or []
    if not frames:
        return ["шаг интерактивный: нужны 2–6 кадров с меняющимся параметром"] if interactive and spec["template"] in RICH else []
    out = []
    if len({f["label"] for f in frames}) < len(frames):
        out.append("у кадров одинаковые подписи")
    if len({svg for _, svg in visual.render_frames(spec, sg)}) < len(frames):
        out.append("есть одинаковые кадры")
    return out


def honesty_issues(spec: dict) -> list[str]:
    """Чего не может утверждать рисунок, собранный без скана и без набора данных."""
    out = []
    if spec["label"] == "реальные данные" or spec.get("origin") == "redrawn_from_book":
        out.append("рисунок собран по тексту: плашка «реальные данные» и «перерисовано» недопустимы")
    for t in visible_texts(spec):
        if re.search(r"карточк|claim|утверждени[ея] c\d|qualitative", t, re.I):
            out.append(f"служебные слова в подписи: «{t[:50]}»")
    if spec["template"] == "curves":
        for where, p in frames_of(spec):
            if len(p["series"]) > 1 and any(not s.get("label") for s in p["series"]):
                out.append(f"{where}: несколько кривых, но подписаны не все")
            if spec["label"] == "схема" and any(s.get("expr") for s in p["series"]) and (_numeric(p["x"]) or _numeric(p["y"])):
                out.append(f"{where}: кривая без формулы главы: у обеих осей нужно qualitative: true")
    return list(dict.fromkeys(out))


def check_spec(spec: dict, sg: dict, *, source: list[str] | None = None, interactive: bool = False) -> list[str]:
    """Все замечания к описанию. source — тексты шага: с ними проверяются ещё и числа."""
    out = schema_issues(spec)
    if out:
        return out
    try:
        out = meaning_issues(spec)
        if out:
            return out
        out = frame_issues(spec, sg, interactive)
        out += visual.layout_issues(spec, sg)
        if source is not None:
            out += honesty_issues(spec) + text_number_issues(spec, source) + plotted_number_issues(spec, source)
    except (SpecError, KeyError, ValueError, TypeError, IndexError, OverflowError, ZeroDivisionError, SyntaxError) as e:
        out.append(f"не рисуется: {type(e).__name__}: {str(e)[:120]}")
    return out
