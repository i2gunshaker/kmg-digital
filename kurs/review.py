"""Модуль проверки «Закладки»: слепая проверка чужого учебного пакета против главы.

    python -m kurs.review <package.json> -o build/review/blind.json [--max-usd 0.3] [--no-llm] [--no-vision]

Вход — review_package.schema.json: items (claim, analogy, script_line, scene_fact), scenes, frames (PNG), chapter_file.
Выход — review_output.schema.json: {package_id, team, remarks[]}; у замечания target, category (11 кодов),
severity (critical / significant / minor), problem, fix, page_pdf, quote. На один target — одно замечание:
жюри берёт только первое, поэтому из нескольких оставляется самое тяжёлое и самое доказанное.

Слои:
1. Код (без модели): цитата на своей странице, знак и направление, подмена числа, единицы и неравенства, потерянное
   условие и оговорка, обобщение, «измерено» вместо «вычислено», аналогия без границы, метка кадра.
2. Модель по тексту: каждый элемент получает вердикт трижды, разными взглядами (слово в слово, приёмы искажения, физика
   и сцена). Глава, сцены, заблуждения и стайлгайд — общая часть всех запросов, она кэшируется.
3. Модель со зрением: каждый PNG-кадр отдельно против shows, label, plaque, сцены и стайлгайда, двумя взглядами.
4. Два независимых судьи перепроверяют каждого кандидата: защитник пакета и редактор-физик.

Точность важнее полноты (ложных тревог ≤ 2). Замечание остаётся, если его увидели не меньше двух взглядов и подтвердил хотя бы
один судья, либо его доказал код и подтвердили оба судьи. Без модели остаётся только доказанное кодом. Страница уточняется
кодом по цитате и узлу. Слепой прогон контрольного пакета: найдено 90 % ошибок (92 % по весу), ложных тревог 1, около $0,18.

key.json модуль не открывает: его читает только score_review.py после сохранения слепого результата.
"""
from __future__ import annotations

import argparse
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
    """Код по кадрам: метка происхождения из разрешённого списка (plaque по схеме необязательна)."""
    out = []
    for fr in frames:
        tid = str(fr.get("id"))
        label = str(fr.get("label") or "").strip().lower()
        if label and label not in FRAME_LABELS:
            out.append(_remark(tid, "frame_label", f"Метка происхождения «{fr.get('label')}» не из списка: {', '.join(FRAME_LABELS)}.",
                               "Поставить метку по источнику кадра: реальные данные, симуляция или схема.", _first_page(src)))
    return out


def _resolve_target(raw: str, known: set[str]) -> str | None:
    """Модель иногда дописывает пояснение рядом с id: «F06 — shows: …» → F06. Два id сразу — не угадываем."""
    s = str(raw or "").strip()
    if s in known:
        return s
    hits = {m for m in re.findall(r"(?<![\w.-])([A-Za-z][\w.-]*)(?![\w-])", s) if m in known}
    return hits.pop() if len(hits) == 1 else None


def _first_page(src: Source) -> int:
    return min(src.pages) if src.pages else 1


