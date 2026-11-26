"""Модуль «Закладки»: контракт ответа, проверки кодом, модель и зрение на подставной модели — без сети и ключа."""
from __future__ import annotations

import base64
import json

import pytest

from kurs import config as config_mod
from kurs.config import Config
from kurs.llm import LLM, Reply, Usage
from kurs.review import CATEGORIES, SEVERITIES, _resolve_target, check_frames, main, review, self_check
from kurs.verify import Source

MD = """<!-- page 20 -->
# § 3. Коллекторы и их свойства

Пористость песчаников меняется от 5 до 25 % и зависит от укладки зёрен и цемента.

С увеличением глубины залегания пористость песчаников уменьшается.

Высокая пористость не гарантирует высокую проницаемость, потому что поры бывают не связаны между собой.

<!-- page 21 -->
Как правило, глинистые породы отдают нефть только при большом перепаде давления.

Удельное сопротивление нефтеносных пород выше, чем водоносных, если пористость одинакова.

Граничное значение пористости для коллектора составляет 17 %.

Коэффициент нефтенасыщенности рассчитывают по формуле Арчи из удельного сопротивления.

Приток нефти к скважине обеспечивает упругое расширение жидкости и породы при снижении давления.
"""
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")

ITEMS = [
    # верные
    {"id": "C1", "type": "claim", "kind": "observation", "text": "Граничное значение пористости для коллектора составляет 17 %.", "page_pdf": 21,
     "quote": "Граничное значение пористости для коллектора составляет 17 %."},
    # приманка: звучит странно, но верно
    {"id": "C2", "type": "claim", "kind": "observation", "text": "Высокая пористость сама по себе не гарантирует высокую проницаемость.", "page_pdf": 20,
     "quote": "Высокая пористость не гарантирует высокую проницаемость"},
    # закладки
    {"id": "C3", "type": "claim", "kind": "observation", "text": "Граничное значение пористости для коллектора составляет 12 %.", "page_pdf": 21,
     "quote": "Граничное значение пористости для коллектора составляет 17 %."},
    {"id": "C4", "type": "claim", "kind": "observation", "text": "С увеличением глубины залегания пористость песчаников увеличивается.", "page_pdf": 20},
    {"id": "C5", "type": "claim", "kind": "observation", "text": "Удельное сопротивление нефтеносных пород выше, чем водоносных.", "page_pdf": 21},
    {"id": "C6", "type": "claim", "kind": "observation", "text": "Коэффициент нефтенасыщенности измеряют прибором по удельному сопротивлению.", "page_pdf": 21},
    {"id": "A1", "type": "analogy", "text": "Поры как сеть дорог: важны не перекрёстки, а связи между ними.", "breaks_where": ""},
    {"id": "A2", "type": "analogy", "text": "Пласт как губка: держит жидкость в порах.", "breaks_where": "Губка мягкая, а порода не сжимается заметно при отборе."},
    {"id": "S1", "type": "script_line", "text": "Граничное значение пористости — 17 процентов.", "t_start": 1.0, "t_end": 3.0, "claim_ref": "C1", "on_screen": "Граница 27 %"},
    {"id": "S2", "type": "script_line", "text": "Нефть к скважине толкает напор газа из шапки.", "t_start": 3.0, "t_end": 6.0, "claim_ref": "C1", "on_screen": "Что толкает нефть"},
]
FRAMES = [
    {"id": "F1", "video_id": "v1", "file": "frames/F1.png", "t": 1.0, "frame_class": "B", "label": "схема", "shows": "разрез пласта", "plaque": "схема · стр. 20"},
    {"id": "F2", "video_id": "v1", "file": "frames/F2.png", "t": 2.0, "frame_class": "A", "label": "реальные данные", "shows": "керн", "plaque": None},
    {"id": "F3", "video_id": "v1", "file": "frames/F3.png", "t": 3.0, "frame_class": "B", "label": "симуляция", "shows": "газ, нефть и вода в ловушке", "plaque": "симуляция"},
]


@pytest.fixture()
def pkg(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "BUILD", tmp_path / "build")
    monkeypatch.delenv("KURS_LLM_OFFLINE", raising=False)
    d = tmp_path / "control"
    (d / "frames").mkdir(parents=True)
    (d / "chapter.md").write_text(MD, encoding="utf-8")
    for f in FRAMES:
        (d / f["file"]).write_bytes(PNG)
    p = d / "package.json"
    p.write_text(json.dumps({
        "package_id": "ctrl-1",
        "chapter": "demo",
        "chapter_file": "chapter.md",
        "items": ITEMS,
        "scenes": [{"video_id": "v1", "spec": {"id": "sc1", "objects": []}}],
        "frames": FRAMES,
    }, ensure_ascii=False), encoding="utf-8")
    return Config(), p, tmp_path


