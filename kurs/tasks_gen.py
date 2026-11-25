"""Задания и маршрутизация форматов из готового слоя: модель предлагает, код проверяет и отбирает."""
from __future__ import annotations

import ast
import hashlib
import json
import math
import operator
import random
import re

import jsonschema

from .config import Chapter, Config
from .layer_gen import (
    B,
    S,
    arr,
    load_errata,
    load_misconceptions,
    node_text,
    obj,
    prompt,
    system_blocks,
    term_regex,
    write_generated,
)
from .llm import LLM, LLMError
from .rules import route_by_rules, tasks_by_rules
from .units import Unit
from .verify import Issue, check_quantities_against_source, load_schema, norm

FORMATS = ["шортс", "длинное видео", "интерактив", "практикум на данных", "карточки", "текст"]
VIDEO_FORMATS = {"шортс", "длинное видео"}
SCORES = ["time", "invisible", "scale", "counterintuitive", "cause_effect"]
TASK_TYPES = ["single", "multiple", "order", "gap", "calc"]
TASK_KINDS = ["recall", "understanding", "transfer", "data"]
MIN_TASKS = 15
MIN_TYPES = 4

# Шаблоны сцен по типам трудности; доменный пакет может добавить свои в templates.json.
SCENE_TEMPLATES = {
    "process-section": "М: процесс на разрезе, state[t] по времени",
    "time-evolution": "В: этапы формирования со шкалой времени",
    "wellbore-log-sync": "И: прибор, кривая и интерпретация синхронно",
    "field-diffusion-2d": "Ф: поле величины во времени с ползунками",
    "geometry-section": "П: тело и движущееся сечение",
    "procedure-flow": "Пр: шаги, входы, точки выбора",
    "scenario-range": "Н: сценарии, диапазон, цена решения",
    "feature-recognition": "Р: признаки на изображении без разметки и с ней",
}

ROUTING_SCHEMA = obj(
    routes=arr(
        obj(
            node_id=S,
            format={"type": "string", "enum": FORMATS},
            secondary_format={"type": "string", "enum": FORMATS + [""]},
            video_scores=obj(**{k: {"type": "integer", "enum": [0, 1, 2]} for k in SCORES}),
            hook_short=B,
            template=S,
            why=S,
        )
    )
)

TASKS_SCHEMA = obj(
    tasks=arr(
        obj(
            type={"type": "string", "enum": TASK_TYPES},
            step_id=S,
            claim_ids=arr(S),
            prompt=S,
            options=arr(obj(id=S, text=S, misconception=S)),
            answer_list=arr(S),
            expression=S,
            tolerance={"type": "number"},
            unit=S,
            explanation=S,
            kind={"type": "string", "enum": TASK_KINDS},
        )
    )
)


def scene_templates(cfg: Config) -> dict[str, str]:
    out = dict(SCENE_TEMPLATES)
    p = cfg.domain_dir / "templates.json"
    if p.exists():
        out.update(json.loads(p.read_text(encoding="utf-8")))
    return out


# ---------------------------------------------------------------- маршрутизация
def check_routing(cfg: Config, routing: dict, layer: dict) -> list[Issue]:
    issues = [
        Issue("error", "routing-schema", e.message[:200])
        for e in jsonschema.Draft202012Validator(load_schema(cfg, "routing.schema.json")).iter_errors(routing)
    ]
    ids = [n["id"] for n in layer["nodes"]]
    got = [r["node_id"] for r in routing.get("routes", [])]
    for nid in ids:
        if got.count(nid) != 1:
            issues.append(Issue("error", nid, f"маршрутов {got.count(nid)}, нужен ровно один"))
    for nid in got:
        if nid not in ids:
            issues.append(Issue("error", nid, "маршрут для несуществующего узла"))
    templates = scene_templates(cfg)
    for r in routing.get("routes", []):
        t = r.get("template") or ""
        if t and t not in templates and not t.startswith("нужен новый"):
            issues.append(Issue("error", r["node_id"], f"неизвестный шаблон сцены: {t}"))
        if r["format"] in VIDEO_FORMATS and not t:
            issues.append(Issue("error", r["node_id"], "у ролика нет шаблона сцены"))
        if r["format"] in VIDEO_FORMATS and sum(r["video_scores"].values()) < 4:
            issues.append(Issue("warn", r["node_id"], "видео при низкой видеоёмкости"))
    hooks = sum(1 for r in routing.get("routes", []) if r.get("hook_short"))
    videos = sum(1 for r in routing.get("routes", []) if r["format"] in VIDEO_FORMATS)
    if hooks and hooks > max(1, 0.2 * max(videos, 1)):
        issues.append(Issue("error", "routing", f"шортсов-крючков {hooks} из {videos} роликов, больше 20%"))
    return issues