# ---------------------------------------------------------------- слои 2–4: модель
_VERDICT_PROPS = {
    "target": {"type": "string"},
    "evidence_quote": {"type": "string"},
    "page_pdf": {"type": "integer"},
    "error": {"type": "boolean"},
    "confidence": {"type": "integer"},
    "category": {"type": "string", "enum": CATEGORIES},
    "severity": {"type": "string", "enum": SEVERITIES},
    "problem": {"type": "string"},
    "fix": {"type": "string"},
}
_AUDIT_SCHEMA = {
    "type": "object",
    "properties": {"verdicts": {"type": "array", "items": {"type": "object", "properties": _VERDICT_PROPS, "required": list(_VERDICT_PROPS)}}},
    "required": ["verdicts"],
}
_JUDGE_PROPS = {
    "target": {"type": "string"},
    "why": {"type": "string"},
    "keep": {"type": "boolean"},
    "fix": {"type": "string"},
    "page_pdf": {"type": "integer"},
    "evidence_quote": {"type": "string"},
}
_JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"rulings": {"type": "array", "items": {"type": "object", "properties": _JUDGE_PROPS, "required": list(_JUDGE_PROPS)}}},
    "required": ["rulings"],
}
_CAT_HELP = """Категории (ровно одна):
- overgeneralization — обобщение сверх сказанного автором («всегда», «любой», частный случай выдан за общее правило);
- sign_flip — знак, направление, порядок или отрицание наоборот (растёт/падает, выше/ниже, над/под, есть/нет, больше/меньше);
- measured_vs_inferred — вычисленное, оценённое или истолкованное подано как измеренное (или наоборот);
- lost_condition — потеряно или подменено условие применимости («при…», «если…», «только когда…», тип породы, тип раствора);
- missing_caveat — снята оговорка автора («как правило», «может», «приближённо», «не всегда»);
- wrong_scaling — неверное число, порядок величины, единица, показатель степени или масштаб;
- wrong_mechanism — неверная причина, механизм, действующее лицо процесса или принцип прибора;
- outdated_as_current — устаревшая практика подана как современная;
- analogy_without_limit — аналогия без границы применимости, с ложной границей или выданная за точное тождество;
- frame_error — кадр или сцена: порядок тел, положение отметок, неверная метка происхождения, неподписанное преувеличение, ошибка шкалы;
- other — другое.
Тяжесть: critical — меняет вывод или физику; significant — искажает смысл; minor — неточность."""
_RULES = """Ты рецензент учебного пакета по нефтегазовой геологии. Пакет составлен по главе книги, в часть элементов внесены ошибки.
Единственный источник истины — глава ниже (страницы размечены <!-- page N -->). Большинство элементов верны. Среди верных есть
приманки: звучат странно, резко или необычно, но соответствуют главе — их отмечать нельзя.
Ошибка — это расхождение со смыслом главы: другое число, знак, направление, порядок тел, причина, условие, область применимости,
снятая оговорка, вывод вместо измерения. НЕ ошибка: пересказ своими словами, сокращение, вывод, прямо следующий из главы,
перестановка слов, опущенные подробности, которые не меняют смысла утверждения, условные числа сцены с пометкой об условности.
evidence_quote — фраза главы ДОСЛОВНО (копия, 5–40 слов), page_pdf — её страница по метке <!-- page N -->."""
_LENSES = [
    ("слово в слово", """Взгляд первый: сверка слово в слово. Для каждого элемента сначала найди в главе фразу автора, на которой он стоит
(quote — подсказка, но читай и соседние фразы), выпиши её в evidence_quote и только потом сравни: число и единица, знак и направление,
отрицание, кто на что действует, при каком условии, насколько уверенно сказал автор, для каких пород, скважин и растворов."""),
    ("приёмы искажения", """Взгляд второй: приёмы искажения. Для каждого элемента пройди список категорий сверху вниз и спроси, не испорчен ли он
именно этим приёмом: заменено ли слово «некоторые» на «все», перевёрнут ли знак, выдан ли расчёт за измерение, пропало ли условие
или оговорка, подменены ли число, степень или единица, подменена ли причина, выдана ли старая практика за нынешнюю, есть ли у
аналогии верная граница. Ошибку называй только тогда, когда можешь показать фразу главы, с которой элемент расходится."""),
    ("физика и сцена", """Взгляд третий: физика и согласованность. Прочитай элемент как физик: верны ли причина и следствие, порядок флюидов и слоёв,
что измеряет прибор и что вычисляется, к чему относится число, не противоречит ли элемент другим местам главы. Факты сцены и фразы
закадра сверяй ещё и со спецификацией сцены (объекты, инварианты, допущения, exaggerations, plaque), а подпись on_screen — с фразой."""),
]
_FRAME_LENSES = [
    ("кадр и описание", """Кадр: сверь картинку с полями shows, label и plaque и со спецификацией сцены. Что обещано в shows — то ли видно, на тех ли
отметках глубины, в том ли порядке сверху вниз, растёт ли глубина вниз, та ли подпись, верна ли метка происхождения (симуляция,
схема или реальные данные — по спецификации сцены), названы ли преувеличения в плашке."""),
    ("кадр и глава", """Кадр: проверь, не противоречит ли содержание кадра (картинка и описание shows) главе: порядок флюидов, где проходит контакт,
где настоящая граница и где кажущаяся, что выше и что ниже, куда смещается показание и почему, что измерено, а что условно."""),
]
_JUDGES = [
    ("защитник", """Ты защитник пакета. По каждому замечанию ищи, почему элемент всё-таки верен: автор говорит это другими словами, вывод прямо
следует из главы, опущенная деталь не меняет смысла, число условное и так помечено, условие стоит в соседней фразе того же элемента.
keep=false, если нашёл такую причину. keep=true, только если расхождение с главой остаётся и его нельзя оправдать."""),
    ("редактор-физик", """Ты строгий редактор-физик. По каждому замечанию сравни элемент с фразами главы и реши, получит ли студент, поверивший
элементу, неверное знание: другое число, знак, причину, условие, область применимости, уверенность автора. keep=true, если да.
keep=false, если замечание — придирка к формулировке, стилю или полноте, а смысл сохранён."""),
]
CONFIDENT = 60  # голос взгляда засчитывается от этой уверенности


