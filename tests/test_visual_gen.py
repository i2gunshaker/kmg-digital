"""Проверка описаний рисунков и их подбор на подставной модели: без сети и без ключа."""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path

import pytest

from kurs import config as config_mod
from kurs import visual
from kurs.config import ROOT, Config
from kurs.illustrations import build_illustrations, load_styleguide, load_visual_specs
from kurs.llm import LLM, Reply, Usage
from kurs.units import build_units
from kurs.visual_check import RICH, check_spec
from kurs.visual_gen import generate_visuals, node_sources, parse_flow, shape

MD = """<!-- page 10 -->
# § 1. Проникновение фильтрата

Против коллектора раствор проникает в пласт и образует зону проникновения, а на стенке скважины остаётся глинистая корка толщиной до 2 см.

Пористость песчаников меняется от 5 до 25 %, а пористость глин для коллектора значения не имеет, потому что глина жидкость не отдаёт.

<!-- page 11 -->
Давление в пласте падает вдали от скважины по закону, который описывает формула (1.5), и со временем область снижения растёт.

$$p = p_0 \\, erfc\\left(\\frac{x}{2\\sqrt{t}}\\right) \\quad (1.5)$$

Залежь нефти лежит в песчанике под глинистой покрышкой, ниже контакта пласт заполнен водой.
"""


