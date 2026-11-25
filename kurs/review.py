"""Модуль проверки «Закладки»: слепая проверка чужого учебного пакета против главы.

    python -m kurs.review <package.json> -o build/review/blind.json [--max-usd 0.3] [--no-llm] [--no-vision]

Вход — review_package.schema.json: items (claim, analogy, script_line, scene_fact), scenes, frames (PNG), chapter_file.
Выход — review_output.schema.json: {package_id, team, remarks[]}; у замечания target, category (11 кодов),
severity (critical / significant / minor), problem, fix, page_pdf, quote. На один target — одно замечание:
жюри берёт только первое, поэтому из нескольких оставляется самое тяжёлое и самое доказанное.

Три слоя:
1. Код (без модели): цитата на своей странице, знак и направление, подмена числа, единицы и неравенства, потерянное
   условие и оговорка, обобщение, «измерено» вместо «вычислено», аналогия без границы, метка кадра.
2. Модель по тексту: claim, analogy, script_line, scene_fact против страниц главы, сцен и списка заблуждений.
3. Модель со зрением: PNG-кадры против shows, label, plaque, сцены и стайлгайда.

Точность важнее полноты (ложных тревог ≤ 2). Второй проход — строгий фильтр: замечание модели остаётся, только если
её цитата-доказательство дословно найдена в главе (страница уточняется кодом), а скептическая перепроверка
подтверждает противоречие. Верные, но странно звучащие утверждения-приманки так отсеиваются.

key.json модуль не открывает: его читает только score_review.py после сохранения слепого результата.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

from .claimcheck import _FLIP_WORDS, claim_findings, content_stems, overlap, sentences
from .config import Chapter, Config
from .verify import Source, extract_quantities, norm

CATEGORIES = [
    "overgeneralization", "sign_flip", "measured_vs_inferred", "lost_condition", "missing_caveat", "wrong_scaling",
    "wrong_mechanism", "outdated_as_current", "analogy_without_limit", "frame_error", "other",
]
SEVERITIES = ["critical", "significant", "minor"]
WEIGHT = {"critical": 3, "significant": 2, "minor": 1}
FRAME_LABELS = ("реальные данные", "симуляция", "схема")

# Код проверки (claimcheck / verify) → категория организаторов и тяжесть
CODE_MAP: dict[str, tuple[str, str]] = {
    "direction_flip": ("sign_flip", "critical"),
    "negation_dropped": ("sign_flip", "critical"),
    "negation_added": ("sign_flip", "critical"),
    "condition_flipped": ("sign_flip", "critical"),
    "number_not_in_source": ("wrong_scaling", "critical"),
    "unit_mismatch": ("wrong_scaling", "critical"),
    "condition_dropped": ("lost_condition", "significant"),
    "assumption_as_fact": ("missing_caveat", "significant"),
    "overgeneralization": ("overgeneralization", "significant"),
    "measured_vs_inferred": ("measured_vs_inferred", "significant"),
    "analogy_without_limit": ("analogy_without_limit", "significant"),
    "frame_label": ("frame_error", "significant"),
    "quote_not_on_page": ("other", "minor"),
    "quote_not_found": ("other", "minor"),
}
MIN_SUPPORT = 0.55  # доля смысловых слов элемента в опоре: ниже — опоры нет, код молчит

# «Вычислено, оценено, по интерпретации» у автора, а в пакете — «измерено»
_INFERRED = re.compile(r"(?<!\w)(?:рассчит\w*|вычисл\w*|оцен\w*|определя\w* по|по данным интерпретац\w*|интерпрет\w*|предполаг\w*|"
                       r"по-видимому|вероятно|следует|косвенн\w*|по формуле|модел\w*|estimated|computed|inferred|derived)(?!\w)", re.I)
_MEASURED = re.compile(r"(?<!\w)(?:измер\w*|замер\w*|зарегистрир\w*|прям\w* измерен\w*|наблюда\w*|measured|recorded|observed)(?!\w)", re.I)


# ---------------------------------------------------------------- пакет
def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def find_schema(cfg: Config, name: str, near: Path | None = None) -> dict | None:
    dirs = [near] if near else []
    dirs += [cfg.input_dir / "пакет_вводных" / "02_модуль_проверки", cfg.schemas_dir, cfg.input_dir]
    for d in dirs:
        if d is None:
            continue
        for p in (d / name, d.parent / name):
            if p.exists():
                return load_json(p)
    return None


def item_text(it: dict) -> str:
    for k in ("text", "statement", "claim", "line", "fact", "content"):
        if isinstance(it.get(k), str) and it[k].strip():
            return it[k].strip()
    return ""


def item_kind(it: dict) -> str:
    """Тип элемента: claim | analogy | script_line | scene_fact. У claim поле kind — эпистемика, а не тип."""
    t = it.get("type") or it.get("item_type")
    if t:
        return str(t)
    if it.get("kind") in ("claim", "analogy", "script_line", "scene_fact"):
        return str(it["kind"])
    if "breaks_where" in it:
        return "analogy"
    if "t_start" in it or "claim_ref" in it:
        return "script_line"
    if "spec_path" in it:
        return "scene_fact"
    return "claim"


def chapter_of(cfg: Config, pkg: dict, pkg_path: Path, override: str | None = None) -> Chapter:
    from .units import build_units, cut_chapter

    raw = override or pkg.get("chapter_file") or ""
    p = Path(raw)
    cands = [p] if p.is_absolute() else [pkg_path.parent / p, cfg.input_dir / p, Path.cwd() / p]
    md = next((c for c in cands if c.exists()), None)
    if md is None:
        raise FileNotFoundError(f"chapter_file не найден: {raw} (искал рядом с пакетом и в KURS_INPUT_DIR)")
    ch = cfg.chapter(str(md.resolve()))
    cut_chapter(cfg, ch)
    build_units(cfg, ch)
    return ch


# ---------------------------------------------------------------- опора и исправление
def find_support(src: Source, text: str, page: int | None) -> tuple[str, int, float]:
    """Предложение главы с наибольшим числом общих смысловых слов: (текст, страница, доля). Сначала страница и соседние."""
    pages = sorted(src.pages)
    best = ("", page if page is not None else (pages[0] if pages else 0), 0.0)
    if not content_stems(text):
        return best
    near = [p for p in pages if page is not None and abs(p - page) <= 1]
    for scope in ([near] if near else []) + [pages]:
        for p in scope:
            sents = sentences(re.sub(r"<!--.*?-->", " ", src.pages[p]))
            for s in sents + [a + " " + b for a, b in zip(sents, sents[1:])]:
                sc = overlap(text, s)
                if sc > best[2] + 1e-9 or (abs(sc - best[2]) < 1e-9 and best[0] and len(s) < len(best[0])):
                    best = (s, p, sc)
        if best[2] >= MIN_SUPPORT:
            break
    return best


def corrected(text: str, code: str, support: str) -> str:
    """Исправленная формулировка: точечная правка, где это надёжно, иначе — слова автора."""
    if code == "direction_flip":
        sup = norm(support)
        for w in re.findall(r"[А-Яа-яЁёA-Za-z]+", text):
            opp = _FLIP_WORDS.get(w.lower()) or _FLIP_WORDS.get(w.lower().replace("ё", "е"))
            if opp and norm(opp) in sup and norm(w) not in sup:
                return re.sub(rf"(?<!\w){re.escape(w)}(?!\w)", opp if w[:1].islower() else opp[:1].upper() + opp[1:], text, count=1)
    if code in ("number_not_in_source", "unit_mismatch"):
        mine = [q for q in extract_quantities(text)]
        theirs = [q for q in extract_quantities(support)]
        tv = {q.value for q in theirs}
        bad = [q for q in mine if q.value not in tv]
        good = [q for q in theirs if q.value not in {m.value for m in mine}]
        if len(bad) == 1 and len(good) == 1:
            m = re.search(r"\d+(?:[.,]\d+)?", bad[0].raw)
            g = re.search(r"\d+(?:[.,]\d+)?", good[0].raw)
            if m and g and m.group(0) in text:
                return text.replace(m.group(0), g.group(0), 1)
    return f"Как у автора: «{support.strip()}»"


# ---------------------------------------------------------------- слой 1: код
def _remark(target: str, code_or_cat: str, problem: str, fix: str, page: int | None, quote: str = "", severity: str | None = None,
            source: str = "code", evidence: str = "") -> dict:
    cat, sev = CODE_MAP.get(code_or_cat, (code_or_cat if code_or_cat in CATEGORIES else "other", "significant"))
    return {
        "target": target, "category": cat, "severity": severity or sev, "problem": problem.strip(), "fix": fix.strip(),
        "page_pdf": page, "quote": (quote or evidence)[:400], "_source": source, "_evidence": evidence or quote,
    }


def check_item(src: Source, it: dict, claims_by_id: dict[str, dict], pkg_dir: Path) -> list[dict]:
    """Детерминированные замечания к одному элементу: только там, где у автора есть явная опора."""
    tid, kind, text = str(it.get("id")), item_kind(it), item_text(it)
    page = it.get("page_pdf")
    out: list[dict] = []

    if kind == "analogy":
        bw = it.get("breaks_where")
        if bw is None or not str(bw).strip():
            out.append(_remark(tid, "analogy_without_limit", "У аналогии не указано, где она перестаёт работать.",
                               "Добавить границу применимости: где аналогия ломается и почему (поле breaks_where).", page or _first_page(src)))
        return out  # остальное в аналогии — своими словами, код молчит

    ref = claims_by_id.get(str(it.get("claim_ref") or "")) if kind == "script_line" else None
    quote = it.get("quote") if isinstance(it.get("quote"), str) else (ref or {}).get("quote")
    qpage = page if page is not None else (ref or {}).get("page_pdf")
    support, spage, score = "", qpage, 0.0
    if quote:
        ok, hits = src.find_quote(quote, qpage if qpage is not None else -1)
        if ok:
            support, spage, score = quote, qpage, 1.0
        elif hits:
            support, spage, score = quote, hits[0], 1.0
            if kind == "claim":
                out.append(_remark(tid, "quote_not_on_page", f"Цитата на стр. {hits[0]}, а указана стр. {qpage}.",
                                   f"Указать page_pdf = {hits[0]}.", hits[0], quote=quote))
    if score < MIN_SUPPORT and text:
        support, spage, score = find_support(src, text, qpage)
    if score < MIN_SUPPORT or not text:
        return out
    if quote and support == quote and overlap(text, quote) < 0.3:
        # цитата есть, но про другое: опору ищем по тексту
        s2, p2, sc2 = find_support(src, text, qpage)
        if sc2 >= MIN_SUPPORT:
            support, spage = s2, p2

    for f in claim_findings(text, support):
        if f.code == "quote_unsupported":
            continue
        out.append(_remark(tid, f.code, _problem(f.code, f.msg), corrected(text, f.code, support), spage, quote=support, evidence=support))

    # «измерено» вместо «вычислено»
    if kind in ("claim", "script_line", "scene_fact"):
        epi = str(it.get("kind") or "").lower()
        says_measured = bool(_MEASURED.search(text)) or epi in ("observation", "measured", "наблюдение")
        if says_measured and _INFERRED.search(support) and not _INFERRED.search(text) and overlap(text, support) >= 0.5:
            out.append(_remark(tid, "measured_vs_inferred", "Подано как прямое измерение, а у автора это вывод или расчёт.",
                               f"Указать, что величина получена расчётом или интерпретацией: «{support.strip()[:220]}»", spage,
                               quote=support, evidence=support))

    # подпись на экране не может спорить с фразой
    if kind == "script_line" and it.get("on_screen"):
        extra = {q.value for q in extract_quantities(str(it["on_screen"]))} - {q.value for q in extract_quantities(text + " " + support)}
        if extra:
            out.append(_remark(tid, "number_not_in_source", f"На экране число {', '.join(sorted(extra))}, которого нет ни в фразе, ни у автора.",
                               "Подпись на экране привести к числу из фразы и источника.", spage, quote=support, evidence=support))
    return out


def _problem(code: str, msg: str) -> str:
    return {
        "direction_flip": "Направление изменения обратно авторскому", "negation_dropped": "Потеряно отрицание автора",
        "negation_added": "Добавлено отрицание, которого у автора нет", "condition_flipped": "Перевёрнута граница (больше/меньше)",
        "number_not_in_source": "Число не совпадает с текстом автора", "unit_mismatch": "Единица измерения не та, что у автора",
        "condition_dropped": "Потеряно условие, при котором утверждение верно", "assumption_as_fact": "Оговорка автора выдана за безусловный факт",
        "overgeneralization": "Обобщение, которого у автора нет",
    }.get(code, code) + f": {msg}."


def check_frames(frames: list[dict], pkg_dir: Path, src: Source) -> list[dict]:
    """Код по кадрам: метка происхождения из разрешённого списка (plaque по схеме опциональна)."""
    out = []
    for fr in frames:
        tid = str(fr.get("id"))
        label = str(fr.get("label") or "").strip().lower()
        if label and label not in FRAME_LABELS:
            out.append(_remark(tid, "frame_label", f"Метка происхождения «{fr.get('label')}» не из списка: {', '.join(FRAME_LABELS)}.",
                               "Поставить метку по источнику кадра: реальные данные, симуляция или схема.", _first_page(src)))
    return out


def _first_page(src: Source) -> int:
    return min(src.pages) if src.pages else 1


# ---------------------------------------------------------------- слой 2 и 3: модель
_REMARKS_SCHEMA = {
    "type": "object",
    "properties": {
        "remarks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "severity": {"type": "string", "enum": SEVERITIES},
                    "problem": {"type": "string"},
                    "fix": {"type": "string"},
                    "page_pdf": {"type": "integer"},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["target", "category", "severity", "problem", "fix", "page_pdf", "evidence_quote"],
            },
        }
    },
    "required": ["remarks"],
}
_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"target": {"type": "string"}, "keep": {"type": "boolean"}, "why": {"type": "string"}},
                "required": ["target", "keep", "why"],
            },
        }
    },
    "required": ["verdicts"],
}
_CAT_HELP = """Категории (ровно одна):
- overgeneralization — обобщение сверх сказанного автором («всегда», «любой», частное выдано за общее);
- sign_flip — знак, направление или отрицание наоборот (растёт/падает, выше/ниже, есть/нет, больше/меньше);
- measured_vs_inferred — вычисленное, оценённое или интерпретированное подано как измеренное (или наоборот);
- lost_condition — потеряно условие применимости («при…», «если…», «только когда…»);
- missing_caveat — снята оговорка автора («как правило», «может», «приближённо»);
- wrong_scaling — неверное число, порядок величины, единица или масштаб;
- wrong_mechanism — неверная причина или механизм процесса;
- outdated_as_current — устаревшая практика подана как современная;
- analogy_without_limit — аналогия без указания, где она перестаёт работать;
- frame_error — ошибка кадра (глубина вверх, газ под нефтью, неверная метка происхождения, неподписанное преувеличение, сломанная шкала);
- other — другое.
Тяжесть: critical — меняет вывод или физику; significant — искажает смысл; minor — неточность."""
_RULES = """Ты рецензент учебного пакета по нефтегазовой геологии. Ищешь только настоящие ошибки против текста главы.
Точность важнее полноты: ложная тревога хуже пропуска. В пакете есть верные, но странно звучащие утверждения-приманки —
их НЕ отмечай. Замечание делай, только если в главе есть фраза, которая прямо противоречит элементу или показывает
потерянное условие/оговорку. Эту фразу верни ДОСЛОВНО в evidence_quote (копия из главы, без пересказа) и её страницу
в page_pdf (по меткам <!-- page N -->). fix — исправленная формулировка элемента целиком (или что поправить в кадре).
В поле target пиши ТОЛЬКО точный id элемента/кадра из списка (например C012, A03, L08, S04, F06), без пояснений и текста.
На один элемент — не больше одного замечания, самого важного. Ничего не нашёл — пустой список."""


def _remarks_schema(allowed_targets: list[str] | None = None) -> dict:
    sc = copy.deepcopy(_REMARKS_SCHEMA)
    if allowed_targets:
        sc["properties"]["remarks"]["items"]["properties"]["target"]["enum"] = list(dict.fromkeys(allowed_targets))
    return sc


def _verdict_schema(allowed_targets: list[str] | None = None) -> dict:
    sc = copy.deepcopy(_VERDICT_SCHEMA)
    if allowed_targets:
        sc["properties"]["verdicts"]["items"]["properties"]["target"]["enum"] = list(dict.fromkeys(allowed_targets))
    return sc


def _resolve_target(raw: str, known: set[str]) -> str | None:
    s = (raw or "").strip()
    if s in known:
        return s
    hits = {
        m.group(1)
        for m in re.finditer(r"(?<![A-Za-z0-9_.-])([A-Z][A-Z0-9_.-]*)(?![A-Za-z0-9_.-])", s)
        if m.group(1) in known
    }
    if len(hits) == 1:
        return next(iter(hits))
    return None


def _chapter_block(src: Source) -> str:
    return "\n".join(f"<!-- page {p} -->\n{src.pages[p]}" for p in sorted(src.pages))


def llm_text_audit(llm, src: Source, items: list[dict], scenes: list, misc: list[dict], pkg_dir: Path, batch: int = 25, log=print) -> list[dict]:
    """Слой 2: смысловой аудит всех элементов батчами (глава в общей части — кэшируется)."""
    from .llm import LLMError

    system = [_RULES + "\n\n" + _CAT_HELP, "Глава:\n" + _chapter_block(src),
              "Типичные заблуждения домена:\n" + "\n".join(f"{m['id']}: {m['text']}" for m in misc)]
    view = []
    for it in items:
        v = {k: it[k] for k in it if k not in ("raw",)}
        if item_kind(it) == "scene_fact" and it.get("spec_path"):
            sp = pkg_dir / str(it["spec_path"])
            if sp.exists():
                v["scene_spec"] = load_json(sp)
        view.append(v)
    scenes_blob = json.dumps(scenes, ensure_ascii=False)[:20000]
    out: list[dict] = []
    for i in range(0, max(1, len(view)), batch):
        part = view[i : i + batch]
        if not part:
            continue
        user = "Сцены роликов (контекст):\n" + scenes_blob + "\n\nЭлементы пакета:\n" + json.dumps(part, ensure_ascii=False)
        schema = _remarks_schema([str(x["id"]) for x in part])
        try:
            data = llm.json("senior", system, user, schema, label=f"review-text-{i // batch}", effort="high", max_tokens=16000)
            out += [_from_llm(r, "llm") for r in data["remarks"]]
        except LLMError as e:
            log(f"смысловой аудит (батч {i // batch}) пропущен: {str(e)[:160]}")
    return out


def llm_frame_audit(llm, src: Source, frames: list[dict], scenes: list, styleguide: dict, pkg_dir: Path, batch: int = 3, log=print) -> list[dict]:
    """Слой 3: кадры моделью со зрением, по 3 за запрос."""
    from .llm import LLMError

    out = []
    system = [
        _RULES + "\n\n" + _CAT_HELP + "\n\nДля кадров категория почти всегда frame_error. Сверяй картинку с полями shows, label, plaque, "
        "со сценой и со стайлгайдом: глубина растёт вниз, газ над нефтью над водой, метка происхождения соответствует источнику, "
        "преувеличение масштаба подписано, шкалы монотонны и подписаны. evidence_quote — фраза главы, подтверждающая исправление.",
        "Стайлгайд:\n" + json.dumps(styleguide, ensure_ascii=False)[:8000],
        "Глава:\n" + _chapter_block(src),
    ]
    for i in range(0, len(frames), batch):
        part = [f for f in frames[i : i + batch] if (pkg_dir / str(f.get("file", ""))).exists()]
        if not part:
            continue
        meta = [{k: v for k, v in f.items()} for f in part]
        user = "Сцены (контекст):\n" + json.dumps(scenes, ensure_ascii=False)[:12000] + "\n\nКадры по порядку картинок:\n" + json.dumps(meta, ensure_ascii=False)
        schema = _remarks_schema([str(f["id"]) for f in part])
        try:
            data = llm.json("senior", system, user, schema, label=f"review-frames-{i // batch}", effort="high", max_tokens=16000,
                            images=[pkg_dir / str(f["file"]) for f in part])
            out += [_from_llm(r, "vision") for r in data["remarks"]]
        except LLMError as e:
            log(f"проверка кадров (батч {i // batch}) пропущена: {str(e)[:160]}")
    return out


def _from_llm(r: dict, source: str) -> dict:
    return {"target": r["target"], "category": r["category"], "severity": r["severity"], "problem": r["problem"], "fix": r["fix"],
            "page_pdf": r["page_pdf"], "quote": r["evidence_quote"][:400], "_source": source, "_evidence": r["evidence_quote"]}


def evidence_ok(src: Source, r: dict) -> bool:
    """Строгий фильтр: доказательство дословно есть в главе; страница уточняется по тексту."""
    ev = (r.get("_evidence") or "").strip()
    if len(norm(ev)) < 12:
        return False
    ok, hits = src.find_quote(ev, r.get("page_pdf") if r.get("page_pdf") is not None else -1)
    if ok:
        return True
    if hits:
        r["page_pdf"] = hits[0]
        return True
    return False


def skeptic(llm, src: Source, remarks: list[dict], items_by_id: dict[str, dict], frames_by_id: dict[str, dict]) -> set[str]:
    """Второй проход по тексту: модель ищет, почему замечание может быть ложным. Возвращает target'ы, которые оставить."""
    if not remarks:
        return set()
    system = [
        "Ты строгий проверяющий. Тебе дают элемент учебного пакета, замечание рецензента и фразу главы-доказательство. "
        "Оставь замечание (keep=true), только если фраза главы прямо противоречит элементу или элемент явно теряет условие/оговорку "
        "автора, а исправление верно. Если элемент — верный пересказ, допустимое упрощение или странно звучащая, но правильная "
        "мысль (приманка), — keep=false. Сомневаешься — keep=false.",
        "Глава:\n" + _chapter_block(src),
    ]
    view = [{"target": r["target"], "element": items_by_id.get(r["target"]) or frames_by_id.get(r["target"]), "category": r["category"],
             "problem": r["problem"], "fix": r["fix"], "evidence": r["_evidence"], "page_pdf": r["page_pdf"]} for r in remarks]
    data = llm.json("senior", system, json.dumps(view, ensure_ascii=False), _verdict_schema([r["target"] for r in remarks]),
                    label="review-skeptic", effort="high", max_tokens=12000)
    return {v["target"] for v in data["verdicts"] if v["keep"]}


