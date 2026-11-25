"""CLI конвейера.

  python -m kurs units   <глава|путь.md>              — вырезать главу и разбить на единицы
  python -m kurs check   <глава> [layer.json] [cov]   — проверить слой и покрытие
  python -m kurs course  <глава> [--layer ...]        — собрать автономный офлайн-курс (file://)
  python -m kurs specs   [--out specs]                — собрать и проверить спецификации роликов для ментора
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .config import Config
from .units import build_units, cut_chapter


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kurs")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("units")
    p.add_argument("chapter", nargs="+")

    p = sub.add_parser("check")
    p.add_argument("chapter")
    p.add_argument("layer", nargs="?")
    p.add_argument("coverage", nargs="?")
    p.add_argument("--layer-only", action="store_true", help="не требовать coverage.json для одиночного слоя")

    p = sub.add_parser("course")
    p.add_argument("chapter")
    p.add_argument("--layer", help="путь к layer.json (по умолчанию build/<chapter>/layer.json)")
    p.add_argument("--tasks", help="путь к tasks.json")
    p.add_argument("--routing", help="путь к routing.json")
    p.add_argument("--out", help="выходная папка курса (по умолчанию build/<chapter>/course)")

    p = sub.add_parser("specs")
    p.add_argument("--out", help="папка для спецификаций и паспортов (по умолчанию specs/)")

    args = ap.parse_args(argv)
    cfg = Config()

    if args.cmd == "units":
        ids = list(cfg.chapters) if args.chapter == ["all"] else args.chapter
        for cid in ids:
            ch = cfg.chapter(cid)
            cut_chapter(cfg, ch)
            units = build_units(cfg, ch)
            kinds = Counter(u.kind + ("*" if u.model_description else "") for u in units)
            pages = sorted({u.page_pdf for u in units})
            lang = ch.language or "ru"
            print(f"{ch.id} [{lang}]: {len(units)} единиц, стр. {pages[0]}–{pages[-1]}; {dict(kinds)}")
        return 0

    if args.cmd == "check":
        from .verify import Issue, check_coverage, check_layer, report

        ch = cfg.chapter(args.chapter)
        if not ch.md_path.exists() or not (ch.build_dir / "units.json").exists():
            cut_chapter(cfg, ch)
            build_units(cfg, ch)

        lp = Path(args.layer) if args.layer else ch.build_dir / "layer.json"
        layer = json.loads(lp.read_text(encoding="utf-8"))

        cov_data = None
        issues: list[Issue] = []
        if args.coverage:
            cp = Path(args.coverage)
            if not cp.exists():
                issues.append(Issue("error", "coverage", f"файл покрытия не найден: {cp}"))
            else:
                cov_data = json.loads(cp.read_text(encoding="utf-8"))
        elif not args.layer:
            cp = ch.build_dir / "coverage.json"
            if not cp.exists():
                issues.append(Issue("error", "coverage", f"отсутствует обязательный файл покрытия: {cp}"))
            else:
                cov_data = json.loads(cp.read_text(encoding="utf-8"))
        elif not args.layer_only:
            sibling_cov = lp.parent / "coverage.json"
            if sibling_cov.exists():
                cov_data = json.loads(sibling_cov.read_text(encoding="utf-8"))
            else:
                issues.append(Issue("warn", "coverage", "coverage.json не передан рядом с layer.json"))

        issues += check_layer(cfg, ch, layer, coverage=cov_data)
        if cov_data is not None:
            issues += check_coverage(cfg, ch, cov_data, layer)
        print(report(issues))
        return 1 if any(i.level == "error" for i in issues) else 0

    if args.cmd == "course":
        from .course import build_course, verify_course_dir

        ch = cfg.chapter(args.chapter)
        lp = Path(args.layer) if args.layer else ch.build_dir / "layer.json"
        layer = json.loads(lp.read_text(encoding="utf-8"))
        tasks_doc = json.loads(Path(args.tasks).read_text(encoding="utf-8")) if args.tasks else None
        routing_doc = json.loads(Path(args.routing).read_text(encoding="utf-8")) if args.routing else None
        out_dir = Path(args.out) if args.out else None
        built = build_course(cfg, ch, layer, tasks_doc=tasks_doc, routing_doc=routing_doc, out_dir=out_dir)
        errs = verify_course_dir(cfg, ch, built)
        if errs:
            print("Ошибки приёмки курса:\n" + "\n".join(errs))
            return 1
        print(f"Курс собран и проверен: {built / 'index.html'}")
        return 0

    if args.cmd == "specs":
        from .video_spec import build_training_specs

        out_dir = Path(args.out) if args.out else None
        res = build_training_specs(cfg, out_dir=out_dir)
        for k, v in res.items():
            print(f"{k}: {v}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
