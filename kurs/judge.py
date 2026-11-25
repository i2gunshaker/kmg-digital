"""Смысловая проверка карточек: сверка с единицами главы, которую код сделать не может.

Судья видит карточку, её единицы и страницы PDF (если есть) и возвращает замечания вместе с готовой
правкой места. Правку вносит код, поэтому остальная часть карточки не меняется.
"""
from __future__ import annotations

import copy
import json
import re

from .llm import LLM
from .units import Unit

_S = {"type": "string"}
_SL = {"type": "array", "items": _S}
BLOCKS = ["mechanism", "steps", "practice", "nuances", "deeper", "conditions"]
KINDS = ["observation", "inference", "assumption"]
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": _S,
                    "severity": {"type": "string", "enum": ["error", "minor"]},
                    "problem": _S,
                    "action": {"type": "string", "enum": ["replace", "delete", "add"]},
                    "text": _S,
                    "quote": _S,
                    "unit_ids": _SL,
                    "block": {"type": "string", "enum": BLOCKS + [""]},
                    "kind": {"type": "string", "enum": KINDS + [""]},
                    "items": _SL,
                    "extra": _S,
                },
            },
        },
        "source_doubts": {
            "type": "array",
            "items": {"type": "object", "properties": {"page": {"type": "integer"}, "what": _S}},
        },
    },
}

_EDIT_FIELDS = ("path", "action", "text", "quote", "unit_ids", "block", "kind", "items", "extra")
_ISSUE_PROPS = JUDGE_SCHEMA["properties"]["issues"]["items"]["properties"]
# Правка без рассуждений: для машинных замечаний, где что не так уже известно.
PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "edits": {"type": "array", "items": {"type": "object", "properties": {k: _ISSUE_PROPS[k] for k in _EDIT_FIELDS}}}
    },
}

MIN_QUOTE_WORDS = 5
_PROSE = {"paragraph", "worked_example", "task", "figure"}
_SOURCE_KIND = {"formula": "formula", "table": "table", "task": "task", "figure": "figure_caption"}
_TEXT_FIELDS = {
    "topic", "main", "misconception_text", "data_from_chapter", "data_breaks", "analogy_text", "analogy_breaks_where",
    "now_practice", "decision_rule", "decision_conclusion", "decision_counterexample",
}
_LIST_FIELDS = {"data_hypotheses", "inefficiency", "decision_features"}
# Пустая строка в этих полях ломает карточку, поэтому удаление не допускается.
_REQUIRED = {"topic", "main", "data_from_chapter", "data_breaks", "analogy_text", "analogy_breaks_where"}


def numbered_card(wire: dict) -> str:
    """Карточка с подписанными местами: по этим подписям судья указывает path."""
    out = [f"topic: {wire['topic']}", f"main: {wire['main']}"]
    for i, p in enumerate(wire["paragraphs"]):
        out.append(f"paragraphs[{i}] (block {p['block']}; {', '.join(p['unit_ids']) or 'без единиц'}): {p['text']}")
    for i, c in enumerate(wire["claims"]):
        out.append(f"claims[{i}] ({c['kind']}; {c['unit_id']}): {c['text']}\n    цитата: «{c['quote']}»")
    for name in ("formulas", "variables", "worked_examples", "glossary", "needs_scan"):
        for i, x in enumerate(wire[name]):
            out.append(f"{name}[{i}]: {json.dumps(x, ensure_ascii=False)}")
    for name in (
        "misconception_ref", "misconception_text", "data_from_chapter", "data_breaks", "data_hypotheses", "analogy_text",
        "analogy_breaks_where", "now_practice", "inefficiency", "decision_features", "decision_rule", "decision_conclusion",
        "decision_counterexample", "to_data",
    ):
        v = wire[name]
        if isinstance(v, list):
            out += [f"{name}[{i}]: {x}" for i, x in enumerate(v)]
        elif v:
            out.append(f"{name}: {v}")
    return "\n".join(out)


