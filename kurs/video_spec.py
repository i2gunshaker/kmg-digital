"""Генерация и проверка спецификаций сцен, паспортов и брифа ментору для двух обучающих роликов."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import jsonschema

from .config import BUILD, ROOT, Chapter, Config
from .datasets import load_volve_slice
from .verify import Source, check_quantities_against_source

_DIFF_TO_TEMPLATE = {
    "М": "process-section",
    "В": "time-evolution",
    "И": "wellbore-log-sync",
    "Ф": "field-diffusion-2d",
    "П": "geometry-section",
    "Пр": "procedure-flow",
    "Н": "scenario-range",
    "Р": "feature-recognition",
}
SUPPORTED_TEMPLATES = set(_DIFF_TO_TEMPLATE.values())


def _exp1(x: float) -> float:
    """Точная аппроксимация E1(x) = -Ei(-x) для x > 0 (Абрамовиц и Стиган, точность < 2e-7)."""
    if x <= 0:
        return 10.0
    if x <= 1.0:
        return (
            -math.log(x)
            - 0.57721566
            + 0.99999193 * x
            - 0.24991055 * x**2
            + 0.05519968 * x**3
            - 0.00976004 * x**4
            + 0.00107857 * x**5
        )
    num = x**4 + 8.5733287401 * x**3 + 18.0590169730 * x**2 + 8.6347608925 * x + 0.2677737343
    den = x**4 + 9.5733223454 * x**3 + 25.6329561486 * x**2 + 21.0996530827 * x + 3.9584969228
    return (math.exp(-x) / x) * (num / den)


def _latyshova_state_sample(cfg: Config | None = None) -> dict:
    """Единая модель состояния для шортса по Латышовой: разрез и синхронные кривые (глубина растёт вниз)."""
    depths = [round(2190.0 + i * 2.0, 1) for i in range(21)]  # 2190..2230 м
    rows = []
    for z in depths:
        if z < 2202.0:
            lith, fluid, gr, res, kp = "shale", "water", 82.0, 2.5, 8.0
        elif z < 2214.0:
            lith, fluid, gr, res, kp = "dense", "none", 16.0, 28.0, 4.0
        else:
            lith, fluid, gr, res, kp = "sand", "oil", 22.0, 24.0, 24.0
        rows.append({"depth_m": z, "lithology": lith, "fluid": fluid, "GR": gr, "RES": res, "kp_pct": kp})
    volve = load_volve_slice(cfg.domain_dir if cfg else ROOT / "domain/geology")
    volve_summary = []
    if volve:
        for iv in volve.get("intervals", []):
            volve_summary.append({
                "id": iv["id"],
                "label": iv["label"],
                "summary": iv["summary"],
                "points": iv["points"],
                "depth_range_m": [iv["d_min"], iv["d_max"]],
            })
    return {
        "depth_direction": "down",
        "school": "русская школа ГИС (ГК, ПС, КС, kп)",
        "profile_rows": rows,
        "crossplot_cutoff_kp_pct": 17.0,
        "examples": [
            {"name": "Пример 46", "rho_p": 20.0, "kp_pct": 25.0, "verdict": "безводная нефть"},
            {"name": "Пример 47а", "rho_p": 10.0, "kp_pct": 15.0, "verdict": "неколлектор"},
            {"name": "Пример 47б", "rho_p": 10.0, "kp_pct": 20.0, "verdict": "коллектор нефтеносный"},
            {"name": "Пример 47в", "rho_p": 10.0, "kp_pct": 25.0, "verdict": "нефть с водой"},
        ],
        "volve_intervals": volve_summary,
    }


def _basniev_state_sample() -> dict:
    """Единая модель состояния для разбора 16:9 по Басниеву: p(r, t) по -Ei и R(t) по методу ПССС."""
    kappa = 1.0  # м²/с (пример со стр. 492)
    r_c = 0.1    # м (стр. 492)
    p_k = 25.0   # МПа
    coef = 1.2   # Q0 * mu / (4 * pi * k * h), МПа
    radii = [0.5 * 300 ** (i / 120) for i in range(121)]
    times_s = [1.0, 60.0, 600.0, 3600.0]
    profiles = []
    for t in times_s:
        r_front = round(math.sqrt(r_c**2 + 4.0 * kappa * t), 2)
        pts = []
        for r in radii:
            x = (r**2) / (4.0 * kappa * t)
            p = round(p_k - coef * _exp1(x), 5)
            pts.append({"r_m": r, "p_MPa": p})
        profiles.append({"t_sec": t, "R_psss_m": r_front, "curve": pts})
    task_kappa = (100**2 - r_c**2) / (4 * 3600)
    return {
        "radius_task": {
            "kappa_m2_s": task_kappa,
            "curve": [{"hours": i / 60, "radius_m": math.sqrt(r_c**2 + 4 * task_kappa * i * 60)}
                      for i in range(241)],
        },
        "kappa_m2_s": kappa,
        "r_c_m": r_c,
        "p_k_MPa": p_k,
        "profiles": profiles,
    }


def _build_checklist(
    answers_override: dict[str, tuple[str, str]],
    pc_path: Path | None = None,
    *,
    domain_dir: Path | None = None,
) -> list[dict]:
    if pc_path is None:
        pc_path = (domain_dir / "physics_checklist.json") if domain_dir else (ROOT / "domain/geology/physics_checklist.json")
    elif pc_path.is_dir():
        pc_path = pc_path / "physics_checklist.json"
    if not pc_path.exists():
        pc_path = ROOT / "domain/geology/physics_checklist.json"
    items = json.loads(pc_path.read_text(encoding="utf-8"))
    out = []
    for it in items:
        ans, note = answers_override.get(it["id"], ("н/п", "В данном ролике процесс не показан."))
        out.append({"item": f"{it['id']}: {it['item']}", "answer": ans, "note": note})
    return out


def build_training_specs(cfg: Config, out_dir: Path | None = None) -> dict:
    """Создаёт и валидирует спецификации сцен, паспорта двух роликов и MENTOR_BRIEF.md."""
    out_dir = out_dir or (BUILD / "specs")
    out_dir.mkdir(parents=True, exist_ok=True)
    passport_schema = json.loads((cfg.schemas_dir / "passport.schema.json").read_text(encoding="utf-8"))

    lat_ch = cfg.chapter("latyshova_gl5_par17-20")
    bas_ch = cfg.chapter("basniev_gl23-24")
    lat_src = Source(lat_ch)
    bas_src = Source(bas_ch)

    # ---------------- РОЛИК 1: ШОРТС 9:16 (ЛАТЫШОВА) ----------------
    lat_node_id = "latyshova.rho_kp_palette"
    lat_sources = [
        {
            "claim_id": f"{lat_node_id}#c1",
            "page_or_dataset": "стр. 94 (табл. 21)",
            "quote": "Ангидриты, чистые плотные кристаллические известняки | максимальное | Отсутствие проникновения",
        },
        {
            "claim_id": f"{lat_node_id}#c2",
            "page_or_dataset": "стр. 95",
            "quote": "построение разреза и особенно выделение коллектора можно осуществить лишь на основании количественной интерпретации геофизических данных",
        },
        {
            "claim_id": f"{lat_node_id}#c3",
            "page_or_dataset": "стр. 97",
            "quote": "Поле зависимостей ρн = f (kн) разделяется здесь на области неколлекторов (А) и коллекторов (Б) при kн* = 17%.",
        },
        {
            "claim_id": f"{lat_node_id}#c4",
            "page_or_dataset": "стр. 98",
            "quote": "Пример 46. Определить характер насыщения коллектора в разрезе нефтяного месторождения, для которого ρn = 20 Ом·м, kn = 25 %. Построив точку с указанными координатами, определяем, что коллектор даст при испытании безводную нефть.",
        },
        {
            "claim_id": f"{lat_node_id}#c5",
            "page_or_dataset": "стр. 98",
            "quote": "Пример 47. Установить, чем представлена порода, имеющая ρn = 10 Ом·м, при значениях kn: а) 15%; б) 20%; в) 25 %. Ответ: а) неколлектор; б) коллектор нефтеносный; в) коллектор, который при испытании даст нефть с водой.",
        },
        {
            "claim_id": f"{lat_node_id}#c6",
            "page_or_dataset": "data.volve_15_9_f1b (стр. Volve 3305–3310 м)",
            "quote": "Equinor Volve 15/9-F-1 B: интервал A (PHIF=0.228, RT=37.27 Ом·м, SW=0.131) vs интервал B (PHIF=0.020, RT=7.43 Ом·м, SAND_FLAG=0)",
        },
    ]

    lat_scene_path = out_dir / "latyshova_shorts_scene.json"
    lat_scene = {
        "video_id": "latyshova_shorts_high_rt",
        "format": "9:16",
        "resolution": [1080, 1920],
        "fps": 30,
        "duration_sec": 66.0,
        "template_id": "wellbore-log-sync",
        "objects": [
            {"id": "wellbore", "kind": "ствол скважины", "depth_direction": "down"},
            {"id": "logging_tool", "kind": "зонд ГИС", "sync": "depth_m"},
            {"id": "track_porosity", "kind": "пористость, %", "sync": "depth_m"},
            {"id": "track_res", "kind": "кривая КС", "color": "#F1C40F"},
            {"id": "crossplot_p69", "kind": "перерисовка рис. 69, стр. 98"},
        ],
        "parameters": {
            "depth_range_m": [2190, 2230],
            "kp_cutoff_pct": 17.0,
            "vertical_exaggeration_default": 25,
            "fixed_timeline": True,
            "true_scale_window_sec": [28.0, 32.0],
            "prediction_pause_sec": [41.0, 42.5],
        },
        "invariants": [
            "Глубина на разрезе и планшете растёт строго вниз.",
            "Положение прибора и текущая точка кривых вычислены из одной глубины z(t).",
            "Кривая — косвенный отклик, а не сама порода.",
            "На каждом кадре видна метка происхождения. Масштабные линейки относятся к разрезу.",
        ],
        "assumptions": [
            "Слои разреза показаны условно горизонтальными для демонстрации двух причин пика сопротивления.",
            "Модельные пористости 4% и 24%. Масштаб по вертикали уменьшается с ×25 до 1:1 за 26–28 с.",
        ],
        "state_sample": _latyshova_state_sample(cfg),
    }
    lat_scene_path.write_text(json.dumps(lat_scene, ensure_ascii=False, indent=2), encoding="utf-8")

    lat_passport = {
        "video_id": "latyshova_shorts_high_rt",
        "node_id": lat_node_id,
        "node_card": {
            "chapter": "latyshova_gl5_par17-20",
            "title": "Почему высокое сопротивление — ещё не нефть",
            "difficulty": {"primary": "И", "secondary": "Н"},
            "format": "шортс",
            "duration_sec": 66.0,
            "refutes_misconceptions": ["misc.high_res_oil", "misc.log_is_rock"],
        },
        "script": [
            {"t_start": 0.0, "t_end": 8.0, "text": "Как найти нефть внутри камня? Солёная вода в мелких пустотах проводит ток. Нефть проводит его очень плохо.", "on_screen": "Где пройдёт ток?", "claim_id": f"{lat_node_id}#c1", "is_analogy": False},
            {"t_start": 8.0, "t_end": 16.0, "text": "А вот плотный известняк. В нём воде почти негде пройти. Току снова трудно. Прибор показывает высокое сопротивление в обоих слоях.", "on_screen": "Две причины высокого сопротивления", "claim_id": f"{lat_node_id}#c1", "is_analogy": False},
            {"t_start": 16.0, "t_end": 26.0, "text": "Посмотрим, сколько места занимают пустоты. Их доля называется пористостью. Если камень может хранить и отдавать жидкость, его называют коллектором.", "on_screen": "Сколько места внутри?", "claim_id": f"{lat_node_id}#c2", "is_analogy": False},
            {"t_start": 26.0, "t_end": 32.0, "text": "Уберём увеличение по вертикали. Вот те же слои в одном масштабе по обеим осям.", "on_screen": "Масштаб один к одному", "claim_id": f"{lat_node_id}#c2", "is_analogy": False},
            {"t_start": 32.0, "t_end": 41.0, "text": "Теперь пример из книги. На графике при пятнадцати процентах пор камень не даёт притока. А что получим при двадцати?", "on_screen": "Что даст этот слой?", "claim_id": f"{lat_node_id}#c5", "is_analogy": False},
            {"t_start": 42.5, "t_end": 49.0, "text": "Здесь получим нефть. При двадцати пяти процентах пойдёт нефть с водой. Сопротивление во всех трёх случаях одинаковое.", "on_screen": "Нефть с водой", "claim_id": f"{lat_node_id}#c5", "is_analogy": False},
            {"t_start": 49.0, "t_end": 57.0, "text": "Модель выучила на песчанике одно правило: высокий сигнал означает нефть. Попался известняк. По такому прогнозу можно пробурить сухую скважину.", "on_screen": "Сменилась порода", "claim_id": f"{lat_node_id}#c2", "is_analogy": True},
            {"t_start": 57.0, "t_end": 66.0, "text": "Теперь реальная скважина в Северном море. В каком слое вы заподозрите ловушку? Посмотрите, где для жидкости меньше места.", "on_screen": "Где ловушка?", "claim_id": f"{lat_node_id}#c6", "is_analogy": False},
        ],
        "sources": lat_sources,
        "scene_spec": str(lat_scene_path.relative_to(ROOT)) if lat_scene_path.is_relative_to(ROOT) else str(lat_scene_path),
        "frames": [
            {"t": 4, "class": "B", "label": "схема"},
            {"t": 12, "class": "B", "label": "симуляция"},
            {"t": 21, "class": "B", "label": "симуляция"},
            {"t": 30, "class": "B", "label": "симуляция"},
            {"t": 40, "class": "A", "label": "схема"},
            {"t": 47, "class": "A", "label": "схема"},
            {"t": 55, "class": "B", "label": "схема"},
            {"t": 65.8, "class": "A", "label": "реальные данные"},
        ],
        "exaggerations": [
            "вертикальный масштаб разреза ×25 переходит к 1:1 за 2 с, затем 4 с выдержки",
            "зонд и увеличенные окна пор показаны условно",
        ],
        "prediction_question": "Что даст слой при пористости 20% и сопротивлении пласта 10 Ом·м в примере 47?",
        "transfer_question": "В каком интервале Volve меньше места для жидкости?",
        "data_it_bridge": "Правило по одному сопротивлению ошибается при смене породы. На Volve сравниваются медианы RT и PHIF двух интервалов, насыщение A дополнительно характеризует SW.",
        "physics_checklist": _build_checklist({
            "pc.layers_order": ("да", "Последовательность слоёв сохраняется во всех кадрах."),
            "pc.fluids_in_pores": ("да", "Нефть показана в порах песчаника, в плотном известняке модельная пористость 4%."),
            "pc.causality": ("да", "Сначала свойства породы и флюида, затем отклик зонда."),
            "pc.tool_mechanism": ("да", "Зонд измеряет кажущееся сопротивление."),
            "pc.signal_vs_property": ("да", "Отклик КС отделён от интерпретации пористости и насыщения."),
            "pc.applicability": ("да", "Показано, когда простое правило по сопротивлению ломается."),
            "pc.curve_sync": ("да", "Кривые считаются из одной координаты глубины z(t)."),
            "pc.plausible_numbers": ("да", "Порог 17% и точки 15%, 20%, 25% при 10 Ом·м локальны для рис. 69. Медианы Volve приведены отдельно."),
            "pc.exaggeration_named": ("да", "Линейки по двум осям имеют общий масштаб на 28–32 с."),
            "pc.objects_stable": ("да", "Зонд и слои не меняют геометрию между кадрами."),
            "pc.depth_down": ("да", "Глубина растёт сверху вниз, шкалы подписаны."),
            "pc.label_matches": ("да", "Метки «симуляция», «схема» и «реальные данные» соответствуют источникам кадров."),
            "pc.modern_or_historic": ("да", "Книжный пример и современные измерения Volve подписаны отдельно."),
        }),
        "audio_check": {
            "target_lufs": -16.0,
            "max_wer": 0.10,
            "max_sync_drift_sec": 0.5,
            "prediction_pause_sec": 1.5,
            "status": "pending_render",
            "stress_review_words": ["сопротивле́ние", "известня́к", "ка́жущегося", "пале́тку", "неколле́ктор", "по́ристости"],
        },
    }

    # ---------------- РОЛИК 2: РАЗБОР 16:9 (БАСНИЕВ) ----------------
    bas_node_id = "basniev.elastic_regime_mechanism"
    bas_sources = [
        {
            "claim_id": f"{bas_node_id}#c1",
            "page_or_dataset": "стр. 474",
            "quote": "основной формой пластовой энергии, обеспечивающей приток жидкости к скважинам в рассматриваемых неустановившихся процессах, является энергия упругой деформации жидкостей (нефти и/или воды) и твердого скелета пласта",
        },
        {
            "claim_id": f"{bas_node_id}#c2",
            "page_or_dataset": "стр. 476",
            "quote": "Коэффициент $\\beta^*$ называется коэффициентом упругоемкости пласта. Из формулы (23.4) следует, что коэффициент упругоемкости пласта $\\beta^*$ численно равен изменению упругого запаса жидкости в единице объема пласта при изменении пластового давления в нем на единицу",
        },
        {
            "claim_id": f"{bas_node_id}#c3",
            "page_or_dataset": "стр. 478",
            "quote": "Так, например, на крупнейшем месторождении Тенгиз при упругом режиме будет отобрано до 20% запасов нефти.",
        },
        {
            "claim_id": f"{bas_node_id}#c4",
            "page_or_dataset": "стр. 480",
            "quote": "для нефтей $\\beta_\\text{ж}$ изменяется в диапазоне от $7\\cdot10^{-10}$ Па$^{-1}$ до $30\\cdot10^{-10}$ Па$^{-1}$",
        },
        {
            "claim_id": f"{bas_node_id}#c5",
            "page_or_dataset": "стр. 481",
            "quote": "Наиболее часто встречающиеся в нефтепромысловой практике значения коэффициента пьезопроводности заключены в пределах от 0,1 до 5 м²/с.",
        },
        {
            "claim_id": f"{bas_node_id}#c6",
            "page_or_dataset": "стр. 492",
            "quote": "Следовательно, уже через 1 с после пуска скважины расчеты забойного давления, выполненные по формуле (23.52), будут иметь погрешность, не превышающую 0,6%.",
        },
        {
            "claim_id": f"{bas_node_id}#c7",
            "page_or_dataset": "стр. 494",
            "quote": "В каждый момент времени весь пласт условно разделяется на две области – возмущенную и невозмущенную.",
        },
        {
            "claim_id": f"{bas_node_id}#c8",
            "page_or_dataset": "стр. 498",
            "quote": "$$R(t) = \\sqrt{r_c^2 + 4\\kappa t}. \\quad (24.14)$$",
        },
    ]

    bas_scene_path = out_dir / "basniev_deepdive_scene.json"
    bas_scene = {
        "video_id": "basniev_deepdive_elastic_funnel",
        "format": "16:9",
        "resolution": [1920, 1080],
        "fps": 30,
        "duration_sec": 100.5,
        "template_id": "field-diffusion-2d",
        "objects": [
            {"id": "reservoir_slab", "kind": "упругий пористый пласт с покрышкой", "depth_direction": "down"},
            {"id": "micro_pores_inset", "kind": "врезка порового пространства (сжатие скелета + расширение нефти)"},
            {"id": "pressure_plot", "kind": "график p(r, t) по формуле (23.52)"},
            {"id": "radius_plot", "kind": "фронт возмущения R(t) по методу ПССС (24.14)"},
        ],
        "parameters": {
            "kappa_m2_s": 1.0,
            "r_c_m": 0.1,
            "p_k_MPa": 25.0,
            "fixed_timeline": True,
            "true_scale_window_sec": [29.65, 37.0],
            "prediction_pause_sec": [67.0, 68.5],
        },
        "invariants": [
            "Профиль и полоса давления используют одну строку состояния при κ = 1 м²/с. Радиус задачи рассчитан отдельно.",
            "Флюид течёт от высокого пластового давления к низкому забойному.",
            "R(t) строго монотонно возрастает пропорционально корню из времени, а не линейно со скоростью звука.",
            "Соблюдается баланс массы: отобранный объём равен изменению упругого запаса в возмущённой зоне.",
        ],
        "assumptions": [
            "Однофазная слабосжимаемая жидкость (давление выше давления насыщения), k = const, μ = const.",
            "Поры увеличены, деформация усилена. В модели задано 25 МПа. Разрез 1000 × 20 м снимает вертикальное увеличение ×20 за 1,4 с.",
        ],
        "state_sample": _basniev_state_sample(),
    }
    bas_scene_path.write_text(json.dumps(bas_scene, ensure_ascii=False, indent=2), encoding="utf-8")

    bas_passport = {
        "video_id": "basniev_deepdive_elastic_funnel",
        "node_id": bas_node_id,
        "node_card": {
            "chapter": "basniev_gl23-24",
            "title": "Упругий запас пласта и как растёт воронка депрессии",
            "difficulty": {"primary": "М", "secondary": "Ф"},
            "format": "длинное видео",
            "duration_sec": 100.5,
            "refutes_misconceptions": ["misc.reservoir_void", "misc.pressure_sound_speed"],
        },
        "script": [
            {"t_start": 0, "t_end": 8.5, "text": "Почему нефть сама идёт в скважину? Под километрами породы она сжата в порах. В нашем примере давление около двухсот пятидесяти атмосфер.", "on_screen": "Что толкает нефть?", "claim_id": f"{bas_node_id}#c1", "is_analogy": False},
            {"t_start": 8.75, "t_end": 20, "text": "Открываем скважину. Давление у трубы падает. Нефть чуть расширяется. Под весом породы стенки пор сближаются. Нефть течёт к трубе, как вода из сжатой губки.", "on_screen": "Нефть течёт к скважине", "claim_id": f"{bas_node_id}#c1", "is_analogy": True},
            {"t_start": 20.25, "t_end": 28, "text": "В одной поре перемена едва заметна. А на всём Тенгизе, по оценке Басниева, так можно добыть до двадцати процентов запасов нефти.", "on_screen": "Тенгиз: до 20% запасов", "claim_id": f"{bas_node_id}#c3", "is_analogy": False},
            {"t_start": 28.25, "t_end": 37, "text": "Отъедем от поры. Уберём растяжение по вертикали. Километр в длину, двадцать метров в толщину. Видите, какой тонкой полоской стал пласт?", "on_screen": "Один масштаб по обеим осям", "claim_id": f"{bas_node_id}#c1", "is_analogy": False},
            {"t_start": 37.25, "t_end": 48, "text": "Давление падает сначала у трубы, потом всё дальше, как тепло расходится по ложке. Насколько быстро перемена скажется вдали? Это описывает коэффициент пьезопроводности.", "on_screen": "Как далеко заметно падение?", "claim_id": f"{bas_node_id}#c5", "is_analogy": True},
            {"t_start": 48.25, "t_end": 56.5, "text": "Посмотрите на кривую. Слева давление уже заметно упало. Справа оно почти прежнее. Для расчёта удобно условно провести границу между этими зонами.", "on_screen": "Где давление уже изменилось?", "claim_id": f"{bas_node_id}#c7", "is_analogy": False},
            {"t_start": 56.75, "t_end": 67, "text": "Радиус этой зоны растёт как корень из времени. Допустим, через час он равен ста метрам. До двухсот метров ждать ещё час или ещё три?", "on_screen": "Ещё час или три?", "claim_id": f"{bas_node_id}#c8", "is_analogy": False},
            {"t_start": 68.5, "t_end": 81, "text": "Ещё три часа. Вдвое дальше — вчетверо дольше. Всего четыре часа от пуска. А если учесть ширину скважины? В примере Басниева уже через секунду расчёт давления у трубы отличается максимум на шесть десятых процента.", "on_screen": "Четыре часа от пуска", "claim_id": f"{bas_node_id}#c6", "is_analogy": False},
            {"t_start": 81.25, "t_end": 90, "text": "Представьте граф. Каждая пора связана с соседями. Если дальше другая порода и связи там слабее, модель с одинаковыми связями ошибётся.", "on_screen": "А если связи слабее?", "claim_id": f"{bas_node_id}#c7", "is_analogy": True},
            {"t_start": 90.25, "t_end": 100.5, "text": "Остановим отбор. Датчик пишет, как восстанавливается давление. С какой кривой вы сравните запись, чтобы проверить, есть ли рядом разлом, который не пропускает нефть?", "on_screen": "Как проверить гипотезу о разломе?", "claim_id": f"{bas_node_id}#c1", "is_analogy": False},
        ],
        "sources": bas_sources,
        "scene_spec": str(bas_scene_path.relative_to(ROOT)) if bas_scene_path.is_relative_to(ROOT) else str(bas_scene_path),
        "frames": [
            {"t": 4, "class": "B", "label": "схема"},
            {"t": 14, "class": "B", "label": "схема"},
            {"t": 25, "class": "B", "label": "схема"},
            {"t": 33, "class": "B", "label": "схема"},
            {"t": 45, "class": "A", "label": "симуляция"},
            {"t": 53, "class": "B", "label": "симуляция"},
            {"t": 65, "class": "A", "label": "симуляция"},
            {"t": 70, "class": "A", "label": "симуляция"},
            {"t": 78, "class": "B", "label": "схема"},
            {"t": 87, "class": "B", "label": "схема"},
            {"t": 96, "class": "B", "label": "схема"},
        ],
        "exaggerations": [
            "поровая врезка увеличена, деформация усилена",
            "вертикальное растяжение ×20 снимается за 1,4 с, затем масштаб 1:1",
            "время процесса сжато для показа распространения воронки за 1 час",
        ],
        "prediction_question": "Через час радиус равен 100 м. Сколько ещё ждать до 200 м?",
        "transfer_question": "С какой кривой сравнить запись давления после остановки, чтобы проверить гипотезу о разломе?",
        "data_it_bridge": "Толщина рёбер графа показывает проводимость между порами. Запись восстановления давления после остановки позволяет сравнить две гипотезы о границе при одной истории отбора.",
        "physics_checklist": _build_checklist({
            "pc.layers_order": ("да", "Покрышка сверху, коллектор снизу, порядок не меняется."),
            "pc.fluids_in_pores": ("да", "Нефть показана в порах породы, не в подземном озере."),
            "pc.flow_high_to_low": ("да", "Нефть движется от высокого пластового давления p_к к низкому p_c."),
            "pc.causality": ("да", "Падение давления у забоя вызывает расширение флюида и сжатие пор, затем рост воронки."),
            "pc.balance": ("да", "Отобранный объём равен изменению упругого запаса в возмущённой зоне."),
            "pc.views_consistent": ("да", "Разрез и профиль показывают одно и то же состояние давления."),
            "pc.curve_sync": ("да", "Условная граница на профиле использует κ = 1 м²/с. Задача 100–200 м использует κ ≈ 0,694 м²/с."),
            "pc.plausible_numbers": ("да", "20% (стр. 478), κ=0.1–5 м²/с (стр. 481), t=1 с и 0.6% (стр. 492) взяты из книги."),
            "pc.exaggeration_named": ("да", "Деформация пор усилена. В масштабе 1:1 длина 1450 пикселей, толщина 29 пикселей."),
            "pc.depth_down": ("да", "Глубина на разрезе растёт вниз, оси графиков подписаны."),
            "pc.label_matches": ("да", "Метки «симуляция» и «схема» соответствуют расчётным кадрам."),
            "pc.modern_or_historic": ("да", "Показаны современные датчики забойного давления ГДИС."),
        }),
        "audio_check": {
            "target_lufs": -16.0,
            "max_wer": 0.10,
            "max_sync_drift_sec": 0.5,
            "prediction_pause_sec": 1.5,
            "status": "pending_render",
            "stress_review_words": ["за́лежь", "упругоёмкости", "пьезопрово́дности", "забойного", "воро́нки", "погре́шность"],
        },
    }

    lat_pass_path = out_dir / "latyshova_shorts_passport.json"
    bas_pass_path = out_dir / "basniev_deepdive_passport.json"
    # Номера утверждений — из готового слоя: цитата источника находит своё утверждение
    from .passport_bind import bind

    for ch_, pp in ((lat_ch, lat_passport), (bas_ch, bas_passport)):
        lp = ch_.build_dir / "layer.json"
        if lp.exists():
            for line in bind(pp, json.loads(lp.read_text(encoding="utf-8"))):
                print(f"{pp['video_id']}: {line}")
    lat_pass_path.write_text(json.dumps(lat_passport, ensure_ascii=False, indent=2), encoding="utf-8")
    bas_pass_path.write_text(json.dumps(bas_passport, ensure_ascii=False, indent=2), encoding="utf-8")

    # Жёсткая проверка обоих паспортов по схеме, цитатам и числам
    jsonschema.validate(lat_passport, passport_schema)
    jsonschema.validate(bas_passport, passport_schema)
    issues = verify_passport_sources(lat_passport, lat_src) + verify_passport_sources(bas_passport, bas_src)
    if issues:
        raise ValueError("Ошибки проверки паспортов:\n" + "\n".join(issues))

    brief_path = out_dir / "MENTOR_BRIEF.md"
    brief_path.write_text(_render_mentor_brief(lat_passport, lat_scene, bas_passport, bas_scene), encoding="utf-8")

    return {
        "latyshova_scene": lat_scene_path,
        "latyshova_passport": lat_pass_path,
        "basniev_scene": bas_scene_path,
        "basniev_passport": bas_pass_path,
        "mentor_brief": brief_path,
    }


def verify_passport_sources(passport: dict, src: Source) -> list[str]:
    """Проверяет дословные цитаты книжных источников и базовые требования ролика."""
    errs: list[str] = []
    for s in passport.get("sources", []):
        pg = s.get("page_or_dataset", "")
        if "стр." in pg and "data." not in pg:
            digits = [int(x) for x in __import__("re").findall(r"\d+", pg)]
            if not digits:
                errs.append(f"{s['claim_id']}: не указан номер страницы в {pg!r}")
                continue
            ok, hits = src.find_quote(s.get("quote", ""), digits[0])
            if not ok:
                errs.append(f"{s['claim_id']}: цитата не найдена на стр. {digits[0]} (найдена на {hits})")
    for line in passport.get("script", []):
        words = (line.get("on_screen") or "").split()
        if len(words) > 8:
            errs.append(f"{passport['video_id']} t={line['t_start']}: on_screen длиннее 8 слов ({len(words)})")
    # Проверка паузы предсказания >= 1.0 с
    script = passport.get("script", [])
    max_gap = max((script[i + 1]["t_start"] - script[i]["t_end"] for i in range(len(script) - 1)), default=0.0)
    if max_gap < 1.0:
        errs.append(f"{passport['video_id']}: нет паузы на предсказание >= 1.0 с (max_gap={max_gap:.2f})")
    return errs


def _render_mentor_brief(p1: dict, s1: dict, p2: dict, s2: dict) -> str:
    lines = [
        "# Спецификации двух обучающих роликов (на ревью ментору до 17:30)",
        "",
        "Оба ролика спроектированы от единой расчётной модели состояния (классы A и B, без генерации картинок/видео).",
        "",
    ]
    for idx, (p, s) in enumerate(((p1, s1), (p2, s2)), 1):
        nc = p["node_card"]
        lines += [
            f"## Ролик {idx}. {nc['title']} (`{p['video_id']}`, {s['format']}, {s['duration_sec']} с)",
            f"- **Глава и узел:** `{nc['chapter']}` / `{p['node_id']}` (типы трудности: `{nc['difficulty']['primary']}`, `{nc['difficulty'].get('secondary')}`)",
            f"- **Шаблон сцены:** `{s['template_id']}` (`{p['scene_spec']}`)",
            f"- **Опровергаемые заблуждения:** {', '.join(nc.get('refutes_misconceptions', []))}",
            f"- **Преувеличения:** {'; '.join(p['exaggerations'])}",
            f"- **Вопрос-предсказание:** {p['prediction_question']}",
            f"- **Вопрос на перенос:** {p['transfer_question']}",
            f"- **Мост к данным и ИТ:** {p['data_it_bridge']}",
            f"- **Ударения для проверки на слух:** {', '.join(p['audio_check']['stress_review_words'])}",
            "",
            "### Сценарий с таймкодами",
            "| Таймкод | Закадр | Экран (≤7 слов) | Источник |",
            "|---|---|---|---|",
        ]
        for ln in p["script"]:
            lines.append(f"| {ln['t_start']:.1f}–{ln['t_end']:.1f} с | {ln['text']} | {ln.get('on_screen','')} | `{ln['claim_id']}` |")
        lines += [
            "",
            "### Таблица источников (дословные цитаты проверены кодом)",
            "| ID утверждения | Источник | Цитата |",
            "|---|---|---|",
        ]
        for src in p["sources"]:
            lines.append(f"| `{src['claim_id']}` | {src['page_or_dataset']} | {src['quote']} |")
        lines += [
            "",
            "### Чек-лист физики (заполненные пункты «да»)",
        ]
        for chk in p["physics_checklist"]:
            if chk["answer"] == "да":
                lines.append(f"- **да** — {chk['item']} *({chk['note']})*")
        lines.append("")
    return "\n".join(lines)


def _short_words(text: str, max_words: int = 7) -> str:
    clean = re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip(" .,;:")
    words = clean.split()
    return " ".join(words[:max_words])


def _load_node_visual(cfg: Config, ch: Chapter, node: dict, width: int = 560) -> tuple[dict, list[tuple[str, str]]]:
    """Рисунок узла и его кадры [(подпись, SVG)] — те же, что в курсе. Рисунок без кадров открывается по частям."""
    from .illustrations import auto_spec, load_styleguide, load_visual_specs, with_reveal
    from .visual import canvas_width, render, render_frames

    sg = load_styleguide(cfg)
    entry = load_visual_specs(cfg, ch).get(node["id"])
    if isinstance(entry, list):
        entry = entry[0] if entry else None
    base = {"label": "схема", "class": "B", "title": node.get("title", node["id"])}
    for spec in ({**base, **entry} if isinstance(entry, dict) else None, {**base, **auto_spec(node)}):
        if spec is None:
            continue
        try:
            opened = not spec.get("frames")
            if opened:
                spec = {**with_reveal(spec), "_reveal": True}  # кадры — части рисунка по порядку, не смена состояния
            with canvas_width(width):
                frames = [(str(k), svg) for k, svg in render_frames(spec, sg)] or [("", render(spec, sg))]
            if opened and len(frames) > 1:  # в ролике части появляются по порядку, целиком рисунок стоит в конце
                frames = frames[1:] + frames[:1]
            return spec, frames
        except Exception:  # описание не рисуется — остаётся простая схема из карточки
            continue
    raise ValueError(f"узел {node['id']}: рисунок не строится")


_DEPTH_DOWN = {"layers", "section", "reef"}


def _node_checklist(spec: dict, by: str, scale: str, domain_dir: Path, numbers_ok: bool) -> list[dict]:
    """«да» — только то, что держит код; остальное честно «н/п» до просмотра специалистом."""
    tpl = spec.get("template", "")
    yes: dict[str, tuple[str, str]] = {
        "pc.label_matches": ("да", f"Метка «{spec.get('label', 'схема')}» берётся из описания рисунка; код ставит её на каждый кадр."),
        "pc.exaggeration_named": ("да", f"Подписано на рисунке: {scale}."),
        "pc.objects_stable": ("да", "Все кадры строятся из одного описания: меняется только параметр, остальное неизменно."),
    }
    if by.startswith("model"):
        yes["pc.causality"] = ("да", "Порядок реплик и каждую фразу сверил с карточкой отдельный проход модели-судьи.")
        yes["pc.applicability"] = ("да", "В сценарии есть строка об условии, при котором правило не работает.")
    if numbers_ok:
        yes["pc.plausible_numbers"] = ("да", "Каждое число реплик есть в карточке узла и в цитатах главы (проверка кодом).")
    if tpl in _DEPTH_DOWN:
        yes["pc.depth_down"] = ("да", "Код рисует глубину сверху вниз.")
        yes["pc.layers_order"] = ("да", "Порядок слоёв задан одним списком и одинаков во всех кадрах.")
    out = _build_checklist(yes, domain_dir=domain_dir)
    for row in out:
        if row["answer"] == "н/п":
            row["note"] = "Код этого не проверяет: смотрит специалист по кадрам, если процесс в ролике показан."
    return out


def build_spec_from_node(
    cfg: Config,
    ch: Chapter,
    node: dict,
    *,
    fmt: str = "9:16",
    out_dir: Path | None = None,
    llm: Any = None,
    log: Any = print,
) -> tuple[Path, Path]:
    """scene.json и passport.json для любого узла любого слоя. Сценарий — kurs/node_script.py, рисунок — тот же, что в курсе."""
    from .node_script import claim_map, get_script, script_problems, timeline

    out_dir = out_dir or (BUILD / "specs" / ch.id)
    out_dir.mkdir(parents=True, exist_ok=True)
    passport_schema = json.loads((cfg.schemas_dir / "passport.schema.json").read_text(encoding="utf-8"))

    nid = node["id"]
    is_short = fmt == "9:16"
    video_id = f"{nid.replace('.', '_')}_{'shorts' if is_short else 'deepdive'}"
    diff = node.get("difficulty") or {}
    primary_diff = diff.get("primary", "М")
    template_id = _DIFF_TO_TEMPLATE.get(primary_diff, "process-section")

    spec, frames = _load_node_visual(cfg, ch, node, 560 if is_short else 860)
    label = spec.get("label") if spec.get("label") in ("реальные данные", "симуляция", "схема") else "схема"
    cls = spec.get("class", "B")
    scale = spec.get("scale") or ("данные показаны без преувеличений" if label == "реальные данные" else "форма и размеры условные, не в масштабе")
    caption = spec.get("caption") or spec.get("title", "")
    doc = get_script(cfg, ch, node, fmt, [k for k, _ in frames] if len(frames) > 1 else [], caption, llm=llm, log=log)
    lines = timeline(doc)
    claims = claim_map(node)
    script = [
        {"t_start": x["t_start"], "t_end": x["t_end"], "text": x["text"], "claim_id": claims[x["claim"]]["id"] if x["claim"] in claims else f"{nid}#c1",
         "is_analogy": x["kind"] == "analogy", "on_screen": x["on_screen"]}
        for x in lines
    ]
    sources = []
    for cid in dict.fromkeys(x["claim"] for x in lines):
        c = claims.get(cid)
        if c:
            sources.append({"claim_id": c["id"], "page_or_dataset": f"стр. {c['page_pdf']}" if c.get("page_pdf") else "по главе", "quote": c.get("quote") or c["text"]})
    if not sources:
        sources = [{"claim_id": f"{nid}#c1", "page_or_dataset": "по главе", "quote": node.get("title", nid)}]
    ask = next((i for i, x in enumerate(lines) if x["kind"] == "predict"), None)
    pause = [script[ask]["t_end"], script[ask + 1]["t_start"]] if ask is not None and ask + 1 < len(script) else []
    total = script[-1]["t_end"] + 1.2
    nuance = ((node.get("layers") or {}).get("nuances") or {}).get("text") or ""

    scene_path = out_dir / f"{video_id}_scene.json"
    scene_doc = {
        "video_id": video_id,
        "format": fmt,
        "resolution": [1080, 1920] if is_short else [1920, 1080],
        "fps": 30,
        "duration_sec": round(total, 2),
        "template_id": template_id,
        "objects": [
            {"id": "picture", "kind": f"рисунок шага, шаблон {spec.get('template')}", "depth_direction": "down" if spec.get("template") in _DEPTH_DOWN else "нет"},
            {"id": "map", "kind": "карта шагов: ключевые слова реплик"},
        ],
        "parameters": {
            "visual": {k: spec[k] for k in ("template", "params", "frames_label") if k in spec},
            "frames": [k for k, _ in frames],
            "prediction_pause_sec": pause,
            "language": "ru",
        },
        "invariants": [
            "Все кадры рисунка построены из одного описания: меняется только подписанный параметр.",
            "Метка происхождения и подпись масштаба видны на каждом кадре.",
            "Рисунок тот же, что на шаге курса: один источник — описание в слое.",
        ],
        "assumptions": [x for x in (scale, nuance) if x],
        "state_sample": {
            "title": node.get("title", nid),
            "difficulty": primary_diff,
            "scale_note": scale,
            "frames_label": spec.get("frames_label") or "",
            "visual_frames": [{"label": k, "svg": svg} for k, svg in frames],
            "visual_svg": frames[0][1],
            "story": [
                {"kind": x["kind"], "frame": min(max(1, int(x["frame"])), len(frames)), "on_screen": x["on_screen"],
                 "page": (claims.get(x["claim"]) or {}).get("page_pdf"), "claim_kind": (claims.get(x["claim"]) or {}).get("kind", "")}
                for x in lines
            ],
            "source_line": f"{ch.id.split('_')[0]}, стр. {', '.join(str(p) for p in sorted({claims[x['claim']]['page_pdf'] for x in lines if x['claim'] in claims and claims[x['claim']].get('page_pdf')}))}",
            "misconception": (node.get("misconception") or {}).get("text") or "",
            "visual_spec": spec,
        },
        "script_by": doc.get("by", "rules"),
        "mentor_note": doc.get("checklist_note", ""),
    }
    scene_path.write_text(json.dumps(scene_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    form = script_problems(doc, node, fmt, max(1, len(frames)))
    passport = {
        "video_id": video_id,
        "node_id": nid,
        "node_card": {
            "chapter": ch.id,
            "title": node.get("title", nid),
            "difficulty": {"primary": primary_diff, "secondary": diff.get("secondary")},
            "format": "шортс" if is_short else "длинное видео",
            "duration_sec": round(total, 2),
            "refutes_misconceptions": [node["misconception"]["ref"]] if (node.get("misconception") or {}).get("ref") else [],
        },
        "script": script,
        "sources": sources,
        "scene_spec": str(scene_path.relative_to(ROOT)) if scene_path.is_relative_to(ROOT) else str(scene_path),
        "frames": [{"t": round((x["t_start"] + x["t_end"]) / 2, 2), "class": cls, "label": label} for x in script],
        "exaggerations": [] if label == "реальные данные" else [f"рисунок: {scale}"],
        "prediction_question": doc["prediction_question"],
        "transfer_question": doc["transfer_question"],
        "data_it_bridge": doc["data_bridge"],
        "physics_checklist": _node_checklist(spec, doc.get("by", "rules"), scale, cfg.domain_dir, not any("число" in x for x in form)),
        "audio_check": {
            "target_lufs": -16.0,
            "max_wer": 0.10,
            "max_sync_drift_sec": 0.5,
            "prediction_pause_sec": 1.5,
            "status": "pending_render",
        },
    }
    passport_path = out_dir / f"{video_id}_passport.json"
    passport_path.write_text(json.dumps(passport, ensure_ascii=False, indent=2), encoding="utf-8")
    jsonschema.validate(passport, passport_schema)
    return scene_path, passport_path


def build_template_coverage_report(
    cfg: Config,
    ch: Chapter,
    layer: dict,
    routing: dict | None = None,
    out_dir: Path | None = None,
) -> dict[str, Path]:
    """Отчёт о покрытии видеоёмких и интерактивных узлов главы шаблонами сцен."""
    from .rules import route_by_rules

    out_dir = out_dir or ch.build_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    if routing is None:
        def _txt(n: dict) -> str:
            parts = [n.get("title", "")]
            for v in (n.get("layers") or {}).values():
                if isinstance(v, dict):
                    parts.append(v.get("text", ""))
            return " ".join(parts)
        norm_layer = {
            **layer,
            "nodes": [
                {
                    **n,
                    "steps": n.get("steps") or [],
                    "formulas": n.get("formulas") or [],
                    "decision_on_data": n.get("decision_on_data"),
                }
                for n in layer.get("nodes", [])
            ],
        }
        routing = route_by_rules(ch, norm_layer, {t: t for t in SUPPORTED_TEMPLATES}, _txt)

    from .illustrations import generated_specs, reviewed_specs
    from .video_link import node_story_row

    rev, gen = reviewed_specs(cfg, ch), generated_specs(ch)
    nodes_by_id = {n["id"]: n for n in layer.get("nodes", [])}
    rows = []
    for r in routing.get("routes", []):
        nid = r["node_id"]
        fmt = r.get("format", "")
        sec = r.get("secondary_format") or ""
        if not any(k in (fmt, sec) for k in ("шортс", "длинное видео", "интерактив", "практикум на данных")):
            continue
        node = nodes_by_id.get(nid)
        if node is None:
            continue
        ns = node_story_row(cfg, ch, node, rev, gen)
        rows.append({
            "node_id": nid,
            "title": node.get("title", nid),
            "difficulty": ns["difficulty"],
            "format": fmt,
            "secondary_format": sec or None,
            "template_id": ns["template"],
            "route_template": r.get("template") or _DIFF_TO_TEMPLATE.get(ns["difficulty"], ""),
            "label": ns["label"],
            "picture": ns["picture"],
            "frames": ns["frames"],
            "state_kind": ns["state_kind"],
            "state_model": ns["state_model"],
            "script_9x16": ns["9:16"],
            "script_16x9": ns["16:9"],
            "status": "covered" if ns["covered"] else "needs_new_template",
            "status_ru": ns["status"],
        })

    report_json = out_dir / "template_coverage.json"
    report_md = out_dir / "template_coverage.md"
    payload = {
        "chapter": ch.id,
        "template": "NodeStory (video/src/scenes/NodeStory.tsx) + рисунок узла из kurs/visual.py",
        "total_visual_nodes": len(rows),
        "covered_nodes": sum(1 for x in rows if x["status"] == "covered"),
        "needs_new_count": sum(1 for x in rows if x["status"] != "covered"),
        "rows": rows,
    }
    report_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        f"# Покрытие узлов шаблонами сцен: `{ch.id}`",
        "",
        f"Видеоёмких узлов по маршрутизации: {payload['total_visual_nodes']}. Закрыто шаблоном NodeStory: **{payload['covered_nodes']}**, "
        f"нужен новый шаблон: **{payload['needs_new_count']}**.",
        "",
        "Шаблон один: реплики сценария идут по кадрам рисунка узла; модель состояния — то, что меняется между кадрами. "
        "«Расчёт» — кадры считаются по параметру, «состояния» — схема в нескольких состояниях, «части» — рисунок раскрывается по частям, "
        "«статичный» — один кадр. Узлу с трудностью «время» нужен расчёт или состояния.",
        "",
        "| Узел | Трудность | Формат | Шаблон (рисунок) | Метка | Модель состояния | Сценарий 9:16 без модели | Статус |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        md_lines.append(f"| `{row['node_id']}` {row['title']} | {row['difficulty']} | {row['format']} | {row['template_id']} ({row['picture']}) | "
                        f"{row['label']} | {row['state_model']} | {'да' if row['script_9x16']['rules_pass'] else 'нет, моделью'} | {row['status_ru']} |")
    report_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return {"json": report_json, "md": report_md}
