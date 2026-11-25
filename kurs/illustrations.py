"""Детерминированный генератор иллюстраций классов A и B (SVG из модели и данных, без AI-генерации)."""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

import jsonschema

from .config import Chapter, Config
from .datasets import load_volve_slice


def load_styleguide(cfg: Config) -> dict:
    sg_domain = cfg.domain_dir / "styleguide.json"
    if sg_domain.exists():
        return json.loads(sg_domain.read_text(encoding="utf-8"))
    sg_input = cfg.input_dir / "пакет_вводных/03_стайлгайд/styleguide.json"
    return json.loads(sg_input.read_text(encoding="utf-8"))


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _svg_wrap(width: int, height: int, bg: str, title: str, body: str, prov_label: str, prov_color: str) -> str:
    pill_w = max(130, len(prov_label) * 9 + 24)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" role="img" aria-label="{_esc(title)}" '
        f'style="background:{bg};border-radius:10px;display:block;font-family:\'Segoe UI\',system-ui,-apple-system,sans-serif">\n'
        f'  <title>{_esc(title)}</title>\n'
        f'  <rect x="0" y="0" width="{width}" height="{height}" rx="10" fill="{bg}"/>\n'
        f'{body}\n'
        f'  <g transform="translate({width - pill_w - 14}, 12)">\n'
        f'    <rect width="{pill_w}" height="24" rx="12" fill="{prov_color}"/>\n'
        f'    <text x="{pill_w / 2}" y="16" text-anchor="middle" fill="#FFFFFF" '
        f'font-size="11.5" font-weight="600">{_esc(prov_label)}</text>\n'
        f'  </g>\n'
        f'</svg>\n'
    )