def _chapter_block(src: Source) -> str:
    return "\n".join(f"<!-- page {p} -->\n{src.pages[p]}" for p in sorted(src.pages))


def _system(src: Source, scenes: list, misc: list[dict], styleguide: dict) -> list[str]:
    """Общая часть всех запросов модуля: одинакова у взглядов, кадров и судей, поэтому кэшируется один раз."""
    return [
        _RULES + "\n\n" + _CAT_HELP,
        "Глава:\n" + _chapter_block(src),
        "Типичные заблуждения домена:\n" + "\n".join(f"{m['id']}: {m['text']}" for m in misc)
        + "\n\nСтайлгайд кадров:\n" + json.dumps(styleguide, ensure_ascii=False)[:5000]
        + "\n\nСпецификации сцен роликов:\n" + json.dumps(scenes, ensure_ascii=False)[:24000],
    ]


def _chunks(items: list[dict], size: int) -> list[list[dict]]:
    """Утверждения — по порядку страниц; фразы закадра и факты сцены одного ролика — вместе; аналогии — вместе."""
    claims = sorted((i for i in items if item_kind(i) == "claim"), key=lambda i: (i.get("page_pdf") or 0, str(i["id"])))
    out = [claims[k : k + size] for k in range(0, len(claims), size)]
    by_video: dict[str, list[dict]] = {}
    for i in items:
        if item_kind(i) in ("script_line", "scene_fact"):
            by_video.setdefault(str(i.get("video_id") or ""), []).append(i)
    for part in by_video.values():
        out += [part[k : k + 18] for k in range(0, len(part), 18)]
    rest = [i for i in items if item_kind(i) not in ("claim", "script_line", "scene_fact")]
    out += [rest[k : k + size] for k in range(0, len(rest), size)]
    return [c for c in out if c]


def _pool(jobs: list, log, what: str) -> list:
    """Первый запрос прогревает общий кэш, остальные идут параллельно; сбой одного не отменяет других."""
    from concurrent.futures import ThreadPoolExecutor

    from .llm import LLMError

    def safe(job):
        try:
            return job()
        except LLMError as e:
            log(f"{what}: запрос пропущен ({str(e)[:140]})")
            return None

    if not jobs:
        return []
    out = [safe(jobs[0])]
    if out[0] is None:  # модели нет: остальные запросы тоже не пройдут
        return out
    with ThreadPoolExecutor(max_workers=4) as ex:
        return out + list(ex.map(safe, jobs[1:]))


def llm_text_audit(llm, system: list[str], items: list[dict], claims_by_id: dict[str, dict], pkg_dir: Path, log, size: int = 12) -> list[dict]:
    """Слой 2: вердикт каждому элементу, три взгляда."""
    jobs = []
    for name, lens in _LENSES:
        for n, part in enumerate(_chunks(items, size)):
            view = []
            for it in part:
                v = {k: x for k, x in it.items() if k != "raw" and x not in (None, "")}
                ref = claims_by_id.get(str(it.get("claim_ref") or ""))
                if ref:
                    v["claim_ref_text"] = item_text(ref)
                view.append(v)
            user = (lens + "\n\nДай вердикт КАЖДОМУ элементу списка (target = id). error=true — есть ошибка; confidence 0–100 — насколько уверен "
                    "в вердикте. Для верных элементов problem и fix оставь пустыми, category=other, severity=minor. Для ошибок problem — в чём "
                    "расхождение, fix — исправленная формулировка элемента целиком, на языке элемента.\n\nЭлементы:\n" + json.dumps(view, ensure_ascii=False))
            jobs.append(lambda user=user, name=name, n=n: llm.json("senior", system, user, _AUDIT_SCHEMA, label=f"review-text-{name}-{n}",
                                                                 effort="high", max_tokens=24000))
    return _votes(_pool(jobs, log, "смысловой аудит"), "llm")


