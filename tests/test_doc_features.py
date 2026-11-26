"""Новое по архитектурному документу: смысловые проверки, фаззинг, «Закладки», пересборка, задания, происхождение, ролики, метрики.

Все тесты без сети, без модели и без материалов организаторов: глава — маленький md прямо здесь.
"""
from __future__ import annotations

import csv
import json

import pytest

from kurs import config as config_mod
from kurs.claimcheck import CAUGHT_BY, claim_findings, mutations
from kurs.config import Config
from kurs.units import build_units, load_pages

MD = """<!-- page 20 -->
# § 3. Коллекторы и их свойства

Пористость песчаников меняется от 5 до 25 % и зависит от укладки зёрен и цемента.

С увеличением глубины залегания пористость песчаников уменьшается.

Высокая пористость не гарантирует высокую проницаемость, потому что поры бывают не связаны между собой.

<!-- page 21 -->
Как правило, глинистые породы отдают нефть только при большом перепаде давления.

Удельное сопротивление нефтеносных пород выше, чем водоносных, если пористость одинакова.

Граничное значение пористости для коллектора составляет 17 %.

Давление в пласте 25 МПа поддерживается напором краевых вод.
"""

CLAIMS = [
    ("c1", "Пористость песчаников меняется от 5 до 25 % и зависит от укладки зёрен.", "Пористость песчаников меняется от 5 до 25 % и зависит от укладки зёрен и цемента.", 20),
    ("c2", "С ростом глубины залегания пористость песчаников уменьшается.", "С увеличением глубины залегания пористость песчаников уменьшается.", 20),
    ("c3", "Высокая пористость не гарантирует высокую проницаемость.", "Высокая пористость не гарантирует высокую проницаемость, потому что поры бывают не связаны между собой.", 20),
    ("c4", "Как правило, глинистые породы отдают нефть только при большом перепаде давления.", "Как правило, глинистые породы отдают нефть только при большом перепаде давления.", 21),
    ("c5", "Удельное сопротивление нефтеносных пород выше, чем водоносных, если пористость одинакова.", "Удельное сопротивление нефтеносных пород выше, чем водоносных, если пористость одинакова.", 21),
    ("c6", "Граничное значение пористости для коллектора составляет 17 %.", "Граничное значение пористости для коллектора составляет 17 %.", 21),
    ("c7", "Давление в пласте 25 МПа поддерживается напором краевых вод.", "Давление в пласте 25 МПа поддерживается напором краевых вод.", 21),
]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "BUILD", tmp_path / "build")
    for k in ("KURS_LLM_OFFLINE", "KURS_LLM_BUDGET", "KURS_TTS"):
        monkeypatch.delenv(k, raising=False)
    md = tmp_path / "kollektory.md"
    md.write_text(MD, encoding="utf-8")
    cfg = Config()
    ch = cfg.chapter(str(md))
    units = build_units(cfg, ch)
    return cfg, ch, units, tmp_path


def _unit_of(units, quote):
    from kurs.verify import norm

    return next(u for u in units if norm(quote) in norm(u.text))


