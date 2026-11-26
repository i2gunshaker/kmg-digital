"""Пакет сдачи: собирает готовое из build/ в раскладку deliverables/ и называет, чего не хватает."""
from __future__ import annotations

import json

from kurs import handin


def test_pack_collects_parts_and_reports_missing(tmp_path, monkeypatch):
    build = tmp_path / "build"
    ch = build / "ch1"
    (ch / "course").mkdir(parents=True)
    for name in handin.LAYER_FILES[:-1]:  # tasks.json нет
        (ch / name).write_text("{}", encoding="utf-8")
    (ch / "course" / "index.html").write_text("<html></html>", encoding="utf-8")
    (ch / "videos.json").write_text(json.dumps({"videos": [{"id": "v1", "title": "Шортс: тест", "node_ids": ["ch1.n1"]}]}), encoding="utf-8")
    vdir = build / "videos" / "v1"
    vdir.mkdir(parents=True)
    (vdir / "v1.mp4").write_bytes(b"mp4")
    (vdir / "passport.json").write_text("{}", encoding="utf-8")
    (vdir / "remotion_props.json").write_text(json.dumps({"scene": {"video_id": "v1"}, "timings": {}}), encoding="utf-8")
    monkeypatch.setattr(handin, "BUILD", build)

    out = tmp_path / "deliverables"
    (out / "videos" / "v1").mkdir(parents=True)
    (out / "videos" / "v1" / "old.mp4").write_bytes(b"old")
    (out / "README.md").write_text("свой README", encoding="utf-8")
    missing = handin.pack(out, chapters=("ch1",))
    assert (out / "courses" / "ch1" / "course" / "index.html").exists() and (out / "courses" / "ch1" / "layer.json").exists()
    assert (out / "videos" / "v1" / "v1.mp4").exists() and not (out / "videos" / "v1" / "old.mp4").exists()
    assert json.loads((out / "videos" / "v1" / "scene.json").read_text(encoding="utf-8")) == {"video_id": "v1"}
    assert (out / "README.md").read_text(encoding="utf-8") == "свой README"  # README папки не трогается
    assert any("tasks.json" in m for m in missing)
    assert any("роликов в курсах 1" in m for m in missing)  # нужен второй ролик