def vision_skeptic(llm, src: Source, remarks: list[dict], frames_by_id: dict[str, dict], pkg_dir: Path) -> set[str]:
    """Второй проход по кадрам: передаём сами PNG-кадры, чтобы скептик видел картинку и отсеивал ложные тревоги."""
    if not remarks:
        return set()
    imgs: list[Path] = []
    view = []
    for r in remarks:
        fr = frames_by_id.get(r["target"]) or {}
        p = pkg_dir / str(fr.get("file", ""))
        if p.exists():
            imgs.append(p)
        view.append({
            "target": r["target"],
            "frame": fr,
            "category": r["category"],
            "problem": r["problem"],
            "fix": r["fix"],
            "evidence": r.get("_evidence", ""),
            "page_pdf": r.get("page_pdf"),
        })
    system = [
        "Ты строгий визуальный проверяющий. Смотри на сами PNG-кадры и метаданные (shows, label, plaque). "
        "Оставь замечание (keep=true), только если на картинке реально видна физическая ошибка, неверная ось/направление глубины, "
        "противоречие с shows/label или потерянное обязательное предупреждение. Если кадр корректен или замечание придирчиво — keep=false.",
        "Глава:\n" + _chapter_block(src),
    ]
    data = llm.json(
        "senior", system, json.dumps(view, ensure_ascii=False), _verdict_schema([r["target"] for r in remarks]),
        label="review-vision-skeptic", effort="high", max_tokens=12000, images=imgs or None,
    )
    return {v["target"] for v in data["verdicts"] if v["keep"]}


