"""Тесты генератора курса, иллюстраций, Volve, паспортов роликов и вёрстки 390/1280 px."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import jsonschema

from kurs.config import ROOT, Config
from kurs.course import build_course, verify_course_dir
from kurs.datasets import load_volve_slice
from kurs.units import build_units, cut_chapter
from kurs.verify import Source
from kurs.video_spec import build_training_specs, verify_passport_sources


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

    out_dir = tmp_path / "course_out"
    build_course(cfg, ch, ex_layer, out_dir=out_dir)
    errs = verify_course_dir(cfg, ch, out_dir)
    assert errs == [], f"Ошибки приёмки курса: {errs}"

    html_text = (out_dir / "index.html").read_text(encoding="utf-8")
    assert "перерисовано по рис. 69, стр. 98" in html_text
    assert "Основываясь на результатах решения предыдущей задачи" in html_text
    assert "Подтянуто к задаче (задача 100, стр. 104)" in html_text
    assert "Volve 15/9-F-1 B" in html_text

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