def make_layer(ch, units, claims=CLAIMS) -> dict:
    nodes = []
    groups = [claims[:4], claims[4:]]
    for i, group in enumerate(groups, 1):
        nid = f"{ch.prefix}.n{i}"
        cl = [
            {"id": f"{nid}#{cid}", "text": t, "kind": "observation", "page_pdf": p, "page_print": None, "quote": q, "source_kind": "text",
             "by_model": True, "needs_scan": False, "unit_id": _unit_of(units, q).id}
            for cid, t, q, p in group
        ]
        nodes.append({
            "id": nid, "order": i, "title": "Пористость и проницаемость" if i == 1 else "Сопротивление и граничные значения",
            "why_here": {"text": "Без этого не понять, где коллектор.", "by_model": True},
            "difficulty": {"primary": "М", "secondary": None}, "threshold": i == 2, "pages": [20, 21], "unit_ids": [c["unit_id"] for c in cl],
            "layers": {"topic": {"text": "т", "by_model": True}, "main": {"text": "Суть шага.", "by_model": True},
                       "mechanism": {"text": "Пояснение.", "by_model": True, "paragraphs": [{"text": "Пояснение.", "by_model": True, "unit_ids": [cl[0]["unit_id"]], "pages": [20]}]},
                       "practice": {"text": "", "by_model": True, "paragraphs": []}, "nuances": {"text": "", "by_model": True, "paragraphs": []}, "deeper": []},
            "steps": [], "conditions": [], "claims": cl, "formulas": [], "variables": [], "links": [],
            "misconception": {"ref": "misc.porosity_eq_perm", "text": "Пористость и проницаемость — одно и то же.", "by_model": True} if i == 1 else None,
            "data_meaning": {"from_chapter": {"text": "Сопротивление и пористость вместе.", "by_model": True}, "breaks": {"text": "", "by_model": True}, "hypotheses": []},
            "it_analogy": {"text": "Как классификатор по одному признаку ошибается на сдвиге данных.", "breaks_where": "", "by_model": True},
            "now_practice": None, "inefficiency_lens": None, "glossary": [], "terms_introduced": [], "terms_used": [], "needs_scan": [],
            "decision_on_data": {"features": ["высокое сопротивление", "пористость выше граничной"], "rule": "Коллектор с нефтью — высокое сопротивление при пористости выше граничной.",
                                 "conclusion": "Пласт является нефтеносным коллектором.", "counterexample": "Плотный известняк тоже даёт высокое сопротивление."} if i == 2 else None,
            "worked_examples": [], "book_tasks": [],
        })
    return {"chapter": ch.id, "nodes": nodes}


# ---------------------------------------------------------------- 1. смысловые проверки
@pytest.mark.parametrize(
    "claim, quote, code",
    [
        ("Высокая пористость гарантирует высокую проницаемость.", CLAIMS[2][2], "negation_dropped"),
        ("С увеличением глубины пористость песчаников увеличивается.", CLAIMS[1][2], "direction_flip"),
        ("Удельное сопротивление нефтеносных пород ниже, чем водоносных, если пористость одинакова.", CLAIMS[4][2], "direction_flip"),
        ("Глинистые породы отдают нефть только при большом перепаде давления.", CLAIMS[3][2], "assumption_as_fact"),
        ("Удельное сопротивление нефтеносных пород выше, чем водоносных.", CLAIMS[4][2], "condition_dropped"),
        ("Пористость песчаников всегда меняется от 5 до 25 %.", CLAIMS[0][2], "overgeneralization"),
        ("Граничное значение пористости для коллектора составляет 71 %.", CLAIMS[5][2], "number_not_in_source"),
    ],
)
def test_semantic_checks_catch_errors(claim, quote, code):
    assert code in {f.code for f in claim_findings(claim, quote)}


def test_correct_claims_raise_no_alarm():
    for _, text, quote, _ in CLAIMS:
        assert claim_findings(text, quote) == [], text


def test_negation_in_other_clause_is_not_an_alarm():
    quote = "Пласт отдаёт нефть, но не отдаёт воду при малом перепаде."
    assert not [f for f in claim_findings("Пласт отдаёт нефть.", quote) if f.code.startswith("negation")]


def test_every_mutation_is_caught_by_its_check():
    for _, text, quote, _ in CLAIMS:
        for m in mutations(text, seed=text):
            codes = {f.code for f in claim_findings(m.text, quote)}
            assert codes & CAUGHT_BY[m.kind], (m.kind, m.text, codes)


# ---------------------------------------------------------------- 2. покрытие: закрытый список причин, не преподано
def test_reason_codes():
    from kurs.verify import coded_reason, reason_code

    assert reason_code("номер страницы") == "page_furniture"
    assert reason_code("toc: оглавление") == "toc"
    assert reason_code("список литературы к главе") == "bibliography"
    assert reason_code("просто так") is None
    assert coded_reason("колонтитул") == "page_furniture: колонтитул"
    assert coded_reason("page_furniture: колонтитул") == "page_furniture: колонтитул"


