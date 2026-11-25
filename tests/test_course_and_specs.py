"""Тесты генератора курса, иллюстраций, Volve, паспортов роликов и вёрстки 390/1280 px."""
from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import jsonschema
import pytest

from kurs.config import ROOT, Config
from kurs import visual
from kurs.course import OcrFix, build_course, verify_course_dir
from kurs.course_view import clean_tex, plain_note, rich
from kurs.illustrations import auto_spec, load_styleguide, load_visual_specs, with_reveal
from kurs.datasets import load_volve_slice
from kurs.units import build_units, cut_chapter
from kurs.verify import Source
from kurs.video_spec import build_training_specs, verify_passport_sources


CHAPTERS = ("latyshova_gl5_par17-20", "basniev_gl23-24", "kuznetsov_gl10")


def _demo_tasks(node: dict) -> dict:
    """Задания всех пяти типов к одному шагу."""
    cid, sid = node["claims"][0]["id"], node["id"]
    base = {"step_id": sid, "claim_ids": [cid], "explanation": "Так написано в шаге."}
    opts = [{"id": k, "text": t, "misconception": None} for k, t in (("a", "Нужна количественная интерпретация"), ("b", "Нужны геологические сведения"), ("c", "Хватит одной кривой"))]
    return {
        "chapter": "latyshova_gl5_par17-20",
        "tasks": [
            {**base, "id": f"{sid}.t1", "type": "single", "prompt": "Что нужно смешанному разрезу?", "options": opts, "answer": "a", "kind": "recall"},
            {**base, "id": f"{sid}.t2", "type": "multiple", "prompt": "Что привлекают?", "options": opts, "answer": ["a", "b"], "kind": "recall"},
            {**base, "id": f"{sid}.t3", "type": "order", "prompt": "Расположите этапы.", "options": opts, "answer": ["b", "a", "c"], "kind": "understanding"},
            {**base, "id": f"{sid}.t4", "type": "gap", "prompt": "Плотный прослой это {{1}}, а не коллектор.", "answer": ["неколлектор"], "kind": "data"},
            {**base, "id": f"{sid}.t5", "type": "calc", "prompt": "Во сколько раз 0,228 больше 0,020?", "answer": 11.4, "tolerance": 0.3, "unit": "раз", "kind": "data"},
        ],
    }


def test_volve_dataset_and_domain_pack():
    cfg = Config()
    for fname in (
        "styleguide.json",
        "misconceptions.json",
        "physics_checklist.json",
        "glossary.md",
        "it_bridges.md",
        "pronunciation.md",
        "stress_dictionary.md",
        "errata/latyshova_gl5_par17-20.json",
    ):
        assert (cfg.domain_dir / fname).exists(), f"Не найден {fname}"
    volve = load_volve_slice(cfg.domain_dir)
    assert volve is not None
    assert [iv["id"] for iv in volve["intervals"]] == ["A", "B", "C"]
    for iv in volve["intervals"]:
        assert len(iv["points"]) == 50
    by_id = {iv["id"]: iv["summary"] for iv in volve["intervals"]}
    assert by_id["A"]["median_SW"] < 0.2 and by_id["B"]["sand_flag_1_count"] == 0 and by_id["C"]["median_SW"] == 1.0


