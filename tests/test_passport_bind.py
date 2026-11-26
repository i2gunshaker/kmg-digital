"""Привязка паспорта ролика к утверждениям слоя."""
from kurs.passport_bind import bind, foreign_claims

LAYER = {"nodes": [
    {"id": "t.a", "claims": [{"id": "t.a#c1", "page_pdf": 97, "quote": "Коэффициент пористости определяется по палетке с порогом семнадцать процентов", "text": "x"},
                             {"id": "t.a#c2", "page_pdf": 98, "quote": "Совсем другая фраза о бурении на воде", "text": "y"}]},
    {"id": "t.b", "claims": [{"id": "t.b#c4", "page_pdf": 95, "quote": "выделение коллектора можно осуществить лишь на основании количественной интерпретации", "text": "z"}]},
]}


def passport():
    return {"video_id": "v", "node_card": {}, "sources": [
        {"claim_id": "t.a#c2", "page_or_dataset": "стр. 97 (табл. 3)", "quote": "пористости определяется по палетке с порогом семнадцать процентов"},
        {"claim_id": "t.a#c9", "page_or_dataset": "стр. 95", "quote": "выделение коллектора можно осуществить лишь на основании количественной интерпретации геофизических данных"},
        {"claim_id": "t.a#c6", "page_or_dataset": "data.volve_15_9_f1b (интервал A)", "quote": "PHIF=0.228"},
        {"claim_id": "t.a#c8", "page_or_dataset": "стр. 98", "quote": "Такой цитаты в слое нет ни в одном утверждении"},
    ], "script": [{"t_start": 0, "t_end": 1, "claim_id": "t.a#c2"}, {"t_start": 1, "t_end": 2, "claim_id": "t.a#c9"},
                  {"t_start": 2, "t_end": 3, "claim_id": "t.a#c6"}, {"t_start": 3, "t_end": 4, "claim_id": "t.a#c8"}]}


def test_sources_and_lines_get_real_claims():
    p = passport()
    left = bind(p, LAYER)
    assert [s["claim_id"] for s in p["sources"][:3]] == ["t.a#c1", "t.b#c4", "data.volve_15_9_f1b"]
    assert [ln["claim_id"] for ln in p["script"][:3]] == ["t.a#c1", "t.b#c4", "data.volve_15_9_f1b"]
    assert len(left) == 1 and "t.a#c8" in left[0]
    assert foreign_claims(p, LAYER) == ["t=3: t.a#c8"]
