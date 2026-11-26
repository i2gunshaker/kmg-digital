"""Приёмка рисунков зрением: снимок, два прохода, снятие неверного описания."""
from __future__ import annotations

import json

import pytest

from kurs import visual_vision as vv
from kurs.config import Config
from kurs.llm import LLM, Reply, Usage

NODE = {
    "id": "d.n1", "title": "Газ над нефтью", "unit_ids": ["p001.01"], "difficulty": {"primary": "пространство"},
    "layers": {"main": {"text": "Газ лежит выше нефти."}, "mechanism": {"text": "Плотность газа меньше."}},
    "claims": [{"id": "d.n1#c1", "text": "Газ лежит выше нефти.", "quote": "Газ лежит выше нефти", "page_pdf": 1}],
}
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 560 300"><rect width="560" height="300" fill="#222"/></svg>'


@pytest.fixture
def ch(tmp_path, monkeypatch):
    monkeypatch.delenv("KURS_LLM_OFFLINE", raising=False)  # подставной транспорт должен отвечать

    class Chapter:
        id = "demo"
        build_dir = tmp_path / "demo"

    c = Chapter()
    img = c.build_dir / "course" / "assets" / "img"
    img.mkdir(parents=True)
    (img / "node_01.svg").write_text(SVG, encoding="utf-8")
    (c.build_dir / "illustrations.json").write_text(json.dumps([{"node_id": "d.n1", "file": "assets/img/node_01.svg", "caption": "Ловушка", "label": "схема"}]), encoding="utf-8")
    (c.build_dir / "visual.json").write_text(json.dumps({"chapter": "demo", "nodes": {"d.n1": {"template": "layers"}}, "rejected": {}}), encoding="utf-8")

    def shoot(rows, out, log=print):
        for r in rows:
            r["pngs"] = [f.with_suffix(".png") for f in r["frames"]]
            for p in r["pngs"]:
                p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    monkeypatch.setattr(vv, "shoot", shoot)
    return c


def fake(cfg, tmp, issues, real):
    def transport(model, system, user, schema, effort, max_tokens, images=None):
        assert images and images[0].exists()
        if "rulings" in schema["properties"]:
            return Reply(json.dumps({"rulings": [{"n": i + 1, "why": ".", "real": real} for i in range(len(issues))]}), model, Usage(input=10, output=5))
        return Reply(json.dumps({"issues": issues}, ensure_ascii=False), model, Usage(input=10, output=5))

    return LLM(cfg, scope="demo", transport=transport, cache_dir=tmp / "cache", log_path=tmp / "usage.jsonl", budget=1.0)


BAD = {"severity": "critical", "seen": "газ под нефтью", "truth": "газ над нефтью", "evidence": "Газ лежит выше нефти"}


def test_confirmed_error_drops_generated_spec(ch, tmp_path):
    cfg = Config()
    doc = vv.check(cfg, ch, fake(cfg, tmp_path, [BAD], True), {"nodes": [NODE]}, log=lambda *_: None)
    assert doc["pictures"] == 1 and len(doc["with_issues"]) == 1
    assert vv.apply(ch, doc) == ["d.n1"]
    vis = json.loads((ch.build_dir / "visual.json").read_text(encoding="utf-8"))
    assert "d.n1" not in vis["nodes"] and "газ под нефтью" in vis["rejected"]["d.n1"][0]


def test_defender_and_invented_evidence_clear_the_picture(ch, tmp_path):
    cfg = Config()
    doc = vv.check(cfg, ch, fake(cfg, tmp_path, [BAD], False), {"nodes": [NODE]}, log=lambda *_: None)
    assert not doc["with_issues"] and doc["dropped"] == 1
    made_up = {**BAD, "evidence": "Вода всегда легче газа"}
    doc = vv.check(cfg, ch, fake(cfg, tmp_path / "b", [made_up], True), {"nodes": [NODE]}, log=lambda *_: None)
    assert not doc["with_issues"] and vv.apply(ch, doc) == []


def test_all_frames_in_order(tmp_path):
    for k in ("node_02_1", "node_02_2", "node_02_10", "node_02_1_m", "node_05b_1", "node_05b_2", "node_01"):
        (tmp_path / f"{k}.svg").write_text(SVG, encoding="utf-8")
    assert [x.stem for x in vv.frames_of(tmp_path / "node_02_1.svg")] == ["node_02_1", "node_02_2", "node_02_10"]
    assert [x.stem for x in vv.frames_of(tmp_path / "node_05b_1.svg")] == ["node_05b_1", "node_05b_2"]
    assert [x.stem for x in vv.frames_of(tmp_path / "node_01.svg")] == ["node_01"]