def note_source(ch: Chapter, name: str, value: str) -> None:
    """Кто сделал файл — модель или правила: пометка в gen_report.json для метрик."""
    p = ch.build_dir / "gen_report.json"
    rep = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    rep[name] = value
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def generate_routing(cfg: Config, ch: Chapter, llm: LLM, layer: dict, *, force: bool = False, log=print) -> tuple[dict, list[Issue]]:
    """Маршруты предлагает модель; нет денег, связи или ответ негоден — маршруты строятся по правилам."""
    templates = scene_templates(cfg)
    brief = "\n".join(
        f"{n['id']} | {n['difficulty']['primary']}/{n['difficulty']['secondary'] or '—'}"
        f"{' | пороговый' if n['threshold'] else ''}{' | решение на данных' if n['decision_on_data'] else ''}"
        f" | {n['title']} | {n['layers']['main']['text']}"
        for n in layer["nodes"]
    )
    user = prompt("routing", nodes=brief, templates="; ".join(f"{k} ({v})" for k, v in templates.items()))

    def shape(raw: dict) -> dict:
        return {
            "chapter": ch.id,
            "routes": [
                {
                    **r,
                    "secondary_format": r["secondary_format"] or None,
                    "template": r["template"].strip() or None,
                }
                for r in raw["routes"]
            ],
        }

    routing, made_by = None, "model"
    try:
        routing = shape(llm.json("senior", [prompt("common")], user, ROUTING_SCHEMA, label="routing", effort="medium"))
        bad = [i for i in check_routing(cfg, routing, layer) if i.level == "error"]
        if bad:
            log(f"  маршруты модели не прошли проверку ({len(bad)}), беру правила: {bad[0]}")
            routing = None
    except LLMError as e:
        log(f"  маршруты по правилам, без модели: {str(e)[:120]}")
    if routing is None:
        routing, made_by = route_by_rules(ch, layer, templates, node_text), "rules"
    write_generated(ch, "routing.json", routing, force)
    note_source(ch, "routing_by", made_by)
    return routing, check_routing(cfg, routing, layer)


# ---------------------------------------------------------------- расчёт ответа
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_FUNCS = {"sqrt": math.sqrt, "log": math.log, "log10": math.log10, "exp": math.exp}
_CONSTS = {"pi": math.pi, "e": math.e}