def test_mapped_but_untaught_and_free_reason(env, monkeypatch):
    from kurs import verify

    cfg, ch, units, _ = env
    monkeypatch.setattr(verify.jsonschema, "Draft202012Validator", lambda s: type("V", (), {"iter_errors": lambda self, d: iter(())})())
    monkeypatch.setattr(verify, "load_schema", lambda cfg, name: {})
    layer = make_layer(ch, units, CLAIMS[:6])  # единица последнего утверждения не преподана
    taught = verify.taught_units(layer["nodes"][0])
    spare = _unit_of(units, CLAIMS[6][2])
    rows = []
    for u in units:
        owner = next((n["id"] for n in layer["nodes"] if u.id in verify.taught_units(n)), None)
        rows.append({"unit_id": u.id, "kind": u.kind, "page_pdf": u.page_pdf, "excerpt": "", "node_ids": [owner] if owner else [],
                     "reason": None if owner else "просто так"})
    free = _unit_of(units, CLAIMS[5][2])
    for r in rows:
        if r["unit_id"] == spare.id:
            r.update(node_ids=[layer["nodes"][0]["id"]], reason=None)
        if r["unit_id"] == free.id:
            r.update(node_ids=[], reason="просто так")
    issues = verify.check_coverage(cfg, ch, {"chapter": ch.id, "rows": rows}, layer)
    assert any("mapped_but_untaught" in i.msg and i.where == spare.id for i in issues)
    assert any("закрытого списка" in i.msg for i in issues)
    assert all(i.level == "warn" for i in issues if "mapped_but_untaught" in i.msg or "закрытого" in i.msg)
    strict = verify.check_coverage(cfg, ch, {"chapter": ch.id, "rows": rows}, layer, strict=True)
    assert any(i.level == "error" and "mapped_but_untaught" in i.msg for i in strict)
    assert taught


# ---------------------------------------------------------------- 3. фаззинг
def test_fuzz_reports_recall_and_no_background(env):
    from kurs.fuzz import fuzz_chapter

    cfg, ch, units, _ = env
    (ch.build_dir / "layer.json").write_text(json.dumps(make_layer(ch, units), ensure_ascii=False), encoding="utf-8")
    rep, md = fuzz_chapter(cfg, ch)
    assert rep["baseline_flagged"] == 0 and rep["injected"] >= 20
    assert rep["recall"] >= 0.9, rep["by_kind"]
    assert {"page_shift", "quote_swap", "negation", "number_not_in_source"} <= set(rep["by_kind"])
    assert "Внесено ошибок" in md.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 5. пересборка по хэшам
def test_plan_finds_stale_tasks_and_videos(env):
    from kurs.pipeline import plan, plan_text, write_manifest

    cfg, ch, units, _ = env
    layer = make_layer(ch, units)
    n1, n2 = layer["nodes"]
    (ch.build_dir / "tasks.json").write_text(json.dumps({"tasks": [
        {"id": "t1", "step_id": n1["id"], "claim_ids": [n1["claims"][0]["id"]]},
        {"id": "t2", "step_id": n2["id"], "claim_ids": [n2["claims"][1]["id"]]},
    ]}), encoding="utf-8")
    (ch.build_dir / "videos.json").write_text(json.dumps({"videos": [{"id": "v2", "node_ids": [n2["id"]]}]}), encoding="utf-8")
    write_manifest(cfg, ch, layer)
    assert plan(cfg, ch, layer)["changed_claims"] == []

    n2["claims"][1]["text"] = n2["claims"][1]["text"].replace("17", "18")
    p = plan(cfg, ch, layer)
    assert p["changed_claims"] == [n2["claims"][1]["id"]]
    assert p["stale_tasks"] == ["t2"] and p["stale_videos"] == ["v2"] and p["course"]
    assert "t2" in plan_text(p)
    assert plan(cfg, ch, layer, only_node=n1["id"])["stale_tasks"] == []

    # Ролик заменён тем же id (другой голос, новые подписи) — слой тот же, а курс пересобирается
    n2["claims"][1]["text"] = n2["claims"][1]["text"].replace("18", "17")
    assert not plan(cfg, ch, layer)["course"]
    (ch.build_dir / "videos.json").write_text(json.dumps({"videos": [{"id": "v2", "node_ids": [n2["id"]], "cues": [{"text": "новые"}]}]}), encoding="utf-8")
    p = plan(cfg, ch, layer)
    assert p["course"] and p["videos_changed"] and not p["changed_claims"]
    assert "заменены ролики" in plan_text(p)