def render_volve_well_log_svg(dataset: dict, sg: dict, out_path: Path) -> None:
    """Класс A: каротажный планшет по реальным данным Volve 15/9-F-1 B (3 интервала A, B, C)."""
    w, h = 760, 400
    bg = sg["background"]
    panel = sg["panel"]
    txt = sg["text"]
    muted = sg["muted"]
    c_gr = sg["curves"]["GR"]["color"]
    c_rt = sg["curves"]["RES"]["color"]
    c_sw = sg["fluids"]["water"]["color"]
    c_oil = sg["fluids"]["oil"]["color"]
    prov_color = sg["provenance"]["реальные данные"]

    parts: list[str] = [
        f'  <text x="18" y="28" fill="{txt}" font-size="15" font-weight="700">'
        f'Volve 15/9-F-1 B: три интервала с разной природой сигнала</text>',
        f'  <text x="18" y="46" fill="{muted}" font-size="11.5">'
        f'Глубина растёт вниз (MD RKB, м) · Западная шкала (GR API, RT Ом·м, PHIF, SW)</text>',
    ]

    col_w = 228
    gap = 18
    x0 = 18
    top_y = 64
    plot_h = 266

    for idx, iv in enumerate(dataset["intervals"]):
        cx = x0 + idx * (col_w + gap)
        pts = iv["points"]
        s = iv["summary"]
        parts.append(f'  <rect x="{cx}" y="{top_y}" width="{col_w}" height="{plot_h}" rx="8" fill="{panel}" stroke="#2E3B38"/>')
        parts.append(f'  <text x="{cx + 10}" y="{top_y + 20}" fill="{txt}" font-size="12.5" font-weight="700">{_esc(iv["label"])}</text>')
        parts.append(
            f'  <text x="{cx + 10}" y="{top_y + 36}" fill="{muted}" font-size="10.5">'
            f'GR={s["median_GR"]} · RT={s["median_RT"]} · PHIF={s["median_PHIF"]}</text>'
        )

        # Три мини-трека: GR (0..100), RT (лог 0.2..200), SW (0..1)
        ty = top_y + 48
        th = plot_h - 86
        tw = (col_w - 28) / 3
        track_defs = [
            ("GR", c_gr, lambda p: min(1.0, max(0.0, (p["GR"] or 0.0) / 100.0))),
            ("RT (lg)", c_rt, lambda p: min(1.0, max(0.0, (math.log10(max(0.2, p["RT"] or 0.2)) - math.log10(0.2)) / (math.log10(200.0) - math.log10(0.2))))),
            ("SW", c_sw, lambda p: min(1.0, max(0.0, p["SW"] if p["SW"] is not None else 1.0))),
        ]
        n = max(1, len(pts) - 1)
        for ti, (tname, tcol, fn) in enumerate(track_defs):
            tx = cx + 10 + ti * (tw + 4)
            parts.append(f'  <rect x="{tx:.1f}" y="{ty}" width="{tw:.1f}" height="{th}" fill="#0B1015" stroke="#24303C"/>')
            parts.append(f'  <text x="{tx + tw / 2:.1f}" y="{ty + 12}" text-anchor="middle" fill="{tcol}" font-size="9.5" font-weight="600">{tname}</text>')
            poly = []
            for pi, pt in enumerate(pts):
                px = tx + 4 + fn(pt) * (tw - 8)
                py = ty + 18 + (pi / n) * (th - 24)
                poly.append(f"{px:.1f},{py:.1f}")
            if tname == "SW":
                # Подсветка нефтенасыщения (1 - SW)
                fill_col = c_oil if s["median_SW"] < 0.5 else c_sw
                parts.append(f'  <polyline fill="none" stroke="{fill_col}" stroke-width="2" points="{" ".join(poly)}"/>')
            else:
                parts.append(f'  <polyline fill="none" stroke="{tcol}" stroke-width="2" points="{" ".join(poly)}"/>')

        badge_col = c_oil if iv["id"] == "A" else ("#8C7B6B" if iv["id"] == "B" else c_sw)
        verdict = (
            f'Коллектор с нефтью (SW={s["median_SW"]})'
            if iv["id"] == "A"
            else (f'Плотный неколлектор (SAND=0)' if iv["id"] == "B" else f'Водоносный песок (SW={s["median_SW"]})')
        )
        parts.append(f'  <rect x="{cx + 10}" y="{top_y + plot_h - 28}" width="{col_w - 20}" height="20" rx="5" fill="{badge_col}" fill-opacity="0.22" stroke="{badge_col}"/>')
        parts.append(f'  <text x="{cx + col_w / 2:.1f}" y="{top_y + plot_h - 14}" text-anchor="middle" fill="{txt}" font-size="10.5" font-weight="600">{verdict}</text>')

    parts.append(
        f'  <text x="18" y="358" fill="{txt}" font-size="11.5">'
        f'Вывод: в интервале B сопротивление RT=7.4 Ом·м выше, чем в песке C (0.57 Ом·м), но пористость PHIF=0.02 — коллектора нет.</text>'
    )
    parts.append(
        f'  <text x="18" y="378" fill="{muted}" font-size="10.5">'
        f'Источник: Equinor Volve open dataset, скв. 15/9-F-1 B (CC BY 4.0) · Построено кодом из LAS</text>'
    )
    svg = _svg_wrap(w, h, bg, "Каротажный планшет Volve 15/9-F-1 B", "\n".join(parts), "реальные данные", prov_color)
    out_path.write_text(svg, encoding="utf-8")