def by_target(doc):
    return {r["target"]: r for r in doc["remarks"]}


# ---------------------------------------------------------------- контракт
def test_output_contract_and_one_remark_per_target(pkg):
    cfg, p, tmp = pkg
    res = review(cfg, p, tmp / "out" / "blind.json", use_llm=False)
    doc = json.loads((tmp / "out" / "blind.json").read_text(encoding="utf-8"))
    assert set(doc) == {"package_id", "team", "remarks"} and doc["package_id"] == "ctrl-1"
    assert self_check(doc) == [] and res["errors"] == []
    targets = [r["target"] for r in doc["remarks"]]
    assert len(targets) == len(set(targets))
    for r in doc["remarks"]:
        assert r["category"] in CATEGORIES and r["severity"] in SEVERITIES
        assert r["problem"] and r["fix"] and r["page_pdf"] in (20, 21)


def test_self_check_catches_contract_breaks():
    bad = {"package_id": "x", "team": "t", "remarks": [
        {"target": "C1", "category": "typo", "severity": "high", "problem": "p", "fix": "f", "page_pdf": 1},
        {"target": "C1", "category": "other", "severity": "minor", "problem": "p", "fix": "", "page_pdf": 1},
    ]}
    errs = self_check(bad)
    assert any("категория" in e for e in errs) and any("тяжесть" in e for e in errs)
    assert any("пустое fix" in e for e in errs) and any("второе замечание" in e for e in errs)


# ---------------------------------------------------------------- слой 1: код
def test_deterministic_layer_finds_planted_and_spares_baits(pkg):
    cfg, p, tmp = pkg
    doc = review(cfg, p, tmp / "b.json", use_llm=False)["doc"]
    r = by_target(doc)
    assert r["C3"]["category"] == "wrong_scaling" and r["C3"]["severity"] == "critical"
    assert "17" in r["C3"]["fix"] and r["C3"]["page_pdf"] == 21  # исправленная формулировка с числом автора
    assert r["C4"]["category"] == "sign_flip" and "уменьшается" in r["C4"]["fix"]
    assert r["C5"]["category"] == "lost_condition" and r["C5"]["page_pdf"] == 21
    assert r["C6"]["category"] == "measured_vs_inferred"
    assert r["A1"]["category"] == "analogy_without_limit"
    assert r["S1"]["category"] == "wrong_scaling"  # на экране 27 %, в фразе и у автора 17 %
    for ok in ("C1", "C2", "A2", "F1", "F2"):
        assert ok not in r, (ok, r.get(ok))


def test_check_frames_catches_invalid_label_and_ignores_null_plaque(tmp_path):
    md = tmp_path / "c.md"
    md.write_text(MD, encoding="utf-8")
    src = Source.from_md(md) if hasattr(Source, "from_md") else type("S", (), {"pages": {20: MD}})()
    rems = check_frames([
        {"id": "F1", "label": "схема", "plaque": None},
        {"id": "F2", "label": "фото", "plaque": "фото"},
    ], tmp_path, src)
    targets = {x["target"]: x for x in rems}
    assert "F1" not in targets
    assert targets["F2"]["category"] == "frame_error"


def test_key_json_is_never_read(pkg, tmp_path):
    cfg, _, tmp = pkg
    key = tmp_path / "key.json"
    key.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        review(cfg, key, tmp / "x.json", use_llm=False)


