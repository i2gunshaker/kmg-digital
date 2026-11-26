"""Готовый ролик Remotion → videos.json главы; правка числа в слое → то же число в репликах обучающего ролика."""
from __future__ import annotations

import json

from kurs import video_link as vl

LAYER = {"nodes": [{"id": "d.n1", "claims": [
    {"id": "d.n1#c1", "text": "На глубине 1000 м давление около 11,8 МПа."},
    {"id": "d.n1#c2", "text": "Формула верна только для неподвижного столба."},
]}]}


def render_dir(tmp_path):
    d = tmp_path / "videos" / "d_n1_shorts"
    (d / "frames").mkdir(parents=True)
    (d / "d_n1_shorts.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    for i in (1, 2, 3):
        (d / "frames" / f"f{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    passport = {
        "video_id": "d_n1_shorts", "node_id": "d.n1",
        "node_card": {"title": "Давление столба", "format": "шортс", "chapter": "demo"},
        "script": [
            {"t_start": 0, "t_end": 3, "text": "Тысяча метров дают около 11,8 МПа.", "on_screen": "Около 11,8 МПа", "claim_id": "d.n1#c1"},
            {"t_start": 3, "t_end": 6, "text": "Только для неподвижного столба.", "on_screen": "Только без циркуляции", "claim_id": "d.n1#c2"},
        ],
        "audio_check": {"backend": "mock", "measured_lufs": -16.0, "wer": 0.0, "max_sync_drift_sec": 0.1, "status": "ok"},
    }
    (d / "passport.json").write_text(json.dumps(passport, ensure_ascii=False), encoding="utf-8")
    (d / "timings.json").write_text(json.dumps({"segments": [
        {"t_start": 0.1, "t_end": 2.9, "spoken_ref": "Ты+сяча ме́тров дают около 11,8 МПа."},
        {"t_start": 3.1, "t_end": 5.8, "spoken_ref": "Только для неподвижного столба."},
    ]}, ensure_ascii=False), encoding="utf-8")
    return d


def chapter(tmp_path):
    class Chapter:
        id = "demo"
        build_dir = tmp_path / "build" / "demo"

    Chapter.build_dir.mkdir(parents=True)
    return Chapter()


def test_register_puts_video_on_its_step(tmp_path):
    ch, d = chapter(tmp_path), render_dir(tmp_path)
    e = vl.register(None, ch, d, kind="node", layer=LAYER, args={"backend": "mock"})
    assert e["node_ids"] == ["d.n1"] and e["vertical"] and e["format"] == "9:16" and e["title"] == "Шортс: Давление столба"
    assert (ch.build_dir / e["file"]).exists() and (ch.build_dir / e["poster"]).exists()
    assert e["cues"][0]["text"] == "Тысяча метров дают около 11,8 МПа."  # знаки ударения сняты
    assert e["claim_ids"] == ["d.n1#c1", "d.n1#c2"] and e["snapshot"]["d.n1#c1"] == LAYER["nodes"][0]["claims"][0]["text"]
    assert e["audio"]["measured_lufs"] == -16.0 and e["duration_s"] == 5.8  # секунды видео для metrics.csv
    # повторная регистрация заменяет запись, а не дублирует
    vl.register(None, ch, d, kind="node", layer=LAYER)
    assert len(json.loads((ch.build_dir / "videos.json").read_text(encoding="utf-8"))["videos"]) == 1


def test_training_passport_gets_new_number(tmp_path):
    d = render_dir(tmp_path)
    old = {c["id"]: c["text"] for c in LAYER["nodes"][0]["claims"]}
    new = json.loads(json.dumps(LAYER))
    new["nodes"][0]["claims"][0]["text"] = "На глубине 1000 м давление около 12,4 МПа."
    new["nodes"][0]["claims"][1]["text"] = "Формула верна лишь для статического столба."
    notes = []
    assert vl._patch_training(d / "passport.json", old, new, log=notes.append) == 1
    p = json.loads((d / "passport.json").read_text(encoding="utf-8"))
    assert p["script"][0]["text"] == "Тысяча метров дают около 12,4 МПа." and p["script"][0]["on_screen"] == "Около 12,4 МПа"
    assert any("проверьте вручную" in x for x in notes)  # смысловая правка без чисел — человеку


def test_node_story_coverage_reports_picture_frames_and_script(tmp_path):
    from kurs.config import Config
    from tests.test_node_script import node

    ch = chapter(tmp_path)
    doc = vl.node_story_coverage(Config(), ch, {"nodes": [node()]})
    r = doc["rows"][0]
    assert doc["template"] == "NodeStory" and doc["nodes"] == 1
    assert r["frames"] >= 1 and r["picture"] == "по карточке узла" and r["template"]
    assert r["9:16"] == {"script": "нет", "rules_pass": True} and r["covered"] and r["status"] == "закрыт; сценарий без модели"
    assert r["state_kind"] in ("расчёт", "состояния", "части", "статичный") and r["state_model"]
    # процессу во времени статичного рисунка мало — отчёт честно просит новый шаблон
    slow = vl.node_story_coverage(Config(), ch, {"nodes": [{**node(), "difficulty": {"primary": "В"}}]})["rows"][0]
    assert slow["state_kind"] in ("расчёт", "состояния") or (not slow["covered"] and slow["status"].startswith("нужен новый"))


def test_state_model_kinds():
    assert vl.state_model({"frames": [{"label": "t1", "vars": {"t": 1}}, {"label": "t2", "vars": {"t": 2}}]}, 2)[0] == "расчёт"
    assert vl.state_model({"frames": [{"label": "до", "params": {}}, {"label": "после", "params": {}}]}, 2)[0] == "состояния"
    assert vl.state_model({"_reveal": True}, 3)[0] == "части"
    assert vl.state_model({}, 1)[0] == "статичный"
