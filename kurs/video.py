"""Ролики из проверенного слоя: озвученные сцены, ни одного кадра или звука без проверки.

Устройство (вариант A архитектурного документа):
1. Сценарий (VideoSpec) собирается кодом из узла слоя. Озвучка каждой сцены — проверенное утверждение
   с цитатой и страницей. Новый текст — только связки (вопрос, переход) без чисел, с пометкой модели.
2. Кадры — HTML-сцены на тех же рисунках (SVG) и формулах (KaTeX), что и курс; Playwright снимает PNG.
3. Звук — tts.py: числа словами, словарь произношения и ударений; длительность сцены = длина фразы + пауза,
   поэтому синхронизация получается сама. Пауза на вопрос-предсказание — 1,5 с тишины.
4. ffmpeg склеивает сцены, loudnorm приводит к −16 LUFS; субтитры — из исходного текста с цифрами.
5. Проверка: громкость, WER по распознаванию, сдвиг начала фраз, длительность формата; паспорт по схеме.

Шортс 9:16 — 40–75 с, разбор 16:9 — 90–180 с. Генеративное видео не используется.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .config import ROOT, Chapter, Config
from .verify import Source, numbers

FORMATS = {
    "shorts": {"size": (1080, 1920), "vertical": True, "min": 40.0, "max": 75.0, "claims": (3, 5), "ru": "шортс"},
    "long": {"size": (1920, 1080), "vertical": False, "min": 90.0, "max": 180.0, "claims": (5, 10), "ru": "длинное видео"},
}
CPS = 13.5  # символов в секунду у диктора: оценка длительности до синтеза
PAUSE = 0.35
PREDICT_PAUSE = 1.5
UNVERIFIED_NOTE = "Автоматически не проверяется: кадры ролика — рисунки курса; пункт подтверждает специалист при просмотре."


def _plain(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\$+[^$]*\$+", "", t or "")).strip()


def _first_sentence(t: str, limit: int = 220) -> str:
    s = re.split(r"(?<=[.!?])\s+", _plain(t))[0] if t else ""
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"


def on_screen(text: str, limit: int = 7) -> str:
    """Подпись на экране: не длиннее 7 слов, без хвостовых предлогов."""
    ws = _plain(text).rstrip(".").split()
    if len(ws) <= limit:
        return " ".join(ws)
    cut = ws[:limit]
    while cut and len(cut[-1]) <= 2 and cut[-1].isalpha():  # хвостовой предлог, но не число
        cut.pop()
    return " ".join(cut) + "…"


def _no_numbers(text: str) -> str:
    """Связка модели не может нести чисел: фразы с числами выбрасываются."""
    keep = [s for s in re.split(r"(?<=[.!?])\s+", _plain(text)) if not numbers(s)]
    return " ".join(keep).strip()


# ---------------------------------------------------------------- выбор узлов
def pick_nodes(layer: dict, routing: dict | None) -> dict[str, str]:
    """{формат: id узла}. Видео — там, куда его направила маршрутизация; шортс — если нет, лучший по критериям."""
    from .rules import video_scores
    from .layer_gen import node_text

    routes = {r["node_id"]: r for r in (routing or {}).get("routes", [])}
    nodes = [n for n in layer["nodes"] if len(n.get("claims", [])) >= 3]

    def score(n: dict) -> tuple:
        r = routes.get(n["id"]) or {}
        sc = r.get("video_scores") or video_scores(n, node_text(n))
        return (sum(sc.values()), sum(v == 2 for v in sc.values()), len(n["claims"]))

    def routed(fmt_ru: str) -> list[dict]:
        return [n for n in nodes if fmt_ru in ((routes.get(n["id"]) or {}).get("format"), (routes.get(n["id"]) or {}).get("secondary_format"))]

    out: dict[str, str] = {}
    longs = sorted(routed("длинное видео") or [n for n in nodes if len(n["claims"]) >= 5] or nodes, key=score, reverse=True)
    if longs:
        out["long"] = longs[0]["id"]
    shorts = sorted(routed("шортс") or [n for n in nodes if n.get("threshold") or n.get("misconception")] or nodes, key=score, reverse=True)
    shorts = [n for n in shorts if n["id"] != out.get("long")] or shorts
    if shorts:
        out["shorts"] = shorts[0]["id"]
    return out


# ---------------------------------------------------------------- сценарий
def _illustrations(ch: Chapter) -> dict[str, list[dict]]:
    p = ch.build_dir / "illustrations.json"
    by: dict[str, list[dict]] = {}
    if p.exists():
        for ill in json.loads(p.read_text(encoding="utf-8")).get("illustrations", []):
            by.setdefault(ill["node_id"], []).append(ill)
    return by


def build_script(ch: Chapter, node: dict, fmt: str, misc_text: dict[str, str] | None = None) -> dict:
    """Сценарий ролика из узла. Каждая сцена: что звучит (speech_src), что на экране, на что опирается."""
    f = FORMATS[fmt]
    ills = _illustrations(ch).get(node["id"], [])
    claims = [c for c in node["claims"] if 25 <= len(_plain(c["text"])) <= 260 and "$" not in c["text"]]
    lo, hi = f["claims"]
    scenes: list[dict] = []

    def ill(i: int) -> dict | None:
        return ills[i % len(ills)] if ills else None

    # 1. крючок: заблуждение или зачем этот шаг — текст модели без чисел
    misc = (node.get("misconception") or {}).get("text") or ""
    misc_l = misc.rstrip(".")[:1].lower() + misc.rstrip(".")[1:] if misc else ""
    hook = _no_numbers(f"Часто думают, что {misc_l}. Проверим по книге." if misc else _first_sentence(node["why_here"]["text"]))
    first = claims[0] if claims else node["claims"][0]
    # Ответ на вопрос-предсказание выбирается заранее, чтобы не повторять сцену с тем же утверждением.
    from .claimcheck import overlap

    dec = node.get("decision_on_data") or {}
    answer_claim = None
    if misc and not (dec.get("features") and dec.get("conclusion")):
        answer_claim = max(claims or node["claims"], key=lambda c: overlap(misc, c["text"]))
    pool = [c for c in claims if c is not answer_claim]
    scenes.append({"kind": "hook", "speech_src": hook or node["title"], "on_screen": on_screen(node["title"]), "claim_id": first["id"],
                   "by_model": True, "is_analogy": False, "image": None, "page": first["page_pdf"]})

    # 2. утверждения автора: каждое — сцена с рисунком шага
    budget = f["max"] - 18.0
    used = len(hook) / CPS
    chosen: list[dict] = []
    for c in pool:
        need = len(_plain(c["text"])) / CPS + PAUSE
        if len(chosen) >= hi or (len(chosen) >= lo and used + need > budget):
            break
        chosen.append(c)
        used += need
    for i, c in enumerate(chosen):
        scenes.append({"kind": "claim", "speech_src": _plain(c["text"]), "on_screen": on_screen(c["text"]), "claim_id": c["id"],
                       "by_model": False, "is_analogy": False, "image": ill(i), "page": c["page_pdf"], "quote": c["quote"]})

    # 3. формула (только в разборе): на экране формула, вслух — смысл, без чтения символов
    if fmt == "long" and node.get("formulas"):
        fm = node["formulas"][0]
        meaning = _no_numbers(fm.get("meaning", "")) or "Формула автора связывает эти величины."
        scenes.append({"kind": "formula", "speech_src": meaning, "on_screen": on_screen(meaning), "latex": fm.get("latex_fixed") or fm["latex"],
                       "claim_id": chosen[-1]["id"] if chosen else first["id"], "by_model": True, "is_analogy": False, "image": None, "page": fm["page_pdf"]})

    # 4. вопрос-предсказание с паузой и ответ автора
    rest = [c for c in pool if c not in chosen]
    if dec.get("features") and dec.get("conclusion"):
        q = "Вопрос: по каким признакам здесь принимают решение? Подумайте."
        answer_src, answer_claim = _plain(dec["rule"]) or _plain(dec["conclusion"]), (rest or chosen or [first])[0]
    elif answer_claim is not None:
        q = _no_numbers(f"Так верно ли, что {misc_l}? Подумайте.") or "Так верно ли это? Подумайте."
        answer_src = _plain(answer_claim["text"])
    else:
        answer_claim = (rest or chosen or [first])[0]
        q = "Как думаете, что говорит об этом автор? Подумайте."
        answer_src = _plain(answer_claim["text"])
    scenes.append({"kind": "predict", "speech_src": q, "on_screen": "Пауза: ваш ответ?", "claim_id": answer_claim["id"], "by_model": True,
                   "is_analogy": False, "image": None, "page": answer_claim["page_pdf"], "pause": PREDICT_PAUSE})
    scenes.append({"kind": "answer", "speech_src": answer_src, "on_screen": on_screen(answer_src), "claim_id": answer_claim["id"],
                   "by_model": answer_src != _plain(answer_claim["text"]), "is_analogy": False, "image": ill(len(chosen)), "page": answer_claim["page_pdf"],
                   "quote": answer_claim["quote"]})

    # 5. мост к ИТ (если есть) и вопрос на перенос
    an = node.get("it_analogy") or {}
    bridge = _no_numbers(an.get("text", ""))
    if bridge and fmt == "long":
        scenes.append({"kind": "analogy", "speech_src": _first_sentence(bridge), "on_screen": "Аналогия из ИТ", "claim_id": first["id"],
                       "by_model": True, "is_analogy": True, "image": None, "page": first["page_pdf"]})
    transfer = _no_numbers(dec.get("counterexample") or "") or "Где ещё в данных скважин работает это правило, а где ломается?"
    transfer = f"На перенос: {transfer[:1].lower() + transfer[1:]}" if not transfer.endswith("?") else transfer
    scenes.append({"kind": "transfer", "speech_src": transfer, "on_screen": "Вопрос на перенос", "claim_id": first["id"], "by_model": True,
                   "is_analogy": False, "image": None, "page": first["page_pdf"]})

    vid = f"{ch.prefix}_{fmt}_{node['id'].split('.', 1)[-1]}"[:80]
    return {
        "video_id": vid, "node_id": node["id"], "format": fmt, "title": node["title"], "scenes": scenes,
        "prediction_question": q, "transfer_question": transfer,
        "data_it_bridge": bridge or _no_numbers((node.get("data_meaning") or {}).get("from_chapter", {}).get("text", "")) or "Мост к данным не описан в шаге.",
    }


# ---------------------------------------------------------------- кадры
_SCENE_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; margin: 0; }
body { width: %(w)dpx; height: %(h)dpx; overflow: hidden; background: #0f1720; color: #f2f5f7; font: 500 %(fs)dpx/1.25 "Inter", "Segoe UI", Arial, sans-serif;
       display: flex; flex-direction: column; padding: %(pad)dpx; gap: %(gap)dpx; }
.top { font-size: 0.5em; color: #9fb0bf; letter-spacing: .02em; }
h1 { font-size: 1.15em; font-weight: 700; }
.fig { flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center; background: #f7f8f5; border-radius: 18px; padding: 2%%; }
.fig img { max-width: 100%%; max-height: 100%%; object-fit: contain; }
.formula { flex: 1; display: flex; align-items: center; justify-content: center; font-size: 1.4em; }
.big { flex: 1; display: flex; align-items: center; font-size: 1.5em; font-weight: 700; }
.plaque { display: flex; gap: .6em; font-size: 0.5em; color: #0f1720; }
.plaque span { background: #e8d36b; border-radius: 8px; padding: .2em .6em; }
.plaque span.prov { background: #b9d7f0; }
.timer { font-size: .6em; color: #e8d36b; }
"""