# ---------------------------------------------------------------- слой 2 и 3: подставная модель
def fake_llm(cfg, tmp, calls):
    """Подставная модель: вердикты по элементам (schema.verdicts) и решения судей (schema.rulings)."""
    def verdict(target, error, cat="other", sev="minor", problem="", fix="", page=20, ev="Пористость песчаников меняется от 5 до 25 %"):
        return {"target": target, "evidence_quote": ev, "page_pdf": page, "error": error, "confidence": 90, "category": cat, "severity": sev,
                "problem": problem, "fix": fix}

    def transport(model, system, user, schema, effort, max_tokens, images=None):
        props = schema["properties"]
        calls.append(("judge" if "rulings" in props else "vision" if images else "text", len(images or [])))
        if "rulings" in props:
            view = json.loads(user[user.index("Кандидаты:\n") + len("Кандидаты:\n"):])
            advocate = "Ты защитник" in user
            out = []
            for v in view:
                # приманку C2 снимают оба судьи, A2 — защитник (взгляд один, значит не проходит)
                keep = v["target"] not in ("C2",) and not (advocate and v["target"] == "A2")
                out.append({"target": v["target"], "why": ".", "keep": keep, "fix": "Исправлено." if keep else "", "page_pdf": 20, "evidence_quote": ""})
            return Reply(json.dumps({"rulings": out}, ensure_ascii=False), model, Usage(input=100, output=50))
        if images:
            view = json.loads(user[user.index("Кадр:\n") + len("Кадр:\n"):])
            bad = view["id"] == "F3"
            out = [verdict(view["id"], bad, "frame_error", "critical", "Газ нарисован под нефтью." if bad else "", "Газ над нефтью." if bad else "")]
            return Reply(json.dumps({"verdicts": out}, ensure_ascii=False), model, Usage(input=100, output=50))
        out = [verdict(v["id"], False) for v in json.loads(user[user.index("Элементы:\n") + len("Элементы:\n"):])]
        for o in out:
            if o["target"] == "S2":
                o.update(verdict("S2", True, "wrong_mechanism", "critical", "Механизм притока не тот.",
                                 "Нефть к скважине поступает за счёт упругого расширения жидкости и породы при снижении давления.", 20,
                                 "Приток нефти к скважине обеспечивает упругое расширение жидкости и породы при снижении давления."))
            if o["target"] == "C2":
                o.update(verdict("C2", True, "overgeneralization", "significant", "?", "?", 20, "Высокая пористость не гарантирует высокую проницаемость"))
            if o["target"] == "A2" and "Взгляд первый" in user:
                o.update(verdict("A2", True, "wrong_mechanism", "critical", "?", "?", 21, "Губка всегда сжимается сильнее любой горной породы."))
        out.append(verdict("ZZ", True, "other", "minor", "?", "?", 21, "Граничное значение"))  # чужой target
        return Reply(json.dumps({"verdicts": out}, ensure_ascii=False), model, Usage(input=100, output=50))

    return LLM(cfg, scope="review", transport=transport, cache_dir=tmp / "cache", log_path=tmp / "usage.jsonl", budget=1.0)


def test_llm_layers_with_judges(pkg):
    cfg, p, tmp = pkg
    calls: list = []
    doc = review(cfg, p, tmp / "b.json", llm=fake_llm(cfg, tmp, calls), log=lambda *_: None)["doc"]
    r = by_target(doc)
    assert r["S2"]["category"] == "wrong_mechanism" and r["S2"]["page_pdf"] == 21  # страница утверждения, на которое ссылается фраза
    assert "C2" not in r and "A2" not in r and "ZZ" not in r
    assert r["F3"]["category"] == "frame_error"
    assert r["C3"]["category"] == "wrong_scaling" and r["C3"]["page_pdf"] == 21
    kinds = [c[0] for c in calls]
    assert kinds.count("vision") == 6 and all(n == 1 for k, n in calls if k == "vision")  # три кадра, два взгляда, по кадру на запрос
    assert kinds.count("text") == 9  # три взгляда: утверждения, закадр, аналогии
    assert self_check(doc) == []
    trace = json.loads((tmp / "b.trace.json").read_text(encoding="utf-8"))
    assert next(t for t in trace if t["target"] == "S2")["votes"] == 3
    assert _resolve_target("F06 — shows: ...", {"F06", "F08"}) == "F06"
    assert _resolve_target("F07, F08", {"F07", "F08"}) is None
    assert _resolve_target("ZZ", {"F06"}) is None


def test_parallel_system_is_shared(pkg):
    """Общая часть одинакова у всех запросов: глава кэшируется один раз."""
    cfg, p, tmp = pkg
    seen = set()
    llm = fake_llm(cfg, tmp, [])
    inner = llm._transport

    def spy(model, system, user, *a, **k):
        seen.add(tuple(system))
        return inner(model, system, user, *a, **k)

    llm._transport = spy
    review(cfg, p, tmp / "b.json", llm=llm, log=lambda *_: None)
    assert len(seen) == 1


def test_without_money_only_code_remarks_survive(pkg):
    """Модель недоступна (нет --max-usd): второго прохода нет, поэтому выдаётся только доказанное кодом."""
    cfg, p, tmp = pkg
    doc = review(cfg, p, tmp / "b.json", max_usd=None, log=lambda *_: None)["doc"]
    r = by_target(doc)
    assert {"C3", "C4", "C5", "A1"} <= set(r) and "S2" not in r


def test_cli_entry(pkg, capsys):
    cfg, p, tmp = pkg
    rc = main([str(p), "-o", str(tmp / "cli.json"), "--no-llm"])
    assert rc == 0 and (tmp / "cli.json").exists()
    assert "замечаний" in capsys.readouterr().out
