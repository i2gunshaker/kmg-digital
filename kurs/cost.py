"""Прогноз цены главы до единого платного вызова.

Вход считается по длине текста главы, ответы — по средним размерам из журнала прошлых запусков.
"""
from __future__ import annotations

from .config import Chapter, Config
from .layer_gen import content_units, load_errata, system_blocks
from .llm import CHARS_PER_TOKEN, LLM, Usage
from .units import build_units, cut_chapter

# Средние размеры по журналу: вход без общей части и ответ, токены.
CARD = (7000, 3300)
JUDGE = (8300, 1900)
PATCH = (7500, 900)
OUTLINE_OUT = 16000
ROUTING = (4400, 6600)
TASKS = (36500, 17000)
VISUAL_STEP = (1900, 900)  # карточка шага на входе и описание рисунка в ответе
VISUAL_RULES = 3800  # правила и каталог шаблонов
VISUAL_REDO = 0.5  # доля шагов, которые уходят на исправление
PATCH_SHARE = 0.35  # доля узлов, где после кода осталось что править моделью
NODE_CHARS = 2600


def node_count(units: list) -> int:
    return max(6, sum(len(u.text) for u in content_units(units) if u.kind != "table") // NODE_CHARS)


def estimate(cfg: Config, ch: Chapter, llm: LLM) -> dict[str, float]:
    """Цена этапов главы, $."""
    cut_chapter(cfg, ch)
    units = build_units(cfg, ch)
    system = system_blocks(cfg, ch, units, load_errata(cfg, ch))
    whole = int(sum(map(len, system)) / CHARS_PER_TOKEN)
    base = int(sum(map(len, system[:2])) / CHARS_PER_TOKEN)
    n = node_count(units)
    senior, fast = llm.model_for("senior"), llm.model_for("fast")
    judge = str(cfg.llm.get("judge", "senior"))
    rounds = int(cfg.llm.get("judge_rounds", 1)) if judge != "off" else 0

    def many(model: str, calls: int, size: tuple[int, int], prefix: int) -> float:
        if calls <= 0:
            return 0.0
        first = llm.cost(model, Usage(input=size[0], cache_write=prefix, output=size[1]))
        return first + (calls - 1) * llm.cost(model, Usage(input=size[0], cache_read=prefix, output=size[1]))

    out = {
        "граф узлов": llm.cost(senior, Usage(input=1000, cache_write=whole, output=OUTLINE_OUT)),
        "карточки": many(fast, n, CARD, whole),
        "правки": many(fast, max(1, round(n * PATCH_SHARE)), PATCH, base),
    }
    if rounds:
        m = llm.model_for(judge)
        out["судья"] = many(m, n, JUDGE, base) + (many(m, max(1, n - 4), JUDGE, base) if rounds > 1 else 0)
        out["правки после судьи"] = many(fast, max(1, round(n * PATCH_SHARE)), PATCH, base)
    out["маршруты"] = llm.cost(senior, Usage(input=ROUTING[0], output=ROUTING[1]))
    out["задания"] = llm.cost(senior, Usage(input=TASKS[0], cache_write=base, output=TASKS[1])) + llm.cost(
        fast, Usage(input=8000, output=3000)
    )
    draw = llm.model_for(str(cfg.llm.get("visual", "fast")))
    packs = -(-n // 4)
    size = (VISUAL_STEP[0] * 4, VISUAL_STEP[1] * 4)
    visual = many(draw, packs, size, VISUAL_RULES)
    visual += many(draw, max(1, round(packs * VISUAL_REDO)), (size[0] + size[1], size[1]), VISUAL_RULES)
    check = str(cfg.llm.get("visual_judge", "senior"))
    if check != "off":
        visual += many(llm.model_for(check), -(-n // 8), (8 * sum(VISUAL_STEP) + 1500, 1500), 0)
    out["рисунки"] = visual
    return out


def estimate_text(cfg: Config, ch: Chapter, llm: LLM) -> tuple[str, float]:
    parts = estimate(cfg, ch, llm)
    total = sum(parts.values())
    line = ", ".join(f"{k} ${v:.3f}" for k, v in parts.items())
    return f"{ch.id}: около ${total:.2f} ({line})", total
