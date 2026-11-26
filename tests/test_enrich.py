"""Дополнение слоя: пустые поля заполняются, выдуманное число и чужая цитата не проходят, судья снимает спорное."""
from __future__ import annotations

import json

import pytest

from kurs import config as config_mod
from kurs.config import Config
from kurs.llm import LLM, Reply, Usage

MD = """<!-- page 20 -->
# § 3. Коллекторы

Пористость песчаников меняется от 5 до 25 % и зависит от укладки зёрен.

Палетку накладывают на бумажную диаграмму вручную и читают сопротивление пласта.

Высокая пористость не гарантирует высокую проницаемость, потому что поры бывают не связаны.
"""


def para(text, uid):
    return {"text": text, "by_model": True, "paragraphs": [{"text": text, "by_model": True, "unit_ids": [uid], "pages": [20]}]}


@pytest.fixture()
def chapter(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "BUILD", tmp_path / "build")
    monkeypatch.delenv("KURS_LLM_OFFLINE", raising=False)
    md = tmp_path / "t3.md"
    md.write_text(MD, encoding="utf-8")
    cfg = Config()
    if not (cfg.schemas_dir / "node.schema.json").exists():
        pytest.skip("нет схем организаторов (KURS_INPUT_DIR)")
    ch = cfg.chapter(str(md))
    from kurs.units import build_units, cut_chapter

    cut_chapter(cfg, ch)
    us = {u.text[:9]: u for u in build_units(cfg, ch)}
    a, b, c = us["Пористост"], us["Палетку н"], us["Высокая п"]
    node = {"id": f"{ch.prefix}.poro", "order": 1, "title": "Пористость и проницаемость песчаника", "title_book": "§ 3", "difficulty": {"primary": "И", "secondary": None},
            "pages": [20], "unit_ids": [a.id, b.id, c.id], "links": [], "terms_introduced": [], "terms_used": [], "glossary": [], "needs_scan": [],
            "layers": {"topic": {"text": "Пористость", "by_model": True}, "main": {"text": "Пористость меняется. Она зависит от укладки.", "by_model": True},
                       "mechanism": para("Пористость песчаников меняется от 5 до 25 %.", a.id), "practice": para("Палетку накладывают вручную.", b.id),
                       "nuances": {"text": "", "by_model": True, "paragraphs": []}, "deeper": []},
            "claims": [{"id": f"{ch.prefix}.poro#c1", "text": "Пористость песчаников меняется от 5 до 25 %.", "kind": "observation", "page_pdf": 20, "page_print": None,
                        "quote": "Пористость песчаников меняется от 5 до 25 %", "source_kind": "text", "by_model": True, "needs_scan": False, "unit_id": a.id}],
            "conditions": [], "formulas": [], "variables": [], "worked_examples": [], "book_tasks": [], "misconception": None, "now_practice": None, "inefficiency_lens": None}
    (ch.build_dir / "layer.json").write_text(json.dumps({"chapter": ch.id, "source": f"{ch.id}.md", "nodes": [node]}, ensure_ascii=False), encoding="utf-8")
    rows = [{"unit_id": u.id, "kind": u.kind, "page_pdf": 20, "excerpt": u.excerpt, "node_ids": [node["id"]] if u.kind != "heading" else [],
             "reason": None if u.kind != "heading" else "heading: заголовок"} for u in us.values()]
    (ch.build_dir / "coverage.json").write_text(json.dumps({"chapter": ch.id, "rows": rows}, ensure_ascii=False), encoding="utf-8")
    return cfg, ch, tmp_path, node["id"], c.id


def fake(cfg, tmp, item, keep=lambda f: True):
    def transport(model, system, user, schema, effort, max_tokens, images=None):
        if "items" in schema["properties"]:
            return Reply(json.dumps({"items": [item]}, ensure_ascii=False), model, Usage(input=10, output=10))
        view = json.loads(user[user.index("Узлы и дополнения:\n") + len("Узлы и дополнения:\n"):])
        out = [{"node": x["node"]["id"], "field": f, "why": ".", "keep": keep(f), "fix": ""} for x in view for f in x["additions"]]
        return Reply(json.dumps({"rulings": out}, ensure_ascii=False), model, Usage(input=10, output=10))

    return LLM(cfg, transport=transport, cache_dir=tmp / "cache", log_path=tmp / "u.jsonl", budget=1.0)


