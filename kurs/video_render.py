"""Рендер видеороликов конвейера: TTS + -16 LUFS + mlx-whisper + Remotion 4 + проверочные PNG-кадры."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import jsonschema

from .config import BUILD, ROOT, Config
from .illustrations import load_styleguide
from .tts import synthesize_passport
from .units import build_units, cut_chapter
from .verify import Source
from .video_spec import build_spec_from_node, build_training_specs, verify_passport_sources


def _probe_video(mp4_path: Path) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(mp4_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(out.stdout)
    v_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    return {
        "has_video": v_stream is not None,
        "has_audio": a_stream is not None,
        "width": int(v_stream["width"]) if v_stream else 0,
        "height": int(v_stream["height"]) if v_stream else 0,
        "duration_sec": round(float(info["format"]["duration"]), 3),
        "size_mb": round(mp4_path.stat().st_size / (1024 * 1024), 2),
    }


def _extract_check_frames(mp4_path: Path, passport: dict, frames_dir: Path) -> list[Path]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    dur = float(passport["node_card"]["duration_sec"])
    # Снимаем 6 контрольных кадров, включая 1:1 и срез после паузы предсказания
    targets = []
    for f in passport.get("frames", []):
        t = min(max(0.2, float(f["t"])), max(0.3, dur - 0.3))
        if not any(abs(t - x) < 1.5 for x in targets):
            targets.append(t)
    saved: list[Path] = []
    for idx, t in enumerate(targets[:8]):
        out_png = frames_dir / f"frame_{idx:02d}_{t:.1f}s.png"
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-ss", f"{t:.2f}", "-i", str(mp4_path), "-frames:v", "1", str(out_png)],
            capture_output=True,
            text=True,
            check=True,
        )
        saved.append(out_png)
    return saved


def render_from_spec(
    cfg: Config,
    passport_path: Path,
    scene_path: Path,
    *,
    out_dir: Path | None = None,
    backend: str = "auto",
    voice_id: str | None = None,
    model_id: str | None = None,
    max_chars: int = 8000,
    lang: str = "ru",
    skip_asr: bool = False,
) -> dict[str, Any]:
    """Полный цикл сборки ролика из готовых passport.json + scene.json: звук -> Remotion -> PNG -> проверка."""
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    video_id = passport["video_id"]
    out_dir = out_dir or (BUILD / "videos" / video_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Озвучка + склейка с паузой 1.5 с + 2-pass loudnorm -16 LUFS + mlx-whisper
    tts_res = synthesize_passport(
        cfg,
        passport_path,
        out_dir,
        backend=backend,
        voice_id=voice_id,
        model_id=model_id,
        max_chars=max_chars,
        lang=lang,
        skip_asr=skip_asr,
    )

    updated_passport = json.loads(Path(tts_res["passport_json"]).read_text(encoding="utf-8"))
    timings = json.loads(Path(tts_res["timings_json"]).read_text(encoding="utf-8"))
    sg = load_styleguide(cfg)

    # 2. Публичный аудио-ассет для Remotion staticFile()
    pub_dir = ROOT / "video" / "public" / "_render"
    pub_dir.mkdir(parents=True, exist_ok=True)
    audio_rel = f"_render/{video_id}.mp3"
    shutil.copy2(tts_res["master_mp3"], ROOT / "video" / "public" / audio_rel)

    props = {
        "audioSrc": audio_rel,
        "scene": scene,
        "passport": updated_passport,
        "timings": timings,
        "styleguide": sg,
    }
    props_path = out_dir / "remotion_props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")

    # 3. Рендер MP4 в Remotion 4
    comp_id = "KursVideo9x16" if scene.get("format") == "9:16" else "KursVideo16x9"
    mp4_path = out_dir / f"{video_id}.mp4"
    subprocess.run(
        [
            "npx", "remotion", "render",
            "src/index.ts", comp_id, str(mp4_path),
            f"--props={props_path}",
            "--concurrency=4",
        ],
        cwd=ROOT / "video",
        check=True,
    )

    # 4. Проверочные кадры и валидация итогового паспорта
    pngs = _extract_check_frames(mp4_path, updated_passport, out_dir / "frames")
    probe = _probe_video(mp4_path)
    passport_schema = json.loads((cfg.schemas_dir / "passport.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(updated_passport, passport_schema)

    return {
        "video_id": video_id,
        "mp4": mp4_path,
        "passport": Path(tts_res["passport_json"]),
        "subtitles": Path(tts_res["subtitles_vtt"]),
        "frames_dir": out_dir / "frames",
        "frames_count": len(pngs),
        "probe": probe,
        "audio": {
            "backend": tts_res["backend"],
            "measured_lufs": tts_res["measured_lufs"],
            "wer": tts_res["wer"],
            "max_sync_drift_sec": tts_res["max_sync_drift_sec"],
            "duration_sec": tts_res["total_duration_sec"],
        },
    }


def render_training_videos(
    cfg: Config,
    *,
    backend: str = "auto",
    voice_id: str | None = None,
    model_id: str | None = None,
    max_chars: int = 8000,
    skip_asr: bool = False,
) -> list[dict[str, Any]]:
    """Собирает спецификации и рендерит оба обучающих ролика (9:16 Латышова + 16:9 Басниев)."""
    for cid in ("latyshova_gl5_par17-20", "basniev_gl23-24"):
        ch = cfg.chapter(cid)
        if not ch.md_path.exists() or not (ch.build_dir / "units.json").exists():
            cut_chapter(cfg, ch)
            build_units(cfg, ch)
    specs = build_training_specs(cfg)
    r1 = render_from_spec(
        cfg,
        specs["latyshova_passport"],
        specs["latyshova_scene"],
        backend=backend,
        voice_id=voice_id,
        model_id=model_id,
        max_chars=max_chars,
        lang="ru",
        skip_asr=skip_asr,
    )
    r2 = render_from_spec(
        cfg,
        specs["basniev_passport"],
        specs["basniev_scene"],
        backend=backend,
        voice_id=voice_id,
        model_id=model_id,
        max_chars=max_chars,
        lang="ru",
        skip_asr=skip_asr,
    )
    return [r1, r2]


def render_node_video(
    cfg: Config,
    chapter_arg: str,
    node_id: str,
    *,
    fmt: str = "9:16",
    layer_path: Path | None = None,
    backend: str = "auto",
    voice_id: str | None = None,
    model_id: str | None = None,
    max_chars: int = 8000,
    skip_asr: bool = False,
) -> dict[str, Any]:
    """Собирает ролик из любого узла layer.json (для тестового дня и сдвига домена)."""
    ch = cfg.chapter(chapter_arg)
    if not ch.md_path.exists() or not (ch.build_dir / "units.json").exists():
        cut_chapter(cfg, ch)
        build_units(cfg, ch)
    lp = layer_path or (ch.build_dir / "layer.json")
    layer = json.loads(lp.read_text(encoding="utf-8"))
    node = next((n for n in layer.get("nodes", []) if n["id"] == node_id), None)
    if node is None:
        raise KeyError(f"Узел {node_id} не найден в {lp}")
    scene_path, passport_path = build_spec_from_node(cfg, ch, node, fmt=fmt)
    res = render_from_spec(
        cfg,
        passport_path,
        scene_path,
        backend=backend,
        voice_id=voice_id,
        model_id=model_id,
        max_chars=max_chars,
        lang=ch.language or "ru",
        skip_asr=skip_asr,
    )
    src = Source(ch)
    p = json.loads(Path(res["passport"]).read_text(encoding="utf-8"))
    res["source_issues"] = verify_passport_sources(p, src)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kurs.video_render")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("training", help="отрендерить оба обучающих ролика (9:16 и 16:9)")
    p1.add_argument("--backend", default="auto")
    p1.add_argument("--voice-id")
    p1.add_argument("--model-id")
    p1.add_argument("--max-chars", type=int, default=8000)
    p1.add_argument("--skip-asr", action="store_true")

    p2 = sub.add_parser("node", help="отрендерить ролик по одному узлу layer.json")
    p2.add_argument("chapter")
    p2.add_argument("node_id")
    p2.add_argument("--format", default="9:16", choices=["9:16", "16:9"])
    p2.add_argument("--layer")
    p2.add_argument("--backend", default="auto")
    p2.add_argument("--voice-id")
    p2.add_argument("--model-id")
    p2.add_argument("--max-chars", type=int, default=8000)
    p2.add_argument("--skip-asr", action="store_true")

    args = ap.parse_args(argv)
    cfg = Config()
    if args.cmd == "training":
        out = render_training_videos(
            cfg,
            backend=args.backend,
            voice_id=args.voice_id,
            model_id=args.model_id,
            max_chars=args.max_chars,
            skip_asr=args.skip_asr,
        )
        print(json.dumps(out, default=str, ensure_ascii=False, indent=2))
        return 0

    out = render_node_video(
        cfg,
        args.chapter,
        args.node_id,
        fmt=args.format,
        layer_path=Path(args.layer) if args.layer else None,
        backend=args.backend,
        voice_id=args.voice_id,
        model_id=args.model_id,
        max_chars=args.max_chars,
        skip_asr=args.skip_asr,
    )
    print(json.dumps(out, default=str, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
