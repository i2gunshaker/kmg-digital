"""Машинные проверки слоя: схема, цитаты на странице, числа (знак, единица, условие), покрытие, порядок терминов.

Правило организаторов: цитата — дословно из выданного md, при сверке не учитываются
пробелы, переносы строк и регистр. Номер страницы — из метки <!-- page N -->.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import jsonschema

from .config import Chapter, Config
from .units import Unit, load_pages, load_units

_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    """Нормализация для сверки цитат: без пробелов и переносов, нижний регистр.

    Перенос слова через дефис на границе строки тоже склеивается."""
    s = s.replace("­", "")
    s = re.sub(r"-\s*\n\s*", "", s)
    s = s.replace("ё", "е").replace("Ё", "Е")
    return _WS.sub("", s).lower()


@dataclass
class Issue:
    level: str  # error | warn
    where: str
    msg: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.where}: {self.msg}"


class Source:
    """Корпус главы для проверок."""

    def __init__(self, ch: Chapter):
        self.ch = ch
        self.pages = load_pages(ch)
        self.units = load_units(ch)
        self.unit_by_id = {u.id: u for u in self.units}
        self.author_units = [u for u in self.units if not u.model_description]
        self.norm_units = [(u, norm(u.text)) for u in self.author_units]
        self.npages = {p: norm(t) for p, t in self.pages.items()}
        order = sorted(self.pages)
        self.order = order
        self.all_norm = "".join(self.npages[p] for p in order)

    def find_quote(self, quote: str, page: int) -> tuple[bool, list[int]]:
        """(найдена на указанной странице, все страницы, где найдена)."""
        q = norm(quote)
        if not q:
            return False, []
        hits = [p for p, t in self.npages.items() if q in t]
        if page in hits:
            return True, hits
        # Цитата через границу страниц: начинается на page или заканчивается на page.
        if page in self.npages:
            idx = self.order.index(page)
            if idx + 1 < len(self.order):
                joined = self.npages[page] + self.npages[self.order[idx + 1]]
                pos = joined.find(q)
                if pos != -1 and pos < len(self.npages[page]):
                    return True, hits or [page]
            if idx > 0:
                prev = self.npages[self.order[idx - 1]]
                joined = prev + self.npages[page]
                pos = joined.find(q)
                if pos != -1 and pos + len(q) > len(prev):
                    return True, hits or [page]
        return False, hits

    def find_quote_units(self, quote: str, page: int) -> list[Unit]:
        """Возвращает исходные авторские единицы, содержащие цитату на указанной странице."""
        q = norm(quote)
        if not q:
            return []
        matched = [
            u for u, nu in self.norm_units
            if q in nu and (page in u.pages or u.page_pdf == page)
        ]
        if matched:
            return matched
        # Цитата на границе двух соседних единиц (в т.ч. через границу страниц).
        for idx in range(len(self.norm_units) - 1):
            u1, nu1 = self.norm_units[idx]
            u2, nu2 = self.norm_units[idx + 1]
            if page not in u1.pages and page not in u2.pages:
                continue
            joined = nu1 + nu2
            pos = joined.find(q)
            if pos != -1 and pos < len(nu1) and pos + len(q) > len(nu1):
                return [u1, u2]
        # Фоллбэк на случай неточного номера страницы — для контекста, если страница не совпала.
        return [u for u, nu in self.norm_units if q in nu]


# ---------------------------------------------------------------- числа, знак, единицы, условия
@dataclass(frozen=True)
class Quantity:
    value: str       # нормализованное значение со знаком, напр. "-15", "15", "2.5"
    unit: str | None # каноническая единица или None
    cond: str | None # "upper" (<, ≤, не более, до) | "lower" (>, ≥, не менее, свыше) | None
    raw: str


_LIST_MARKER_RE = re.compile(r"(?m)^\s*\d{1,3}[.)]\s+(?![%°a-zA-Zа-яА-Я])")
_NUM_MATCH_RE = re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9_.,])([+\-\u2212\u2013]?)\s*(\d+(?:[.,]\d+)?)(?!\d|[.,]\d)")

_UNIT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\s*(?:°\s*[cс]|градус(?:а|ов)?\s*[cс]?|deg\s*c)\b", re.I), "°c"),
    (re.compile(r"^\s*(?:%|процент(?:а|ов)?\b|percent\b)", re.I), "%"),
    (re.compile(r"^\s*(?:ом\s*[·∙*⋅.]\s*м|омм|ohm\s*[·∙*⋅\-]?\s*m|o\s*[·∙.]\s*m)\b", re.I), "ом·м"),
    (re.compile(r"^\s*(?:м³\s*/\s*сут|м3\s*/\s*сут|m3\s*/\s*d(?:ay)?)\b", re.I), "м³/сут"),
    (re.compile(r"^\s*(?:т\s*/\s*сут|t\s*/\s*d(?:ay)?)\b", re.I), "т/сут"),
    (re.compile(r"^\s*(?:г\s*/\s*см³|г\s*/\s*см3|g\s*/\s*cm[³3])\b", re.I), "г/см³"),
    (re.compile(r"^\s*(?:кг\s*/\s*м³|кг\s*/\s*м3|kg\s*/\s*m[³3])\b", re.I), "кг/м³"),
    (re.compile(r"^\s*(?:мкс\s*/\s*м|[uµ]s\s*/\s*m)\b", re.I), "мкс/м"),
    (re.compile(r"^\s*(?:мкм²|мкм2|[uµ]m[²2])\b", re.I), "мкм²"),
    (re.compile(r"^\s*(?:мкм|[uµ]m)\b", re.I), "мкм"),
    (re.compile(r"^\s*(?:мм|mm)\b", re.I), "мм"),
    (re.compile(r"^\s*(?:см|cm)\b", re.I), "см"),
    (re.compile(r"^\s*(?:км|km)\b", re.I), "км"),
    (re.compile(r"^\s*(?:мв|mv)\b", re.I), "мв"),
    (re.compile(r"^\s*(?:мд|md)\b", re.I), "мд"),
    (re.compile(r"^\s*(?:мпа|mpa)\b", re.I), "мпа"),
    (re.compile(r"^\s*(?:кпа|kpa)\b", re.I), "кпа"),
    (re.compile(r"^\s*(?:па|pa)\b", re.I), "па"),
    (re.compile(r"^\s*(?:атм|atm)\b", re.I), "атм"),
    (re.compile(r"^\s*(?:бар|bar)\b", re.I), "бар"),
    (re.compile(r"^\s*psi\b", re.I), "psi"),
    (re.compile(r"^\s*ft\b", re.I), "ft"),
    (re.compile(r"^\s*(?:сут|days?)\b", re.I), "сут"),
    (re.compile(r"^\s*(?:мин|min)\b", re.I), "мин"),
    (re.compile(r"^\s*(?:м|m)\b", re.I), "м"),
]

_UPPER_COND_RE = re.compile(r"(?:<=|≤|<|не\s+более|не\s+выше|менее|меньше|ниже|до|at\s+most|less\s+than|below|under)\s*$", re.I)
_LOWER_COND_RE = re.compile(r"(?:>=|≥|>|не\s+менее|не\s+ниже|более|больше|выше|свыше|от|at\s+least|more\s+than|greater\s+than|above|over)\s*$", re.I)


def _norm_num_token(sign: str, num: str) -> str:
    val = num.replace(",", ".")
    if "." in val:
        val = val.rstrip("0").rstrip(".") or "0"
    if sign in ("-", "\u2212", "\u2013") and val != "0":
        return "-" + val
    return val


def _extract_unit(tail: str) -> str | None:
    for pat, canon in _UNIT_PATTERNS:
        if pat.match(tail):
            return canon
    return None


def _extract_cond(prefix: str) -> str | None:
    window = prefix[-32:]
    if _UPPER_COND_RE.search(window):
        return "upper"
    if _LOWER_COND_RE.search(window):
        return "lower"
    return None


_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
_POWER_RE = re.compile(r"(?<=\d)([⁻⁺]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+)")
_TEX_SPACE_RE = re.compile(r"(?<=\d)\\[,;: !]\s*(?=\d{3}(?!\d))")
_GROUP_RE = re.compile(r"(?<![\d.,])([1-9]\d{0,2})((?:[ \u00a0\u2009\u202f]\d{3})+)(?![\d]|[.,]\d{3})")
_EN_GROUP_RE = re.compile(r"(?<![\d.,])([1-9]\d{0,2})((?:,\d{3})+)(?!\d)")


def normalize_numbers(text: str) -> str:
    """Приводит запись чисел к одному виду: «10 000», «10\\,000», «1,500» в английском тексте, степени «10⁻⁵»."""
    s = _POWER_RE.sub(lambda m: "^{" + m.group(1).translate(_SUP) + "}", text or "")
    s = _TEX_SPACE_RE.sub(" ", s)
    s = _GROUP_RE.sub(lambda m: m.group(1) + re.sub(r"\s", "", m.group(2)), s)
    if not re.search(r"[А-Яа-яЁё]", s):
        s = _EN_GROUP_RE.sub(lambda m: m.group(1) + m.group(2).replace(",", ""), s)
    return s


def extract_quantities(text: str, strip_list_markers: bool = False) -> list[Quantity]:
    """Извлекает числа с сохранением знака, канонизированной единицы и неравенства."""
    s = normalize_numbers(text)
    if strip_list_markers:
        s = _LIST_MARKER_RE.sub("", s)
    out: list[Quantity] = []
    for m in _NUM_MATCH_RE.finditer(s):
        sign = m.group(1)
        num = m.group(2)
        start = m.start()
        prefix = s[:start]
        if sign in ("-", "\u2212", "\u2013"):
            # Минус/тире с пробелом после знака («13,5 – 4,5») или сразу после цифры/буквы («700–750») — не унарный.
            has_gap_after_sign = m.start(2) > m.start(1) + 1
            prev_ch = prefix[-1] if prefix else ""
            if prev_ch.isdigit() or prev_ch.isalnum() or prev_ch == ")":
                sign = ""
            elif has_gap_after_sign and prev_ch not in ("", "=", ":", "(", "[", ",", ";", "<", ">", "≤", "≥", "≈", "~"):
                sign = ""
        value = _norm_num_token(sign, num)
        unit = _extract_unit(s[m.end():])
        cond = _extract_cond(prefix)
        out.append(Quantity(value=value, unit=unit, cond=cond, raw=m.group(0).strip()))
    return out


def numbers(text: str) -> set[str]:
    """Множество нормализованных чисел со знаком."""
    return {q.value for q in extract_quantities(text)}


def numbers_in_source(text: str, src_norm_nums: set[str]) -> list[str]:
    """Числа из текста (со знаком), которых нет в множестве исходных чисел."""
    missing = {
        q.value for q in extract_quantities(text, strip_list_markers=True)
        if q.value not in src_norm_nums
    }
    return sorted(missing)


def check_quantities_against_source(text: str, source_texts: list[str]) -> list[str]:
    """Сверяет значения со знаком, единицы и знаки условий с исходными единицами."""
    src_qs: list[Quantity] = []
    for st in source_texts:
        src_qs.extend(extract_quantities(st, strip_list_markers=False))
    src_vals = {q.value for q in src_qs}
    units_by_val: dict[str, set[str]] = {}
    conds_by_val: dict[str, set[str]] = {}
    for q in src_qs:
        if q.unit:
            units_by_val.setdefault(q.value, set()).add(q.unit)
        if q.cond:
            conds_by_val.setdefault(q.value, set()).add(q.cond)

    problems: list[str] = []
    seen: set[str] = set()
    for q in extract_quantities(text, strip_list_markers=True):
        if q.value not in src_vals:
            msg = f"{q.value} (нет в исходных единицах)"
            if msg not in seen:
                seen.add(msg)
                problems.append(msg)
            continue
        allowed_units = units_by_val.get(q.value, set())
        if q.unit and allowed_units and q.unit not in allowed_units:
            msg = f"{q.value} {q.unit} (в источнике: {', '.join(sorted(allowed_units))})"
            if msg not in seen:
                seen.add(msg)
                problems.append(msg)
            continue
        allowed_conds = conds_by_val.get(q.value, set())
        if q.cond and allowed_conds and q.cond not in allowed_conds:
            if (q.cond == "upper" and "lower" in allowed_conds) or (q.cond == "lower" and "upper" in allowed_conds):
                msg = f"{q.value} (перевёрнуто условие: {q.cond} вместо {'/'.join(sorted(allowed_conds))})"
                if msg not in seen:
                    seen.add(msg)
                    problems.append(msg)
    return problems


# ---------------------------------------------------------------- слой
def load_schema(cfg: Config, name: str) -> dict:
    return json.loads((cfg.schemas_dir / name).read_text(encoding="utf-8"))


def _texts_of_node(node: dict) -> list[tuple[str, str]]:
    """Все тексты узла (кроме claims, которые проверяются отдельно по своей цитате): (путь, текст)."""
    out = []
    for k, v in node.get("layers", {}).items():
        if isinstance(v, dict):
            out.append((f"layers.{k}", v.get("text", "")))
        elif isinstance(v, list):
            out += [(f"layers.{k}[{i}]", x.get("text", "")) for i, x in enumerate(v)]
    for f in ("steps", "conditions"):
        out += [(f"{f}[{i}]", p.get("text", "")) for i, p in enumerate(node.get(f) or [])]
    for i, w in enumerate(node.get("worked_examples") or []):
        out.append((f"worked_examples[{i}]", " ".join([w.get("condition", ""), *w.get("steps", []), w.get("result", "")])))
    dm = node.get("data_meaning") or {}
    if dm.get("from_chapter"):
        out.append(("data_meaning.from_chapter", dm["from_chapter"].get("text", "")))
    dec = node.get("decision_on_data")
    if isinstance(dec, dict):
        out.append(("decision_on_data", " ".join([*dec.get("features", []), dec.get("rule", ""), dec.get("conclusion", "")])))
    return out


def check_layer(
    cfg: Config,
    ch: Chapter,
    layer: dict,
    check_numbers: bool = True,
    coverage: dict | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    schema = load_schema(cfg, "node.schema.json")
    v = jsonschema.Draft202012Validator(schema)
    for e in v.iter_errors(layer):
        issues.append(Issue("error", "schema:" + "/".join(map(str, e.absolute_path)), e.message[:200]))
    src = Source(ch)
    node_ids = [n["id"] for n in layer.get("nodes", [])]
    if len(node_ids) != len(set(node_ids)):
        issues.append(Issue("error", "nodes", "повторяющиеся id узлов"))
    order_of = {n["id"]: n.get("order", 0) for n in layer.get("nodes", [])}
    introduced_at: dict[str, int] = {}
    for n in layer.get("nodes", []):
        for t in n.get("terms_introduced", []):
            introduced_at.setdefault(t.lower(), n.get("order", 0))

    cov_units_by_node: dict[str, list[Unit]] = {}
    if coverage:
        for row in coverage.get("rows", []):
            u = src.unit_by_id.get(row.get("unit_id", ""))
            if u and not u.model_description:
                for nid in row.get("node_ids", []):
                    cov_units_by_node.setdefault(nid, []).append(u)

    for n in layer.get("nodes", []):
        nid = n.get("id", "?")
        node_units: list[Unit] = []
        seen_uids: set[str] = set()

        def add_units(us: list[Unit]) -> None:
            for u in us:
                if u.id not in seen_uids and not u.model_description:
                    seen_uids.add(u.id)
                    node_units.append(u)

        for uid in n.get("unit_ids", []):
            u = src.unit_by_id.get(uid)
            if u:
                add_units([u])
        add_units(cov_units_by_node.get(nid, []))

        for c in n.get("claims", []):
            page_pdf = c.get("page_pdf", -1)
            quote = c.get("quote", "")
            ok, hits = src.find_quote(quote, page_pdf)
            if not ok:
                if hits:
                    issues.append(Issue("error", c["id"], f"цитата не на стр. {page_pdf}, а на стр. {hits}"))
                else:
                    issues.append(Issue("error", c["id"], f"цитата не найдена в главе: «{quote[:80]}»"))
            if not c["id"].startswith(nid + "#"):
                issues.append(Issue("error", c["id"], f"id утверждения не начинается с id узла {nid}"))

            claim_units: list[Unit] = []
            if c.get("unit_id") and c["unit_id"] in src.unit_by_id:
                u = src.unit_by_id[c["unit_id"]]
                if not u.model_description:
                    claim_units.append(u)
            if ok and not claim_units:
                claim_units = src.find_quote_units(quote, page_pdf)
            add_units(claim_units)

            if check_numbers and ok:
                src_texts = [u.text for u in claim_units] if claim_units else ([quote] if quote else [])
                probs = check_quantities_against_source(c.get("text", ""), src_texts)
                if probs:
                    issues.append(Issue("error", f"{nid}/claim {c['id']}", f"числа не подтверждены исходной единицей: {'; '.join(probs)}"))

        for l in n.get("links", []):
            if l["type"] == "prerequisite":
                t = l["target"]
                if t in order_of and order_of[t] >= n.get("order", 0):
                    issues.append(Issue("error", nid, f"предпосылка {t} стоит не раньше узла"))
        for t in n.get("terms_used", []):
            at = introduced_at.get(t.lower())
            if at is not None and at > n.get("order", 0):
                issues.append(Issue("error", nid, f"термин «{t}» используется раньше узла, где объяснён"))

        if check_numbers:
            node_src_texts = [u.text for u in node_units]
            for path, text in _texts_of_node(n):
                if not text or not any(ch.isdigit() for ch in text):
                    continue
                probs = check_quantities_against_source(text, node_src_texts)
                if probs:
                    issues.append(Issue("error", f"{nid}/{path}", f"числа не подтверждены единицами узла: {'; '.join(probs)}"))
    return issues


_BAD_REASONS = ("непоказуем", "нельзя показать", "unshowable")


def check_coverage(cfg: Config, ch: Chapter, coverage: dict, layer: dict) -> list[Issue]:
    issues: list[Issue] = []
    schema = load_schema(cfg, "coverage.schema.json")
    for e in jsonschema.Draft202012Validator(schema).iter_errors(coverage):
        issues.append(Issue("error", "coverage-schema", e.message[:200]))

    units_list = load_units(ch)
    units_by_id = {u.id: u for u in units_list}
    node_ids = {n["id"] for n in layer.get("nodes", [])}

    rows_list = coverage.get("rows", [])
    uid_counts = Counter(r.get("unit_id", "") for r in rows_list)
    for uid, cnt in uid_counts.items():
        if cnt > 1:
            issues.append(Issue("error", uid or "coverage", f"дубликат строки покрытия для единицы {uid} ({cnt}x)"))

    seen_uids: set[str] = set()
    for r in rows_list:
        uid = r.get("unit_id", "")
        if uid not in units_by_id:
            issues.append(Issue("error", uid or "coverage", f"посторонняя единица {uid} отсутствует в реестре главы"))
            continue
        seen_uids.add(uid)
        u = units_by_id[uid]
        if r.get("kind") != u.kind:
            issues.append(Issue("error", uid, f"неверный kind={r.get('kind')!r}, ожидалось {u.kind!r}"))
        if r.get("page_pdf") != u.page_pdf:
            issues.append(Issue("error", uid, f"неверная page_pdf={r.get('page_pdf')}, ожидалось {u.page_pdf}"))
        reason = (r.get("reason") or "").strip()
        if not r.get("node_ids") and not reason:
            issues.append(Issue("error", uid, "нет ни узла, ни причины"))
        if any(bad in reason.lower() for bad in _BAD_REASONS):
            issues.append(Issue("error", uid, f"недопустимая причина исключения: «{reason}»"))
        for nid in r.get("node_ids", []):
            if nid not in node_ids:
                issues.append(Issue("error", uid, f"ссылка на несуществующий узел {nid}"))

    for uid in units_by_id.keys() - seen_uids:
        issues.append(Issue("error", uid, "единица главы не попала в таблицу покрытия"))
    return issues


def report(issues: list[Issue]) -> str:
    errs = sum(1 for i in issues if i.level == "error")
    warns = len(issues) - errs
    lines = [str(i) for i in issues]
    lines.append(f"итого: ошибок {errs}, предупреждений {warns}")
    return "\n".join(lines)