def render_crossplot_p69_svg(sg: dict, out_path: Path) -> None:
    """Класс A: перерисовка по рис. 69, стр. 98 (Латышова) — кроссплот ρп = f(kп) с примерами 46–47."""
    w, h = 720, 410
    bg = sg["background"]
    panel = sg["panel"]
    txt = sg["text"]
    muted = sg["muted"]
    c_oil = sg["fluids"]["oil"]["color"]
    c_water = sg["fluids"]["water"]["color"]
    c_res = sg["curves"]["RES"]["color"]
    prov_color = sg["provenance"]["схема"]

    px0, py0, pw, ph = 72, 62, 590, 265

    def map_x(kp: float) -> float:
        return px0 + ((kp - 5.0) / 35.0) * pw

    def map_y(rho: float) -> float:
        lo, hi = math.log10(0.8), math.log10(40.0)
        val = (math.log10(max(0.8, min(40.0, rho))) - lo) / (hi - lo)
        return py0 + ph - val * ph

    # Кривые границ по рис. 69 (качественно-оцифрованные огибающие для примеров 46-47: kp*=17%)
    kps = [8, 12, 17, 20, 25, 30, 35, 39]
    rho_water = [34.0 / (k / 10.0) ** 1.85 for k in kps]
    rho_star2 = [1.55 * r for r in rho_water]
    rho_star1 = [2.9 * r for r in rho_water]
    rho_max = [8.5 * r for r in rho_water]

    def pts_str(ys: list[float]) -> str:
        return " ".join(f"{map_x(k):.1f},{map_y(y):.1f}" for k, y in zip(kps, ys))

    # Области I (нефть), II (нефть+вода), III (вода) правее kp*=17%
    k_cut_idx = 2  # kp=17
    poly_oil = " ".join(f"{map_x(k):.1f},{map_y(y):.1f}" for k, y in zip(kps[k_cut_idx:], rho_max[k_cut_idx:])) + " " + " ".join(f"{map_x(k):.1f},{map_y(y):.1f}" for k, y in reversed(list(zip(kps[k_cut_idx:], rho_star1[k_cut_idx:]))))
    poly_two = " ".join(f"{map_x(k):.1f},{map_y(y):.1f}" for k, y in zip(kps[k_cut_idx:], rho_star1[k_cut_idx:])) + " " + " ".join(f"{map_x(k):.1f},{map_y(y):.1f}" for k, y in reversed(list(zip(kps[k_cut_idx:], rho_star2[k_cut_idx:]))))
    poly_wat = " ".join(f"{map_x(k):.1f},{map_y(y):.1f}" for k, y in zip(kps[k_cut_idx:], rho_star2[k_cut_idx:])) + " " + " ".join(f"{map_x(k):.1f},{map_y(y):.1f}" for k, y in reversed(list(zip(kps[k_cut_idx:], rho_water[k_cut_idx:]))))

    x_cut = map_x(17.0)
    parts: list[str] = [
        f'  <text x="18" y="28" fill="{txt}" font-size="15" font-weight="700">Зависимость ρп = f(kп): граница коллектор / нефть / вода</text>',
        f'  <text x="18" y="46" fill="{muted}" font-size="11.5">Одно и то же сопротивление 10 Ом·м при разной пористости даёт разный флюид</text>',
        f'  <rect x="{px0}" y="{py0}" width="{pw}" height="{ph}" rx="6" fill="{panel}" stroke="#2E3B38"/>',
        f'  <rect x="{px0}" y="{py0}" width="{x_cut - px0:.1f}" height="{ph}" fill="#8C7B6B" fill-opacity="0.18"/>',
        f'  <polygon points="{poly_oil}" fill="{c_oil}" fill-opacity="0.28"/>',
        f'  <polygon points="{poly_two}" fill="{c_res}" fill-opacity="0.22"/>',
        f'  <polygon points="{poly_wat}" fill="{c_water}" fill-opacity="0.26"/>',
        f'  <line x1="{x_cut:.1f}" y1="{py0}" x2="{x_cut:.1f}" y2="{py0 + ph}" stroke="#ECEFF4" stroke-width="1.6" stroke-dasharray="5,4"/>',
        f'  <polyline fill="none" stroke="{c_oil}" stroke-width="2.2" points="{pts_str(rho_max)}"/>',
        f'  <polyline fill="none" stroke="{c_res}" stroke-width="2" points="{pts_str(rho_star1)}"/>',
        f'  <polyline fill="none" stroke="{c_water}" stroke-width="2" stroke-dasharray="4,3" points="{pts_str(rho_star2)}"/>',
        f'  <polyline fill="none" stroke="{c_water}" stroke-width="2.2" points="{pts_str(rho_water)}"/>',
    ]

    # Сетка и подписи осей
    for kv in (10, 17, 20, 25, 30, 35):
        xv = map_x(kv)
        parts.append(f'  <line x1="{xv:.1f}" y1="{py0}" x2="{xv:.1f}" y2="{py0 + ph}" stroke="#2B3642" stroke-width="0.8"/>')
        lbl = "17* (kп*)" if kv == 17 else str(kv)
        parts.append(f'  <text x="{xv:.1f}" y="{py0 + ph + 18}" text-anchor="middle" fill="{muted}" font-size="11">{lbl}</text>')
    for rv in (1, 2, 5, 10, 20, 30):
        yv = map_y(rv)
        parts.append(f'  <line x1="{px0}" y1="{yv:.1f}" x2="{px0 + pw}" y2="{yv:.1f}" stroke="#2B3642" stroke-width="0.8"/>')
        parts.append(f'  <text x="{px0 - 8}" y="{yv + 4:.1f}" text-anchor="end" fill="{muted}" font-size="11">{rv}</text>')

    parts.append(f'  <text x="{px0 + pw / 2}" y="{py0 + ph + 38}" text-anchor="middle" fill="{txt}" font-size="12">Пористость kп, %</text>')
    parts.append(f'  <text x="22" y="{py0 + ph / 2}" text-anchor="middle" transform="rotate(-90 22 {py0 + ph / 2})" fill="{txt}" font-size="12">Сопротивление ρп, Ом·м (лог. шкала)</text>')

    # Точки Примеров 46 и 47
    points = [
        (25, 20, "Пр.46: 25%, 20 Ом·м → чистая нефть", c_oil),
        (15, 10, "Пр.47а: 15%, 10 Ом·м → неколлектор", "#BDC3C7"),
        (20, 10, "Пр.47б: 20%, 10 Ом·м → нефть", c_oil),
        (25, 10, "Пр.47в: 25%, 10 Ом·м → нефть+вода", c_res),
    ]
    for kp, rho, plbl, pcol in points:
        px, py = map_x(kp), map_y(rho)
        parts.append(f'  <circle cx="{px:.1f}" cy="{py:.1f}" r="5.5" fill="{pcol}" stroke="#0F1419" stroke-width="1.5"/>')
        dx = 10 if kp >= 20 else -8
        anch = "start" if kp >= 20 else "end"
        dy = -8 if rho == 20 else (15 if kp == 25 else -9)
        parts.append(f'  <text x="{px + dx:.1f}" y="{py + dy:.1f}" text-anchor="{anch}" fill="{txt}" font-size="10.5" font-weight="600">{_esc(plbl)}</text>')

    parts.append(f'  <text x="{px0 + 16}" y="{py0 + 24}" fill="#E8C872" font-size="11" font-weight="600">А: неколлектор</text>')
    parts.append(f'  <text x="{px0 + pw - 16}" y="{py0 + 24}" text-anchor="end" fill="{c_oil}" font-size="11" font-weight="600">I: безводная нефть</text>')
    parts.append(f'  <text x="18" y="392" fill="{muted}" font-size="10.5">перерисовано по рис. 69, стр. 98 (Латышова, 1991) · Построено кодом по примерам 46–47</text>')

    svg = _svg_wrap(w, h, bg, "Перерисовка рис. 69, стр. 98: зависимость ρп = f(kп)", "\n".join(parts), "схема", prov_color)
    out_path.write_text(svg, encoding="utf-8")