# ---------------------------------------------------------------- сборка ответа
def pick_one(remarks: list[dict]) -> list[dict]:
    """Одно замечание на target: доказанное кодом важнее модели, тяжёлое — лёгкого, конкретная категория — other."""
    rank = {"code": 0, "vision": 1, "llm": 2}
    best: dict[str, dict] = {}
    for r in remarks:
        key = (rank.get(r.get("_source", "llm"), 3), -WEIGHT[r["severity"]], r["category"] == "other")
        cur = best.get(r["target"])
        if cur is None or key < cur[0]:
            best[r["target"]] = (key, r)
    return [r for _, r in best.values()]


def clean(r: dict, pages: set[int]) -> dict:
    out = {k: v for k, v in r.items() if not k.startswith("_")}
    if out.get("page_pdf") not in pages and pages:
        out["page_pdf"] = min(pages, key=lambda p: abs(p - int(out.get("page_pdf") or 0)))
    if not out.get("quote"):
        out.pop("quote", None)
    return out


def review(cfg: Config, pkg_path: Path, out_path: Path, *, team: str | None = None, max_usd: float | None = None,
           use_llm: bool = True, use_vision: bool = True, chapter_file: str | None = None, llm=None, log=print) -> dict:
    if pkg_path.name.lower() == "key.json":
        raise ValueError("key.json не читается: его открывает только score_review.py после сохранения слепого результата")
    pkg = load_json(pkg_path)
    pkg_dir = pkg_path.parent
    schema_in = find_schema(cfg, "review_package.schema.json", pkg_dir)
    errors: list[str] = []
    if schema_in:
        import jsonschema

        errors += [f"вход: {e.message[:200]}" for e in jsonschema.Draft202012Validator(schema_in).iter_errors(pkg)]
    ch = chapter_of(cfg, pkg, pkg_path, chapter_file)
    src = Source(ch)
    items = [i for i in pkg.get("items", []) if isinstance(i, dict) and i.get("id") is not None]
    frames = [f for f in pkg.get("frames", []) if isinstance(f, dict) and f.get("id") is not None]
    scenes = pkg.get("scenes", [])
    claims_by_id = {str(i["id"]): i for i in items if item_kind(i) == "claim"}
    items_by_id = {str(i["id"]): i for i in items}
    frames_by_id = {str(f["id"]): f for f in frames}

    remarks: list[dict] = []
    for it in items:
        remarks += check_item(src, it, claims_by_id, pkg_dir)
    remarks += check_frames(frames, pkg_dir, src)
    log(f"код: замечаний {len(remarks)} по {len({r['target'] for r in remarks})} элементам")

    if use_llm:
        from .layer_gen import load_misconceptions
        from .llm import LLM, LLMError

        llm = llm or LLM(cfg, scope="review", budget=max_usd)
        model_remarks: list[dict] = []
        try:
            model_remarks += llm_text_audit(llm, src, items, scenes, load_misconceptions(cfg), pkg_dir, log=log)
        except LLMError as e:
            log(f"смысловой аудит пропущен: {str(e)[:160]}")
        if use_vision and frames:
            sg_p = cfg.domain_dir / "styleguide.json"
            try:
                model_remarks += llm_frame_audit(llm, src, frames, scenes, load_json(sg_p) if sg_p.exists() else {}, pkg_dir, log=log)
            except LLMError as e:
                log(f"проверка кадров пропущена: {str(e)[:160]}")
        known = set(items_by_id) | set(frames_by_id)
        resolved: list[dict] = []
        for r in model_remarks:
            tgt = _resolve_target(str(r.get("target") or ""), known)
            if tgt is None:
                continue
            r["target"] = tgt
            resolved.append(r)
        model_remarks = resolved
        kept: list[dict] = []
        for r in model_remarks:
            if r["_source"] == "vision":
                if r["target"] in frames_by_id and str(r.get("problem") or "").strip() and str(r.get("fix") or "").strip():
                    evidence_ok(src, r)  # уточняем page_pdf по тексту, но не выкидываем визуальный дефект без цитаты
                    kept.append(r)
            elif evidence_ok(src, r):
                kept.append(r)
        log(f"модель: замечаний {len(model_remarks)}, после проверки цитат/кадров {len(kept)}")
        remarks += kept

        dedup = pick_one(remarks)
        objective_keep = {
            r["target"] for r in dedup
            if r.get("_source") == "code" and r.get("category") in ("analogy_without_limit", "frame_error")
        }
        vision_pool = [r for r in dedup if r.get("_source") == "vision" and r["target"] not in objective_keep]
        text_pool = [r for r in dedup if r.get("_source") != "vision" and r["target"] not in objective_keep]

        survivors = set(objective_keep)
        try:
            survivors |= skeptic(llm, src, text_pool, items_by_id, frames_by_id)
            survivors |= vision_skeptic(llm, src, vision_pool, frames_by_id, pkg_dir)
            before = len(dedup)
            remarks = [r for r in dedup if r["target"] in survivors]
            log(f"второй проход: оставлено {len(remarks)} из {before}")
        except LLMError as e:
            # без перепроверки оставляем только доказанное кодом: точность важнее полноты
            log(f"второй проход недоступен ({str(e)[:120]}): оставляю только замечания кода")
            remarks = [r for r in remarks if r["_source"] == "code"]

    final = [clean(r, set(src.pages)) for r in pick_one(remarks)]
    order = {str(x.get("id")): i for i, x in enumerate(items + frames)}
    final.sort(key=lambda r: order.get(r["target"], 10**6))
    doc = {"package_id": str(pkg.get("package_id") or pkg.get("id") or pkg_path.stem), "team": team or (cfg.raw.get("review") or {}).get("team") or "kmg-digital",
           "remarks": final}
    schema_out = find_schema(cfg, "review_output.schema.json", pkg_dir)
    if schema_out:
        import jsonschema

        errors += [f"выход: {e.message[:200]}" for e in jsonschema.Draft202012Validator(schema_out).iter_errors(doc)]
    else:
        errors += [f"выход: {e}" for e in self_check(doc)]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"doc": doc, "errors": errors, "out": out_path, "chapter": ch.id}


