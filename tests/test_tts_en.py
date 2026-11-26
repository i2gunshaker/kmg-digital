"""Озвучка английского текста (lang="en"): без русских раскрытий и ударений, английский голос и language_code."""
from __future__ import annotations

import json
import urllib.request

from kurs import tts
from kurs.config import Config
from kurs.tts import DEFAULT_EL_VOICE_ID_EN, normalize_for_speech, synth, synthesize_passport
from kurs.units import build_units, cut_chapter
from kurs.video_spec import build_training_specs

EN_TEXT = "Porosity above 17% → oil-saturated rock; RT ≠ GR at 1:1 scale, κ = 1 м²/с."


def test_normalize_for_speech_en_no_russian():
    cfg = Config()
    for backend in ("elevenlabs", "silero", "edge_tts"):
        tts_text, ref = normalize_for_speech(EN_TEXT, backend, cfg.domain_dir, lang="en")
        assert tts_text == ref
        for ru in ("переходит в", "не равно", "процентов", "эр-тэ", "джи-ар", "каппа", " к ", "и более"):
            assert ru not in tts_text, (backend, ru, tts_text)
        assert "́" not in tts_text and "+" not in tts_text
        assert "leads to" in tts_text and "is not equal to" in tts_text
        assert "  " not in tts_text
    # Русская ветка не изменилась
    ru_txt, _ = normalize_for_speech("RT → GR", "elevenlabs", cfg.domain_dir)
    assert "переходит в" in ru_txt and "эр-тэ" in ru_txt


class _Resp:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a) -> None:
        return None


def test_synth_elevenlabs_en_payload_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    for var in ("ELEVENLABS_VOICE_ID_EN", "ELEVENLABS_VOICE_ID", "ELEVENLABS_MODEL_ID", "ELEVENLABS_MODEL"):
        monkeypatch.delenv(var, raising=False)
    calls: list[tuple[str, dict]] = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.full_url, json.loads(req.data.decode())))
        return _Resp(b"ID3fake")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "a.mp3"
    text = "Porosity controls how much oil a rock can hold."

    synth(text, out, backend="elevenlabs", lang="en")
    assert len(calls) == 1
    url, payload = calls[0]
    assert url.endswith("/" + DEFAULT_EL_VOICE_ID_EN) and DEFAULT_EL_VOICE_ID_EN == "JBFqnCBsd6RMkjVDRZzb"
    assert payload["language_code"] == "en" and payload["model_id"] == "eleven_v4"
    assert out.with_suffix(".txt").read_text(encoding="utf-8") == f"elevenlabs|None|en|{text}"

    # Тот же текст и язык — из кэша, без запроса
    synth(text, out, backend="elevenlabs", lang="en")
    assert len(calls) == 1
    # Другой язык — кэш не подходит, новый запрос с русским голосом и language_code
    synth(text, out, backend="elevenlabs", lang="ru")
    assert len(calls) == 2
    url_ru, payload_ru = calls[1]
    assert payload_ru["language_code"] == "ru" and url_ru.endswith("/3EuKHIEZbSzrHGNmdYsx")

    # Переопределение английского голоса через окружение
    monkeypatch.setenv("ELEVENLABS_VOICE_ID_EN", "custom_en_voice")
    synth(text, tmp_path / "b.mp3", backend="elevenlabs", lang="en")
    assert calls[2][0].endswith("/custom_en_voice")


def test_elevenlabs_segment_language_code(tmp_path, monkeypatch):
    captured: dict = {}

    class _TTS:
        def convert_with_timestamps(self, voice_id, **kwargs):
            captured.update(kwargs, voice_id=voice_id)
            raise RuntimeError("stop")

    class _Client:
        text_to_speech = _TTS()

    try:
        tts._synth_elevenlabs_segment(_Client(), "hi", "v", "eleven_v4", tmp_path / "x.mp3", lang="en")
    except RuntimeError:
        pass
    assert captured["language_code"] == "en"


def test_synthesize_passport_mock_en(tmp_path, monkeypatch):
    for var in ("ELEVENLABS_VOICE_ID_EN", "ELEVENLABS_VOICE_ID"):
        monkeypatch.delenv(var, raising=False)
    cfg = Config()
    for cid in ("latyshova_gl5_par17-20", "basniev_gl23-24"):
        ch = cfg.chapter(cid)
        cut_chapter(cfg, ch)
        build_units(cfg, ch)
    specs = build_training_specs(cfg, out_dir=tmp_path / "specs")
    passport = json.loads(specs["latyshova_passport"].read_text(encoding="utf-8"))
    for i, line in enumerate(passport["script"]):
        line["text"] = f"Line {i}: porosity above 17% → reservoir rock."
    en_passport = tmp_path / "passport_en.json"
    en_passport.write_text(json.dumps(passport, ensure_ascii=False), encoding="utf-8")

    res = synthesize_passport(cfg, en_passport, tmp_path / "tts_en", backend="mock", skip_asr=True, lang="en")
    assert res["backend"] == "mock"
    assert res["resolved_voice_id"] == DEFAULT_EL_VOICE_ID_EN
    vtt = res["subtitles_vtt"].read_text(encoding="utf-8")
    assert "leads to" in vtt and "переходит" not in vtt

    monkeypatch.setenv("ELEVENLABS_VOICE_ID_EN", "custom_en_voice")
    res2 = synthesize_passport(cfg, en_passport, tmp_path / "tts_en2", backend="mock", skip_asr=True, lang="en")
    assert res2["resolved_voice_id"] == "custom_en_voice"