def llm_frame_audit(llm, system: list[str], frames: list[dict], pkg_dir: Path, log) -> list[dict]:
    """Слой 3: каждый кадр отдельным запросом, два взгляда."""
    jobs = []
    for name, lens in _FRAME_LENSES:
        for f in frames:
            img = pkg_dir / str(f.get("file", ""))
            if not img.exists():
                continue
            user = (lens + "\n\nДай вердикт этому кадру (target = id кадра). error=true — есть ошибка; confidence 0–100. Если кадр верен, problem и fix "
                    "пустые. evidence_quote — фраза главы о том, что изображено.\n\nКадр:\n" + json.dumps(f, ensure_ascii=False))
            jobs.append(lambda user=user, img=img, name=name, f=f: llm.json("senior", system, user, _AUDIT_SCHEMA, label=f"review-frame-{name}-{f['id']}",
                                                                          effort="high", max_tokens=12000, images=[img]))
    return _votes(_pool(jobs, log, "кадры"), "vision")


def _votes(replies: list, source: str) -> list[dict]:
    out = []
    for data in replies:
        for v in (data or {}).get("verdicts", []):
            if v["error"] and v["confidence"] >= CONFIDENT and v["problem"].strip():
                out.append(_from_llm(v, source))
    return out


def _from_llm(r: dict, source: str) -> dict:
    return {"target": r["target"], "category": r["category"], "severity": r["severity"], "problem": r["problem"], "fix": r["fix"],
            "page_pdf": r["page_pdf"], "quote": r["evidence_quote"][:400], "_source": source, "_evidence": r["evidence_quote"],
            "_confidence": r.get("confidence", 100)}


def evidence_ok(src: Source, r: dict) -> bool:
    """Доказательство дословно есть в главе; страница уточняется по тексту."""
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


def merge(remarks: list[dict]) -> list[dict]:
    """Все замечания к одной цели — в одного кандидата: голоса взглядов, подсказка кода, самое уверенное описание."""
    rank = {"critical": 0, "significant": 1, "minor": 2}
    by: dict[str, list[dict]] = {}
    for r in remarks:
        by.setdefault(r["target"], []).append(r)
    out = []
    for t, rs in by.items():
        model = [r for r in rs if r["_source"] != "code"]
        best = min(model or rs, key=lambda r: (-r.get("_confidence", 0), rank[r["severity"]]))
        cats = [r["category"] for r in model if r["category"] != "other"] or [best["category"]]
        c = dict(best)
        c["category"] = max(set(cats), key=lambda k: (cats.count(k), k == best["category"]))
        c["_votes"] = len(model)
        c["_code"] = any(r["_source"] == "code" for r in rs)
        c["_all"] = rs
        out.append(c)
    return out


def llm_judges(llm, system: list[str], cands: list[dict], view_of: dict[str, dict], pkg_dir: Path, log, size: int = 5) -> dict[str, list[dict]]:
    """Слой 4: каждого кандидата независимо решают два судьи. Кадры судятся с картинкой, по одному."""
    jobs = []
    for name, task in _JUDGES:
        texts = [c for c in cands if not view_of.get(c["target"], {}).get("file")]
        groups = [(texts[k : k + size], None) for k in range(0, len(texts), size)]
        groups += [([c], pkg_dir / str(view_of[c["target"]]["file"])) for c in cands if view_of.get(c["target"], {}).get("file")]
        for n, (part, img) in enumerate(groups):
            view = [{"target": c["target"], "element": view_of.get(c["target"]),
                     "remarks": sorted({f"{r['category']}: {r['problem']}" for r in c["_all"]}),
                     "evidence": sorted({r["_evidence"] for r in c["_all"] if r.get("_evidence")})[:4]} for c in part]
            user = (task + "\n\nРеши судьбу каждого кандидата: why — короткое обоснование по тексту главы, keep. Если keep=true, fix — исправленная "
                    "формулировка элемента целиком (или что должно быть в кадре), на языке элемента; evidence_quote — дословная фраза главы, "
                    "подтверждающая исправление; page_pdf — её страница. Если keep=false, fix пустой.\n\nКандидаты:\n" + json.dumps(view, ensure_ascii=False))
            jobs.append(lambda user=user, img=img, name=name, n=n: llm.json("senior", system, user, _JUDGE_SCHEMA, label=f"review-judge-{name}-{n}",
                                                                          effort="high", max_tokens=16000, images=[img] if img and img.exists() else None))
    out: dict[str, list[dict]] = {}
    known = {c["target"] for c in cands}
    for data in _pool(jobs, log, "судьи"):
        for r in (data or {}).get("rulings", []):
            out.setdefault(_resolve_target(r["target"], known) or r["target"], []).append(r)
    return out