def eval_expr(expr: str) -> float:
    """Арифметика без eval: числа, + - * / **, скобки, sqrt, log, log10, exp, pi."""

    def walk(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return walk(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = walk(n.left), walk(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 100:
                raise ValueError("слишком большая степень")
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](walk(n.operand))
        if isinstance(n, ast.Name) and n.id in _CONSTS:
            return _CONSTS[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and len(n.args) == 1:
            return _FUNCS[n.func.id](walk(n.args[0]))
        raise ValueError(f"недопустимое выражение: {ast.dump(n)[:60]}")

    return walk(ast.parse(expr.replace("^", "**").replace(",", "."), mode="eval"))


# ---------------------------------------------------------------- задания
def task_problems(t: dict, layer: dict, units: dict[str, Unit], misc_ids: set[str]) -> list[str]:
    """Замечания к одному заданию в форме схемы организаторов (плюс поле expression у calc)."""
    out: list[str] = []
    node = next((n for n in layer["nodes"] if n["id"] == t["step_id"]), None)
    if node is None:
        return [f"нет шага {t['step_id']}"]
    claim_ids = {c["id"] for c in node["claims"]}
    bad = [c for c in t["claim_ids"] if c not in claim_ids]
    if bad:
        out.append(f"утверждения не из этого шага: {bad}")
    if not t["claim_ids"]:
        out.append("нет ссылки на утверждение")
    opts = t.get("options") or []
    ids = [o["id"] for o in opts]
    texts = [norm(o["text"]) for o in opts]
    if len(set(ids)) != len(ids) or len(set(texts)) != len(texts):
        out.append("повторяются варианты")
    for o in opts:
        if o.get("misconception") and o["misconception"] not in misc_ids:
            out.append(f"неизвестное заблуждение {o['misconception']}")
    ans = t["answer"]
    src = [units[u].text for u in node["unit_ids"] if u in units]
    body = node_text(node)
    if t["type"] == "single":
        if not 3 <= len(opts) <= 5 or ans not in ids:
            out.append("single: нужно 3–5 вариантов и один верный id")
    elif t["type"] == "multiple":
        if not isinstance(ans, list) or len(opts) < 4 or not 2 <= len(ans) < len(opts) or not set(ans) <= set(ids):
            out.append("multiple: нужно от 4 вариантов и не меньше 2 верных")
    elif t["type"] == "order":
        if not isinstance(ans, list) or len(opts) < 3 or sorted(ans) != sorted(ids):
            out.append("order: answer должен быть перестановкой всех id")
        elif ans == ids:
            out.append("order: варианты уже стоят в верном порядке")
    elif t["type"] == "gap":
        if not isinstance(ans, list) or len(ans) != 1 or t["prompt"].count("{{1}}") != 1 or "{{2}}" in t["prompt"]:
            out.append("gap: нужен ровно один пропуск {{1}} и один ответ")
        else:
            word = norm(str(ans[0]))
            stem = word[: max(4, len(word) - 3)]
            if stem not in norm(body):
                out.append(f"gap: слова «{ans[0]}» нет в тексте шага")
    elif t["type"] == "calc":
        if not isinstance(ans, (int, float)) or isinstance(ans, bool):
            out.append("calc: ответ должен быть числом")
        else:
            try:
                val = eval_expr(t.get("expression") or "")
                tol = t.get("tolerance") or 0
                if abs(val - ans) > max(tol, 1e-9 + abs(ans) * 0.005):
                    out.append(f"calc: выражение даёт {val:.6g}, а ответ {ans:.6g}")
            except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, TypeError) as e:
                out.append(f"calc: выражение не считается ({e})")
            if ans and not 0.01 <= abs(ans) < 1e5:
                out.append(f"calc: ответ {ans:g} неудобно вводить, подбери исходные числа, чтобы он был от 0,01 до 99999")
    if t["type"] != "calc":
        for part, text in [("prompt", t["prompt"].replace("{{1}}", " ")), ("explanation", t["explanation"])] + [
            (f"option {o['id']}", o["text"]) for o in opts
        ]:
            if part.startswith("option") and (t["type"] != "order") and o_is_wrong(part, t):
                continue
            probs = check_quantities_against_source(text, src)
            if probs:
                out.append(f"{part}: числа не из шага: {'; '.join(probs)}")
    if len(re.split(r"(?<=[.!?])\s+", t["explanation"].strip())) > 2:
        out.append("explanation: нужна одна фраза")
    return out


def o_is_wrong(part: str, t: dict) -> bool:
    """Неверный вариант может содержать чужое число: это и есть ошибка, которую он изображает."""
    oid = part.split(" ", 1)[1]
    ans = t["answer"]
    return oid not in (ans if isinstance(ans, list) else [ans])


def shape_task(raw: dict, tid: str) -> dict:
    ans: object
    lst = raw["answer_list"]
    if raw["type"] == "single":
        ans = lst[0] if lst else ""
    elif raw["type"] == "calc":
        try:
            ans = float(lst[0].replace(",", ".").replace(" ", "")) if lst else ""
        except ValueError:
            ans = lst[0]
    else:
        ans = lst
    t = {
        "id": tid,
        "type": raw["type"],
        "step_id": raw["step_id"],
        "claim_ids": raw["claim_ids"],
        "prompt": raw["prompt"].strip(),
        "answer": ans,
        "explanation": raw["explanation"].strip(),
        "kind": raw["kind"],
    }
    if raw["type"] in ("single", "multiple", "order"):
        t["options"] = [
            {"id": o["id"], "text": o["text"].strip(), "misconception": o["misconception"].strip() or None}
            for o in raw["options"]
        ]
    if raw["type"] == "calc":
        t["tolerance"] = raw["tolerance"]
        t["unit"] = raw["unit"].strip() or None
        t["expression"] = raw["expression"].strip()
    return t


def shuffle_options(t: dict) -> dict:
    """Перемешивает варианты и переименовывает их по месту: модель ставит верный ответ первым, а порядок — по буквам.

    Перемешивание зависит только от текста задания, поэтому повторный запуск даёт тот же файл."""
    opts = t.get("options")
    if not opts:
        return t
    rnd = random.Random(hashlib.sha256((t["prompt"] + "".join(o["text"] for o in opts)).encode("utf-8")).hexdigest())
    ans = t["answer"] if isinstance(t["answer"], list) else [t["answer"]]
    mixed = list(opts)
    for _ in range(20):
        rnd.shuffle(mixed)
        if t["type"] != "order" or [o["id"] for o in mixed] != ans:
            break
    new_id = {o["id"]: chr(ord("a") + i) for i, o in enumerate(mixed)}
    t = {**t, "options": [{**o, "id": new_id[o["id"]]} for o in mixed]}
    mapped = [new_id.get(a, a) for a in ans]
    if t["type"] == "single":
        t["answer"] = mapped[0] if mapped else ""
    else:
        t["answer"] = sorted(mapped) if t["type"] == "multiple" else mapped
    return t


def check_tasks(cfg: Config, doc: dict, layer: dict, units: list[Unit]) -> list[Issue]:
    issues = [
        Issue("error", "tasks-schema", e.message[:200])
        for e in jsonschema.Draft202012Validator(load_schema(cfg, "task.schema.json")).iter_errors(doc)
    ]
    by_id = {u.id: u for u in units}
    misc_ids = {m["id"] for m in load_misconceptions(cfg)}
    tasks = doc.get("tasks", [])
    if len(tasks) < MIN_TASKS:
        issues.append(Issue("error", "tasks", f"заданий {len(tasks)}, нужно не меньше {MIN_TASKS}"))
    types = {t["type"] for t in tasks}
    if len(types) < MIN_TYPES:
        issues.append(Issue("error", "tasks", f"типов {len(types)}, нужно не меньше {MIN_TYPES}"))
    if len({t["id"] for t in tasks}) != len(tasks):
        issues.append(Issue("error", "tasks", "повторяются id заданий"))
    for t in tasks:
        issues += [Issue("error", t["id"], p) for p in task_problems(t, layer, by_id, misc_ids)]
    return issues


def _nodes_for_tasks(layer: dict) -> str:
    out = []
    for n in layer["nodes"]:
        parts = [f"<step id=\"{n['id']}\" title=\"{n['title']}\" difficulty=\"{n['difficulty']['primary']}\">"]
        parts.append("Суть: " + n["layers"]["main"]["text"])
        parts.append("Как работает: " + n["layers"]["mechanism"]["text"])
        if n["steps"]:
            parts.append("По шагам: " + " | ".join(p["text"] for p in n["steps"]))
        if n["conditions"]:
            parts.append("Цифры и условия: " + " | ".join(p["text"] for p in n["conditions"]))
        for f in n["formulas"]:
            parts.append(f"Формула: {f['latex_fixed'] or f['latex']} — {f['meaning']}")
        for w in n["worked_examples"]:
            parts.append(f"Пример автора: {w['condition']} | {' | '.join(w['steps'])} | {w['result']}")
        for t in n["book_tasks"]:
            parts.append(f"Задача книги {t['number']}: {t['text']}" + (f" (нужен {t['figure']})" if t["figure"] else ""))
        if n["misconception"]:
            parts.append("Заблуждение: " + n["misconception"]["text"])
        parts.append("Утверждения:\n" + "\n".join(f"{c['id']}: {c['text']}" for c in n["claims"]))
        parts.append("</step>")
        out.append("\n".join(parts))
    return "\n\n".join(out)


def generate_tasks(
    cfg: Config, ch: Chapter, llm: LLM, layer: dict, units: list[Unit], *, force: bool = False, log=print
) -> tuple[dict, list[Issue]]:
    by_id = {u.id: u for u in units}
    misc = load_misconceptions(cfg)
    misc_ids = {m["id"] for m in misc}
    want = max(20, min(30, len(layer["nodes"]) + 6))
    system = system_blocks(cfg, ch, units, load_errata(cfg, ch))[:2]
    user = prompt(
        "tasks",
        count=want,
        nodes=_nodes_for_tasks(layer),
        misc="\n".join(f"{m['id']}: {m['text']}" for m in misc),
    )

    def split(raw_tasks: list[dict]) -> tuple[list[dict], list[tuple[dict, list[str]]]]:
        ok, bad = [], []
        for r in raw_tasks:
            probs = task_problems(shape_task(r, "t"), layer, by_id, misc_ids)
            (bad.append((r, probs)) if probs else ok.append(r))
        return ok, bad

    ok: list[dict] = []
    bad: list[tuple[dict, list[str]]] = []
    try:
        ok, bad = split(llm.json("senior", system, user, TASKS_SCHEMA, label="tasks", effort="medium")["tasks"])
        if bad:
            # На исправление идут только дефектные задания и только их шаги.
            log(f"  задания: {len(bad)} из {len(ok) + len(bad)} на исправление")
            steps = {r["step_id"] for r, _ in bad}
            part = {"nodes": [n for n in layer["nodes"] if n["id"] in steps]}
            fixed = llm.json(
                "fast",
                system,
                prompt("tasks", count=len(bad), nodes=_nodes_for_tasks(part), misc="; ".join(sorted(misc_ids)))
                + f"\n\nЭти задания не прошли проверку. Исправь их и верни только их, {len(bad)} штук:\n"
                + "\n".join(json.dumps(r, ensure_ascii=False) + "\nЗамечания: " + "; ".join(p) for r, p in bad),
                TASKS_SCHEMA,
                label="tasks-fix1",
                effort="medium",
            )["tasks"]
            ok += split(fixed)[0]
    except LLMError as e:
        log(f"  задания без модели: {str(e)[:120]}")
    made_by = "model" if ok else "rules"

    # Не хватает заданий или типов — добираем из слоя по правилам.
    if len(ok) < MIN_TASKS or len({r["type"] for r in ok}) < MIN_TYPES:
        spare, broken = split(tasks_by_rules(layer, misc, term_regex, want * 2))
        have = {r["type"] for r in ok}
        goal = want if not ok else MIN_TASKS + 3
        per_step: dict[str, int] = {}
        for r in ok:
            per_step[r["step_id"]] = per_step.get(r["step_id"], 0) + 1
        spare.sort(key=lambda r: r["type"] in have)
        added = 0
        for r in spare:
            if len(ok) >= goal:
                break
            if per_step.get(r["step_id"], 0) < 2:
                per_step[r["step_id"]] = per_step.get(r["step_id"], 0) + 1
                ok.append(r)
                added += 1
        log(f"  по правилам добавлено {added}, отброшено проверкой {len(broken)}")
        if made_by == "model" and added:
            made_by = "model+rules"
    order = {n["id"]: n["order"] for n in layer["nodes"]}
    ok.sort(key=lambda r: order[r["step_id"]])
    doc = {
        "chapter": ch.id,
        "tasks": [shuffle_options(shape_task(r, f"{ch.prefix}.t{i:02d}")) for i, r in enumerate(ok, 1)],
    }
    write_generated(ch, "tasks.json", doc, force)
    note_source(ch, "tasks_by", made_by)
    return doc, check_tasks(cfg, doc, layer, units)