def render_node_diagram_svg(node: dict, sg: dict, out_path: Path) -> tuple[str, str, str]:
    """Класс B/A: параметрическая схема по типу трудности узла. Возвращает (frame_class, origin, label)."""
    w, h = 720, 340
    bg = sg["background"]
    panel = sg["panel"]
    txt = sg["text"]
    muted = sg["muted"]
    diff = (node.get("difficulty") or {}).get("primary", "И")
    title = node.get("title", "Схема узла")

    if diff in ("Ф", "М"):
        # Воронка депрессии p(r, t) по уравнению пьезопроводности / ПССС (глубина/давление согласованы)
        c_p = sg["curves"]["PRESSURE"]["color"]
        c_sand = sg["lithology"]["sand"]["color"]
        c_shale = sg["lithology"]["shale"]["color"]
        prov = "симуляция"
        parts = [
            f'  <text x="18" y="28" fill="{txt}" font-size="14.5" font-weight="700">{_esc(title[:62])}</text>',
            f'  <text x="18" y="46" fill="{muted}" font-size="11">Расчёт модели упругого режима: R(t) = √(r_c² + 4κt) · пласт в порах, не пустота</text>',
            f'  <rect x="18" y="60" width="320" height="225" rx="8" fill="{panel}" stroke="#2E3B38"/>',
            f'  <rect x="356" y="60" width="346" height="225" rx="8" fill="{panel}" stroke="#2E3B38"/>',
            # Левая панель: разрез пласта (покрышка, пористый коллектор, скважина)
            f'  <rect x="34" y="88" width="288" height="44" fill="{c_shale}" fill-opacity="0.65"/>',
            f'  <text x="178" y="114" text-anchor="middle" fill="{txt}" font-size="11">Непроницаемая покрышка (глина)</text>',
            f'  <rect x="34" y="132" width="288" height="92" fill="{c_sand}" fill-opacity="0.35" stroke="{c_sand}"/>',
            f'  <rect x="34" y="224" width="288" height="36" fill="{c_shale}" fill-opacity="0.5"/>',
            f'  <rect x="52" y="72" width="12" height="162" fill="#ECEFF4" fill-opacity="0.85"/>',
            f'  <text x="72" y="84" fill="{txt}" font-size="10.5">Скважина r_c</text>',
            f'  <text x="178" y="182" text-anchor="middle" fill="{txt}" font-size="11">Пористый коллектор (сжатие пор + расширение нефти)</text>',
            f'  <text x="178" y="274" text-anchor="middle" fill="{muted}" font-size="10.5">верт. масштаб ×25 (не в масштабе) · глубина ↓</text>',
        ]
        # Правая панель: кривые p(r, t) для t1 < t2 < t3
        gx, gy, gw, gh = 386, 86, 294, 168
        parts.append(f'  <line x1="{gx}" y1="{gy + gh}" x2="{gx + gw}" y2="{gy + gh}" stroke="{muted}" stroke-width="1.2"/>')
        parts.append(f'  <line x1="{gx}" y1="{gy}" x2="{gx}" y2="{gy + gh}" stroke="{muted}" stroke-width="1.2"/>')
        parts.append(f'  <line x1="{gx}" y1="{gy + 22}" x2="{gx + gw}" y2="{gy + 22}" stroke="{muted}" stroke-dasharray="4,4"/>')
        parts.append(f'  <text x="{gx + gw - 4}" y="{gy + 16}" text-anchor="end" fill="{muted}" font-size="10.5">p_к (начальное)</text>')
        for ti, (kappa_t, col, tlbl) in enumerate([(0.06, "#9BD7F5", "t₁"), (0.20, c_p, "t₂"), (0.48, "#2ECC71", "t₃")]):
            pts_c = []
            for s in range(0, 45):
                r_norm = 0.03 + (s / 44.0) * 0.97
                p_drop = math.exp(-(r_norm ** 2) / kappa_t) * (0.45 + 0.14 * ti)
                px = gx + r_norm * gw
                py = gy + 22 + min(gh - 26, p_drop * (gh - 30))
                pts_c.append(f"{px:.1f},{py:.1f}")
            parts.append(f'  <polyline fill="none" stroke="{col}" stroke-width="2.2" points="{" ".join(pts_c)}"/>')
            parts.append(f'  <text x="{gx + 40 + ti * 62}" y="{gy + gh - 12}" fill="{col}" font-size="10.5" font-weight="700">{tlbl}: R({tlbl})</text>')
        parts.append(f'  <text x="{gx + gw / 2}" y="{gy + gh + 18}" text-anchor="middle" fill="{txt}" font-size="10.5">Расстояние r от скважины</text>')
        parts.append(f'  <text x="18" y="316" fill="{muted}" font-size="10.5">Сигнал давления диффундирует по закону √(κt), а не со скоростью звука.</text>')
        svg = _svg_wrap(w, h, bg, title, "\n".join(parts), prov, sg["provenance"][prov])
        out_path.write_text(svg, encoding="utf-8")
        return "B", "code_from_model", prov

    if diff in ("Р", "В", "П"):
        # Схема разреза рифовой постройки / дельтового комплекса
        c_reef = sg["lithology"]["reef"]["color"]
        c_lime = sg["lithology"]["limestone"]["color"]
        c_shale = sg["lithology"]["shale"]["color"]
        prov = "схема"
        parts = [
            f'  <text x="18" y="28" fill="{txt}" font-size="14.5" font-weight="700">{_esc(title[:62])}</text>',
            f'  <text x="18" y="46" fill="{muted}" font-size="11">Схематический профиль (верт. масштаб ×15, не в масштабе) · глубина растёт вниз</text>',
            f'  <rect x="18" y="60" width="684" height="232" rx="8" fill="{panel}" stroke="#2E3B38"/>',
            f'  <line x1="40" y1="95" x2="680" y2="95" stroke="#3498DB" stroke-dasharray="6,4" stroke-width="1.5"/>',
            f'  <text x="676" y="88" text-anchor="end" fill="#3498DB" font-size="11">Уровень моря</text>',
            f'  <polygon points="40,260 220,260 290,115 390,115 470,260 680,260 680,278 40,278" fill="{c_shale}" fill-opacity="0.45"/>',
            f'  <polygon points="235,260 295,120 385,120 445,260" fill="{c_reef}" fill-opacity="0.72" stroke="{c_reef}" stroke-width="2"/>',
            f'  <polygon points="150,260 235,260 295,120 265,165" fill="{c_lime}" fill-opacity="0.65"/>',
            f'  <polygon points="445,260 530,260 415,165 385,120" fill="{c_lime}" fill-opacity="0.65"/>',
            f'  <text x="340" y="195" text-anchor="middle" fill="#0F1419" font-size="12" font-weight="700">Ядро рифа</text>',
            f'  <text x="200" y="242" text-anchor="middle" fill="{txt}" font-size="11">Предрифовый шлейф</text>',
            f'  <text x="492" y="242" text-anchor="middle" fill="{txt}" font-size="11">Тыльная зона / лагуна</text>',
            f'  <text x="18" y="316" fill="{muted}" font-size="10.5">Фации распределены зонально; вторичная пористость (карст/доломитизация) управляет коллектором.</text>',
        ]
        svg = _svg_wrap(w, h, bg, title, "\n".join(parts), prov, sg["provenance"][prov])
        out_path.write_text(svg, encoding="utf-8")
        return "B", "code_from_model", prov

    # По умолчанию (И, Пр, Н): скважина с зоной проникновения + синхронные кривые ГИС (симуляция)
    c_sand = sg["lithology"]["sand"]["color"]
    c_shale = sg["lithology"]["shale"]["color"]
    c_filt = sg["fluids"]["filtrate"]["color"]
    c_oil = sg["fluids"]["oil"]["color"]
    c_gr = sg["curves"]["GR"]["color"]
    c_sp = sg["curves"]["SP"]["color"]
    c_res = sg["curves"]["RES"]["color"]
    prov = "симуляция"
    parts = [
        f'  <text x="18" y="28" fill="{txt}" font-size="14.5" font-weight="700">{_esc(title[:62])}</text>',
        f'  <text x="18" y="46" fill="{muted}" font-size="11">Синхронизация: ствол (глубина ↓) → сигнал прибора → интерпретация</text>',
        f'  <rect x="18" y="60" width="290" height="232" rx="8" fill="{panel}" stroke="#2E3B38"/>',
        f'  <rect x="322" y="60" width="380" height="232" rx="8" fill="{panel}" stroke="#2E3B38"/>',
        # Пласты
        f'  <rect x="30" y="74" width="266" height="58" fill="{c_shale}" fill-opacity="0.6"/>',
        f'  <text x="288" y="106" text-anchor="end" fill="{txt}" font-size="11">Глина</text>',
        f'  <rect x="30" y="132" width="266" height="82" fill="{c_sand}" fill-opacity="0.35"/>',
        f'  <rect x="64" y="132" width="66" height="82" fill="{c_filt}" fill-opacity="0.45"/>',
        f'  <rect x="130" y="132" width="166" height="82" fill="{c_oil}" fill-opacity="0.35"/>',
        f'  <text x="288" y="168" text-anchor="end" fill="{txt}" font-size="11" font-weight="700">Коллектор (нефть)</text>',
        f'  <text x="102" y="204" text-anchor="middle" fill="{txt}" font-size="9.5">Зона проникн.</text>',
        f'  <rect x="30" y="214" width="266" height="62" fill="#8C7B6B" fill-opacity="0.55"/>',
        f'  <text x="288" y="248" text-anchor="end" fill="{txt}" font-size="11">Плотная порода</text>',
        # Ствол и прибор
        f'  <rect x="46" y="70" width="18" height="210" fill="#7A6A58"/>',
        f'  <rect x="50" y="156" width="10" height="28" rx="3" fill="{sg["measurement"]["color"]}"/>',
        # Правая панель: кривые ПС/ГК и КС
        f'  <text x="392" y="84" text-anchor="middle" fill="{c_sp}" font-size="11" font-weight="600">ПС / ГК</text>',
        f'  <text x="580" y="84" text-anchor="middle" fill="{c_res}" font-size="11" font-weight="600">Сопротивление (КС / БК)</text>',
        f'  <polyline fill="none" stroke="{c_sp}" stroke-width="2.4" points="430,92 428,128 352,140 350,206 410,218 412,276"/>',
        f'  <polyline fill="none" stroke="{c_gr}" stroke-width="1.8" stroke-dasharray="4,3" points="442,92 440,128 366,140 364,206 382,218 384,276"/>',
        f'  <polyline fill="none" stroke="{c_res}" stroke-width="2.6" points="512,92 514,128 654,142 652,204 648,218 650,276"/>',
        f'  <text x="660" y="176" text-anchor="end" fill="{c_res}" font-size="10">Пик КС (нефть)</text>',
        f'  <text x="660" y="250" text-anchor="end" fill="#ECEFF4" font-size="10">Пик КС (плотный!)</text>',
        f'  <text x="18" y="316" fill="{muted}" font-size="10.5">Схема (не в масштабе: ствол расширен ×20): высокое сопротивление дают и нефть, и плотная порода.</text>',
    ]
    svg = _svg_wrap(w, h, bg, title, "\n".join(parts), prov, sg["provenance"][prov])
    out_path.write_text(svg, encoding="utf-8")
    return "B", "code_from_model", prov