def _scene_html(ch: Chapter, script: dict, s: dict, idx: int, course_dir: Path, fmt: str) -> str:
    from html import escape

    w, h = FORMATS[fmt]["size"]
    vertical = FORMATS[fmt]["vertical"]
    css = _SCENE_CSS % {"w": w, "h": h, "fs": 64 if vertical else 54, "pad": 72 if vertical else 64, "gap": 36 if vertical else 28}
    rel = Path("../../../course")  # build/<ch>/video/<vid>/scenes → build/<ch>/course
    head = (
        f'<meta charset="utf-8"><style>{css}</style>'
        f'<link rel="stylesheet" href="{rel}/assets/katex.min.css"><script src="{rel}/assets/katex.min.js"></script>'
    )
    body = [f'<div class="top">{escape(ch.title)}</div>', f"<h1>{escape(s['on_screen'])}</h1>"]
    label, prov = "схема", "пояснение" if s["by_model"] else "из книги"
    if s.get("image"):
        f = s["image"]["file"]
        if vertical:
            m = re.sub(r"\.svg$", "_m.svg", f)
            f = m if (course_dir / m).exists() else f
        body.append(f'<div class="fig"><img src="{rel}/{escape(f)}" alt=""></div>')
        label = s["image"].get("label") or label
    elif s["kind"] == "formula":
        body.append(f'<div class="formula" id="tex" data-tex="{escape(s["latex"].strip("$ "))}"></div>')
    else:
        body.append(f'<div class="big">{escape(s["speech_src"] if s["kind"] in ("predict", "transfer", "hook", "analogy") else "")}</div>')
    if s["kind"] == "predict":
        body.append('<div class="timer">⏸ 1,5 с на ответ</div>')
    body.append(f'<div class="plaque"><span>{escape(label)}</span><span class="prov">{prov}</span><span>стр. {s["page"]}</span></div>')
    script_js = (
        "<script>window.addEventListener('load',function(){var e=document.getElementById('tex');"
        "if(e&&window.katex){katex.render(e.dataset.tex,e,{displayMode:true,throwOnError:false});}document.body.dataset.ready='1';});</script>"
    )
    return f"<!doctype html><html lang=\"ru\"><head>{head}</head><body>{''.join(body)}{script_js}</body></html>"


