"""Пакет сдачи обучающей части в раскладке deliverables/: курсы со слоем и ролики с паспортами.

    python -m kurs.handin                 # в build/handin — посмотреть, что получится
    python -m kurs.handin deliverables    # обновить копию в репозитории (README.md папки не трогается)

Ничего не пересобирает: только собирает готовое из build/ и печатает, чего не хватает (код выхода 1).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from .config import BUILD

TRAINING = ("latyshova_gl5_par17-20", "basniev_gl23-24", "kuznetsov_gl10")
LAYER_FILES = ("layer.json", "coverage.json", "routing.json", "tasks.json")
VIDEO_FILES = ("passport.json", "subtitles.vtt")

README = """# Готовые артефакты

- `courses/<глава>/course/index.html` — демо-курс; открыть двойным щелчком, сервер и сеть не нужны.
  Рядом — учебный слой (`layer.json`), покрытие единиц (`coverage.json`), маршрутизация (`routing.json`), задания (`tasks.json`).
- `videos/<ролик>/` — MP4, паспорт (`passport.json`), субтитры (`subtitles.vtt`), сцена Remotion (`scene.json`).

Ролики стоят в курсе на своих шагах:
{videos}
"""


def pack(out: Path, chapters: tuple[str, ...] = TRAINING) -> list[str]:
    """Обновляет out/courses/<глава> и out/videos/<ролик>. Возвращает список того, чего не хватает."""
    missing: list[str] = []
    placed: list[str] = []
    out.mkdir(parents=True, exist_ok=True)
    for cid in chapters:
        src, dst = BUILD / cid, out / "courses" / cid
        if not (src / "course" / "index.html").exists():
            missing.append(f"{cid}: нет курса (python -m kurs course {cid})")
            continue
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src / "course", dst / "course")
        for name in LAYER_FILES:
            if (src / name).exists():
                shutil.copy2(src / name, dst / name)
            else:
                missing.append(f"{cid}: нет {name}")
        vj = src / "videos.json"
        for v in json.loads(vj.read_text(encoding="utf-8")).get("videos", []) if vj.exists() else []:
            vdir, vdst = BUILD / "videos" / v["id"], out / "videos" / v["id"]
            mp4 = vdir / f"{v['id']}.mp4"
            if not mp4.exists():
                missing.append(f"{v['id']}: нет MP4 в {vdir}")
                continue
            if vdst.exists():
                shutil.rmtree(vdst)
            vdst.mkdir(parents=True)
            shutil.copy2(mp4, vdst / mp4.name)
            for name in VIDEO_FILES:
                if (vdir / name).exists():
                    shutil.copy2(vdir / name, vdst / name)
            props = vdir / "remotion_props.json"
            if props.exists():  # сцена, из которой ролик пересобирается
                scene = json.loads(props.read_text(encoding="utf-8")).get("scene")
                (vdst / "scene.json").write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
            placed.append(f"- `{v['id']}` — {v.get('title', '')}; глава `{cid}`, узлы: {', '.join(v.get('node_ids', []))}")
    if len(placed) < 2:
        missing.append(f"роликов в курсах {len(placed)}, нужно два (шортс 9:16 и разбор 16:9 на разных главах)")
    if not (out / "README.md").exists():
        (out / "README.md").write_text(README.format(videos="\n".join(placed) or "- нет"), encoding="utf-8")
    return missing


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out = Path(args[0]) if args else BUILD / "handin"
    missing = pack(out)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e6
    print(f"пакет: {out} ({size:.1f} МБ)")
    for m in missing:
        print(f"  не хватает: {m}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