def build_illustrations(
    cfg: Config,
    ch: Chapter,
    layer: dict,
    out_dir: Path,
) -> dict:
    """Генерирует иллюстрации A/B для всех шагов слоя и проверяет illustration.schema.json."""
    sg = load_styleguide(cfg)
    assets_img_dir = out_dir / "assets" / "img"
    assets_img_dir.mkdir(parents=True, exist_ok=True)
    volve = load_volve_slice(cfg.domain_dir)

    items: list[dict] = []
    # Подготовим общие перерисованные/данные иллюстрации, если релевантны
    has_crossplot = any("69" in (w.get("figure") or "") or "69" in " ".join(c.get("quote", "") for c in n.get("claims", []))
                        for n in layer.get("nodes", []) for w in (n.get("worked_examples") or []))
    crossplot_rel = None
    if has_crossplot or ch.prefix in ("latyshova", "vendelshtein"):
        cp_file = assets_img_dir / "fig_69_crossplot.svg"
        render_crossplot_p69_svg(sg, cp_file)
        crossplot_rel = "assets/img/fig_69_crossplot.svg"

    volve_rel = None
    if volve:
        vl_file = assets_img_dir / "volve_15_9_f1b.svg"
        render_volve_well_log_svg(volve, sg, vl_file)
        volve_rel = "assets/img/volve_15_9_f1b.svg"

    for idx, n in enumerate(layer.get("nodes", []), 1):
        nid = n["id"]
        step_id = nid
        claim_ids = [c["id"] for c in n.get("claims", [])]

        # Основная иллюстрация шага
        if n.get("decision_on_data") and volve_rel:
            items.append(
                {
                    "id": f"ill.{idx:02d}.volve",
                    "step_id": step_id,
                    "node_id": nid,
                    "file": volve_rel,
                    "frame_class": "A",
                    "origin": "code_from_data",
                    "label": "реальные данные",
                    "caption": "Реальные каротажные интервалы скважины Volve 15/9-F-1 B (GR, RT, SW).",
                    "alt": "Три интервала каротажа Volve 15/9-F-1 B: нефтеносный коллектор A, плотный неколлектор B и водоносный песок C.",
                    "source": "Equinor Volve open dataset, 15/9-F-1 B (CC BY 4.0)",
                    "claim_ids": claim_ids,
                }
            )
        elif crossplot_rel and (idx == 1 or any("69" in (w.get("figure") or "") for w in (n.get("worked_examples") or []))):
            items.append(
                {
                    "id": f"ill.{idx:02d}.crossplot",
                    "step_id": step_id,
                    "node_id": nid,
                    "file": crossplot_rel,
                    "frame_class": "A",
                    "origin": "redrawn_from_book",
                    "label": "схема",
                    "caption": "Зависимость удельного сопротивления ρп от пористости kп (перерисовано по рис. 69, стр. 98).",
                    "alt": "Кроссплот ρп от kп с границей коллектор-неколлектор kп*=17% и областями нефти, нефти с водой и воды.",
                    "source": "перерисовано по рис. 69, стр. 98",
                    "claim_ids": claim_ids,
                }
            )
            # И добавим параметрическую схему процесса B для первого шага или если нужна вторая картинка
            diag_file = assets_img_dir / f"node_{idx:02d}.svg"
            fclass, origin, lbl = render_node_diagram_svg(n, sg, diag_file)
            items.append(
                {
                    "id": f"ill.{idx:02d}.diag",
                    "step_id": step_id,
                    "node_id": nid,
                    "file": f"assets/img/node_{idx:02d}.svg",
                    "frame_class": fclass,
                    "origin": origin,
                    "label": lbl,
                    "caption": f"Модель процесса для шага «{n['title']}».",
                    "alt": f"Схема процесса: {n['title']}.",
                    "source": "построено кодом из модели состояния",
                    "claim_ids": claim_ids,
                }
            )
        else:
            diag_file = assets_img_dir / f"node_{idx:02d}.svg"
            fclass, origin, lbl = render_node_diagram_svg(n, sg, diag_file)
            items.append(
                {
                    "id": f"ill.{idx:02d}.diag",
                    "step_id": step_id,
                    "node_id": nid,
                    "file": f"assets/img/node_{idx:02d}.svg",
                    "frame_class": fclass,
                    "origin": origin,
                    "label": lbl,
                    "caption": f"Расчётная схема: {n['title']}.",
                    "alt": f"Схема для шага «{n['title']}».",
                    "source": "построено кодом из модели состояния",
                    "claim_ids": claim_ids,
                }
            )

        # Если есть Volve, но в слое ещё не добавлен ни один реальный планшет A — прикрепим к последнему шагу
        if idx == len(layer.get("nodes", [])) and volve_rel and not any(it["file"] == volve_rel for it in items):
            items.append(
                {
                    "id": f"ill.{idx:02d}.volve",
                    "step_id": step_id,
                    "node_id": nid,
                    "file": volve_rel,
                    "frame_class": "A",
                    "origin": "code_from_data",
                    "label": "реальные данные",
                    "caption": "Практикум на реальных данных Volve 15/9-F-1 B: сопоставление GR, RT, PHIF и SW.",
                    "alt": "Графики GR, RT и SW по трём 5-метровым интервалам скважины Volve 15/9-F-1 B.",
                    "source": "Equinor Volve open dataset, 15/9-F-1 B (CC BY 4.0)",
                    "claim_ids": claim_ids,
                }
            )

    doc = {"chapter": ch.id, "illustrations": items}
    schema = json.loads((cfg.schemas_dir / "illustration.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(doc, schema)
    (ch.build_dir / "illustrations.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return doc