def item(nid, uid, **kw):
    base = {"node": nid, "misconception_ref": "", "misconception_text": "Новичок думает, что пористый песчаник всегда проницаем. Это неверно: поры бывают не связаны.",
            "now_practice": "Сейчас палетку заменяет расчёт на компьютере, а смысл сопоставления прежний.",
            "inefficiency": ["диаграмма и палетка", "накладывает палетку вручную", "выбор совпадения", "в главе не указано", "сдвиг палетки"],
            "taught": [{"unit_id": uid, "place": "nuances", "paragraph": "Высокая пористость не гарантирует высокую проницаемость: поры бывают не связаны.",
                        "claim": "Высокая пористость не гарантирует высокую проницаемость.", "claim_kind": "inference",
                        "quote": "Высокая пористость не гарантирует высокую проницаемость"}]}
    return {**base, **kw}


def test_fills_empty_fields_and_teaches_lost_unit(chapter):
    from kurs.enrich import enrich_layer

    cfg, ch, tmp, nid, uid = chapter
    rep = enrich_layer(cfg, ch, fake(cfg, tmp, item(nid, uid)), log=lambda *_: None)
    n = json.loads((ch.build_dir / "layer.json").read_text(encoding="utf-8"))["nodes"][0]
    assert n["misconception"]["text"].startswith("Новичок") and n["now_practice"]["hypothesis"] is True
    assert n["inefficiency_lens"]["manual_steps"] == "накладывает палетку вручную"
    assert n["claims"][-1]["id"].endswith("#c2") and n["claims"][-1]["unit_id"] == uid
    assert rep["untaught_after"] == rep["untaught_before"] - 1
    assert "не связаны" in n["layers"]["nuances"]["text"]


def test_invented_number_and_foreign_quote_do_not_pass(chapter):
    from kurs.enrich import enrich_layer

    cfg, ch, tmp, nid, uid = chapter
    bad = item(nid, uid, misconception_text="Новичок думает, что пористость всегда 40 %. Это неверно.", now_practice="Сейчас прибор работает в 10 раз быстрее и точнее прежнего.",
               inefficiency=["данные", "шаги", "суждение", "2 часа на пласт", "ошибки"])
    bad["taught"][0]["quote"] = "Такой фразы в главе нет совсем"
    rep = enrich_layer(cfg, ch, fake(cfg, tmp, bad), log=lambda *_: None)
    n = json.loads((ch.build_dir / "layer.json").read_text(encoding="utf-8"))["nodes"][0]
    assert rep["added"] == {} and n["misconception"] is None and n["now_practice"] is None and len(n["claims"]) == 1


def test_judge_drops_field(chapter):
    from kurs.enrich import enrich_layer

    cfg, ch, tmp, nid, uid = chapter
    rep = enrich_layer(cfg, ch, fake(cfg, tmp, item(nid, uid), keep=lambda f: f != "now_practice"), log=lambda *_: None)
    n = json.loads((ch.build_dir / "layer.json").read_text(encoding="utf-8"))["nodes"][0]
    assert n["now_practice"] is None and n["misconception"] and rep["dropped"] == 1


def test_scraps_leave_coverage_with_coded_reason():
    from kurs.enrich import scrap_reason
    from kurs.verify import reason_code

    for text, rep in (("200\nа", 1), ("б", 1), ("Уровень моря", 4), ("или", 1), ("откуда", 1), ("Таким образом, в результате получим", 1),
                      ("Начальные и граничные условия задачи следующие:", 1),
                      ("Плоскорадиальный неустановившийся фильтрационный поток упругой жидкости.", 1),
                      ("Определение эффективной толщины \\( h \\) продуктивного коллектора.", 1)):
        why = scrap_reason(text, rep)
        assert why and reason_code(why), text
    for text in ("где $C_1$ – постоянная интегрирования.", "Пористость растёт с глубиной.", "Нефть легче воды.", "Порог равен 17 %."):
        assert scrap_reason(text, 1) is None, text