def self_check(doc: dict) -> list[str]:
    """Проверка контракта, если схемы рядом нет."""
    errs = []
    for k in ("package_id", "team", "remarks"):
        if k not in doc:
            errs.append(f"нет поля {k}")
    seen = set()
    for r in doc.get("remarks", []):
        for k in ("target", "category", "severity", "problem", "fix", "page_pdf"):
            if r.get(k) in (None, ""):
                errs.append(f"{r.get('target')}: пустое {k}")
        if r.get("category") not in CATEGORIES:
            errs.append(f"{r.get('target')}: категория {r.get('category')}")
        if r.get("severity") not in SEVERITIES:
            errs.append(f"{r.get('target')}: тяжесть {r.get('severity')}")
        if r.get("target") in seen:
            errs.append(f"{r.get('target')}: второе замечание на тот же target")
        seen.add(r.get("target"))
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m kurs.review", description="Модуль «Закладки»: слепая проверка пакета")
    ap.add_argument("package")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--team", help="название команды в ответе (иначе review.team в config.yaml)")
    ap.add_argument("--max-usd", type=float, help="сколько можно потратить на модель; без него — только код и кэш")
    ap.add_argument("--no-llm", action="store_true", help="только проверки кодом")
    ap.add_argument("--no-vision", action="store_true", help="без проверки кадров моделью")
    ap.add_argument("--chapter-file", help="путь к .md главы, если chapter_file в пакете не находится")
    a = ap.parse_args(argv)
    cfg = Config()
    res = review(cfg, Path(a.package), Path(a.out), team=a.team, max_usd=a.max_usd, use_llm=not a.no_llm, use_vision=not a.no_vision,
                 chapter_file=a.chapter_file)
    rem = res["doc"]["remarks"]
    by_cat: dict[str, int] = {}
    for r in rem:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    print(f"замечаний {len(rem)} {by_cat} → {res['out']}")
    for e in res["errors"]:
        print("  схема: " + e, file=sys.stderr)
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
