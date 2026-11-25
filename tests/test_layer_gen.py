"""Генератор слоя на подставной модели: без сети и без ключа."""
from __future__ import annotations

import copy
import json

import pytest

from kurs import config as config_mod
from kurs import pdfref
from kurs.config import Config
from kurs.judge import apply_edits, weak_quotes
from kurs.layer_gen import (
    ManualEdits,
    check_outline,
    generate_layer,
    load_misconceptions,
    long_sentences,
    place_terms,
    repair_outline,
    snap_quote,
    strict_norm,
    task_figure,
    to_wire,
    write_generated,
)
from kurs.llm import (
    LLM,
    BudgetExceeded,
    LLMError,
    OfflineMiss,
    Reply,
    SchemaTooLarge,
    Usage,
    parse_json,
    restore_latex,
    strict_schema,
)
from kurs.tasks_gen import eval_expr, generate_routing, generate_tasks, shuffle_options, task_problems
from kurs.units import Unit, build_units, load_pages
from kurs.verify import check_coverage, check_layer

MD = """<!-- page 10 -->
# § 1. Пористость пород

Пористость показывает долю пустот в объёме породы. Эта доля меняется от 5 до 25 % в песчаниках и зависит от укладки зёрен, цемента и глины между ними.

Проницаемость показывает, насколько легко жидкость проходит через породу. Высокая пористость не гарантирует высокую проницаемость, потому что поры бывают не связаны между собой, и такая порода жидкость не отда-

<!-- page 11 -->
ёт при любом перепаде давления, хотя формально содержит её в большом количестве внутри.

$$k = a \\cdot b$$

11

# Задачи

1. Определить пористость песчаника по данным рис. 5 при заданных условиях опыта.
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "BUILD", tmp_path / "build")
    for k in ("KURS_LLM_OFFLINE", "KURS_LLM_BUDGET", "KURS_LLM_TOTAL", "KURS_LLM_MODEL", "KURS_LLM_BACKEND", "KURS_LLM_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    md = tmp_path / "demo_glava.md"
    md.write_text(MD, encoding="utf-8")
    cfg = Config()
    ch = cfg.chapter(str(md))
    units = build_units(cfg, ch)
    return cfg, ch, units, tmp_path


def make_llm(cfg, tmp_path, transport, **kw) -> LLM:
    if "wallet" not in kw:
        kw.setdefault("budget", 5.0)
    return LLM(
        cfg,
        scope="test",
        transport=transport,
        cache_dir=tmp_path / "cache",
        log_path=tmp_path / "usage.jsonl",
        **kw,
    )


def reply(doc) -> Reply:
    return Reply(json.dumps(doc, ensure_ascii=False), "claude-sonnet-5-5", Usage(input=1000, output=1000))


# ---------------------------------------------------------------- llm
SCHEMA_A = {"type": "object", "properties": {"a": {"type": "string"}}}


def echo(calls=None):
    def transport(model, system, user, schema, effort, max_tokens):
        if calls is not None:
            calls.append(user)
        return reply({"a": user})

    return transport


def test_llm_cache_and_offline(env, monkeypatch):
    cfg, _, _, tmp = env
    calls: list[str] = []
    llm = make_llm(cfg, tmp, echo(calls))
    assert llm.json("fast", ["s"], "один", SCHEMA_A, label="x") == {"a": "один"}
    assert llm.json("fast", ["s"], "один", SCHEMA_A, label="x") == {"a": "один"}
    assert len(calls) == 1 and llm.cache_hits == 1
    assert llm.spent == pytest.approx(0.0006)  # Haiku: 1000 токенов входа и 1000 выхода

    monkeypatch.setenv("KURS_LLM_OFFLINE", "1")
    off = make_llm(cfg, tmp, echo())
    assert off.json("fast", ["s"], "один", SCHEMA_A, label="x") == {"a": "один"}
    with pytest.raises(OfflineMiss):
        off.json("fast", ["s"], "новый", SCHEMA_A, label="x")
    log = [json.loads(x) for x in (tmp / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(r["from_cache"] for r in log) == 2 and all("key" not in json.dumps(r) for r in log)


def test_paid_calls_are_off_by_default(env):
    cfg, _, _, tmp = env
    calls: list[str] = []
    llm = make_llm(cfg, tmp, echo(calls), budget=None)
    with pytest.raises(BudgetExceeded, match="выключены"):
        llm.json("fast", ["s"], "один", SCHEMA_A, label="x")
    assert calls == []


def test_run_limit_is_shared_between_chapters(env):
    cfg, _, _, tmp = env
    first = make_llm(cfg, tmp, echo(), budget=0.008)
    second = make_llm(cfg, tmp, echo(), wallet=first.wallet)
    first.json("fast", ["s"], "один", SCHEMA_A, label="x")
    second.json("fast", ["s"], "два", SCHEMA_A, label="x")
    assert first.wallet.spent == pytest.approx(0.0012)
    with pytest.raises(BudgetExceeded, match="на запуск"):
        for k in range(20):
            second.json("fast", ["s"], f"ещё {k}", SCHEMA_A, label="x")
    assert first.wallet.spent <= 0.008


def test_total_limit_counts_previous_runs(env):
    cfg, _, _, tmp = env
    cfg.llm["total_usd"] = 20.0
    row = {"cost_usd": 19.999, "from_cache": False}
    (tmp / "usage.jsonl").write_text(json.dumps(row) + "\nбитая строка\n", encoding="utf-8")
    calls: list[str] = []
    llm = make_llm(cfg, tmp, echo(calls), budget=5.0)
    assert llm.wallet.before == pytest.approx(19.999)
    with pytest.raises(BudgetExceeded, match="за всё время"):
        llm.json("fast", ["s"], "один", SCHEMA_A, label="x")
    assert calls == []


def test_parallel_calls_reserve_money_first(env):
    """Восемь запросов разом не выходят за предел: место под цену бронируется до вызова."""
    import time as _time
    from concurrent.futures import ThreadPoolExecutor

    cfg, _, _, tmp = env
    calls: list[str] = []

    def slow(*a):
        _time.sleep(0.05)
        calls.append(a[2])
        return Reply(json.dumps({"a": a[2]}), "m", Usage(output=11000))

    llm = make_llm(cfg, tmp, slow, budget=0.02)

    def one(k):
        try:
            llm.json("fast", ["s"], f"запрос {k}", SCHEMA_A, label="x")
        except BudgetExceeded:
            return 0
        return 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        ok = sum(pool.map(one, range(8)))
    assert 1 <= ok <= 3 and llm.wallet.spent <= 0.02 and llm.wallet.reserved == pytest.approx(0)


def test_second_paid_process_is_refused(env):
    cfg, _, _, tmp = env
    a = make_llm(cfg, tmp, echo())
    a.json("fast", ["s"], "один", SCHEMA_A, label="x")
    b = make_llm(cfg, tmp, echo())
    with pytest.raises(BudgetExceeded, match="уже идёт"):
        b.json("fast", ["s"], "два", SCHEMA_A, label="x")
    a.wallet.release()
    assert b.json("fast", ["s"], "два", SCHEMA_A, label="x") == {"a": "два"}


def test_model_without_price_is_not_called(env, monkeypatch):
    cfg, _, _, tmp = env
    monkeypatch.setenv("KURS_LLM_MODEL", "неизвестная")
    calls: list[str] = []
    with pytest.raises(LLMError, match="нет цены"):
        make_llm(cfg, tmp, echo(calls)).json("fast", ["s"], "один", SCHEMA_A, label="x")
    assert calls == []
    # Бесплатной локальной модели предел не нужен.
    cfg.llm["prices"]["неизвестная"] = {"input": 0, "output": 0}
    assert make_llm(cfg, tmp, echo(calls), budget=None).json("fast", ["s"], "один", SCHEMA_A, label="x") == {"a": "один"}


def test_only_allowed_models_are_called(env, monkeypatch):
    cfg, _, _, tmp = env
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    cfg.use_profile("max")
    cfg.llm["allow"] = ["claude-haiku-5-5"]
    llm = LLM(cfg, scope="t", cache_dir=tmp / "c", log_path=tmp / "u.jsonl", budget=5.0)
    with pytest.raises(LLMError, match="запрещена"):
        llm.json("senior", ["s"], "один", SCHEMA_A, label="x")
    assert llm._transport is None and llm.spent == 0


def test_cut_reply_is_paid_and_counted(env):
    cfg, _, _, tmp = env

    def cut(model, system, user, schema, effort, max_tokens):
        return Reply("{", "claude-haiku-5-5", Usage(input=1000, output=1000), stop="ответ обрезан на 10 токенах")

    llm = make_llm(cfg, tmp, cut)
    with pytest.raises(LLMError, match="обрезан"):
        llm.json("fast", ["s"], "один", SCHEMA_A, label="x")
    assert llm.calls == 1 and llm.spent == pytest.approx(0.0006)
    assert json.loads((tmp / "usage.jsonl").read_text(encoding="utf-8"))["cost_usd"] == pytest.approx(0.0006)


def test_profiles_and_forecast(env):
    from kurs.cost import estimate

    cfg, ch, _, tmp = env
    llm = make_llm(cfg, tmp, echo())
    assert llm.model_for("senior") == "claude-haiku-5-5" and cfg.llm["judge_rounds"] == 1
    cheap = sum(estimate(cfg, ch, llm).values())
    cfg.use_profile("max")
    dear = sum(estimate(cfg, ch, make_llm(cfg, tmp, echo())).values())
    assert 0 < cheap < 0.1 and dear > 20 * cheap
    with pytest.raises(KeyError):
        cfg.use_profile("нет такого")
    long = Usage(input=200000)
    assert llm.cost("claude-haiku-5-5", long) == pytest.approx(0.1)


def test_llm_bad_json_retried_then_fails(env):
    cfg, _, _, tmp = env
    n = []

    def transport(*a):
        n.append(1)
        return Reply("не json", "m", Usage())

    with pytest.raises(LLMError):
        make_llm(cfg, tmp, transport).json("fast", [], "u", {"type": "object", "properties": {}}, label="x")
    assert len(n) == 3


def test_llm_large_schema_falls_back_to_words(env):
    cfg, _, _, tmp = env
    seen = []

    def transport(model, system, user, schema, effort, max_tokens):
        seen.append(schema is None)
        if schema is not None:
            raise SchemaTooLarge("grammar is too large")
        return Reply('```json\n{"a": "\\frac{1}{2}"}\n```' if "этой схеме" not in user else '{"a": 1}' if len(seen) == 2 else '{"a": "ok"}', "m", Usage())

    llm = make_llm(cfg, tmp, transport)
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert llm.json("fast", [], "u", schema, label="x") == {"a": "ok"}
    assert seen == [False, True, True]
    llm.json("fast", [], "ещё", schema, label="x")
    assert seen[-1] is True


def test_latex_survives_json():
    assert parse_json('текст {"a": "$\\alpha_{CII}$"} хвост') == {"a": "$\\alpha_{CII}$"}
    assert restore_latex(json.loads('{"a": "\\frac{1}{2} \\rho \\beta \\text{x}"}')) == {"a": "\\frac{1}{2} \\rho \\beta \\text{x}"}
    assert restore_latex({"a": ["два\tслова"]}) == {"a": ["два\tслова"]}


def test_strict_schema_closes_objects():
    s = strict_schema({"type": "array", "items": {"type": "object", "properties": {"a": {"type": "string"}}}})
    assert s["items"]["required"] == ["a"] and s["items"]["additionalProperties"] is False


# ---------------------------------------------------------------- цитаты
def test_snap_quote_exact_soft_typo_and_page_break(env):
    _, ch, units, _ = env
    pages = load_pages(ch)
    u1 = next(u for u in units if u.text.startswith("Пористость показывает"))
    u2 = next(u for u in units if u.text.startswith("Проницаемость"))

    q, p = snap_quote("доля  меняется от 5 до 25 %", u1, pages)
    assert (q, p) == ("доля меняется от 5 до 25 %", 10)
    # ё/е и опечатка модели: в слой попадает текст книги, а не модели.
    q, p = snap_quote("долю пустот в объеме породы", u1, pages)
    assert q == "долю пустот в объёме породы"
    q, _ = snap_quote("зависит от укладки зерн, цемента и глины между ними", u1, pages)
    assert "укладки зёрен, цемента" in q
    assert snap_quote("в известняках пористость всегда равна нулю и точка", u1, pages) is None
    assert u2.pages == [10, 11] or u2.pages == [10]
    for unit in units:
        hit = snap_quote(unit.text, unit, pages)
        if hit and len(unit.text) > 20:
            assert strict_norm(hit[0]) in strict_norm(pages[hit[1]])


def test_long_sentences_ignore_formula_and_abbr():
    assert not long_sentences("Порода (см. рис. 5) содержит воду. $a = b + c + d + e$ верно.")
    assert long_sentences(" ".join(["слово"] * 21) + ".")
    assert task_figure("по данным рис. 5 и рис. 24") == "рис. 5, 24"
    assert task_figure("вычислить пористость") is None


# ---------------------------------------------------------------- граф
def outline_for(units):
    paras = [u.id for u in units if u.kind == "paragraph" and len(u.text) > 60]
    rest = [u.id for u in units if u.id not in paras and u.kind != "formula" and len(u.text) > 5]
    form = [u.id for u in units if u.kind == "formula"]
    return {
        "nodes": [
            dict(slug="porosity", title="Что такое пористость породы", title_book="§ 1", gist="доля пустот",
                 why_here="с неё всё начинается", primary="М", secondary="", threshold=True, decision_on_data=False,
                 prerequisites=[], terms_introduced=["пористость"], unit_ids=paras[:1]),
            dict(slug="permeability", title="Почему поры не всегда пропускают", title_book="§ 1", gist="связь пор",
                 why_here="нужна пористость", primary="И", secondary="Н", threshold=False, decision_on_data=True,
                 prerequisites=["porosity"], terms_introduced=["проницаемость"], unit_ids=paras[1:] + form + rest),
        ],
        "excluded": [{"unit_id": u.id, "reason": "номер страницы"} for u in units if u.text == "11"],
    }


def test_outline_check_and_deterministic_repair(env):
    _, _, units, _ = env
    ol = outline_for(units)
    assert check_outline(ol, units) == []

    bad = copy.deepcopy(ol)
    lost = bad["nodes"][1]["unit_ids"].pop(0)
    bad["excluded"].append({"unit_id": lost, "reason": "непоказуемо"})
    bad["nodes"][0]["prerequisites"] = ["permeability"]
    bad["nodes"][1]["terms_introduced"].append("Пористость")
    bad["nodes"][1]["slug"] = "porosity"
    msgs = "\n".join(check_outline(bad, units))
    for part in ("содержательная", "не стоит раньше", "вводится дважды", "повторяется"):
        assert part in msgs
    repair_outline(bad, units)
    assert lost in {u for n in bad["nodes"] for u in n["unit_ids"]}
    assert [n["slug"] for n in bad["nodes"]] == ["porosity", "porosity_2"]
    assert bad["nodes"][0]["prerequisites"] == []
    assert sum(len(n["terms_introduced"]) for n in bad["nodes"]) == 2


# ---------------------------------------------------------------- полный прогон
def card_for(node_units, units, *, wrong_number=False):
    by_id = {u.id: u for u in units}
    main_units = [u for u in node_units if by_id[u].kind != "task"]
    claims = [
        dict(text="Автор описывает свойство породы.", kind="observation", unit_id=u,
             quote=" ".join(by_id[u].text.split()[:6]), source_kind="text")
        for u in main_units if by_id[u].kind == "paragraph" and len(by_id[u].text) > 60
    ]
    num = "Доля меняется от 5 до 40 %." if wrong_number else "Доля зависит от укладки."
    return dict(
        topic="Свойства породы", main="Порода хранит жидкость. Её количество зависит от пустот.",
        mechanism=[dict(text=num, unit_ids=main_units)], steps=[], practice=[dict(text="Так делают сейчас.", unit_ids=[])],
        nuances=[], deeper=[], conditions=[], claims=claims,
        formulas=[dict(unit_id=u, meaning="произведение", latex_fixed="") for u in main_units if by_id[u].kind == "formula"],
        variables=[dict(symbol="k", meaning="коэффициент", unit="")], worked_examples=[],
        glossary=[], misconception=dict(ref="misc.porosity_eq_perm", text="Это не одно и то же."),
        data_meaning=dict(from_chapter="Нужны оба свойства.", breaks="Одного признака мало.", hypotheses=["Может помочь второй признак."]),
        it_analogy=dict(text="Объём диска и скорость чтения.", breaks_where="Порода не меняется по команде."),
        now_practice="", inefficiency=dict(present=False, inputs="", manual_steps="", judgment_points="", time="", typical_errors=""),
        decision=dict(present=True, features=["пористость"], rule="Смотри оба признака.", conclusion="Решай по двум.", counterexample=""),
        to_data="data.core", needs_scan=[],
    )


def fake_model(units, log):
    ol = outline_for(units)

    def transport(model, system, user, schema, effort, max_tokens):
        props = schema["properties"]
        if "excluded" in props:
            log.append("outline")
            return reply(ol)
        if "routes" in props:
            log.append("routing")
            ids = [ln.split(" | ")[0] for ln in user.splitlines() if ln.startswith("demo.")]
            return reply({"routes": [
                dict(node_id=i, format="интерактив", secondary_format="", hook_short=False, template="wellbore-log-sync",
                     video_scores={k: 1 for k in ("time", "invisible", "scale", "counterintuitive", "cause_effect")}, why="И")
                for i in ids]})
        if "tasks" in props:
            log.append("tasks")
            return reply({"tasks": [dict(
                type="single", step_id="demo.porosity", claim_ids=["demo.porosity#c1"], prompt="Что такое пористость?",
                options=[dict(id=x, text=f"вариант {x}", misconception="") for x in "abcd"], answer_list=["a"],
                expression="", tolerance=0, unit="", explanation="Так сказано в шаге.", kind="recall")]})
        if "source_doubts" in props:
            i = 0 if "Что такое пористость породы" in user.split("<node>")[1].split("</node>")[0] else 1
            log.append(f"judge:{ol['nodes'][i]['slug']}")
            first = "Нужны оба свойства." in user
            edit = dict(path="data_from_chapter", severity="error", problem="обобщение", action="replace", text="Нужны оба признака.",
                        quote="", unit_ids=[], block="", kind="", items=[], extra="")
            return reply({"issues": [edit] if first and i == 1 else [], "source_doubts": []})
        idx = 0 if "Что такое пористость породы" in user.split("<node>")[1].split("</node>")[0] else 1
        node = ol["nodes"][idx]
        blank = dict(text="", quote="", unit_ids=[], block="", kind="", items=[], extra="")
        if "edits" in props:
            log.append("patch:" + node["slug"])
            if idx == 0:
                edits = [dict(blank, path="paragraphs[0]", action="replace", text="Доля зависит от укладки.")]
            else:
                edits = [dict(blank, path="glossary[+]", action="add", text="свойство породы", items=[node["terms_introduced"][0], "property"])]
            return reply({"edits": edits})
        log.append("card:" + node["slug"])
        card = card_for(node["unit_ids"], units, wrong_number=idx == 0)
        if idx == 0:
            card["glossary"] = [dict(term=t, definition="свойство породы", en="property") for t in node["terms_introduced"]]
        return reply(to_wire(card))

    return transport


def test_generate_layer_end_to_end(env):
    cfg, ch, units, tmp = env
    log: list[str] = []
    llm = make_llm(cfg, tmp, fake_model(units, log))
    rep = generate_layer(cfg, ch, llm, log=lambda *_: None)
    layer = json.loads((ch.build_dir / "layer.json").read_text(encoding="utf-8"))
    cov = json.loads((ch.build_dir / "coverage.json").read_text(encoding="utf-8"))

    # Чужое число и пропущенный термин нашла проверка; модель вернула короткие правки, а не карточки целиком.
    assert log.count("outline") == 1 and log.count("card:porosity") == 1 and log.count("card:permeability") == 1
    assert log.count("patch:porosity") == 1 and log.count("patch:permeability") == 1
    assert layer["nodes"][0]["layers"]["mechanism"]["text"] == "Доля зависит от укладки."
    assert rep["check_errors"] == [] and rep["open_issues"] == {}
    assert not [i for i in check_layer(cfg, ch, layer, coverage=cov) if i.level == "error"]
    assert not [i for i in check_coverage(cfg, ch, cov, layer) if i.level == "error"]
    assert len(cov["rows"]) == len(units)

    pages = load_pages(ch)
    for n in layer["nodes"]:
        for c in n["claims"]:
            assert strict_norm(c["quote"]) in strict_norm(pages[c["page_pdf"]])
    perm = layer["nodes"][1]
    assert perm["book_tasks"][0]["number"] == "1" and perm["book_tasks"][0]["figure"] == "рис. 5"
    assert {"type": "prerequisite", "target": "demo.porosity"} in perm["links"]
    assert perm["layers"]["practice"]["hypothesis"] is True
    assert perm["formulas"][0]["latex"].startswith("$$k")

    # Судья прошёл каждый узел один раз.
    assert log.count("judge:porosity") == 1 and log.count("judge:permeability") == 1
    assert perm["data_meaning"]["from_chapter"]["text"] == "Нужны оба признака."
    assert layer["nodes"][0]["data_meaning"]["from_chapter"]["text"] == "Нужны оба свойства."
    assert rep["judge"]["unresolved"] == {} and rep["judge"]["log"][0]["applied"] is True

    # Повторный запуск бесплатен: всё берётся из кэша.
    n = len(log)
    again = make_llm(cfg, tmp, fake_model(units, log))
    generate_layer(cfg, ch, again, log=lambda *_: None)
    assert len(log) == n and again.calls == 0

    routing, issues = generate_routing(cfg, ch, llm, layer)
    assert [r["node_id"] for r in routing["routes"]] == [x["id"] for x in layer["nodes"]] and not issues
    doc, issues = generate_tasks(cfg, ch, llm, layer, units, log=lambda *_: None)
    assert doc["tasks"] and any("не меньше 15" in i.msg for i in issues)


def test_judge_rounds_follow_profile(env):
    cfg, ch, units, tmp = env
    cfg.use_profile("max")
    log: list[str] = []
    generate_layer(cfg, ch, make_llm(cfg, tmp, fake_model(units, log)), log=lambda *_: None)
    assert log.count("judge:porosity") == 1 and log.count("judge:permeability") == 2  # второй проход — только исправленный узел


def test_without_money_layer_parts_come_from_rules(env):
    """Слой уже есть, а платить нечем: маршруты и задания строятся по правилам."""
    cfg, ch, units, tmp = env
    generate_layer(cfg, ch, make_llm(cfg, tmp, fake_model(units, [])), log=lambda *_: None)
    layer = json.loads((ch.build_dir / "layer.json").read_text(encoding="utf-8"))
    calls: list[str] = []
    poor = make_llm(cfg, tmp, echo(calls), budget=None)
    routing, issues = generate_routing(cfg, ch, poor, layer, log=lambda *_: None)
    assert [r["node_id"] for r in routing["routes"]] == [n["id"] for n in layer["nodes"]]
    assert not [i for i in issues if i.level == "error"]
    doc, _ = generate_tasks(cfg, ch, poor, layer, units, log=lambda *_: None)
    by_id = {u.id: u for u in units}
    misc = {m["id"] for m in load_misconceptions(cfg)}
    assert all(not task_problems(t, layer, by_id, misc) for t in doc["tasks"])
    rep = json.loads((ch.build_dir / "gen_report.json").read_text(encoding="utf-8"))
    assert rep["routing_by"] == "rules" and rep["tasks_by"] == "rules" and calls == []


def test_api_failure_in_the_middle_keeps_the_work(env):
    """Деньги или лимит кончились после карточек: слой собирается, пропуски видны в отчёте."""
    cfg, ch, units, tmp = env
    log: list[str] = []
    good = fake_model(units, log)

    def transport(model, system, user, schema, effort, max_tokens):
        if "source_doubts" in schema["properties"]:
            raise LLMError("credit balance is too low")
        return good(model, system, user, schema, effort, max_tokens)

    rep = generate_layer(cfg, ch, make_llm(cfg, tmp, transport), log=lambda *_: None)
    assert rep["check_errors"] == [] and rep["nodes"] == 2
    assert len(rep["missed_calls"]) == 2 and "credit balance" in rep["missed_calls"][0]
    assert (ch.build_dir / "layer.json").exists()

    # После пополнения тот же запуск доделывает только пропущенное.
    log.clear()
    rep = generate_layer(cfg, ch, make_llm(cfg, tmp, good), log=lambda *_: None)
    assert rep["missed_calls"] == [] and all(x.startswith("judge:") for x in log)


def test_manual_edit_is_not_overwritten(env):
    _, ch, _, _ = env
    write_generated(ch, "x.json", {"a": 1})
    write_generated(ch, "x.json", {"a": 2})
    (ch.build_dir / "x.json").write_text('{"a": 3}', encoding="utf-8")
    with pytest.raises(ManualEdits):
        write_generated(ch, "x.json", {"a": 4})
    write_generated(ch, "x.json", {"a": 4}, force=True)


def test_term_moves_to_first_use():
    def node(i, text, gloss):
        f = {"text": text}
        return dict(
            id=f"a.n{i}", title="т", why_here={"text": ""}, claims=[], worked_examples=[], steps=[], conditions=[],
            layers={k: f for k in ("topic", "main", "mechanism", "practice", "nuances")} | {"deeper": []},
            decision_on_data=None, misconception=None,
            data_meaning={"from_chapter": {"text": ""}, "breaks": {"text": ""}}, glossary=gloss,
        )

    g = dict(term="зона проникновения", definition="о", en="e")
    nodes = [node(1, "Здесь нет его", []), node(2, "В зоне проникновения иначе", []), node(3, "Зона проникновения", [g])]
    place_terms(nodes)
    assert [n["terms_introduced"] for n in nodes] == [[], ["зона проникновения"], []]
    assert nodes[2]["terms_used"] == ["зона проникновения"]


# ---------------------------------------------------------------- задания
def test_eval_expr_is_arithmetic_only():
    assert eval_expr("2*(3+4)/7") == pytest.approx(2)
    assert eval_expr("sqrt(16)+10^2") == pytest.approx(104)
    for bad in ("__import__('os')", "a+1", "[1]", "2**9999"):
        with pytest.raises((ValueError, SyntaxError)):
            eval_expr(bad)


def test_task_problems_catch_bad_tasks():
    u = Unit(id="p1.01", kind="paragraph", page_pdf=1, text="Пористость 20 % у песчаника", pages=[1])
    f = {"text": "Пористость 20 % у песчаника"}
    node = dict(
        id="a.n", title="т", unit_ids=["p1.01"], why_here={"text": ""}, claims=[{"id": "a.n#c1", "text": "т"}],
        worked_examples=[], steps=[], conditions=[], decision_on_data=None, misconception=None,
        layers={k: f for k in ("topic", "main", "mechanism", "practice", "nuances")} | {"deeper": []},
        data_meaning={"from_chapter": {"text": ""}, "breaks": {"text": ""}},
    )
    layer, units = {"nodes": [node]}, {"p1.01": u}

    def t(**kw):
        base = dict(id="t", step_id="a.n", claim_ids=["a.n#c1"], explanation="Так в шаге.", prompt="Вопрос?")
        return task_problems({**base, **kw}, layer, units, set())

    assert t(type="gap", prompt="{{1}} у песчаника", answer=["Пористость"]) == []
    assert any("нет в тексте" in p for p in t(type="gap", prompt="{{1}} у песчаника", answer=["Вязкость"]))
    assert t(type="calc", prompt="Сколько?", answer=4.0, expression="20/5", tolerance=0.1) == []
    assert any("выражение даёт" in p for p in t(type="calc", prompt="Сколько?", answer=5.0, expression="20/5", tolerance=0.1))
    opts = [dict(id=x, text=x * 3, misconception=None) for x in "abc"]
    assert any("уже стоят" in p for p in t(type="order", options=opts, answer=["a", "b", "c"]))
    assert any("числа не из шага" in p for p in t(type="single", options=opts, answer="a", prompt="При 35 % что верно?"))
    assert any("не из этого шага" in p for p in t(type="single", options=opts, answer="a", claim_ids=["a.x#c1"]))


# ---------------------------------------------------------------- запись чисел
@pytest.mark.parametrize(
    "text, expected",
    [
        ("10 000 Па", {"10000"}),
        ("10\\,000 м", {"10000"}),
        ("1,500 psi at 0.25", {"1500", "0.25"}),
        ("давление 1,500 МПа", {"1.5"}),
        ("0,125", {"0.125"}),
        ("7·10⁻¹⁰", {"7", "10", "-10"}),
        ("$7 \\cdot 10^{-10}$", {"7", "10", "-10"}),
        ("2 000 и 35 000", {"2000", "35000"}),
    ],
)
def test_number_notation(text, expected):
    from kurs.verify import numbers

    assert numbers(text) == expected


@pytest.mark.parametrize(
    "text, cond",
    [("глубине менее 5 %", "upper"), ("на глубине более 5 %", "lower"), ("не больше 5 %", "upper"), ("не превышает 5 %", "upper"),
     ("не менее 5 %", "lower"), ("не ниже 5 %", "lower"), ("no more than 5 %", "upper"), ("k \\leq 5", "upper")],
)
def test_negated_conditions(text, cond):
    from kurs.verify import extract_quantities

    assert extract_quantities(text)[0].cond == cond


def test_power_of_ten_after_cdot_is_a_number():
    from kurs.verify import check_quantities_against_source

    assert check_quantities_against_source("от 7·10⁻¹⁰ до 30·10⁻¹⁰", ["от $7\\cdot10^{-10}$ до $30\\cdot10^{-10}$"]) == []


def test_group_mismatch_is_reported():
    from kurs.verify import check_quantities_against_source as chk

    assert chk("давление 10 000 Па", ["давление 10 000 Па"]) == []
    assert chk("давление 1000 Па", ["давление 10 000 Па"])
    assert chk("1500 psi", ["1,500 psi"]) == []
    assert chk("порядка 10⁻⁵", ["порядка $10^{-5}$"]) == []
    assert chk("порядка 10⁻⁶", ["порядка $10^{-5}$"])


# ---------------------------------------------------------------- судья
def edit(path, action="replace", **kw):
    return dict(path=path, severity="error", problem="п", action=action, text="", quote="", unit_ids=[], block="", kind="",
                items=[], extra="") | kw


def test_judge_edits_change_only_their_place(env):
    _, _, units, _ = env
    by_id = {u.id: u for u in units}
    para = next(u for u in units if u.kind == "paragraph" and len(u.text) > 60)
    card = card_for([para.id], units)
    card["claims"] = [dict(text=f"т{i}", kind="observation", unit_id=para.id, quote="q", source_kind="text") for i in range(3)]
    wire = to_wire(card)
    new, done, skipped = apply_edits(wire, [
        edit("claims[1]", text="новое", quote="другая цитата"),
        edit("claims[0]", "delete"),
        edit("claims[+]", "add", text="ещё", quote="цитата", unit_ids=[para.id], kind="inference"),
        edit("paragraphs[+]", "add", text="оговорка", unit_ids=[para.id], block="nuances"),
        edit("paragraphs[+]", "add", text="чужое", unit_ids=["p999.99"], block="nuances"),
        edit("main", text="Суть одна. Суть вторая."),
        edit("main", "delete"),
        edit("inefficiency[9]", text="нет такого"),
        edit("claims[77]", text="нет такого"),
        edit("непонятно", text="x"),
    ], {para.id}, by_id)
    assert [c["text"] for c in new["claims"]] == ["новое", "т2", "ещё"]
    assert new["claims"][0]["quote"] == "другая цитата" and new["claims"][0]["kind"] == "observation"
    assert new["claims"][2]["kind"] == "inference" and new["claims"][2]["source_kind"] == "text"
    assert new["paragraphs"][-1] == {"block": "nuances", "text": "оговорка", "unit_ids": [para.id]}
    assert new["main"] == "Суть одна. Суть вторая."
    assert len(done) == 5 and len(skipped) == 5
    assert wire["claims"][0]["text"] == "т0"  # исходная карточка не тронута


def test_weak_quotes_are_found(env):
    _, _, units, _ = env
    para = next(u for u in units if u.kind == "paragraph" and len(u.text) > 60)
    form = next(u for u in units if u.kind == "formula")
    words = para.text.split()
    claims = [
        dict(text="обрывок", unit_id=para.id, quote=" ".join(words[:2])),
        dict(text="полная", unit_id=para.id, quote=" ".join(words[:7])),
        dict(text="формула", unit_id=form.id, quote=form.text[:6]),
    ]
    weak = weak_quotes({"claims": claims}, {u.id: u for u in units})
    assert len(weak) == 1 and weak[0].startswith("claims[0]")


def test_pdf_reference_only_where_pages_match(env, monkeypatch):
    cfg, ch, units, tmp = env
    pages = load_pages(ch)
    good = max(pages, key=lambda p: len(pages[p]))
    (tmp / "book.pdf").write_bytes(b"%PDF")
    monkeypatch.setattr(pdfref, "candidates", lambda *_: [tmp / "book.pdf"])
    monkeypatch.setattr(pdfref.shutil, "which", lambda _: "/usr/bin/pdftotext")
    monkeypatch.setattr(pdfref, "page_text", lambda pdf, p: pages[p] if p == good else "совсем другая страница другой книги")
    monkeypatch.setattr(pdfref, "MIN_OVERLAP", 0.3)
    got = pdfref.load_pdf_pages(cfg, ch, pages)
    assert set(got) <= {good}
    assert "Текста PDF нет" in pdfref.reference_block({}, [good])
    assert f'page="{good}"' in pdfref.reference_block({good: "kв < 1"}, [good, good + 1])


def test_options_are_shuffled_and_relabelled():
    def t(type_, answer, n, prompt):
        return dict(type=type_, prompt=prompt, answer=answer, options=[dict(id=x, text=f"вариант {x}") for x in "abcde"[:n]])

    spots = set()
    for k in range(40):
        one = shuffle_options(t("single", "a", 4, f"вопрос {k}"))
        assert [o["id"] for o in one["options"]] == list("abcd")
        assert next(o["text"] for o in one["options"] if o["id"] == one["answer"]) == "вариант a"
        spots.add(one["answer"])
        seq = shuffle_options(t("order", list("abcd"), 4, f"порядок {k}"))
        text = {o["id"]: o["text"] for o in seq["options"]}
        assert [text[i] for i in seq["answer"]] == [f"вариант {x}" for x in "abcd"]
        assert seq["answer"] != [o["id"] for o in seq["options"]]
        many = shuffle_options(t("multiple", ["a", "b"], 5, f"много {k}"))
        assert {o["text"] for o in many["options"] if o["id"] in many["answer"]} == {"вариант a", "вариант b"}
    assert spots == set("abcd")
    assert shuffle_options(t("single", "a", 4, "q")) == shuffle_options(t("single", "a", 4, "q"))


# ---------------------------------------------------------------- починка кодом
def test_repair_fixes_what_needs_no_model(env):
    from kurs.repair import repair_card

    _, ch, units, _ = env
    pages, by_id = load_pages(ch), {u.id: u for u in units}
    p1 = next(u for u in units if u.text.startswith("Пористость показывает"))
    p2 = next(u for u in units if u.text.startswith("Проницаемость"))
    node = dict(unit_ids=[p1.id, p2.id], terms_introduced=["пористость"], decision_on_data=False)
    card = card_for([p1.id, p2.id], units)
    card["claims"] = [
        dict(text="Доля меняется до 25 %.", kind="inference", unit_id=p1.id, quote="Эта доля меняется", source_kind="text"),
        dict(text="Высокая пористость не даёт проницаемости.", kind="observation", unit_id=p1.id,
             quote="Высокая пористость не гарантирует высокую проницаемость", source_kind="text"),
        dict(text="Доля растёт до 90 %.", kind="observation", unit_id=p1.id, quote="Эта доля меняется от 5 до 25 %", source_kind="text"),
        dict(text="Совсем посторонняя мысль о погоде.", kind="observation", unit_id=p1.id, quote="завтра будет дождь и ветер", source_kind="text"),
    ]
    card["mechanism"] = [dict(text="Доля зависит от укладки зёрен. Она бывает 77 %.", unit_ids=[p1.id])]
    card["main"] = "Одна. Две. Три. Четыре. Пять."
    card["glossary"] = [dict(term="Пористость", definition="Доля пустот (обычно 20 %) в породе.", en="porosity")]
    log = repair_card(card, node, by_id, pages, {})
    by = {c["text"]: c for c in card["claims"]}
    assert "от 5 до 25 %" in by["Доля меняется до 25 %."]["quote"]  # обрывок расширен до предложения
    assert by["Высокая пористость не даёт проницаемости."]["unit_id"] == p2.id  # единица найдена по цитате
    assert "Доля растёт до 90 %." not in by and "Совсем посторонняя мысль о погоде." not in by
    assert all(snap_quote(c["quote"], by_id[c["unit_id"]], pages) for c in card["claims"])
    assert card["mechanism"][0]["text"] == "Доля зависит от укладки зёрен."
    assert card["main"] == "Одна. Две. Три." and card["glossary"][0]["definition"] == "Доля пустот в породе."
    assert log
    before = json.dumps(card, sort_keys=True)
    repair_card(card, node, by_id, pages, {})
    assert json.dumps(card, sort_keys=True) == before  # повторный вызов ничего не меняет


def test_repair_final_covers_units_with_author_words(env):
    from kurs.quotes import referenced_units
    from kurs.repair import repair_card

    _, ch, units, _ = env
    pages, by_id = load_pages(ch), {u.id: u for u in units}
    ids = [u.id for u in units if (u.kind == "paragraph" and len(u.text) > 60) or u.kind == "formula"]
    node = dict(unit_ids=ids, terms_introduced=[], decision_on_data=False)
    card = card_for(ids[:1], units)
    repair_card(card, node, by_id, pages, {}, final=True)
    assert set(ids) <= referenced_units(card)
    for c in card["claims"]:
        assert strict_norm(c["quote"]) in strict_norm(by_id[c["unit_id"]].text)


def test_long_phrase_is_split_by_conjunction():
    from kurs.repair import sentence_spans, split_long

    long = "Порода хорошо пропускает жидкость при любом обычном перепаде давления в пласте, поэтому скважина даёт заметный приток уже в первые часы испытания."
    out = split_long(long)
    assert ". Поэтому скважина" in out and not long_sentences(out)
    same = "Короткая фраза, но с союзом."
    assert split_long(same) == same
    text = "См. рис. 5 и табл. 2. Далее В. Н. Иванов пишет. Конец"
    assert [text[a:b] for a, b in sentence_spans(text)] == ["См. рис. 5 и табл. 2.", "Далее В. Н. Иванов пишет.", "Конец"]


def test_quote_cut_by_page_hyphen_loses_broken_word(env):
    u = Unit(id="p1.01", kind="paragraph", page_pdf=1, text="Состав связан главным образом с различ-", pages=[1])
    q, p = snap_quote("связан главным образом с различ-", u, {1: "Состав связан главным образом с различ-\n1"})
    assert q == "связан главным образом с" and p == 1