def test_plan_rebuilds_course_when_chapter_visuals_change(env):
    """Перерисовали рисунки главы (kurs gen --only visual) — rebuild пересобирает курс, хотя слой не менялся."""
    from kurs.pipeline import plan, write_manifest

    cfg, ch, units, _ = env
    layer = make_layer(ch, units)
    (ch.build_dir / "visual.json").write_text(json.dumps({"nodes": {}}), encoding="utf-8")
    write_manifest(cfg, ch, layer)
    assert not plan(cfg, ch, layer)["course"]
    (ch.build_dir / "visual.json").write_text(json.dumps({"nodes": {layer["nodes"][0]["id"]: {"template": "flow"}}}), encoding="utf-8")
    assert plan(cfg, ch, layer)["course"]


# ---------------------------------------------------------------- 6. задания: дистракторы из искажений шага
def test_single_task_options_are_caught_distortions(env):
    from kurs.rules import distractors, single_task, transfer_task

    _, ch, units, _ = env
    layer = make_layer(ch, units)
    n1, n2 = layer["nodes"]
    t = single_task(n1, {"misc.porosity_eq_perm": "Пористость и проницаемость — одно и то же."})
    assert t and t["answer_list"] == ["a"] and 3 <= len(t["options"]) <= 5
    good = t["options"][0]["text"]
    claim = next(c for c in n1["claims"] if c["text"].rstrip(".") in good or good.rstrip(".") in c["text"])
    for o in t["options"][1:]:
        if o["misconception"]:
            continue
        assert claim_findings(o["text"], claim["quote"]), o["text"]
    assert all(w.lower() not in {c["text"].lower() for c in n1["claims"]} for w, _ in distractors(n1, claim))
    tr = transfer_task(n2, layer["nodes"])
    assert tr and tr["kind"] == "transfer" and tr["options"][0]["text"].startswith("Пласт является")


# ---------------------------------------------------------------- 7. происхождение
def test_provenance_badges():
    from kurs import provenance as pv

    assert pv.of_claim({"kind": "observation"}) == "stated" and pv.of_claim({"kind": "inference"}) == "inferred"
    assert pv.of_part({"by_model": True, "hypothesis": True}) == "hypothesis"
    assert pv.of_part({"by_model": True}) == "explanatory" and pv.of_part({"by_model": False}) == "stated"
    assert pv.label(False) == "из книги" and pv.label(True) == "пояснение ИИ" and pv.label("external") == "внешнее"


# ---------------------------------------------------------------- 8. озвучка
@pytest.mark.parametrize(
    "text, said",
    [
        ("от 5 до 25 %", "от пяти до двадцати пяти процентов"),
        ("давление 1,5 МПа", "давление одна целая пять десятых мегапаскаля"),
        ("глубина 21 м", "глубина двадцать один метр"),
        ("за 1 с", "за одну секунду"),
        ("не более 0,6 %", "не более нуля целых шести десятых процента"),
        ("2214 м", "две тысячи двести четырнадцать метров"),
    ],
)
def test_numbers_are_spoken(text, said):
    from kurs.tts import say_numbers

    assert say_numbers(text) == said


def test_speech_text_dictionaries_and_wer():
    from kurs.tts import speech_text, wer

    cfg = Config()
    s = speech_text("По данным ГИС пористость 17 %.", cfg.domain_dir)
    assert "гэ-и-э́с" in s and "семнадцать процентов" in s and "́" in s
    assert wer("пористость от 5 до 25 процентов", "пористость от пяти до двадцати пяти процентов") == 0.0
    assert wer("пористость меняется", "проницаемость растёт") == 1.0


# ---------------------------------------------------------------- 9. ролики: сценарий из проверенного слоя
def test_video_script_from_layer_is_checked(env):
    from kurs.video import build_script, check_script, passport, pick_nodes, scene_coverage

    cfg, ch, units, _ = env
    layer = make_layer(ch, units)
    picks = pick_nodes(layer, None)
    assert set(picks) == {"shorts", "long"} or picks
    node = layer["nodes"][0]
    sc = build_script(ch, node, "shorts")
    assert check_script(ch, node, sc) == []
    kinds = [s["kind"] for s in sc["scenes"]]
    assert kinds[0] == "hook" and "predict" in kinds and kinds[-1] == "transfer"
    assert all(len(s["on_screen"].split()) <= 8 for s in sc["scenes"])
    assert any(s.get("pause", 0) >= 1.5 for s in sc["scenes"])
    pas = passport(cfg, ch, node, sc, None, "pending_render")
    assert pas["audio_check"]["status"] == "pending_render" and pas["sources"]
    assert all(s["quote"] for s in pas["sources"])
    cov = scene_coverage(layer, ch)
    assert cov["nodes"] == 2 and cov["shorts_ready"] == 1.0


