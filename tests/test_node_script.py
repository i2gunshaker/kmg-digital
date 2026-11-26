"""Сценарий ролика по узлу: проверки формы, запасной сценарий по правилам, перенос правки факта без модели."""
from __future__ import annotations

import json

from kurs import node_script as ns

C = [
    "Гидростатическое давление создаёт неподвижный столб бурового раствора.",
    "Давление растёт с вертикальной глубиной скважины, а не с длиной ствола.",
    "Чем тяжелее раствор, тем выше давление на забое.",
    "Давление раствора должно быть выше порового давления пласта.",
    "Давление раствора должно быть ниже давления гидроразрыва пласта.",
    "Формула справедлива только для статического столба.",
    "При циркуляции к давлению добавляются потери на трение в затрубье.",
    "На глубине 1000 м столб раствора плотностью 1200 кг/м3 даёт около 11,8 МПа.",
]


def node(claims=C) -> dict:
    return {
        "id": "d.hydro", "title": "Гидростатическое давление", "unit_ids": ["p001.01"], "difficulty": {"primary": "формула"},
        "layers": {
            "main": {"text": "Столб раствора давит на забой."},
            "mechanism": {"text": "Давление равно плотности на ускорение свободного падения и на глубину. Глубина берётся по вертикали."},
            "nuances": {"text": "При циркуляции добавляются потери на трение. Расчёт по длине ствола завышает давление."},
        },
        "claims": [{"id": f"d.hydro#c{i}", "text": t, "quote": t, "page_pdf": 3} for i, t in enumerate(claims, 1)],
        "data_meaning": {"from_chapter": {"text": "Для расчёта берите вертикальную глубину из инклинометрии. Длина по стволу завышает давление."}},
    }


def line(text, kind="mechanism", claim="c1", on_screen="Давление растёт с глубиной", frame=1):
    return {"kind": kind, "text": text, "on_screen": on_screen, "claim": claim, "frame": frame}


def test_line_checks_catch_formulas_letters_meta_and_numbers():
    n = node()
    claims, src = ns.claim_map(n), ns.card(n)
    assert ns.line_problems(line("Давление растёт с вертикальной глубиной скважины."), claims, src, 3) == []
    probs = " | ".join(ns.line_problems(line("Давление p = ρ g h растёт."), claims, src, 3))
    assert "знак" in probs and "обозначение" in probs
    assert any("обозначение" in p for p in ns.line_problems(line("Объём V0 растёт за время dt."), claims, src, 3))
    assert ns.line_problems(line("Кривые GR и ECD растут с вертикальной глубиной."), claims, src, 3) == []
    assert any("карточк" in p for p in ns.line_problems(line("В карточке сказано, что давление растёт."), claims, src, 3))
    assert any("число" in p for p in ns.line_problems(line("На глубине 1000 м давление около 25 МПа."), claims, src, 3))
    assert any("кадра" in p for p in ns.line_problems(line("Давление растёт с вертикальной глубиной скважины.", frame=5), claims, src, 3))
    assert any("слов" in p for p in ns.line_problems(line(" ".join(["слово"] * 20)), claims, src, 3))


def test_rules_script_has_the_video_standard_shape():
    n = node()
    doc = ns.script_by_rules(n, "9:16", 3)
    assert ns.script_problems(doc, n, "9:16", 3) == []
    kinds = [x["kind"] for x in doc["lines"]]
    assert kinds[0] == "hook" and kinds[-1] == "transfer" and doc["lines"][-1]["text"].endswith("?")
    i = kinds.index("predict")
    assert kinds.count("predict") == 1 and kinds[i + 1] == "answer" and i >= 3
    assert "limit" in kinds and "data" in kinds
    # всё, что звучит, — из узла: каждое число есть в карточке
    assert all(not ns.check_quantities_against_source(x["text"], [ns.card(n)]) for x in doc["lines"])