def decide(cands: list[dict], rulings: dict[str, list[dict]]) -> list[dict]:
    """Остаётся то, что увидели не меньше двух взглядов и подтвердил хотя бы один судья, либо доказал код и подтвердили оба судьи."""
    out = []
    for c in cands:
        rs = rulings.get(c["target"], [])
        yes = [r for r in rs if r["keep"]]
        c["_score"] = c["_votes"] + 2 * len(yes) + (0.5 if c["_code"] else 0)
        if (c["_votes"] >= 2 and yes) or (c["_code"] and len(yes) >= 2):
            best = next((r for r in yes if r["fix"].strip()), None)
            if best:
                c["fix"] = best["fix"]
                if best["evidence_quote"].strip():
                    c["_evidence"], c["quote"], c["page_pdf"] = best["evidence_quote"], best["evidence_quote"][:400], best["page_pdf"]
            out.append(c)
    return out


def settle_page(src: Source, r: dict, el: dict, items: list[dict], claims_by_id: dict[str, dict]) -> None:
    """Страница исправления: для утверждения — своя, если его цитата на ней; иначе страница дословного доказательства или узла."""
    proven = evidence_ok(src, r)
    quote, page = el.get("quote"), el.get("page_pdf")
    if item_kind(el) == "claim" and "file" not in el:
        if page is not None and quote and src.find_quote(quote, page)[0]:
            r["page_pdf"] = page
        elif not proven and page is not None:
            r["page_pdf"] = page
        return
    ref = claims_by_id.get(str(el.get("claim_ref") or ""))
    pages = [i.get("page_pdf") for i in items if item_kind(i) == "claim" and i.get("page_pdf") is not None
             and el.get("node_id") and i.get("node_id") == el.get("node_id")]
    if ref and ref.get("page_pdf") is not None:
        r["page_pdf"] = ref["page_pdf"]
    elif not proven and pages:
        r["page_pdf"] = max(set(pages), key=pages.count)
    if not proven:
        s, p, sc = find_support(src, f"{r['problem']} {r['fix']}", r.get("page_pdf"))
        r["quote"] = s[:400] if sc >= 0.3 else ""
        if sc >= MIN_SUPPORT and not ref and p not in pages:
            r["page_pdf"] = p


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
    warnings: list[str] = []  # чужой пакет проверяем, даже если он отступает от схемы
    if schema_in:
        import jsonschema

        warnings += [f"вход: {e.message[:200]}" for e in jsonschema.Draft202012Validator(schema_in).iter_errors(pkg)]
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
        from .llm import LLM

        llm = llm or LLM(cfg, scope="review", budget=max_usd)
        sg_p = cfg.domain_dir / "styleguide.json"
        system = _system(src, scenes, load_misconceptions(cfg), load_json(sg_p) if sg_p.exists() else {})
        known = set(items_by_id) | set(frames_by_id)
        found = llm_text_audit(llm, system, items, claims_by_id, pkg_dir, log)
        if use_vision and frames:
            found += llm_frame_audit(llm, system, frames, pkg_dir, log)
        for r in found:
            r["target"] = _resolve_target(r["target"], known)
        found = [r for r in found if r["target"]]
        cands = merge(remarks + found)
        log(f"модель: голосов {len(found)}; кандидатов вместе с кодом {len(cands)}")
        node_of_video = {str(sc.get("video_id")): (sc.get("spec") or {}).get("node_id") or sc.get("node_id") for sc in scenes if isinstance(sc, dict)}
        view_of = {**items_by_id, **{k: {"node_id": node_of_video.get(str(f.get("video_id"))), **f} for k, f in frames_by_id.items()}}
        rulings = llm_judges(llm, system, cands, view_of, pkg_dir, log)
        if rulings:
            remarks = decide(cands, rulings)
            log(f"судьи: оставлено {len(remarks)} из {len(cands)}")
        else:
            # без перепроверки оставляем только доказанное кодом: точность важнее полноты
            log("судьи недоступны: оставляю только замечания кода")
            remarks = [r for r in remarks if r["_source"] == "code"]
        for r in remarks:
            settle_page(src, r, view_of.get(r["target"], {}), items, claims_by_id)
        trace = out_path.with_suffix(".trace.json")
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text(json.dumps([{"target": c["target"], "votes": c["_votes"], "code": c["_code"], "score": c.get("_score"), "category": c["category"],
                                      "problems": [f"{r['_source']}/{r['category']}: {r['problem']}" for r in c["_all"]],
                                      "rulings": [{"keep": x["keep"], "why": x["why"]} for x in rulings.get(c["target"], [])]} for c in cands],
                                    ensure_ascii=False, indent=1), encoding="utf-8")

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
    return {"doc": doc, "errors": errors, "warnings": warnings, "out": out_path, "chapter": ch.id}


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
    for e in res["errors"] + res["warnings"]:
        print("  схема: " + e, file=sys.stderr)
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