def render_frames(ch: Chapter, script: dict, out: Path) -> list[Path]:
    """HTML-сцены → PNG через Playwright (video/render_scenes.mjs)."""
    course_dir = ch.build_dir / "course"
    if not (course_dir / "assets").exists():
        raise RuntimeError(f"нет курса {course_dir}: сначала kurs course {ch.id} (рисунки ролика — рисунки курса)")
    scenes_dir = out / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    w, h = FORMATS[script["format"]]["size"]
    jobs = []
    for i, s in enumerate(script["scenes"]):
        html = scenes_dir / f"s{i:02d}.html"
        html.write_text(_scene_html(ch, script, s, i, course_dir, script["format"]), encoding="utf-8")
        jobs.append({"html": str(html.resolve()), "png": str((scenes_dir / f"s{i:02d}.png").resolve()), "width": w, "height": h})
    (scenes_dir / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    subprocess.run(["node", str(ROOT / "video" / "render_scenes.mjs"), str(scenes_dir / "jobs.json")], check=True, cwd=ROOT)
    return [Path(j["png"]) for j in jobs]


# ---------------------------------------------------------------- сборка
def assemble(cfg: Config, ch: Chapter, script: dict, out: Path, log=print) -> dict:
    """Озвучка, склейка, громкость, распознавание. Возвращает таймкоды и замеры."""
    from . import tts

    pngs = render_frames(ch, script, out)
    audio_dir = out / "audio"
    t = 0.0
    segs = []
    for i, (s, png) in enumerate(zip(script["scenes"], pngs)):
        speech = tts.speech_text(s["speech_src"], cfg.domain_dir)
        a = tts.synth(speech, audio_dir / f"s{i:02d}.mp3")
        d = tts.duration(a)
        pause = s.get("pause", PAUSE)
        s.update(speech=speech, t_start=round(t, 2), t_end=round(t + d, 2))
        seg = out / "seg" / f"s{i:02d}.mp4"
        seg.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [tts.need("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(png), "-i", str(a),
             "-af", f"apad=pad_dur={pause}", "-t", f"{d + pause:.3f}", "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", "48000", "-ac", "1", str(seg)],
            check=True,
        )
        segs.append(seg)
        t += d + pause
    lst = out / "seg" / "list.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in segs), encoding="utf-8")
    raw = out / "raw.mp4"
    subprocess.run([tts.need("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(raw)], check=True)
    final = out / f"{script['video_id']}.mp4"
    tts.loudnorm(raw, final)
    lufs = tts.measure_lufs(final)
    dur = tts.duration(final)
    check: dict = {"duration_sec": round(dur, 2), "lufs": lufs}
    try:
        words = tts.transcribe(final)
        hyp = " ".join(w["word"] for w in words)
        ref = " ".join(s["speech"] for s in script["scenes"])
        check.update(wer=round(tts.wer(ref, hyp), 3), sync_drift_sec=tts.sync_drift(script["scenes"], words), asr="ok")
    except tts.TTSError as e:
        check.update(wer=None, sync_drift_sec=None, asr=str(e))
    f = FORMATS[script["format"]]
    problems = []
    if not f["min"] <= dur <= f["max"]:
        problems.append(f"длительность {dur:.1f} с вне {f['min']:.0f}–{f['max']:.0f} с")
    if abs(lufs + 16.0) > 1.0:
        problems.append(f"громкость {lufs} LUFS, нужно −16 ± 1")
    if check.get("wer") is not None and check["wer"] > 0.10:
        problems.append(f"WER {check['wer']:.0%} > 10%")
    if check.get("sync_drift_sec") is not None and check["sync_drift_sec"] > 0.5:
        problems.append(f"сдвиг {check['sync_drift_sec']} с > 0,5 с")
    if check.get("wer") is None:
        problems.append("распознавание не выполнено: " + check["asr"])
    check["problems"] = problems
    check["status"] = "passed" if not problems else "failed"
    import shutil

    shutil.copy2(pngs[0], out / "poster.png")
    return check


# ---------------------------------------------------------------- паспорт
def passport(cfg: Config, ch: Chapter, node: dict, script: dict, routing_entry: dict | None, audio_status: str) -> dict:
    from .video_spec import _build_checklist

    by_claim = {c["id"]: c for c in node["claims"]}
    sources, seen = [], set()
    for s in script["scenes"]:
        c = by_claim.get(s["claim_id"])
        if c and c["id"] not in seen:
            seen.add(c["id"])
            sources.append({"claim_id": c["id"], "page_or_dataset": f"стр. {c['page_pdf']}", "quote": c["quote"]})
    frames = []
    for s in script["scenes"]:
        t = round((s.get("t_start", 0.0) + s.get("t_end", 0.0)) / 2, 2)
        img = s.get("image") or {}
        frames.append({"t": t, "class": img.get("frame_class", "B"), "label": img.get("label", "схема")})
    checklist = _build_checklist({
        "pc.plausible_numbers": ("да", "Все числа в ролике взяты из утверждений слоя с дословной цитатой и страницей; связки модели без чисел (проверено кодом)."),
        "pc.label_matches": ("да", "Плашку кадра ставит код по происхождению рисунка курса: схема, симуляция или реальные данные."),
    }, cfg.domain_dir / "physics_checklist.json")
    for item in checklist:
        if item["answer"] == "н/п":
            item["note"] = UNVERIFIED_NOTE
    from .tts import load_dicts

    words = []
    _, stress = load_dicts(cfg.domain_dir)
    for s in script["scenes"]:
        for w in re.findall(r"[А-Яа-яЁё]+", s["speech_src"]):
            st = stress.get(w.lower())
            if st and st not in words:
                words.append(st)
    return {
        "video_id": script["video_id"],
        "node_id": node["id"],
        "node_card": {
            "chapter": ch.id,
            "title": node["title"],
            "difficulty": {"primary": node["difficulty"]["primary"], "secondary": node["difficulty"].get("secondary")},
            "format": FORMATS[script["format"]]["ru"],
            "duration_sec": round(script["scenes"][-1].get("t_end", sum(len(s["speech_src"]) / CPS for s in script["scenes"])), 1),
            "refutes_misconceptions": [l["target"] for l in node.get("links", []) if l["type"] == "refutes_misconception"],
        },
        "script": [
            {"t_start": s.get("t_start", 0.0), "t_end": s.get("t_end", 0.0), "text": s.get("speech", s["speech_src"]), "claim_id": s["claim_id"],
             "is_analogy": s["is_analogy"], "on_screen": s["on_screen"]}
            for s in script["scenes"]
        ],
        "sources": sources,
        "scene_spec": f"build/{ch.id}/video/{script['video_id']}/scene.json",
        "frames": frames,
        "exaggerations": ["масштабы на рисунках — как в рисунках курса, у каждого рисунка подписан масштаб или «схема без масштаба»"],
        "prediction_question": script["prediction_question"],
        "transfer_question": script["transfer_question"],
        "data_it_bridge": script["data_it_bridge"],
        "physics_checklist": checklist,
        "audio_check": {
            "target_lufs": -16.0, "max_wer": 0.10, "max_sync_drift_sec": 0.5, "prediction_pause_sec": PREDICT_PAUSE,
            "status": audio_status, "stress_review_words": words[:12],
        },
    }


def check_script(ch: Chapter, node: dict, script: dict) -> list[str]:
    """Сценарий против источника: цитаты на своих страницах, числа озвучки — только из утверждений, связки без чисел."""
    src = Source(ch)
    by_claim = {c["id"]: c for c in node["claims"]}
    node_nums = set().union(*(numbers(x["text"]) | numbers(x["quote"]) for x in node["claims"]))
    errs = []
    for i, s in enumerate(script["scenes"]):
        c = by_claim.get(s["claim_id"])
        if c is None:
            errs.append(f"сцена {i}: утверждение {s['claim_id']} не из узла")
            continue
        if s["by_model"] and s["kind"] in ("hook", "predict", "transfer", "analogy", "formula") and numbers(s["speech_src"]):
            errs.append(f"сцена {i}: связка модели с числами: {s['speech_src'][:80]}")
        elif s["by_model"] and numbers(s["speech_src"]) - node_nums:
            errs.append(f"сцена {i}: числа не из утверждений шага: {sorted(numbers(s['speech_src']) - node_nums)}")
        if not s["by_model"]:
            ok, hits = src.find_quote(c["quote"], c["page_pdf"])
            if not ok:
                errs.append(f"сцена {i}: цитата {c['id']} не на стр. {c['page_pdf']} ({hits})")
            extra = numbers(s["speech_src"]) - numbers(c["text"]) - numbers(c["quote"])
            if extra - node_nums:
                errs.append(f"сцена {i}: числа не из утверждений: {sorted(extra - node_nums)}")
        if len(s["on_screen"].split()) > 8:
            errs.append(f"сцена {i}: подпись длиннее 8 слов")
    if not any(s.get("pause", 0) >= 1.0 for s in script["scenes"]):
        errs.append("нет паузы на предсказание ≥ 1 с")
    return errs


def scene_coverage(layer: dict, ch: Chapter) -> dict:
    """Отчёт о покрытии узлов шаблонами сцен: какой ролик можно собрать по каждому узлу без ручной работы."""
    ills = _illustrations(ch)
    rows = []
    for n in layer["nodes"]:
        claims = [c for c in n.get("claims", []) if 25 <= len(_plain(c["text"])) <= 260 and "$" not in c["text"]]
        tpl = {
            "title": True, "claim_cards": len(claims), "figure": bool(ills.get(n["id"])), "formula": bool(n.get("formulas")),
            "predict": True, "decision": bool(n.get("decision_on_data")), "analogy": bool((n.get("it_analogy") or {}).get("text")),
        }
        rows.append({"node_id": n["id"], "title": n["title"], **tpl,
                     "shorts": len(claims) >= FORMATS["shorts"]["claims"][0], "long": len(claims) >= FORMATS["long"]["claims"][0]})
    total = len(rows) or 1
    return {"chapter": ch.id, "nodes": len(rows), "shorts_ready": sum(r["shorts"] for r in rows) / total,
            "long_ready": sum(r["long"] for r in rows) / total, "with_figure": sum(r["figure"] for r in rows) / total, "rows": rows}


# ---------------------------------------------------------------- вход
def render_videos(
    cfg: Config, ch: Chapter, layer: dict, *, only: list[str] | None = None, node_id: str | None = None, fmt: str | None = None,
    dry: bool = False, log=print,
) -> list[dict]:
    """Собирает ролики главы: по умолчанию шортс и разбор по узлам из маршрутизации. dry — только сценарий, кадры не снимаются."""
    import jsonschema

    from .layer_gen import load_misconceptions

    rp = ch.build_dir / "routing.json"
    routing = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else None
    routes = {r["node_id"]: r for r in (routing or {}).get("routes", [])}
    nodes = {n["id"]: n for n in layer["nodes"]}
    picks = {fmt or "long": node_id} if node_id else pick_nodes(layer, routing)
    if fmt and not node_id:
        picks = {fmt: picks.get(fmt)} if picks.get(fmt) else {}
    misc = {m["id"]: m["text"] for m in load_misconceptions(cfg)}
    vdir = ch.build_dir / "video"
    videos_path = ch.build_dir / "videos.json"
    vdoc = json.loads(videos_path.read_text(encoding="utf-8")) if videos_path.exists() else {"chapter": ch.id, "videos": []}
    schema_p = cfg.schemas_dir / "passport.schema.json"
    schema = json.loads(schema_p.read_text(encoding="utf-8")) if schema_p.exists() else None
    cov = scene_coverage(layer, ch)
    (ch.build_dir / "scene_coverage.json").write_text(json.dumps(cov, ensure_ascii=False, indent=1), encoding="utf-8")
    results = []
    for f, nid in picks.items():
        if nid not in nodes:
            log(f"  ролик {f}: узла {nid} нет в слое")
            continue
        node = nodes[nid]
        script = build_script(ch, node, f, misc)
        if only and script["video_id"] not in only:
            continue
        out = vdir / script["video_id"]
        out.mkdir(parents=True, exist_ok=True)
        errs = check_script(ch, node, script)
        status, check = "pending_render", {}
        if not dry and not errs:
            check = assemble(cfg, ch, script, out, log=log)
            status = check["status"]
        pas = passport(cfg, ch, node, script, routes.get(nid), status)
        (out / "scene.json").write_text(json.dumps({**script, "resolution": FORMATS[f]["size"], "fps": 30}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        (out / "passport.json").write_text(json.dumps(pas, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "audio_report.json").write_text(json.dumps(check, ensure_ascii=False, indent=1), encoding="utf-8")
        if schema:
            errs += [f"паспорт: {e.message[:160]}" for e in jsonschema.Draft202012Validator(schema).iter_errors(pas)]
        mp4 = out / f"{script['video_id']}.mp4"
        if mp4.exists() and not dry:
            entry = {
                "id": script["video_id"], "title": f"{'Шортс' if f == 'shorts' else 'Разбор'}: {node['title']}",
                "file": str(mp4.relative_to(ch.build_dir)), "poster": str((out / "poster.png").relative_to(ch.build_dir)),
                "node_ids": [nid], "claim_ids": sorted({s["claim_id"] for s in script["scenes"]}), "vertical": FORMATS[f]["vertical"],
                "cues": [{"t_start": s["t_start"], "t_end": s["t_end"], "text": s["speech_src"]} for s in script["scenes"]],
            }
            vdoc["videos"] = [v for v in vdoc["videos"] if v["id"] != entry["id"]] + [entry]
        log(f"  ролик {script['video_id']} ({FORMATS[f]['ru']}, узел {nid}): {status}" + (f"; замечания: {'; '.join(errs + check.get('problems', []))}" if errs or check.get("problems") else ""))
        results.append({"video_id": script["video_id"], "node_id": nid, "format": f, "status": status, "errors": errs, "check": check, "dir": out})
    videos_path.write_text(json.dumps(vdoc, ensure_ascii=False, indent=1), encoding="utf-8")
    return results