def test_video_script_rejects_numbers_in_model_links(env):
    from kurs.video import build_script, check_script

    _, ch, units, _ = env
    layer = make_layer(ch, units)
    node = layer["nodes"][0]
    sc = build_script(ch, node, "shorts")
    sc["scenes"][0]["speech_src"] = "Часто думают, что пористость 40 %."
    assert any("связка модели с числами" in e for e in check_script(ch, node, sc))


# ---------------------------------------------------------------- 10. метрики
def test_metrics_csv_and_manifest(env):
    from kurs.metrics import FIELDS, manifest_section, write_metrics

    cfg, *_ = env
    row = {k: "" for k in FIELDS} | {"chapter": "x", "unit_coverage": 1.0, "quote_validity": 1.0, "numeric_traceability": 0.98, "tasks": 20, "task_types": 5}
    p = write_metrics(cfg, [row])
    write_metrics(cfg, [row | {"tasks": 21}])
    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    assert len(rows) == 1 and rows[0]["tasks"] == "21"
    md = manifest_section([row])
    assert "100.0%" in md and "98.0%" in md and "| x |" in md


# ---------------------------------------------------------------- 11. доменный пакет бурения
def test_drilling_pack_matches_geology_structure(monkeypatch):
    from kurs.config import ROOT
    from kurs.tts import load_dicts, speech_text

    geo, drill = ROOT / "domain" / "geology", ROOT / "domain" / "drilling"
    for name in ("glossary.md", "misconceptions.json", "physics_checklist.json", "it_bridges.md", "styleguide.json", "pronunciation.md", "stress_dictionary.md"):
        assert (drill / name).exists(), name
    misc = json.loads((drill / "misconceptions.json").read_text(encoding="utf-8"))
    pc = json.loads((drill / "physics_checklist.json").read_text(encoding="utf-8"))
    assert len(misc) >= 12 and all(m["id"].startswith("misc.") and m["text"] for m in misc)
    assert len({m["id"] for m in misc}) == len(misc)
    assert len(pc) >= 15 and all(x["id"].startswith("pc.") and x["group"] and x["item"] for x in pc)
    assert {"pc.depth_down", "pc.circulation_direction", "pc.window_order", "pc.plausible_numbers", "pc.label_matches"} <= {x["id"] for x in pc}
    sg_geo = json.loads((geo / "styleguide.json").read_text(encoding="utf-8"))
    sg = json.loads((drill / "styleguide.json").read_text(encoding="utf-8"))
    assert set(sg_geo) <= set(sg) and "drilling" in sg and "pressure_lines" in sg  # движок рисунков читает те же ключи
    gl = (drill / "glossary.md").read_text(encoding="utf-8")
    for term in ("ЭЦД", "ГНВП", "Поглощение", "Окно бурения", "КНБК", "Долото", "Буровой раствор", "ROP", "WOB", "Каверна", "Цементирование"):
        assert term in gl, term
    bridges = (drill / "it_bridges.md").read_text(encoding="utf-8")
    assert "MWD/LWD" in bridges and "WITSML" in bridges and "ГНВП" in bridges and "прихват" in bridges
    abbr, stress = load_dicts(drill)
    assert dict(abbr)["ЭЦД"] == "э-цэ-дэ́" and stress["долото"] == "долото́"
    assert all("́" in v for v in stress.values() if "ё" not in v)
    s = speech_text("ЭЦД при бурении выше плотности раствора 1,2 г/см³.", drill)
    assert "э-цэ-дэ́" in s and "буре́нии" not in s and "одна целая две десятых грамма на кубический сантиметр" in s

    monkeypatch.setenv("KURS_DOMAIN_PACK", "drilling")
    assert Config().domain_dir == drill
    cfg = Config()
    cfg.use_domain("geology")
    assert cfg.domain_dir == geo