def test_script_checks_order_and_spoiler():
    n = node()
    doc = ns.script_by_rules(n, "9:16", 3)
    swapped = json.loads(json.dumps(doc))
    swapped["lines"][0], swapped["lines"][-1] = swapped["lines"][-1], swapped["lines"][0]
    probs = ns.script_problems(swapped, n, "9:16", 3)
    assert any("hook" in p for p in probs) and any("transfer" in p for p in probs)
    spoil = json.loads(json.dumps(doc))
    kinds = [x["kind"] for x in spoil["lines"]]
    spoil["lines"][1] = dict(spoil["lines"][kinds.index("answer")], kind="mechanism")
    assert any("ответ прозвучал" in p for p in ns.script_problems(spoil, n, "9:16", 3))


def test_number_swaps_and_swap():
    assert ns.number_swaps("около 11,8 МПа на 1000 м", "около 12,4 МПа на 1000 м") == {"11,8": "12,4"}
    assert ns.number_swaps("около 11,8 МПа", "примерно 12,4 МПа") == {}
    assert ns.number_swaps("одинаково 5 м", "одинаково 5 м") == {}
    assert ns._swap("Около 11.8 МПа, а не 111,8 и не 11,85.", {"11,8": "12,4"}) == "Около 12,4 МПа, а не 111,8 и не 11,85."


def model_doc(n: dict) -> dict:
    doc = ns.script_by_rules(n, "9:16", 3)
    doc["lines"].insert(4, line("Тысяча метров раствора дают около 11,8 МПа.", claim="c8", on_screen="Около 11,8 МПа"))
    doc["by"] = "model"
    return {**doc, "snapshot": ns.snapshot(n)}


def test_patch_carries_number_and_wording_edits_without_model():
    old = node()
    doc = model_doc(old)
    new_claims = list(C)
    new_claims[7] = new_claims[7].replace("11,8", "12,4")
    new_claims[5] = "Формула верна только для неподвижного столба раствора."
    patched, log = ns.patch_script(doc, node(new_claims), 3)
    texts = [x["text"] for x in patched["lines"]]
    assert "Тысяча метров раствора дают около 12,4 МПа." in texts
    assert any(x["on_screen"] == "Около 12,4 МПа" for x in patched["lines"])
    assert "Формула верна только для неподвижного столба раствора." in texts
    assert not any("11,8" in t for t in texts) and len(log) >= 2


def test_patch_drops_line_of_removed_claim():
    doc = model_doc(node())
    patched, log = ns.patch_script(doc, node(C[:7]), 3)
    assert not any(x["claim"] == "c8" for x in patched["lines"]) and any("снята" in x for x in log)


def test_get_script_saves_reuses_and_rebuilds(tmp_path):
    class Chapter:
        id = "demo"
        build_dir = tmp_path / "demo"

    ch, n = Chapter(), node()
    a = ns.get_script(None, ch, n, "9:16", ["кадр"], "Схема", log=lambda *_: None)
    saved = ch.build_dir / "scripts" / "d_hydro_9x16.json"
    assert saved.exists() and a["by"] == "rules" and a["node_hash"] == ns.node_hash(n, "9:16")
    assert ns.get_script(None, ch, n, "9:16", ["кадр"], "Схема") == a
    # сценарий от модели после правки факта не пишется заново, а правится
    m = model_doc(n)
    saved.write_text(json.dumps({**m, "node_id": n["id"], "format": "9:16", "node_hash": a["node_hash"]}, ensure_ascii=False), encoding="utf-8")
    edited = list(C)
    edited[7] = edited[7].replace("11,8", "12,4")
    b = ns.get_script(None, ch, node(edited), "9:16", ["кадр"], "Схема", log=lambda *_: None)
    assert b["by"] == "model+edit" and any("12,4" in x["text"] for x in b["lines"])
    assert json.loads(saved.read_text(encoding="utf-8"))["node_hash"] == ns.node_hash(node(edited), "9:16")


def test_timeline_pauses_after_prediction():
    tl = ns.timeline(ns.script_by_rules(node(), "9:16", 3))
    i = [x["kind"] for x in tl].index("predict")
    assert abs(tl[i + 1]["t_start"] - tl[i]["t_end"] - 1.5) < 0.01
    assert all(b["t_start"] > a["t_start"] for a, b in zip(tl, tl[1:]))