def node(nid: str, order: int, uids: list[str], page: int, text: str, formulas=()) -> dict:
    para = {"text": text, "by_model": True}
    return {
        "id": nid, "order": order, "title": f"Шаг {nid}", "title_book": "§ 1", "why_here": para,
        "difficulty": {"primary": "Ф", "secondary": None}, "threshold": False, "pages": [page], "unit_ids": uids,
        "layers": {"topic": para, "main": para, "mechanism": para, "practice": para, "nuances": para, "deeper": []},
        "steps": [], "conditions": [], "formulas": list(formulas), "variables": [], "misconception": None,
        "claims": [{"id": f"{nid}#c1", "text": text, "quote": text, "page_pdf": page, "unit_id": uids[0]}],
        "data_meaning": {"from_chapter": para, "breaks": para}, "decision_on_data": None, "worked_examples": [], "book_tasks": [],
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "BUILD", tmp_path / "build")
    for k in ("KURS_LLM_OFFLINE", "KURS_LLM_BUDGET", "KURS_LLM_TOTAL", "KURS_LLM_MODEL", "KURS_LLM_BACKEND", "KURS_LLM_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    md = tmp_path / "demo_risunki.md"
    md.write_text(MD, encoding="utf-8")
    cfg = Config()
    ch = cfg.chapter(str(md))
    units = build_units(cfg, ch)
    by = {k: next(u.id for u in units if k in u.text) for k in ("раствор проникает", "Пористость", "Давление", "erfc", "Залежь")}
    layer = {
        "chapter": ch.id,
        "nodes": [
            node("d.filtrate", 1, [by["раствор проникает"]], 10, "Против коллектора растут зона проникновения и корка до 2 см."),
            node("d.porosity", 2, [by["Пористость"]], 10, "Пористость песчаников меняется от 5 до 25 %."),
            node("d.pressure", 3, [by["Давление"], by["erfc"]], 11, "Область снижения давления растёт со временем.",
                 [{"unit_id": by["erfc"], "page_pdf": 11, "latex": "p = p_0 erfc(x/2\\sqrt{t}) \\tag{1.5}", "latex_fixed": None, "meaning": "давление"}]),
            node("d.trap", 4, [by["Залежь"]], 11, "Залежь нефти под глинистой покрышкой, ниже вода."),
        ],
    }
    routing = {"routes": [{"node_id": "d.pressure", "format": "интерактив", "secondary_format": None}]}
    return cfg, ch, units, layer, routing, tmp_path, by


LAYERS = {"layers": [{"h": 1, "lith": "shale", "label": "глина", "caliper": 1.6}, {"h": 1.4, "lith": "sand", "label": "коллектор", "invasion": 0.2, "cake": 0.3, "caliper": 0.8}], "well": {"x": 0.4, "label": "ствол"}}
BARS = {"rows": [{"label": "песчаник, от", "value": 5}, {"label": "песчаник, до", "value": 25}], "unit": "%", "axis_label": "пористость, %", "min": 0, "max": 30}
CURVE = {
    "x": {"min": 0, "max": 4, "label": "расстояние", "qualitative": True},
    "y": {"min": 0, "max": 1.1, "label": "снижение давления", "qualitative": True},
    "vars": {"t": 1},
    "series": [{"expr": "erfc(x/(2*sqrt(t)))", "label": "давление", "label_at": 1.2}],
}
FRAMES = [{"label": "рано", "vars": {"t": 0.2}}, {"label": "позже", "vars": {"t": 1}}, {"label": "поздно", "vars": {"t": 4}}]
TRAP = {
    "view": "depth",
    "bodies": [
        {"points": [[0, 0.1], [1, 0.1], [1, 0.4], [0.5, 0.25], [0, 0.4]], "lith": "shale", "label": "покрышка"},
        {"points": [[0, 0.4], [0.5, 0.25], [1, 0.4], [1, 0.6], [0, 0.6]], "lith": "sand", "fluid": "oil", "label": "нефть"},
        {"points": [[0, 0.6], [1, 0.6], [1, 0.9], [0, 0.9]], "lith": "sand", "fluid": "water", "label": "вода"},
    ],
    "lines": [{"points": [[0, 0.6], [1, 0.6]], "kind": "contact", "label": "ВНК", "label_t": 0.1}],
    "wells": [{"x": 0.5, "bottom": 0.5, "label": "скв."}],
}


def spec(template: str, params: dict, **kw) -> dict:
    return {"template": template, "label": "схема", "class": "B", "origin": "code_from_model", "title": "Рисунок", "caption": "Рисунок шага.", "params": copy.deepcopy(params), **kw}


# ---------------------------------------------------------------- проверка описаний
def reviewed() -> list[tuple[str, dict]]:
    out = []
    for p in sorted((ROOT / "domain/geology/visual").glob("*.json")):
        for nid, v in json.loads(p.read_text(encoding="utf-8"))["nodes"].items():
            out += [(nid, s) for s in (v if isinstance(v, list) else [v])]
    return out


def test_reviewed_specs_pass_the_checker():
    sg = load_styleguide(Config())
    assert len(reviewed()) >= 60
    bad = {nid: iss for nid, s in reviewed() if (iss := check_spec(s, sg))}
    assert not bad


def broken(base: dict, path: list, value) -> dict:
    out = copy.deepcopy(base)
    cur = out
    for k in path[:-1]:
        cur = cur[k]
    cur[path[-1]] = value
    return out


GOOD = {
    "layers": spec("layers", LAYERS),
    "curves": spec("curves", CURVE, label="симуляция", frames=FRAMES),
    "section": spec("section", TRAP),
    "bars": spec("bars", BARS),
}


@pytest.mark.parametrize(
    "base, path, value, expect",
    [
        ("layers", ["params", "layers", 0, "invasion"], 0.2, "непроницаемом"),
        ("layers", ["params", "layers", 1, "caliper"], 1.4, "корка и увеличенный диаметр"),
        ("layers", ["params", "layers", 1, "fluids"], [{"to": 0.5, "fluid": "water"}, {"to": 1, "fluid": "oil"}], "под более тяжёлым"),
        ("layers", ["params", "unknown"], 1, "unknown"),
        ("section", ["params", "bodies", 1, "fluid"], "water", ""),
        ("section", ["params", "bodies", 2, "fluid"], "gas", "ниже"),
        ("section", ["params", "wells", 0, "bottom"], 0.0, "выше устья"),
        ("curves", ["params", "y", "min"], 5, "min не меньше max"),
        ("curves", ["params", "y"], {"min": 50, "max": 60, "label": "снижение", "qualitative": True}, "за пределами"),
        ("curves", ["params", "y", "label"], "глубина", "расти вниз"),
        ("curves", ["params", "series", 0, "expr"], "__import__('os')", "не рисуется"),
        ("curves", ["frames", 1, "vars", "t"], 0.2, "одинаковые кадры"),
        ("bars", ["params", "rows", 0], {"label": "песчаник", "lo": 9, "hi": 5}, "lo больше hi"),
        ("bars", ["title"], "Доля $k_п$", "запись формулы"),
    ],
)
def test_checker_catches_mistakes(base, path, value, expect):
    sg = load_styleguide(Config())
    assert check_spec(GOOD[base], sg) == []
    issues = " | ".join(check_spec(broken(GOOD[base], path, value), sg))
    assert expect in issues and (issues or not expect)


def test_labels_are_moved_apart_and_hopeless_ones_are_reported():
    sg = load_styleguide(Config())

    def with_points(n: int) -> dict:
        return broken(GOOD["curves"], ["params", "points"], [{"x": 2, "y": 0.5, "label": f"подпись {k}я"} for k in "абвгдежз"[:n]])

    assert check_spec(with_points(2), sg) == []  # код разводит сам
    issues = check_spec(with_points(8), sg)
    assert any("накладываются" in i and "телефон" in i for i in issues) and any("компьютер" in i for i in issues)
    wide = broken(GOOD["bars"], ["params", "axis_label"], "очень длинная подпись оси, которая шире рисунка на телефоне")
    assert any("телефон" in i and "за край" in i for i in check_spec(wide, sg))


def test_numbers_must_stand_in_the_step(env):
    cfg, _, units, layer, *_ = env
    sg = load_styleguide(cfg)
    by = {u.id: u for u in units}
    src = node_sources(layer["nodes"][1], by, {})
    assert check_spec(GOOD["bars"], sg, source=src) == []
    assert any("26" in i for i in check_spec(broken(GOOD["bars"], ["params", "rows", 1, "value"], 26), sg, source=src))
    assert any("40" in i for i in check_spec({**GOOD["bars"], "note": "Бывает и 40 %."}, sg, source=src))
    # «25 м» вместо «25 %»
    assert any("25" in i for i in check_spec({**GOOD["bars"], "note": "Доходит до 25 м."}, sg, source=src))
    # ось с делениями при придуманных точках и форма кривой без формулы главы
    shape = spec("curves", {"x": {"min": 0, "max": 10, "label": "x"}, "y": {"min": 0, "max": 9, "label": "y"}, "series": [{"points": [[0, 1], [7, 8]]}]})
    assert any("нет в тексте шага" in i for i in check_spec(shape, sg, source=src))
    free = broken(broken(GOOD["curves"], ["params", "x", "qualitative"], False), ["label"], "схема")
    assert any("без формулы главы" in i for i in check_spec(free, sg, source=src))
    # отношение времён — число самой модели, а постороннее — нет
    ratio = broken(GOOD["curves"], ["frames", 1, "label"], "5·t₁")
    assert check_spec(ratio, sg, source=src) == []
    assert any("7" in i for i in check_spec(broken(GOOD["curves"], ["frames", 1, "label"], "7·t₁"), sg, source=src))
    assert any("недопустимы" in i for i in check_spec({**GOOD["bars"], "label": "реальные данные"}, sg, source=src))


def test_section_renders_on_both_widths():
    sg = load_styleguide(Config())
    for w in (560, 400):
        with visual.canvas_width(w):
            svg = visual.render(GOOD["section"], sg)
            assert svg.startswith("<svg") and "покрышка" in svg and "ВНК" in svg and "глубина" in svg


# ---------------------------------------------------------------- подбор
def test_parse_flow_reads_yaml_and_json():
    d = parse_flow("{x: {min: 1e-3, label: 2}, rows: [{label: 'а, б', cells: [1, x]}], on: null}")
    assert d["x"] == {"min": 0.001, "label": "2"} and d["rows"][0] == {"label": "а, б", "cells": ["1", "x"]}
    assert parse_flow('{"a": [1, 2.5], "kind": "in"}') == {"a": [1, 2.5], "kind": "in"}


def test_shape_cleans_the_answer(env):
    cfg, _, units, layer, _, _, by = env
    n, um = layer["nodes"][2], {u.id: u for u in units}
    tall = {**CURVE, "y": {**CURVE["y"], "max": 0.2}}
    frames = [{"label": "рано", "vars": {"t": 0.2}, "focus": 1}, {"label": "поздно", "vars": {"t": 4}}]
    spec_, issues = shape(raw("d.pressure", "curves", {**tall, "unknown": 1}, frames, f"[{by['erfc']}]", title="Снижение p_0 у f0"), n, um)
    assert not issues and "unknown" not in spec_["params"] and "focus" not in spec_["frames"][0]
    assert spec_["title"] == "Снижение p₀ у f₀" and spec_["params"]["y"]["max"] > 0.9  # ось растянута под все кадры
    assert spec_["params"]["ghosts"] == [{"t": 0.2}, {"t": 4}] and "expr" in spec_["params"]["series"][0]
    reef, _ = shape(raw("d.trap", "reef", {"kind": "dome", "seal": "покрышка", "highlight": ["flank_l", "нет такой"]}), layer["nodes"][3], um)
    assert reef["params"]["seal"] is True and reef["params"]["highlight"] == ["flank"]
    wrong, issues = shape({**raw("d.trap", "section", {}), "params": "{bodies: ["}, layer["nodes"][3], um)
    assert wrong is None and "YAML" in issues[0]


def raw(nid: str, template: str, params: dict, frames=None, formula: str = "", **kw) -> dict:
    return {
        "node_id": nid, "template": template, "title": "Название рисунка", "caption": "Что показано на рисунке.", "note": "", "scale": "",
        "claim_ids": ["c1"], "formula_unit": formula, "params": json.dumps(params, ensure_ascii=False),
        "frames_label": "Время" if frames else "", "frames": json.dumps(frames, ensure_ascii=False) if frames else "", **kw,
    }


def fake(script):
    """Подставная модель: script(вид запроса, текст) → ответ."""
    lock, calls = threading.Lock(), []

    def transport(model, system, user, schema, effort, max_tokens):
        kind = "judge" if "issues" in schema["properties"] else "fix" if "не прошли проверку" in user else "draft"
        with lock:
            calls.append(kind)
            n = calls.count(kind)
        return Reply(json.dumps(script(kind, user, n), ensure_ascii=False), "claude-haiku-5-5", Usage(input=1000, output=500))

    return transport, calls


def llm_for(cfg, tmp, transport) -> LLM:
    return LLM(cfg, scope="t", transport=transport, cache_dir=tmp / "cache", log_path=tmp / "usage.jsonl", budget=5.0)


def test_generation_fixes_rejects_and_falls_back(env):
    cfg, ch, units, layer, routing, tmp, by = env

    def script(kind, user, n):
        if kind == "draft":
            assert 'id="d.pressure" title="Шаг d.pressure" difficulty="Ф" interactive' in user
            return {"specs": [
                raw("d.filtrate", "layers", LAYERS),
                raw("d.porosity", "bars", broken({"p": BARS}, ["p", "rows", 1, "value"], 31)["p"]),  # числа нет в шаге
                raw("d.pressure", "curves", CURVE, FRAMES, by["erfc"]),
                raw("d.trap", "section", broken({"p": TRAP}, ["p", "bodies", 2, "fluid"], "gas")["p"]),  # газ под нефтью
            ]}
        if kind == "fix":
            assert "числа 31 нет" in user and "d.filtrate" not in user
            return {"specs": [raw("d.porosity", "bars", BARS), raw("d.trap", "section", {"bodies": "нет"})]}
        return {"issues": []}

    transport, calls = fake(script)
    rep = generate_visuals(cfg, ch, llm_for(cfg, tmp, transport), layer, units, routing=routing, log=lambda *_: None)
    assert calls == ["draft", "fix", "judge"]
    assert (rep["accepted"], rep["nodes"], rep["rich"]) == (3, 4, 2) and list(rep["rejected"]) == ["d.trap"]
    doc = json.loads((ch.build_dir / "visual.json").read_text(encoding="utf-8"))
    p = doc["nodes"]["d.pressure"]
    assert p["label"] == "симуляция" and p["source"] == "расчёт по формуле (1.5), стр. 11" and p["scale"] == "безразмерные оси"
    assert doc["nodes"]["d.filtrate"]["source"] == "по тексту главы, стр. 10" and doc["nodes"]["d.filtrate"]["scale"] == "не в масштабе"
    b = doc["nodes"]["d.porosity"]
    assert (b["class"], b["origin"], b["source"]) == ("A", "code_from_data", "числа из текста главы, стр. 10")
    assert p["claim_ids"] == ["d.pressure#c1"] and "d.trap" not in doc["nodes"]
    notes = json.loads((ch.build_dir / "gen_report.json").read_text(encoding="utf-8"))
    assert notes["visual_by"] == "model+rules" and notes["visual_stats"]["fixed"] == 1 and notes["visual_stats"]["judged"] is True

    out = tmp / "course"
    ill = build_illustrations(cfg, ch, layer, out, json_dir=tmp, interactive=frozenset({"d.pressure"}))
    labels = {i["node_id"]: i["label"] for i in ill["illustrations"]}
    assert len(labels) == 4 and labels["d.pressure"] == "симуляция"
    assert any(len(f["items"]) == 3 for f in ill["frames"].values())
    assert all((out / i["file"]).exists() for i in ill["illustrations"])


def test_judge_remark_is_fixed_or_the_picture_is_dropped(env):
    cfg, ch, units, layer, routing, tmp, by = env
    all_good = [raw("d.filtrate", "layers", LAYERS), raw("d.porosity", "bars", BARS), raw("d.pressure", "curves", CURVE, FRAMES, by["erfc"]), raw("d.trap", "section", TRAP)]

    def script(kind, user, n):
        if kind == "draft":
            return {"specs": all_good}
        if kind == "judge" and n == 1:
            return {"issues": [
                {"node_id": "d.trap", "wrong": "ВНК", "truth": "контакт стоит выше нефти", "fix": "опустить"},
                {"node_id": "d.porosity", "wrong": "до 25", "truth": "полосы перепутаны", "fix": "поменять"},
                {"node_id": "d.filtrate", "wrong": "корка", "truth": "корка есть", "fix": "Ошибок по существу не найдено"},
                {"node_id": "чужой", "wrong": "x", "truth": "x", "fix": "x"},
            ]}
        if kind == "fix":
            assert "контакт стоит выше нефти" in user and "полосы перепутаны" in user
            return {"specs": [raw("d.trap", "section", TRAP, title="Исправленный рисунок"), raw("d.porosity", "bars", BARS)]}
        return {"issues": [{"node_id": "d.porosity", "wrong": "до 25", "truth": "полосы всё ещё перепутаны", "fix": "убрать"}]}

    transport, calls = fake(script)
    rep = generate_visuals(cfg, ch, llm_for(cfg, tmp, transport), layer, units, routing=routing, log=lambda *_: None)
    assert calls == ["draft", "judge", "fix", "judge"]
    doc = json.loads((ch.build_dir / "visual.json").read_text(encoding="utf-8"))
    assert doc["nodes"]["d.trap"]["title"] == "Исправленный рисунок" and list(rep["rejected"]) == ["d.porosity"] and "всё ещё перепутаны" in rep["rejected"]["d.porosity"][0]


def test_without_money_pictures_come_from_rules(env, monkeypatch):
    cfg, ch, units, layer, routing, tmp, _ = env
    monkeypatch.setenv("KURS_LLM_OFFLINE", "1")
    rep = generate_visuals(cfg, ch, llm_for(cfg, tmp, None), layer, units, routing=routing, log=lambda *_: None)
    assert rep["accepted"] == 0 and rep["error"] and not (ch.build_dir / "visual.json").exists()
    assert json.loads((ch.build_dir / "gen_report.json").read_text(encoding="utf-8"))["visual_by"] == "rules"
    ill = build_illustrations(cfg, ch, layer, tmp / "course", json_dir=tmp)
    assert len(ill["illustrations"]) == 4


def test_reviewed_spec_wins_over_generated(env, monkeypatch):
    cfg, ch, units, layer, routing, tmp, by = env
    domain = tmp / "domain"
    (domain / "visual").mkdir(parents=True)
    for name in ("styleguide.json", "physics_checklist.json"):
        (domain / name).write_text((Path(ROOT) / "domain/geology" / name).read_text(encoding="utf-8"), encoding="utf-8")
    (domain / "visual" / f"{ch.id}.json").write_text(json.dumps({"nodes": {"d.trap": spec("section", TRAP, title="Проверено")}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(type(cfg), "domain_dir", property(lambda self: domain), raising=False)
    asked: list[str] = []

    def script(kind, user, n):
        asked.append(user)
        return {"issues": []} if kind == "judge" else {"specs": [raw(k, "layers", LAYERS) for k in ("d.filtrate", "d.trap") if f'id="{k}"' in user]}

    transport, _ = fake(script)
    generate_visuals(cfg, ch, llm_for(cfg, tmp, transport), layer, units, routing=routing, log=lambda *_: None)
    assert not any('id="d.trap"' in u for u in asked)
    (ch.build_dir / "visual.json").write_text(json.dumps({"nodes": {"d.trap": spec("layers", LAYERS, title="Модель"), "d.filtrate": spec("layers", LAYERS)}}, ensure_ascii=False), encoding="utf-8")
    specs = load_visual_specs(cfg, ch)
    assert specs["d.trap"]["title"] == "Проверено" and "d.filtrate" in specs


def test_static_reviewed_spec_gets_animated_companion_when_route_needs_frames(env, monkeypatch):
    """Статичная проверенная схема не закрывает шаг, который маршрут отправил в ролик: модель рисует к нему кадры, в курс идут обе."""
    cfg, ch, units, layer, routing, tmp, by = env
    domain = tmp / "domain"
    (domain / "visual").mkdir(parents=True)
    for name in ("styleguide.json", "physics_checklist.json"):
        (domain / name).write_text((Path(ROOT) / "domain/geology" / name).read_text(encoding="utf-8"), encoding="utf-8")
    (domain / "visual" / f"{ch.id}.json").write_text(
        json.dumps({"nodes": {"d.trap": spec("section", TRAP, title="Проверено"), "d.porosity": spec("bars", BARS, title="Проверено")}}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(type(cfg), "domain_dir", property(lambda self: domain), raising=False)
    routing = {"routes": [{"node_id": "d.trap", "format": "длинное видео", "secondary_format": "текст"}, {"node_id": "d.porosity", "format": "текст", "secondary_format": None}]}
    asked: list[str] = []

    def script(kind, user, n):
        asked.append(user)
        return {"issues": []} if kind == "judge" else {"specs": []}

    transport, _ = fake(script)
    generate_visuals(cfg, ch, llm_for(cfg, tmp, transport), layer, units, routing=routing, log=lambda *_: None)
    assert any('id="d.trap"' in u and 'interactive>' in u.split('id="d.trap"', 1)[1].split("\n", 1)[0] for u in asked)
    assert not any('id="d.porosity"' in u for u in asked)  # маршрут кадров не требует — проверенная схема закрывает шаг

    ch.build_dir.mkdir(parents=True, exist_ok=True)
    (ch.build_dir / "routing.json").write_text(json.dumps(routing, ensure_ascii=False), encoding="utf-8")
    moving = spec("layers", LAYERS, title="Модель", frames=[{"label": "1", "params": {}}, {"label": "2", "params": {}}])
    (ch.build_dir / "visual.json").write_text(json.dumps({"nodes": {"d.trap": moving, "d.porosity": spec("layers", LAYERS, title="Модель", frames=moving["frames"])}}, ensure_ascii=False), encoding="utf-8")
    specs = load_visual_specs(cfg, ch)
    assert [sp["title"] for sp in specs["d.trap"]] == ["Проверено", "Модель"]
    assert specs["d.porosity"]["title"] == "Проверено"


def test_retry_rejected_keeps_accepted_and_sends_reasons(env):
    """Повтор: принятые рисунки не трогаются, отклонённый шаг уходит к модели вместе с замечанием прошлой проверки."""
    cfg, ch, units, layer, routing, tmp, by = env
    ch.build_dir.mkdir(parents=True, exist_ok=True)
    keep = spec("layers", LAYERS, title="Принят раньше")
    prev = {"chapter": ch.id, "nodes": {"d.filtrate": keep}, "rejected": {"d.porosity": ["в подписи число 40, которого нет в тексте"]}}
    (ch.build_dir / "visual.json").write_text(json.dumps(prev, ensure_ascii=False), encoding="utf-8")
    asked: list[str] = []

    def script(kind, user, n):
        asked.append(user)
        return {"issues": []} if kind == "judge" else {"specs": []}

    transport, _ = fake(script)
    layer = {**layer, "nodes": [n for n in layer["nodes"] if n["id"] in ("d.filtrate", "d.porosity")]}
    rep = generate_visuals(cfg, ch, llm_for(cfg, tmp, transport), layer, units, routing=routing, retry_rejected=True, log=lambda *_: None)
    assert asked and not any('id="d.filtrate"' in u for u in asked)
    assert 'id="d.porosity"' in asked[0] and "число 40" in asked[0]
    doc = json.loads((ch.build_dir / "visual.json").read_text(encoding="utf-8"))
    assert doc["nodes"]["d.filtrate"]["title"] == "Принят раньше" and "d.porosity" in doc["rejected"] and rep["accepted"] == 1


def test_rich_templates_are_drawn_not_boxed():
    assert RICH == {"curves", "layers", "section", "reef", "plan", "pores"}
