"""CLI конвейера.

  python -m kurs units   <глава|путь.md>              — вырезать главу и разбить на единицы
  python -m kurs cost    <глава|путь.md>              — прогноз цены и остаток общего предела, бесплатно
  python -m kurs gen     <глава|путь.md> --max-usd N  — слой, покрытие, маршрутизация, задания, рисунки
  python -m kurs check   <глава> [layer.json] [cov]   — проверить слой и покрытие
  python -m kurs visual  <глава>                      — что нарисовано к шагам и замечания, бесплатно
  python -m kurs llm                                  — проверить ключ и модели без платных вызовов
  python -m kurs course  <глава> [--layer ...]        — собрать автономный офлайн-курс (file://)
  python -m kurs specs   [--out build/specs]          — собрать и проверить спецификации роликов для ментора
  python -m kurs run     <путь.md|глава> [--max-usd N] [--video] — полный прогон одной командой
  python -m kurs plan    <глава> [узел]                — что устарело после правки слоя (без сборки)
  python -m kurs rebuild <глава> [узел] [--video]      — пересборка после правки факта, без вызовов модели
  python -m kurs fuzz    <глава> [--limit N]           — фаззинг валидатора: сколько внесённых ошибок поймано
  python -m kurs review  <package.json> -o <result.json> [--max-usd N] — модуль «Закладки» (= python -m kurs.review)
  python -m kurs video   <глава> [--node id] [--format shorts|long] [--dry] — ролики из проверенного слоя
  python -m kurs metrics [<глава> ...] [--manifest]    — build/metrics.csv и таблица для MANIFEST.md
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .config import Config
from .units import build_units, cut_chapter


def _money(cfg: Config, wallet) -> str:
    left = wallet.total_limit - wallet.before - wallet.spent
    return f"за всё время потрачено ${wallet.before + wallet.spent:.2f} из ${wallet.total_limit:.2f}, осталось ${max(0.0, left):.2f}"


def _cost(cfg: Config, args) -> int:
    from .cost import estimate_text
    from .llm import LLM, LLMError

    if args.profile:
        cfg.use_profile(args.profile)
    llm = LLM(cfg)
    try:
        total = 0.0
        print(f"набор моделей {cfg.llm.get('profile', '—')}: {llm.describe()}")
        for cid in list(cfg.chapters) if args.chapter == ["all"] else args.chapter:
            text, usd = estimate_text(cfg, cfg.chapter(cid), llm)
            print(text)
            total += usd
        print(f"итого около ${total:.2f}; {_money(cfg, llm.wallet)}")
    except LLMError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def _gen(cfg: Config, args) -> int:
    from .layer_gen import ManualEdits, generate_layer, load_chapter_units, load_layer
    from .llm import LLM, LLMError
    from .tasks_gen import generate_routing, generate_tasks
    from .verify import report
    from .visual_gen import generate_visuals

    if args.profile:
        cfg.use_profile(args.profile)
    rc = 0
    wallet = None  # один на все главы: предел --max-usd общий
    for cid in args.chapter:
        ch = cfg.chapter(cid)
        llm = LLM(cfg, scope=ch.id, budget=args.max_usd, wallet=wallet)
        wallet = llm.wallet

        def todo(part: str) -> bool:
            """Готовый файл второй раз не делается: за него уже заплачено."""
            if args.only not in (None, part):
                return False
            if (ch.build_dir / f"{part}.json").exists() and not args.redo:
                print(f"{ch.id}: {part}.json уже есть, пропускаю (переделать — --redo)")
                return False
            return True

        try:
            if todo("layer"):
                rep = generate_layer(cfg, ch, llm, force=args.force)
                print(f"{ch.id}: узлов {rep['nodes']}, утверждений {rep['claims']}, ошибок проверки {len(rep['check_errors'])}")
                for e in rep["check_errors"]:
                    print("  " + e)
                for nid, msgs in rep["open_issues"].items():
                    for m in msgs:
                        print(f"  осталось {nid}: {m}")
                rc |= 1 if rep["check_errors"] or rep["missed_calls"] else 0
            layer = load_layer(ch)
            if todo("routing"):
                routing, issues = generate_routing(cfg, ch, llm, layer, force=args.force)
                print(f"{ch.id}: маршрутов {len(routing['routes'])}; {report(issues)}")
                rc |= 1 if any(i.level == "error" for i in issues) else 0
            if todo("tasks"):
                doc, issues = generate_tasks(cfg, ch, llm, layer, load_chapter_units(cfg, ch), force=args.force)
                print(f"{ch.id}: заданий {len(doc['tasks'])}; {report(issues)}")
                rc |= 1 if any(i.level == "error" for i in issues) else 0
            if todo("visual"):
                rp = ch.build_dir / "routing.json"
                routing = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else None
                rep = generate_visuals(
                    cfg, ch, llm, layer, load_chapter_units(cfg, ch), routing=routing, everything=bool(args.redo and args.only == "visual"), force=args.force
                )
                print(
                    f"{ch.id}: рисунков моделью {rep['accepted']} из {rep['nodes']}, по существу {rep['rich']};"
                    f" проверенных раньше {rep['reviewed']}, простой схемой {rep['nodes'] - rep['accepted']}"
                )
                for nid, msgs in rep["rejected"].items():
                    print(f"  простая схема {nid}: {'; '.join(msgs[:2])}")
        except (LLMError, ManualEdits, FileNotFoundError) as e:
            print(f"{ch.id}: {e}", file=sys.stderr)
            rc = 1
        finally:
            print(f"{ch.id}: вызовов {llm.calls}, из кэша {llm.cache_hits}, ${llm.spent:.3f}; {_money(cfg, wallet)}")
    if wallet:
        wallet.release()
    return rc


def _visual(cfg: Config, args) -> int:
    from .illustrations import generated_specs, load_visual_specs, reviewed_specs
    from .layer_gen import load_chapter_units, load_layer
    from .visual_check import RICH
    from .visual_gen import survey

    ch = cfg.chapter(args.chapter)
    rp = ch.build_dir / "routing.json"
    routing = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else None
    made, human = generated_specs(ch), reviewed_specs(cfg, ch)
    specs = made if args.generated else load_visual_specs(cfg, ch)
    own = set(made) if args.generated else set(made) - set(human)
    rows = survey(cfg, ch, load_layer(ch), load_chapter_units(cfg, ch), specs, routing, own)
    for r in rows:
        print(f"{r['node']}: {r['template']} ({r['by']})" + ("" if not r["issues"] else " — " + "; ".join(r["issues"][:3])))
    bad = sum(bool(r["issues"]) for r in rows)
    print(f"{ch.id}: рисунков {len(rows)}, по существу {sum(r['template'] in RICH for r in rows)}, с замечаниями {bad}; {dict(Counter(r['by'] for r in rows))}")
    return 1 if bad else 0


def _llm_check(cfg: Config) -> int:
    import os

    from .llm import LLM, LLMError

    try:
        llm = LLM(cfg)
        print(llm.describe())
        if llm.backend == "anthropic":
            import anthropic

            have = {m.id for m in anthropic.Anthropic(max_retries=1, timeout=30).models.list(limit=100)}
        else:
            import openai

            have = {m.id for m in openai.OpenAI(max_retries=1, timeout=30).models.list()}
        for role in ("senior", "fast"):
            m = llm.model_for(role)
            print(f"  {role}: {m} — {'доступна' if m in have else 'нет в списке моделей ключа'}")
        return 0
    except LLMError as e:
        print(e, file=sys.stderr)
        return 1
    except Exception as e:  # ответ SDK: тип ошибки без значения ключа
        env = "ANTHROPIC_API_KEY" if os.environ.get("ANTHROPIC_API_KEY") else "ключ"
        print(f"{env}: {type(e).__name__}", file=sys.stderr)
        return 1


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
    p.add_argument("--strict", action="store_true", help="смысловые замечания (отрицание, условие, допущение…) считать ошибками")

    p = sub.add_parser("gen")
    p.add_argument("chapter", nargs="+")
    p.add_argument("--only", choices=["layer", "routing", "tasks", "visual"], help="только одна часть")
    p.add_argument("--force", action="store_true", help="перезаписать файлы, изменённые вручную")
    p.add_argument("--redo", action="store_true", help="сделать заново то, что уже готово")
    p.add_argument("--max-usd", type=float, help="сколько можно потратить за этот запуск; без него платных вызовов нет")
    p.add_argument("--profile", help="набор моделей: cheap, mid, max")

    p = sub.add_parser("cost")
    p.add_argument("chapter", nargs="+")
    p.add_argument("--profile", help="набор моделей: cheap, mid, max")

    p = sub.add_parser("visual")
    p.add_argument("chapter")
    p.add_argument("--generated", action="store_true", help="только собранные конвейером, без проверенных человеком")

    sub.add_parser("llm")

    p = sub.add_parser("course")
    p.add_argument("chapter")
    p.add_argument("--layer", help="путь к layer.json (по умолчанию build/<chapter>/layer.json)")
    p.add_argument("--tasks", help="путь к tasks.json")
    p.add_argument("--routing", help="путь к routing.json")
    p.add_argument("--out", help="выходная папка курса (по умолчанию build/<chapter>/course)")

    p = sub.add_parser("specs")
    p.add_argument("--out", help="папка для спецификаций и паспортов (по умолчанию build/specs/)")

    p = sub.add_parser("run")
    p.add_argument("chapter")
    p.add_argument("--max-usd", type=float, help="сколько можно потратить; без него — только кэш и правила")
    p.add_argument("--profile", help="набор моделей: cheap, mid, max")
    p.add_argument("--video", action="store_true", help="собрать и ролики")
    p.add_argument("--domain", help="доменный пакет: geology (по умолчанию), drilling или путь к папке")

    p = sub.add_parser("plan")
    p.add_argument("chapter")
    p.add_argument("node", nargs="?")

    p = sub.add_parser("rebuild")
    p.add_argument("chapter")
    p.add_argument("node", nargs="?")
    p.add_argument("--video", action="store_true", help="пересобрать и устаревшие ролики")
    p.add_argument("--force", action="store_true", help="заменять сломанные задания и в tasks.json, правленном вручную")

    p = sub.add_parser("fuzz")
    p.add_argument("chapter")
    p.add_argument("--layer", help="путь к layer.json")
    p.add_argument("--limit", type=int, default=400, help="сколько ошибок внести всего")

    p = sub.add_parser("review")
    p.add_argument("package")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--team")
    p.add_argument("--max-usd", type=float, help="сколько можно потратить на модель; без него — только код и кэш")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--no-vision", action="store_true")
    p.add_argument("--chapter-file", help="путь к .md главы, если chapter_file в пакете не находится")

    p = sub.add_parser("video")
    p.add_argument("chapter")
    p.add_argument("--node", help="узел для ролика (по умолчанию — по маршрутизации)")
    p.add_argument("--format", choices=["shorts", "long"])
    p.add_argument("--only", action="append", help="id ролика")
    p.add_argument("--dry", action="store_true", help="только сценарий и паспорт, без синтеза и рендера")

    p = sub.add_parser("metrics")
    p.add_argument("chapter", nargs="*")
    p.add_argument("--manifest", action="store_true", help="напечатать таблицу для MANIFEST.md")

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

        strict = True if args.strict else None
        issues += check_layer(cfg, ch, layer, coverage=cov_data, strict=strict)
        if cov_data is not None:
            issues += check_coverage(cfg, ch, cov_data, layer, strict=strict)
        print(report(issues))
        return 1 if any(i.level == "error" for i in issues) else 0

    if args.cmd == "gen":
        return _gen(cfg, args)

    if args.cmd == "cost":
        return _cost(cfg, args)

    if args.cmd == "visual":
        return _visual(cfg, args)

    if args.cmd == "llm":
        return _llm_check(cfg)

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

    if args.cmd == "run":
        from .pipeline import run

        if args.domain:
            cfg.use_domain(args.domain)
        return run(cfg, args.chapter, max_usd=args.max_usd, profile=args.profile, video=args.video)

    if args.cmd in ("plan", "rebuild"):
        from .pipeline import plan, plan_text, rebuild

        ch = cfg.chapter(args.chapter)
        if args.cmd == "plan":
            layer = json.loads((ch.build_dir / "layer.json").read_text(encoding="utf-8"))
            print(plan_text(plan(cfg, ch, layer, args.node)))
            return 0
        return rebuild(cfg, ch, args.node, video=args.video, force=args.force)

    if args.cmd == "fuzz":
        from .fuzz import fuzz_chapter

        ch = cfg.chapter(args.chapter)
        rep, md = fuzz_chapter(cfg, ch, Path(args.layer) if args.layer else None, args.limit)
        print(f"{ch.id}: внесено {rep['injected']}, поймано {rep['caught']} ({(rep['recall'] or 0):.0%}); "
              f"фон {rep['baseline_flagged']} из {rep['claims']} утверждений → {md}")
        for kind, st in rep["by_kind"].items():
            print(f"  {kind}: {st['caught']}/{st['injected']}")
        return 0

    if args.cmd == "review":
        from .review import main as review_main

        argv2 = [args.package, "-o", args.out] + (["--team", args.team] if args.team else [])
        argv2 += (["--max-usd", str(args.max_usd)] if args.max_usd is not None else []) + (["--no-llm"] if args.no_llm else [])
        argv2 += (["--no-vision"] if args.no_vision else []) + (["--chapter-file", args.chapter_file] if args.chapter_file else [])
        return review_main(argv2)

    if args.cmd == "video":
        from .video import render_videos

        ch = cfg.chapter(args.chapter)
        layer = json.loads((ch.build_dir / "layer.json").read_text(encoding="utf-8"))
        res = render_videos(cfg, ch, layer, only=args.only, node_id=args.node, fmt=args.format, dry=args.dry)
        bad = [r for r in res if r["errors"] or r["status"] == "failed"]
        return 1 if bad or not res else 0

    if args.cmd == "metrics":
        from .metrics import chapter_metrics, manifest_section, write_metrics

        ids = args.chapter or [c for c in cfg.chapters if (cfg.chapters[c].build_dir / "layer.json").exists()]
        rows = [chapter_metrics(cfg, cfg.chapter(c)) for c in ids]
        print(write_metrics(cfg, rows))
        if args.manifest:
            print(manifest_section(rows))
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