def weak_quotes(card: dict, units: dict[str, Unit]) -> list[str]:
    """Цитаты-обрывки: дословные, но слишком короткие, чтобы что-то подтвердить."""
    out = []
    for i, c in enumerate(card["claims"]):
        u = units.get(c["unit_id"])
        if u is None or u.kind not in _PROSE:
            continue
        words = re.findall(r"\w+", c["quote"])
        whole = len(re.sub(r"\s+", "", c["quote"])) >= 0.8 * len(re.sub(r"\s+", "", u.text))
        if len(words) < MIN_QUOTE_WORDS and not whole:
            out.append(
                f"claims[{i}]: цитата «{c['quote']}» слишком короткая и не подтверждает «{c['text'][:100]}». "
                f"Возьми из {c['unit_id']} кусок от {MIN_QUOTE_WORDS} слов, который сам подтверждает утверждение."
            )
    return out


_PATH = re.compile(r"^\s*([a-z_]+)\s*(?:\[\s*(\d+|\+)\s*\])?")


def _own(ids: list[str], own: set[str]) -> list[str]:
    return [x for x in dict.fromkeys(ids) if x in own]


def _edit_item(name: str, cur: dict, e: dict, own: set[str], units: dict[str, Unit]) -> dict:
    """Поля правки поверх записи; пустое поле правки оставляет прежнее значение."""
    x = copy.deepcopy(cur)
    ids = _own(e["unit_ids"], own)
    text, extra = e["text"].strip(), e["extra"].strip()
    if name == "paragraphs":
        x["text"] = text or x["text"]
        x["block"] = e["block"] or x["block"]
        x["unit_ids"] = ids or x["unit_ids"]
        if extra == "model":  # судья снял абзац с автора
            x["unit_ids"] = []
    elif name == "claims":
        x["text"] = text or x["text"]
        x["quote"] = e["quote"].strip() or x["quote"]
        x["kind"] = e["kind"] or x["kind"]
        if ids:
            x["unit_id"] = ids[0]
            x["source_kind"] = _SOURCE_KIND.get(units[ids[0]].kind, "text")
    elif name == "worked_examples":
        x["condition"] = text or x["condition"]
        x["steps"] = [s for s in e["items"] if s.strip()] or x["steps"]
        x["result"] = extra or x["result"]
        x["unit_ids"] = ids or x["unit_ids"]
    elif name == "glossary":
        x["definition"] = text or x["definition"]
    elif name == "formulas":
        x["meaning"] = text or x["meaning"]
        x["latex_fixed"] = extra or x["latex_fixed"]
    elif name == "variables":
        x["meaning"] = text or x["meaning"]
        x["unit"] = extra or x["unit"]
    elif name == "needs_scan":
        x["what"] = text or x["what"]
    return x


def _new_item(name: str, e: dict, own: set[str], units: dict[str, Unit]) -> dict | None:
    text = e["text"].strip()
    ids = _own(e["unit_ids"], own)
    if not text:
        return None
    if name == "paragraphs":
        return {"block": e["block"] or "mechanism", "text": text, "unit_ids": ids} if ids else None
    if name == "claims" and ids and e["quote"].strip():
        return {
            "text": text, "kind": e["kind"] or "observation", "unit_id": ids[0], "quote": e["quote"].strip(),
            "source_kind": _SOURCE_KIND.get(units[ids[0]].kind, "text"),
        }
    if name == "worked_examples" and ids:
        return {"unit_ids": ids, "condition": text, "steps": [s for s in e["items"] if s.strip()], "result": e["extra"].strip(), "figure": ""}
    if name == "glossary" and e["items"] and e["items"][0].strip():
        return {"term": e["items"][0].strip(), "definition": text, "en": e["items"][1].strip() if len(e["items"]) > 1 else ""}
    if name == "formulas" and ids:
        return {"unit_id": ids[0], "meaning": text, "latex_fixed": e["extra"].strip()}
    if name == "variables" and e["items"] and e["items"][0].strip():
        return {"symbol": e["items"][0].strip(), "meaning": text, "unit": e["extra"].strip()}
    if name == "needs_scan" and e["extra"].strip().isdigit():
        return {"page": int(e["extra"].strip()), "what": text}
    return None


