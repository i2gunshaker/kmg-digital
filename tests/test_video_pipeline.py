"""Офлайн-тесты видеоконвейера: нормализация речи, mock-TTS + loudnorm -16 LUFS, генератор сцен из узла и отчёт шаблонов."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from kurs.config import Config
from kurs.tts import compute_wer, normalize_for_speech, synthesize_passport
from kurs.units import build_units, cut_chapter
from kurs.video_render import render_from_spec
from kurs.video_spec import (
    build_spec_from_node,
    build_template_coverage_report,
    build_training_specs,
    verify_passport_sources,
)
from kurs.verify import Source


def test_normalize_for_speech_backends():
    cfg = Config()
    src = "На Волве кажущееся сопротивление (RT = 10 Ом·м) и пористость kп* = 17%, в масштабе 1:1, κ = 1 м²/с."
    el_txt, ref = normalize_for_speech(src, "elevenlabs", cfg.domain_dir)
    sil_txt, _ = normalize_for_speech(src, "silero", cfg.domain_dir)
    edge_txt, _ = normalize_for_speech(src, "edge_tts", cfg.domain_dir)

    assert "Вольве" not in el_txt or "Volve" not in el_txt
    assert "ом-метр" in el_txt and "1 к 1" in el_txt and "каппа" in el_txt
    assert "\u0301" not in el_txt and "+" not in el_txt
    assert "+" in sil_txt and "\u0301" not in sil_txt
    assert "\u0301" in edge_txt
    assert compute_wer(ref, ref) == 0.0
    assert compute_wer("один два три четыре пять", "один два три четыре шесть") == 0.2
    assert compute_wer("порог семнадцать процентов и ноль целых шести десятых процента", "порог 17% и 0,6%") == 0.0
    assert compute_wer("при двадцати пяти процентах", "при 25%") == 0.0


def test_mock_tts_loudnorm_and_pause(tmp_path):
    cfg = Config()
    for cid in ("latyshova_gl5_par17-20", "basniev_gl23-24"):
        ch = cfg.chapter(cid)
        cut_chapter(cfg, ch)
        build_units(cfg, ch)
    specs = build_training_specs(cfg, out_dir=tmp_path / "specs")
    res = synthesize_passport(
        cfg,
        specs["latyshova_passport"],
        tmp_path / "tts_out",
        backend="mock",
        skip_asr=True,
    )
    assert res["backend"] == "mock"
    assert res["master_wav"].exists() and res["master_mp3"].exists() and res["subtitles_vtt"].exists()
    assert -17.5 <= res["measured_lufs"] <= -14.5
    assert res["pause_window"] is not None
    assert abs((res["pause_window"][1] - res["pause_window"][0]) - 1.5) < 0.05

    passport = json.loads(res["passport_json"].read_text(encoding="utf-8"))
    passport_schema = json.loads((cfg.schemas_dir / "passport.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(passport, passport_schema)
    assert passport["audio_check"]["status"] == "ok"


def test_build_spec_from_arbitrary_node_and_template_coverage(tmp_path):
    cfg = Config()
    ch = cfg.chapter("latyshova_gl5_par17-20")
    cut_chapter(cfg, ch)
    build_units(cfg, ch)
    ex_layer = json.loads((cfg.schemas_dir / "example_layer.json").read_text(encoding="utf-8"))
    node = ex_layer["nodes"][0]

    scene_path, passport_path = build_spec_from_node(cfg, ch, node, fmt="16:9", out_dir=tmp_path / "node_spec")
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    passport_schema = json.loads((cfg.schemas_dir / "passport.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(passport, passport_schema)
    assert verify_passport_sources(passport, Source(ch)) == []

    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    assert scene["format"] == "16:9" and scene["state_sample"]["visual_svg"].startswith("<svg")

    rich_layer = {
        **ex_layer,
        "nodes": [
            {
                **node,
                "steps": [{"text": "Сопоставить ГК, КС и пористость kп", "by_model": False}],
                "decision_on_data": {
                    "features": ["GR", "RT", "PHIF"],
                    "rule": "Высокое RT и высокая пористость",
                    "conclusion": "Коллектор",
                    "counterexample": "Плотный известняк",
                },
            }
        ],
    }
    cov_paths = build_template_coverage_report(cfg, ch, rich_layer, out_dir=tmp_path / "cov")
    cov = json.loads(cov_paths["json"].read_text(encoding="utf-8"))
    assert cov["total_visual_nodes"] >= 1 and cov["needs_new_count"] == 0
    assert cov_paths["md"].exists()


def test_remotion_render_end_to_end_mock_audio(tmp_path):
    cfg = Config()
    ch = cfg.chapter("latyshova_gl5_par17-20")
    cut_chapter(cfg, ch)
    build_units(cfg, ch)
    ex_layer = json.loads((cfg.schemas_dir / "example_layer.json").read_text(encoding="utf-8"))
    node = ex_layer["nodes"][0]
    # Короткий 2-репличный прогон Remotion с mock-аудио для быстрой проверки сборки MP4 и кадров
    scene_path, passport_path = build_spec_from_node(cfg, ch, node, fmt="9:16", out_dir=tmp_path / "spec")
    p = json.loads(passport_path.read_text(encoding="utf-8"))
    p["script"] = [
        {**p["script"][0], "t_start": 0.0, "t_end": 1.2},
        {**p["script"][4], "t_start": 2.7, "t_end": 4.0},
    ]
    p["frames"] = p["frames"][:2]
    passport_path.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")

    res = render_from_spec(
        cfg,
        passport_path,
        scene_path,
        out_dir=tmp_path / "vid",
        backend="mock",
        skip_asr=True,
    )
    assert res["mp4"].exists()
    assert res["probe"]["has_video"] and res["probe"]["has_audio"]
    assert res["probe"]["width"] == 1080 and res["probe"]["height"] == 1920
    assert res["frames_count"] >= 1