def test_course_build_offline_and_playwright_390_1280(tmp_path):
    cfg = Config()
    ch = cfg.chapter("latyshova_gl5_par17-20")
    cut_chapter(cfg, ch)
    build_units(cfg, ch)
    ex_layer = json.loads((cfg.schemas_dir / "example_layer.json").read_text(encoding="utf-8"))

    # Добавим пример и задачу 101 (с errata) + задачу 97 (с рис. 73) на копию слоя, чтобы проверить все 15 блоков и решаемость.
    ex_node = ex_layer["nodes"][0]
    ex_node["worked_examples"] = [
        {
            "page_pdf": 98,
            "condition": "Пример 46: ρп = 20 Ом·м, kп = 25%.",
            "steps": ["Наносим точку на палетку рис. 69.", "Точка попадает в область I."],
            "result": "Безводная нефть.",
            "figure": "рис. 69, стр. 98",
        }
    ]
    ex_node["book_tasks"] = [
        {
            "number": "101",
            "page_pdf": 104,
            "text": "OchoBbAcb Ha pe3yIbTaTax...",
            "figure": "рис. 74, стр. 104",
        }
    ]

    ex_node["decision_on_data"] = {
        "features": ["сопротивление", "пористость"],
        "rule": "Коллектор с нефтью даёт высокое сопротивление при высокой пористости.",
        "conclusion": "Плотный прослой с тем же сопротивлением не коллектор.",
        "counterexample": "Глинистый песчаник.",
    }

    out_dir = tmp_path / "course_out"
    before = {p: p.read_bytes() for p in (ch.build_dir / "illustrations.json", ch.build_dir / "tasks.json") if p.exists()}
    build_course(cfg, ch, ex_layer, tasks_doc=_demo_tasks(ex_node), out_dir=out_dir)
    assert before == {p: p.read_bytes() for p in before}, "сборка в другую папку не трогает файлы главы"
    errs = verify_course_dir(cfg, ch, out_dir)
    assert errs == [], f"Ошибки приёмки курса: {errs}"

    html_text = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "перерисовано по рис. 69, стр. 98" in html_text
    assert "Основываясь на результатах решения предыдущей задачи" in html_text
    assert "Подтянуто к задаче (задача 100, стр. 104)" in html_text
    assert "Volve 15/9-F-1 B" in html_text
    assert "pIIO" not in html_text.replace(" ", ""), "текст с латинскими омоглифами не показываем"
    assert html_text.count("стр. 95 · модель") >= 1 and "Цитата автора" in html_text
    assert '<source media="(max-width: 560px)"' in html_text

    # Реальная браузерная проверка file:// offline на 390px и 1280px
    qa_res = subprocess.run(
        ["node", str(ROOT / "video/qa_course.mjs"), str(out_dir / "index.html")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "QA OK [mobile_390]" in qa_res.stdout
    assert "QA OK [desktop_1280]" in qa_res.stdout


def test_training_video_specs_and_passports(tmp_path):
    cfg = Config()
    for cid in ("latyshova_gl5_par17-20", "basniev_gl23-24"):
        ch = cfg.chapter(cid)
        cut_chapter(cfg, ch)
        build_units(cfg, ch)

    res = build_training_specs(cfg, out_dir=tmp_path / "specs")
    passport_schema = json.loads((cfg.schemas_dir / "passport.schema.json").read_text(encoding="utf-8"))

    for key, ch_id in (("latyshova_passport", "latyshova_gl5_par17-20"), ("basniev_passport", "basniev_gl23-24")):
        p = json.loads(Path(res[key]).read_text(encoding="utf-8"))
        jsonschema.validate(p, passport_schema)
        src = Source(cfg.chapter(ch_id))
        assert verify_passport_sources(p, src) == []

    lat_scene = json.loads(Path(res["latyshova_scene"]).read_text(encoding="utf-8"))
    depths = [r["depth_m"] for r in lat_scene["state_sample"]["profile_rows"]]
    assert depths == sorted(depths) and len(depths) > 5

    bas_scene = json.loads(Path(res["basniev_scene"]).read_text(encoding="utf-8"))
    radii_front = [pr["R_psss_m"] for pr in bas_scene["state_sample"]["profiles"]]
    assert radii_front == sorted(radii_front) and radii_front[-1] > radii_front[0]
    assert Path(res["mentor_brief"]).exists()


def _specs(cfg: Config, cid: str) -> dict[str, list[dict]]:
    return {k: v if isinstance(v, list) else [v] for k, v in load_visual_specs(cfg, cfg.chapter(cid)).items()}


@pytest.mark.parametrize("cid", CHAPTERS)
def test_visual_specs_render_on_both_canvases(cid):
    """Каждая спецификация рисуется для экрана и для телефона; текст внутри полотна и не мельче 16."""
    cfg = Config()
    sg = load_styleguide(cfg)
    allowed = set(json.loads((cfg.schemas_dir / "illustration.schema.json").read_text(encoding="utf-8"))["properties"]["illustrations"]["items"]["properties"]["label"]["enum"])
    for nid, specs in _specs(cfg, cid).items():
        for spec in specs:
            assert spec["label"] in allowed and spec["class"] in ("A", "B"), nid
            for width in (visual.W, visual.phone_width(spec)):
                with visual.canvas_width(width):
                    frames = visual.render_frames(spec, sg) or [("", visual.render(spec, sg))]
                assert len({re.search(r'viewBox="([^"]+)"', svg).group(1) for _, svg in frames}) == 1, f"{nid}: кадры разной высоты"
                for _, svg in frames:
                    ET.fromstring(svg)
                    sizes = [float(x) for x in re.findall(r'font-size="([\d.]+)"', svg)]
                    assert sizes and min(sizes) >= 16, f"{nid}: шрифт {min(sizes)}"
                    assert spec["label"] in svg, f"{nid}: нет плашки происхождения"


@pytest.mark.parametrize("cid", CHAPTERS)
def test_visual_specs_match_layer_nodes(cid):
    """Рисунок есть у каждого шага готового слоя, лишних нет; узлы, отправленные в ролик, имеют кадры."""
    cfg = Config()
    ch = cfg.chapter(cid)
    layer_path = ch.build_dir / "layer.json"
    if not layer_path.exists():
        pytest.skip("слой главы не собран")
    ids = {n["id"] for n in json.loads(layer_path.read_text(encoding="utf-8"))["nodes"]}
    specs = _specs(cfg, cid)
    assert set(specs) == ids
    routes = json.loads((ch.build_dir / "routing.json").read_text(encoding="utf-8"))["routes"]
    for r in routes:
        if "видео" in r.get("format", "") or "шортс" in r.get("format", ""):
            assert any(len(sp.get("frames", [])) > 1 for sp in specs[r["node_id"]]), f"{r['node_id']}: нет ролика и нет модели"
        if "интерактив" in (r.get("format"), r.get("secondary_format")):
            assert any(len(with_reveal(sp).get("frames", [])) > 1 for sp in specs[r["node_id"]]), f"{r['node_id']}: интерактив без кадров"


def test_reveal_and_notes():
    """Интерактив без своих кадров открывается по частям; внутренние номера единиц не видны читателю."""
    flow = {"template": "flow", "title": "т", "params": {"items": [{"text": "а"}, {"text": "б"}, {"text": "в"}]}}
    out = with_reveal(flow)
    assert [f["label"] for f in out["frames"]] == ["всё", "1", "1–2"]
    assert len(out["frames"][1]["params"]["items"]) == 1 and "frames" not in flow
    bars = with_reveal({"template": "bars", "params": {"rows": [{"label": "а", "value": 2}, {"label": "б", "value": 8}]}})
    assert bars["frames"][1]["params"]["max"] >= 8  # шкала не прыгает между кадрами
    assert with_reveal({"template": "curves", "params": {}}) == {"template": "curves", "params": {}}
    assert plain_note("В md в p488.02, p488.03 и (23.39) вместо κt распознано kt.") == "В файле главы вместо κt распознано kt."
    assert "p202" not in plain_note("В p202.02 «на локальных поднятых» — вероятно, пропуск.")
    assert plain_note("В формуле накопленной добычи (p485.04) стоит √t.") == "В формуле накопленной добычи стоит √t."


def test_auto_spec_for_unknown_chapter():
    """Глава без готовых спецификаций получает рисунок по содержанию карточки."""
    cfg = Config()
    sg = load_styleguide(cfg)
    node = json.loads((cfg.schemas_dir / "example_layer.json").read_text(encoding="utf-8"))["nodes"][0]
    for extra in (
        {},
        {"steps": [{"text": f"Шаг {k}. Сравнить кривые."} for k in range(1, 5)]},
        {"steps": [], "decision_on_data": {"features": ["сопротивление"], "rule": "Если больше порога, коллектор.", "conclusion": "Коллектор.", "counterexample": "Плотный пласт."}},
    ):
        spec = auto_spec({**node, **extra})
        assert spec["label"] == "схема" and spec["title"]
        for width in (visual.W, visual.W_PHONE):
            with visual.canvas_width(width):
                ET.fromstring(visual.render(spec, sg))
    assert visual.W == 560


def test_display_fixes_do_not_touch_quotes():
    cfg = Config()
    ocr = OcrFix(cfg.domain_dir, "basniev_gl23-24")
    assert ocr.tex(r"p(r,t) = p_\kappa - Q") == r"p(r,t) = p_{\text{к}} - Q"
    assert ocr.note(493, r"$$\partial p / \partial r = Q_0 \mu / (2 \pi k r).$$")
    assert clean_tex("$$a = b, \\quad (23.19)$$\n482") == r"a = b, \quad (23.19)"
    assert "math-tex" in str(rich("d_{c} = d_{a} при насыщении")) and "\u00a0%" in str(rich("17 %"))