def apply_edits(wire: dict, issues: list[dict], own: set[str], units: dict[str, Unit]) -> tuple[dict, list[dict], list[dict]]:
    """Вносит правки судьи. Возвращает карточку, внесённые и непригодные замечания."""
    w = copy.deepcopy(wire)
    done: list[dict] = []
    skipped: list[dict] = []
    drop: dict[str, set[int]] = {}
    lists = {"paragraphs", "claims", "worked_examples", "glossary", "formulas", "variables", "needs_scan"}
    for e in issues:
        m = _PATH.match(e["path"])
        name, idx = (m.group(1), m.group(2)) if m else ("", None)
        ok = False
        if name in _TEXT_FIELDS and idx is None:
            if e["action"] == "delete" and name not in _REQUIRED:
                w[name], ok = "", True
            elif e["action"] != "delete" and e["text"].strip():
                w[name], ok = e["text"].strip(), True
        elif name in _LIST_FIELDS:
            vals = w[name]
            if e["action"] == "add" and name != "inefficiency" and e["text"].strip():
                vals.append(e["text"].strip())
                ok = True
            elif idx is not None and idx.isdigit() and int(idx) < len(vals):
                if e["action"] == "delete" and name != "inefficiency":
                    drop.setdefault(name, set()).add(int(idx))
                    ok = True
                elif e["action"] == "replace" and e["text"].strip():
                    vals[int(idx)], ok = e["text"].strip(), True
            elif idx is None and e["action"] == "replace" and e["items"] and name != "inefficiency":
                w[name], ok = [s for s in e["items"] if s.strip()], True
        elif name in lists:
            if e["action"] == "add" or idx == "+":
                new = _new_item(name, e, own, units)
                if new:
                    w[name].append(new)
                    ok = True
            elif idx is not None and int(idx) < len(wire[name]):
                i = int(idx)
                if e["action"] == "delete":
                    # Последнее утверждение и термины узла не удаляются: без них узел не проходит проверку.
                    ok = name != "glossary" and not (name == "claims" and len(wire[name]) - len(drop.get(name, ())) <= 1)
                    if ok:
                        drop.setdefault(name, set()).add(i)
                else:
                    w[name][i] = _edit_item(name, w[name][i], e, own, units)
                    ok = w[name][i] != wire[name][i]
        elif name == "misconception_ref" and e["action"] == "delete":
            w["misconception_ref"], ok = "", True
        (done if ok else skipped).append(e)
    for name, ids in drop.items():
        w[name] = [x for i, x in enumerate(w[name]) if i not in ids]
    return w, done, skipped


def judge_card(llm: LLM, role: str, system: list[str], user: str, *, label: str, effort: str = "medium") -> dict:
    res = llm.json(role, system, user, JUDGE_SCHEMA, label=label, effort=effort, max_tokens=24000)
    res["issues"] = [x for x in res["issues"] if x["problem"].strip()]
    return res


def patch_card(llm: LLM, role: str, system: list[str], user: str, *, label: str, effort: str = "low") -> list[dict]:
    """Правки по машинным замечаниям в том же виде, что у судьи."""
    res = llm.json(role, system, user, PATCH_SCHEMA, label=label, effort=effort, max_tokens=12000)
    return [{**e, "severity": "error", "problem": ""} for e in res["edits"]]


def issue_lines(issues: list[dict]) -> list[str]:
    return [f"{x['path']}: {x['problem'].strip()}" + (f" Исправление: {x['text'].strip()}" if x["text"].strip() else "") for x in issues]
