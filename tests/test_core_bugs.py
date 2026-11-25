"""Тесты на 5 известных багов каркаса: страницы в блоках модели, задачи, числа со знаком/единицей/условием, покрытие, незнакомая EN-глава."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from kurs.__main__ import main as cli_main
from kurs.config import Config, slugify_id
from kurs.units import build_units, cut_chapter, detect_language, load_pages, load_units
from kurs.verify import check_coverage, check_layer, numbers


def _make_node(
    nid: str,
    order: int,
    page: int,
    claim_text: str,
    quote: str,
    *,
    unit_ids: list[str] | None = None,
    terms_introduced: list[str] | None = None,
    terms_used: list[str] | None = None,
    layer_main: str = "Основная мысль.",
) -> dict:
    node = {
        "id": nid,
        "order": order,
        "title": "Тестовый узел",
        "why_here": {"text": "Для проверки.", "by_model": False},
        "difficulty": {"primary": "И", "secondary": None},
        "threshold": False,
        "pages": [page],
        "layers": {
            "topic": {"text": "Тема", "by_model": False},
            "main": {"text": layer_main, "by_model": False},
            "mechanism": {"text": "Механизм.", "by_model": False},
            "practice": {"text": "Практика.", "by_model": False},
            "nuances": {"text": "Нюансы.", "by_model": False},
            "deeper": [],
        },
        "claims": [
            {
                "id": f"{nid}#c1",
                "text": claim_text,
                "kind": "observation",
                "page_pdf": page,
                "page_print": None,
                "quote": quote,
                "source_kind": "text",
                "by_model": False,
            }
        ],
        "links": [],
        "misconception": None,
        "data_meaning": {
            "from_chapter": {"text": "Следствие для данных.", "by_model": False},
            "breaks": {"text": "Где ломается.", "by_model": False},
            "hypotheses": [],
        },
        "it_analogy": {"text": "Аналогия.", "breaks_where": "Граница.", "by_model": False},
        "now_practice": None,
        "inefficiency_lens": None,
        "terms_introduced": terms_introduced or [],
        "terms_used": terms_used or [],
        "needs_scan": [],
        "decision_on_data": None,
    }
    if unit_ids is not None:
        node["unit_ids"] = unit_ids
    return node


def _full_coverage(ch_id: str, units, target_nid: str) -> dict:
    return {
        "chapter": ch_id,
        "rows": [
            {
                "unit_id": u.id,
                "kind": u.kind,
                "page_pdf": u.page_pdf,
                "excerpt": u.excerpt or "фрагмент",
                "node_ids": [target_nid] if u.kind != "figure" or not u.model_description else [],
                "reason": "описание рисунка от модели (исключено из корпуса цитат)" if u.model_description else None,
            }
            for u in units
        ],
    }


def test_bug1_pages_inside_model_blocks_and_no_leaks():
    cfg = Config()
    for cid in cfg.chapters:
        ch = cfg.chapter(cid)
        cut_chapter(cfg, ch)
        units = build_units(cfg, ch)
        leaked = [u for u in units if not u.model_description and ("━━━" in u.text or "KEYWORDS:" in u.text)]
        assert not leaked, f"Утечка модельного блока в {cid}: {[u.id for u in leaked]}"

    lat = cfg.chapter("latyshova_gl5_par17-20")
    pages = load_pages(lat)
    units = load_units(lat)
    assert 103 in pages and 104 in pages
    assert "эффективную толщину пласта, если общая толщина пачки 28м" in pages[103]
    assert "Определить эффективную толщину 18-метровой толщи" in pages[104]
    assert any(u.page_pdf == 104 and not u.model_description for u in units)


def test_bug2_latyshova_tasks_numbers_and_ocr():
    cfg = Config()
    lat = cfg.chapter("latyshova_gl5_par17-20")
    units = load_units(lat)
    tasks = [u for u in units if u.kind == "task"]
    assert [u.task_number for u in tasks] == [str(n) for n in range(86, 109)]
    by_num = {u.task_number: u for u in tasks}
    for n in ("96", "97", "98", "99"):
        assert by_num[n].page_pdf == 102
    assert by_num["99"].pages == [102, 103]
    for n in ("100", "101", "102"):
        assert by_num[n].page_pdf == 104
    assert by_num["103"].page_pdf == 105
    for n in ("104", "105", "106", "107", "108"):
        assert by_num[n].page_pdf == 108
    assert by_num["101"].ocr_garbled is True
    assert by_num["102"].ocr_garbled is True
    assert by_num["100"].ocr_garbled is False
    assert detect_language(lat.md_path.read_text(encoding="utf-8")) == "ru"


def test_bug3_numbers_sign_unit_condition_and_unit_scope(tmp_path, monkeypatch):
    monkeypatch.setattr("kurs.config.BUILD", tmp_path / "build")
    cfg = Config()
    # Официальный example_layer.json на Латышовой проходит.
    lat = cfg.chapter("latyshova_gl5_par17-20")
    cut_chapter(cfg, lat)
    build_units(cfg, lat)
    ex_path = cfg.schemas_dir / "example_layer.json"
    ex_layer = json.loads(ex_path.read_text(encoding="utf-8"))
    assert [i for i in check_layer(cfg, lat, ex_layer) if i.level == "error"] == []

    # Синтетическая глава для точных проверок знака, единицы, условия и изоляции единицы.
    md = tmp_path / "num_test.md"
    md.write_text(
        "<!-- page 10 -->\n"
        "Поправка составляет +15 °C на глубине менее 5% глинистости при толщине 15 м.\n\n"
        "На той же странице для другого режима значение равно -20 °C и 77 м.\n",
        encoding="utf-8",
    )
    ch = cfg.chapter(str(md))
    cut_chapter(cfg, ch)
    units = build_units(cfg, ch)
    u1 = units[0]

    assert numbers("−15 и 7") == {"-15", "7"}
    assert numbers("+15 °C") == {"15"}

    # 1) Подмена знака +15 -> −15 и лишнее однозначное число 7.
    bad_sign = {
        "chapter": ch.id,
        "source": md.name,
        "nodes": [_make_node(f"{ch.prefix}.n1", 1, 10, "Поправка равна −15 и 7.", "Поправка составляет +15 °C на глубине менее 5% глинистости при толщине 15 м.")],
    }
    errs = [i for i in check_layer(cfg, ch, bad_sign) if i.level == "error"]
    assert any("-15" in e.msg and "7" in e.msg for e in errs)

    # 2) Подмена -20 на +20.
    bad_neg = {
        "chapter": ch.id,
        "source": md.name,
        "nodes": [_make_node(f"{ch.prefix}.n1", 1, 10, "Значение равно 20 °C.", "значение равно -20 °C и 77 м.")],
    }
    errs = [i for i in check_layer(cfg, ch, bad_neg) if i.level == "error"]
    assert any("20" in e.msg for e in errs)

    # 3) Число 77 с той же страницы, но из другой единицы.
    bad_cross_unit = {
        "chapter": ch.id,
        "source": md.name,
        "nodes": [_make_node(f"{ch.prefix}.n1", 1, 10, "Поправка +15 °C и 77.", "Поправка составляет +15 °C на глубине менее 5% глинистости при толщине 15 м.")],
    }
    errs = [i for i in check_layer(cfg, ch, bad_cross_unit) if i.level == "error"]
    assert any("77" in e.msg for e in errs)

    # 4) Подмена единицы (15 км вместо 15 м/°C).
    bad_unit = {
        "chapter": ch.id,
        "source": md.name,
        "nodes": [_make_node(f"{ch.prefix}.n1", 1, 10, "Толщина 15 км.", "Поправка составляет +15 °C на глубине менее 5% глинистости при толщине 15 м.")],
    }
    errs = [i for i in check_layer(cfg, ch, bad_unit) if i.level == "error"]
    assert any("15 км" in e.msg for e in errs)

    # 5) Переворот условия (> 5% вместо менее 5%).
    bad_cond = {
        "chapter": ch.id,
        "source": md.name,
        "nodes": [_make_node(f"{ch.prefix}.n1", 1, 10, "Глинистость > 5%.", "Поправка составляет +15 °C на глубине менее 5% глинистости при толщине 15 м.")],
    }
    errs = [i for i in check_layer(cfg, ch, bad_cond) if i.level == "error"]
    assert any("перевёрнуто условие" in e.msg for e in errs)

    # 6) Корректное утверждение проходит без ошибок.
    good = {
        "chapter": ch.id,
        "source": md.name,
        "nodes": [_make_node(f"{ch.prefix}.n1", 1, 10, "Поправка равна 15 °C при глинистости менее 5%.", "Поправка составляет +15 °C на глубине менее 5% глинистости при толщине 15 м.", unit_ids=[u1.id])],
    }
    assert [i for i in check_layer(cfg, ch, good) if i.level == "error"] == []


def test_bug4_bijective_coverage_and_cli_required(tmp_path, monkeypatch):
    monkeypatch.setattr("kurs.config.BUILD", tmp_path / "build")
    cfg = Config()
    md = tmp_path / "cov_test.md"
    md.write_text("<!-- page 5 -->\n# Раздел\n\nПервый абзац про пористость.\n\nВторой абзац про проницаемость.\n", encoding="utf-8")
    ch = cfg.chapter(str(md))
    cut_chapter(cfg, ch)
    units = build_units(cfg, ch)
    nid = f"{ch.prefix}.n1"
    layer = {
        "chapter": ch.id,
        "source": md.name,
        "nodes": [_make_node(nid, 1, 5, "Первый абзац.", "Первый абзац про пористость.")],
    }
    (ch.build_dir / "layer.json").write_text(json.dumps(layer, ensure_ascii=False), encoding="utf-8")

    # Без coverage.json CLI check должен падать с кодом 1.
    rc = cli_main(["check", str(md)])
    assert rc == 1

    cov = _full_coverage(ch.id, units, nid)
    assert [i for i in check_coverage(cfg, ch, cov, layer) if i.level == "error"] == []

    # Дубликат unit_id должен ловиться.
    dup_cov = {"chapter": ch.id, "rows": cov["rows"] + [cov["rows"][0]]}
    assert any("дубликат" in i.msg for i in check_coverage(cfg, ch, dup_cov, layer))

    # Посторонний unit_id должен ловиться.
    extra_cov = {"chapter": ch.id, "rows": cov["rows"] + [{**cov["rows"][0], "unit_id": "p999.99"}]}
    assert any("посторонняя" in i.msg for i in check_coverage(cfg, ch, extra_cov, layer))

    # Пропущенный unit_id должен ловиться.
    miss_cov = {"chapter": ch.id, "rows": cov["rows"][:-1]}
    assert any("не попала" in i.msg for i in check_coverage(cfg, ch, miss_cov, layer))

    # Неверная страница и причина «непоказуемо» должны ловиться.
    bad_page_cov = {"chapter": ch.id, "rows": [{**cov["rows"][0], "page_pdf": 99}, *cov["rows"][1:]]}
    assert any("неверная page_pdf" in i.msg for i in check_coverage(cfg, ch, bad_page_cov, layer))

    bad_reason_cov = {
        "chapter": ch.id,
        "rows": [{**cov["rows"][0], "node_ids": [], "reason": "непоказуемо"}, *cov["rows"][1:]],
    }
    assert any("недопустимая причина" in i.msg for i in check_coverage(cfg, ch, bad_reason_cov, layer))


def test_bug5_unknown_english_chapter_path(tmp_path, monkeypatch):
    monkeypatch.setattr("kurs.config.BUILD", tmp_path / "build")
    en_md = tmp_path / "Unknown (EN) Chapter 01!.md"
    en_md.write_text(
        "<!-- page 12 -->\n"
        "# Reservoir Rock Properties\n\n"
        "Porosity of clean sandstone ranges from 15% to 25% in the reservoir.\n\n"
        "----\n"
        "━━━ FORMAT 6: XY PLOT ━━━\n"
        "Synthetic chart note with fake 999.\n"
        "KEYWORDS: porosity, plot\n\n"
        "| Lithology | Porosity |\n"
        "| --- | --- |\n"
        "| Sandstone | 20% |\n\n"
        "## Problems\n\n"
        "1. Calculate effective porosity for the sandstone bed with 20% total porosity.\n",
        encoding="utf-8",
    )
    cfg = Config()
    ch = cfg.chapter(str(en_md))
    assert ch.id == slugify_id(en_md.stem)
    assert ch.language == "en"
    cut_chapter(cfg, ch)
    units = build_units(cfg, ch)
    kinds = {u.kind for u in units}
    assert {"heading", "paragraph", "figure", "table", "task"} <= kinds
    tasks = [u for u in units if u.kind == "task"]
    assert len(tasks) == 1 and tasks[0].task_number == "1"
    assert not any(not u.model_description and "999" in u.text for u in units)

    nid = f"{ch.prefix}.porosity"
    layer = {
        "chapter": ch.id,
        "source": en_md.name,
        "nodes": [
            _make_node(
                nid,
                1,
                12,
                "Clean sandstone porosity ranges from 15% to 25%.",
                "Porosity of clean sandstone ranges from 15% to 25% in the reservoir.",
                terms_introduced=["porosity"],
            )
        ],
    }
    node_schema = json.loads((cfg.schemas_dir / "node.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(layer, node_schema)
    cov = _full_coverage(ch.id, units, nid)
    assert [i for i in check_layer(cfg, ch, layer, coverage=cov) if i.level == "error"] == []
    assert [i for i in check_coverage(cfg, ch, cov, layer) if i.level == "error"] == []
