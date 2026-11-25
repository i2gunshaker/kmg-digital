"""Параметрические рисунки классов A и B: кривые, разрезы, рифы, поры, схемы порядка действий.

Рисунок задаётся спецификацией: имя шаблона и параметры. Один и тот же шаблон рисует и картинку шага,
и кадры с ползунком. Ничего не дорисовывается: форма каждой линии вычислена из параметров.
"""
from __future__ import annotations

import ast
import html
import math
import re
from contextlib import contextmanager
from typing import Any, Callable, Iterator

W = 560  # ширина полотна; для телефона те же шаблоны рисуются на полотне W_PHONE
W_PHONE = 400
PAD = 18
FS = 17  # основной размер шрифта


@contextmanager
def canvas_width(w: int) -> Iterator[None]:
    """Ширина полотна на время отрисовки: шаблоны сами переносят подписи под неё."""
    global W
    old, W = W, w
    try:
        yield
    finally:
        W = old


class SpecError(ValueError):
    """Спецификация рисунка с ошибкой: неизвестный шаблон, параметр или выражение."""


# ---------------------------------------------------------------- выражения


def _exp_integral(x: float) -> float:
    """−Ei(−x) = E1(x), x > 0."""
    if x <= 0:
        return 40.0
    if x <= 1.0:
        s, term, n = -0.5772156649015329 - math.log(x), 1.0, 1
        while n < 60:
            term *= -x / n
            s -= term / n
            n += 1
        return s
    # непрерывная дробь (метод Лентца)
    b = x + 1.0
    c = 1e300
    d = 1.0 / b
    h = d
    for i in range(1, 200):
        an = -i * i
        b += 2.0
        d = 1.0 / (an * d + b)
        c = b + an / c
        delta = c * d
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return h * math.exp(-x)


_FUNCS: dict[str, Callable[..., float]] = {
    "sqrt": lambda v: math.sqrt(max(v, 0.0)),
    "exp": lambda v: math.exp(max(min(v, 700.0), -700.0)),
    "ln": lambda v: math.log(max(v, 1e-300)),
    "lg": lambda v: math.log10(max(v, 1e-300)),
    "erf": math.erf,
    "erfc": math.erfc,
    "e1": _exp_integral,
    "abs": abs,
    "min": min,
    "max": max,
    "sin": math.sin,
    "cos": math.cos,
    "tanh": math.tanh,
}
_BIN = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b if b else math.inf,
    ast.Pow: lambda a, b: a**b,
}
_CMP = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b}


def compile_expr(src: str) -> Callable[[dict[str, float]], float]:
    """Формула из спецификации: только числа, имена параметров и список функций, без вызова кода."""
    try:
        tree = ast.parse(src.replace("^", "**"), mode="eval").body
    except SyntaxError as e:
        raise SpecError(f"не разобрал выражение {src!r}") from e

    def ev(n: ast.AST, env: dict[str, float]) -> float:
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id == "pi":
                return math.pi
            if n.id in env:
                return float(env[n.id])
            raise SpecError(f"неизвестное имя {n.id!r} в {src!r}")
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand, env)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            return _BIN[type(n.op)](ev(n.left, env), ev(n.right, env))
        if isinstance(n, ast.IfExp) and isinstance(n.test, ast.Compare) and len(n.test.ops) == 1 and type(n.test.ops[0]) in _CMP:
            ok = _CMP[type(n.test.ops[0])](ev(n.test.left, env), ev(n.test.comparators[0], env))
            return ev(n.body if ok else n.orelse, env)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            return float(_FUNCS[n.func.id](*[ev(a, env) for a in n.args]))
        raise SpecError(f"недопустимое выражение {src!r}")

    ev(tree, _Probe())  # имена проверяются при отрисовке, запрещённые конструкции — сразу
    return lambda env: ev(tree, env)


class _Probe(dict):
    def __contains__(self, key: object) -> bool:
        return True

    def __getitem__(self, key: str) -> float:
        return 1.0


# ---------------------------------------------------------------- полотно


def esc(s: Any) -> str:
    return html.escape(str(s), quote=True)


def text_w(s: str, size: float = FS, bold: bool = False) -> float:
    k = 0.0
    for ch in s:
        if ch in "ilI.,:;|!'()[] \u00a0·tfrj":
            k += 0.33
        elif ch in "mwMWШЩЖФЮМшщжфюмы@%—":
            k += 0.82
        elif ch.isupper():
            k += 0.68
        else:
            k += 0.56
    return k * size * (1.06 if bold else 1.0)


def _nbsp(s: str) -> str:
    """Число не отрывается от единицы, знак сравнения — от значения."""
    s = re.sub(r"(\d) (?=(?:%|°|м|см|мм|км|Ом|МПа|с|ч|сут|раз|лет)(?![А-Яа-яё]))", "\\1\u00a0", s)
    return re.sub(r" ([≤≥=<>≈~]) ", "\u00a0\\1\u00a0", s)


def wrap(s: str, width: float, size: float = FS, bold: bool = False) -> list[str]:
    out: list[str] = []
    line = ""
    words: list[str] = []
    for word in _nbsp(str(s)).split(" "):
        if "-" in word and text_w(word, size, bold) > width:
            bits = word.split("-")
            words += [b + "-" for b in bits[:-1]] + [bits[-1]]
        else:
            words.append(word)
    for word in words:
        glue = "" if line.endswith("-") and len(line) > 1 and line[-2] != " " and len(words) > 1 else " "
        probe = f"{line}{glue}{word}".strip()
        if line and text_w(probe, size, bold) > width:
            out.append(line)
            line = word
        else:
            line = probe
    if line:
        out.append(line)
    return out or [""]


def _lum(hexcol: str) -> float:
    v = [int(hexcol[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    v = [q / 12.92 if q <= 0.03928 else ((q + 0.055) / 1.055) ** 2.4 for q in v]
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]


def readable(hexcol: str, bg: str = "#1B222B", need: float = 5.0) -> str:
    """Цвет для текста: осветляется, пока контраст с панелью не станет достаточным."""
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", hexcol):
        return hexcol
    rgb = [int(hexcol[i : i + 2], 16) for i in (1, 3, 5)]
    lb = _lum(bg)
    for _ in range(24):
        cur = "#%02X%02X%02X" % tuple(rgb)
        if (max(_lum(cur), lb) + 0.05) / (min(_lum(cur), lb) + 0.05) >= need:
            return cur
        rgb = [min(255, round(v + (255 - v) * 0.12)) for v in rgb]
    return "#FFFFFF"


class Canvas:
    def __init__(self, sg: dict) -> None:
        self.sg = sg
        self.parts: list[str] = []
        self.y = 0.0

    # краски стайлгайда
    @property
    def ink(self) -> str:
        return self.sg["text"]

    @property
    def muted(self) -> str:
        return "#AEB9C5"  # muted стайлгайда на тёмном фоне не проходит по контрасту для мелкого текста

    def color(self, key: str | None, default: str = "#ECEFF4") -> str:
        if not key:
            return default
        if key.startswith("#"):
            return key
        for group in ("lithology", "fluids", "curves"):
            if key in self.sg.get(group, {}):
                return self.sg[group][key]["color"]
        if key == "uncertainty":
            return self.sg["uncertainty"]["color"]
        if key == "measurement":
            return self.sg["measurement"]["color"]
        return {"accent": "#4DC4BF", "ok": "#2ECC71", "bad": "#F27979", "warn": "#F0AD4E", "ink": self.ink, "muted": self.muted}.get(key, default)

    def raw(self, s: str) -> None:
        self.parts.append(s)

    def rect(self, x, y, w, h, fill="none", stroke="none", sw=1.2, rx=0, op=1.0, dash=None) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.raw(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{max(h, 0):.1f}" rx="{rx}" fill="{fill}" fill-opacity="{op}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def line(self, x1, y1, x2, y2, stroke="#ECEFF4", sw=1.5, dash=None, marker=None) -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        m = f' marker-end="url(#{marker})"' if marker else ""
        self.raw(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{sw}"{d}{m}/>')

    def poly(self, pts, fill="none", stroke="none", sw=2.0, op=1.0, dash=None, close=False) -> None:
        tag = "polygon" if close else "polyline"
        d = f' stroke-dasharray="{dash}"' if dash else ""
        p = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        self.raw(f'<{tag} points="{p}" fill="{fill}" fill-opacity="{op}" stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="round"{d}/>')

    def circle(self, x, y, r, fill="none", stroke="none", sw=1.2, op=1.0) -> None:
        self.raw(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{fill}" fill-opacity="{op}" stroke="{stroke}" stroke-width="{sw}"/>')

    def text(self, x, y, s, size=FS, fill=None, anchor="start", bold=False, halo=False, rotate=None) -> None:
        st = ' stroke="#0F1419" stroke-width="4" paint-order="stroke" stroke-linejoin="round"' if halo else ""
        if fill and fill not in ("#0F1419", "#FFFFFF"):
            fill = readable(fill)
        tr = f' transform="rotate({rotate} {x:.1f} {y:.1f})"' if rotate is not None else ""
        self.raw(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill or self.ink}" text-anchor="{anchor}"'
            f'{" font-weight=" + chr(34) + "700" + chr(34) if bold else ""}{st}{tr}>{esc(s)}</text>'
        )

    def text_block(self, x, y, s, width, size=FS, fill=None, anchor="start", bold=False, lh=1.28, halo=False) -> float:
        """Печатает текст с переносом, возвращает y под последней строкой."""
        for ln in wrap(s, width, size, bold):
            self.text(x, y, ln, size, fill, anchor, bold, halo)
            y += size * lh
        return y


def _lines_h(s: str, width: float, size: float = FS, bold: bool = False, lh: float = 1.28) -> float:
    return len(wrap(s, width, size, bold)) * size * lh


def _finish(c: Canvas, spec: dict, body_h: float, top: float) -> str:
    sg = c.sg
    label = spec.get("label") or "схема"
    pill = sg["provenance"].get(label, "#8E44AD")
    head: list[str] = []
    tmp = Canvas(sg)
    pw = text_w(label, 16, True) + 24
    tmp.rect(PAD, 14, pw, 28, fill=pill, rx=14)
    tmp.text(PAD + pw / 2, 33, label, 16, "#FFFFFF", "middle", True)
    if spec.get("scale"):
        for i, ln in enumerate(wrap(spec["scale"], W - PAD * 3 - pw, 16)[:2]):
            tmp.text(PAD + pw + 12, 33 + i * 19, ln, 16, c.muted)
    y = 70.0 if not spec.get("scale") or len(wrap(spec["scale"], W - PAD * 3 - pw, 16)) < 2 else 84.0
    for ln in wrap(spec["title"], W - 2 * PAD, 19, True):
        tmp.text(PAD, y, ln, 19, c.ink, "start", True)
        y += 25
    head = tmp.parts
    off = y - 8
    foot = Canvas(sg)
    fy = off + body_h + 22
    if spec.get("note"):
        fy = foot.text_block(PAD, fy, spec["note"], W - 2 * PAD, 16, c.ink)
    if spec.get("source"):
        fy = foot.text_block(PAD, fy + (3 if spec.get("note") else 0), spec["source"], W - 2 * PAD, 16, c.muted)
    total = fy + 4
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {total:.0f}" width="{W}" height="{total:.0f}" role="img" '
        f'aria-label="{esc(spec["title"])}" font-family="\'Segoe UI\',\'Noto Sans\',system-ui,-apple-system,Roboto,sans-serif">\n'
        f'<title>{esc(spec["title"])}</title>\n'
        '<defs>'
        '<marker id="ar" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 1L10 5L0 9z" fill="#4DC4BF"/></marker>'
        '<marker id="aw" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 1L10 5L0 9z" fill="#ECEFF4"/></marker>'
        '<pattern id="hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" stroke="#95A5A6" stroke-width="2"/></pattern>'
        '<pattern id="dots" width="10" height="10" patternUnits="userSpaceOnUse"><circle cx="3" cy="3" r="1.3" fill="#0F1419"/><circle cx="8" cy="8" r="1.1" fill="#0F1419"/></pattern>'
        '<pattern id="dash" width="14" height="8" patternUnits="userSpaceOnUse"><line x1="1" y1="4" x2="9" y2="4" stroke="#0F1419" stroke-width="1.4"/></pattern>'
        '<pattern id="brick" width="24" height="14" patternUnits="userSpaceOnUse"><path d="M0 0H24M0 7H24M6 0V7M18 7V14" stroke="#0F1419" stroke-width="1.1" fill="none"/></pattern>'
        "</defs>\n"
        f'<rect width="{W}" height="{total:.0f}" rx="10" fill="{sg["background"]}"/>\n'
        + "\n".join(head)
        + f'\n<g transform="translate(0 {off:.1f})">\n'
        + "\n".join(c.parts)
        + "\n</g>\n"
        + "\n".join(foot.parts)
        + "\n</svg>\n"
    )


# ---------------------------------------------------------------- шаблоны


def _fmt(v: float) -> str:
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e5 or a < 1e-3:
        e = int(math.floor(math.log10(a)))
        m = v / 10**e
        sup = str(e).translate(str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹"))
        return f"10{sup}" if abs(m - 1) < 1e-9 else f"{m:g}·10{sup}".replace(".", ",")
    return f"{v:.6g}".replace(".", ",")


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    span = hi - lo
    raw = span / max(n, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw - 1e-12), default=raw)
    t = math.ceil(lo / step - 1e-9) * step
    out = []
    while t <= hi + 1e-9 * span:
        out.append(round(t, 10))
        t += step
    return out


def _grid(a: float, b: float, log: bool, n: int) -> list[float]:
    if log:
        la, lb = math.log10(a), math.log10(b)
        return [10 ** (la + (lb - la) * k / n) for k in range(n + 1)]
    return [a + (b - a) * k / n for k in range(n + 1)]


def _interp(pts: list, x: float, xlog: bool, ylog: bool) -> float:
    """Ломаная по оцифрованным точкам в координатах осей, чтобы прямые на лог-шкале остались прямыми."""
    fx = (lambda v: math.log10(max(v, 1e-12))) if xlog else float
    fy = (lambda v: math.log10(max(v, 1e-12))) if ylog else float
    if x <= pts[0][0]:
        return float(pts[0][1])
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            t = (fx(x) - fx(x0)) / (fx(x1) - fx(x0))
            v = fy(y0) + (fy(y1) - fy(y0)) * t
            return 10**v if ylog else v
    return float(pts[-1][1])


def _smooth(pts: list, xlog: bool, ylog: bool) -> Callable[[float], float]:
    """Монотонный кубический сплайн (Фритш — Карлсон): проходит через точки и не выдумывает экстремумов."""
    if len(pts) < 3:
        return lambda v: _interp(pts, v, xlog, ylog)
    fx = (lambda v: math.log10(max(v, 1e-12))) if xlog else float
    fy = (lambda v: math.log10(max(v, 1e-12))) if ylog else float
    xs, ys = [fx(q[0]) for q in pts], [fy(q[1]) for q in pts]
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    m = [d[0]] + [0.0 if d[i - 1] * d[i] <= 0 else 3 * (h[i - 1] + h[i]) / ((2 * h[i] + h[i - 1]) / d[i - 1] + (h[i] + 2 * h[i - 1]) / d[i]) for i in range(1, n - 1)] + [d[-1]]

    def f(v: float) -> float:
        q = min(max(fx(v), xs[0]), xs[-1])
        i = min(max(next((k for k in range(n - 1) if q <= xs[k + 1]), n - 2), 0), n - 2)
        t = (q - xs[i]) / h[i]
        r = (
            ys[i] * (1 + 2 * t) * (1 - t) ** 2
            + h[i] * m[i] * t * (1 - t) ** 2
            + ys[i + 1] * t * t * (3 - 2 * t)
            + h[i] * m[i + 1] * t * t * (t - 1)
        )
        return 10**r if ylog else r

    return f


def draw_curves(c: Canvas, p: dict) -> float:
    """Семейство кривых y(x) по формулам; пороги, зоны, точки."""
    x, y = p["x"], p["y"]
    ph = float(p.get("height", 300))
    left, right, top = 66.0, 20.0, 26.0
    pw = W - left - right
    xl, xh, yl, yh = float(x["min"]), float(x["max"]), float(y["min"]), float(y["max"])
    if xh <= xl or yh <= yl:
        raise SpecError("пустой диапазон осей")

    def sx(v: float) -> float:
        if x.get("log"):
            return left + (math.log10(max(v, 1e-12)) - math.log10(xl)) / (math.log10(xh) - math.log10(xl)) * pw
        return left + (v - xl) / (xh - xl) * pw

    def sy(v: float) -> float:
        if y.get("log"):
            q = (math.log10(max(v, 1e-12)) - math.log10(yl)) / (math.log10(yh) - math.log10(yl))
        else:
            q = (v - yl) / (yh - yl)
        return top + (q if y.get("down") else 1 - q) * ph

    c.rect(left, top, pw, ph, fill=c.sg["panel"], stroke="#3A4754")
    for b in p.get("bands", []):
        x0, x1 = sx(max(float(b.get("x0", xl)), xl)), sx(min(float(b.get("x1", xh)), xh))
        c.rect(min(x0, x1), top, abs(x1 - x0), ph, fill=c.color(b.get("color"), "#95A5A6"), op=0.2)
    for b in p.get("hbands", []):
        y0, y1 = sy(min(max(float(b["y0"]), yl), yh)), sy(min(max(float(b["y1"]), yl), yh))
        c.rect(left, min(y0, y1), pw, abs(y1 - y0), fill=c.color(b.get("color"), "#95A5A6"), op=0.2)
    xt = x.get("ticks") or ([10**k for k in range(math.ceil(math.log10(xl) - 1e-9), math.floor(math.log10(xh) + 1e-9) + 1)] if x.get("log") else _ticks(xl, xh))
    yt = y.get("ticks") or ([10**k for k in range(math.ceil(math.log10(yl) - 1e-9), math.floor(math.log10(yh) + 1e-9) + 1)] if y.get("log") else _ticks(yl, yh, 4))
    if x.get("ticks") != [] and not x.get("qualitative"):
        for t in xt:
            c.line(sx(t), top, sx(t), top + ph, stroke="#2B3642", sw=1)
            c.text(sx(t), top + ph + 21, _fmt(t), 16, c.muted, "middle")
    if y.get("ticks") != [] and not y.get("qualitative"):
        for t in yt:
            c.line(left, sy(t), left + pw, sy(t), stroke="#2B3642", sw=1)
            c.text(left - 8, sy(t) + 6, _fmt(t), 16, c.muted, "end")
    c.text(left + pw / 2, top + ph + (26 if x.get("qualitative") or x.get("ticks") == [] else 44), x["label"], 17, c.ink, "middle")
    c.text(20, top + ph / 2, y["label"], 17, c.ink, "middle", rotate=-90)

    env0 = {k: float(v) for k, v in (p.get("vars") or {}).items()}
    xlog, ylog = bool(x.get("log")), bool(y.get("log"))
    for ar in p.get("areas", []):
        a, b = float(ar.get("x0", xl)), float(ar.get("x1", xh))
        up, lo_ = _smooth(ar["upper"], xlog, ylog), _smooth(ar["lower"], xlog, ylog)
        pa = [(sx(v), sy(up(v))) for v in _grid(a, b, xlog, 60)]
        pb = [(sx(v), sy(lo_(v))) for v in _grid(a, b, xlog, 60)]
        c.poly(pa + pb[::-1], fill=c.color(ar.get("color"), "#95A5A6"), op=float(ar.get("op", 0.3)), close=True)
    ends: list[tuple[float, float, str, str]] = []
    palette = ["#4DC4BF", "#F1C40F", "#E67E22", "#9BD7F5", "#A98FD1", "#2ECC71"]
    for i, s in enumerate(p.get("series", [])):
        col = c.color(s.get("color"), palette[i % len(palette)])
        env = {**env0, **{k: float(v) for k, v in (s.get("vars") or {}).items()}}
        if s.get("points"):
            dig = s["points"]
            fn = (lambda e, f=_smooth(dig, xlog, ylog): f(e["x"])) if s.get("smooth", True) else (lambda e, dig=dig: _interp(dig, e["x"], xlog, ylog))
            a, b = s.get("domain") or [max(dig[0][0], xl), min(dig[-1][0], xh)]
        else:
            fn = compile_expr(str(s["expr"]))
            a, b = s.get("domain") or [xl, xh]
        pts: list[tuple[float, float]] = []
        n = 160
        for k in range(n + 1):
            xv = 10 ** (math.log10(a) + (math.log10(b) - math.log10(a)) * k / n) if x.get("log") else a + (b - a) * k / n
            try:
                yv = fn({**env, "x": xv})
            except (OverflowError, ZeroDivisionError, ValueError):
                continue
            if not math.isfinite(yv) or yv < yl - 1e-9 * (yh - yl) or yv > yh + 1e-9 * (yh - yl):
                if pts:
                    c.poly(pts, stroke=col, sw=s.get("sw", 2.6), dash=s.get("dash"))
                    pts = []
                continue
            pts.append((sx(xv), sy(yv)))
        if len(pts) > 1:
            c.poly(pts, stroke=col, sw=s.get("sw", 2.6), dash=s.get("dash"))
            if s.get("label"):
                at = s.get("label_at")
                k = min(range(len(pts)), key=lambda j: abs(pts[j][0] - sx(float(at)))) if at is not None else len(pts) - 1
                a0, b0 = pts[max(k - 4, 0)], pts[min(k + 4, len(pts) - 1)]
                slope = (b0[1] - a0[1]) / max(b0[0] - a0[0], 1e-6)  # < 0 — кривая растёт вправо
                dy = 22.0 if slope < -0.12 and at is not None else -8.0
                side = s.get("label_side")
                if side in ("above", "below"):  # подпись слева от точки: над кривой или под ней
                    w = text_w(s["label"], 16, True)
                    ends.append((pts[k][0] - w - 10, pts[k][1] + (-8 if side == "above" else 20), s["label"], col))
                else:
                    ends.append((pts[k][0] + (4 if dy > 0 else 0), pts[k][1] + dy, s["label"], col))
    for ar in p.get("areas", []):
        if ar.get("label"):
            half = text_w(ar["label"], 16, True) / 2
            c.text(min(max(sx(float(ar["label_x"])), left + half + 2), left + pw - half - 2), sy(float(ar["label_y"])), ar["label"], 16, c.ink, "middle", True, halo=True)
    for v in p.get("vlines", []):
        c.line(sx(v["x"]), top, sx(v["x"]), top + ph, stroke=c.color(v.get("color"), "#ECEFF4"), sw=1.8, dash="6,4")
    for v in p.get("hlines", []):
        c.line(left, sy(v["y"]), left + pw, sy(v["y"]), stroke=c.color(v.get("color"), "#ECEFF4"), sw=1.8, dash="6,4")
    for pt in p.get("points", []):
        col = c.color(pt.get("color"), "#F1C40F")
        c.circle(sx(pt["x"]), sy(pt["y"]), 7, fill=col, stroke="#0F1419", sw=2)
        if pt.get("label"):
            w = text_w(pt["label"], 16, True)
            right_side = sx(pt["x"]) + 12 + w < left + pw
            c.text(sx(pt["x"]) + (12 if right_side else -12), sy(pt["y"]) + float(pt.get("dy", -8)), pt["label"], 16, c.ink, "start" if right_side else "end", True, halo=True)
    # подписи зон и порогов — поверх кривых
    for b in p.get("bands", []):
        if b.get("label"):
            x0, x1 = sx(max(float(b.get("x0", xl)), xl)), sx(min(float(b.get("x1", xh)), xh))
            yy = top + 22 + float(b.get("dy", 0))
            for ln in wrap(b["label"], max(abs(x1 - x0) - 8, 60), 16, True):
                half = text_w(ln, 16, True) / 2
                c.text(min(max((x0 + x1) / 2, left + half + 2), left + pw - half - 2), yy, ln, 16, c.ink, "middle", True, halo=True)
                yy += 20
    for b in p.get("hbands", []):
        if b.get("label"):
            c.text(left + pw - 8, (sy(min(max(float(b["y0"]), yl), yh)) + sy(min(max(float(b["y1"]), yl), yh))) / 2 + 6, b["label"], 16, c.ink, "end", True, halo=True)
    for v in p.get("vlines", []):
        if v.get("label"):
            w = text_w(v["label"], 16)
            left_side = sx(v["x"]) + 6 + w > left + pw
            c.text(sx(v["x"]) + (-6 if left_side else 6), top + ph - 10 - float(v.get("dy", 0)), v["label"], 16, c.ink, "end" if left_side else "start", halo=True)
    for v in p.get("hlines", []):
        if v.get("label"):
            c.text(left + pw - 6, sy(v["y"]) - 7 + float(v.get("dy", 0)), v["label"], 16, c.ink, "end", halo=True)
    if p.get("legend"):  # кривые сходятся: подписи столбцом в свободном углу
        wide = max((text_w(e[2], 16, True) for e in ends), default=0)
        lx = left + pw - wide - 46 if p["legend"] == "br" else left + 12
        for k, (_, _, lab, col) in enumerate(ends):
            ly = top + ph - 16 - 22 * (len(ends) - 1 - k)
            c.line(lx, ly - 5, lx + 26, ly - 5, stroke=col, sw=3)
            c.text(lx + 34, ly, lab, 16, col, "start", True, halo=True)
        ends = []
    # подписи кривых: раздвигаем по вертикали, чтобы не слипались
    ends.sort(key=lambda e: e[1])
    last = -1e9
    for ex, ey, lab, col in ends:
        yy = max(ey, last + 20, top + 16)
        last = yy
        w = text_w(lab, 16, True)
        anchor, xx = ("end", ex - 4) if ex + w > left + pw else ("start", ex + 4)
        if anchor == "end" and xx - w < left:
            anchor, xx = "start", left + 6
        c.text(xx, yy, lab, 16, col, anchor, True, halo=True)
    return top + ph + (38 if x.get("qualitative") or x.get("ticks") == [] else 54)


_LITH_PATTERN = {"sand": "dots", "shale": "dash", "limestone": "brick", "dolomite": "brick", "reef": "brick", "dense": "hatch"}


def draw_layers(c: Canvas, p: dict) -> float:
    """Разрез по глубине: пласты, флюид, зона проникновения и корка, контакты, кривые рядом."""
    layers = p["layers"]
    tracks = p.get("tracks", [])
    total = sum(float(l["h"]) for l in layers)
    H = float(p.get("height", 320))
    top = 34.0 if tracks or (p.get("well") or {}).get("label") else 12.0
    left = 46.0
    n_tr = len(tracks)
    sec_w = (W - left - PAD) if not n_tr else max(220.0, W - left - PAD - n_tr * 92 - 6)
    well = p.get("well")
    wx = left + float(well.get("x", 0.2)) * sec_w if well else None
    ww = 26.0
    c.line(left - 18, top, left - 18, top + H, stroke=c.muted, sw=1.6, marker="aw")
    c.text(left - 26, top + H / 2, p.get("depth_label", "глубина"), 16, c.muted, "middle", rotate=-90)
    y = top
    tops: list[tuple[float, float]] = []
    for l in layers:
        h = float(l["h"]) / total * H
        col = c.color(l.get("lith"), "#8C7B6B")
        c.rect(left, y, sec_w, h, fill=col, op=float(l.get("op", 0.55)))
        pat = _LITH_PATTERN.get(str(l.get("lith")))
        if pat:
            c.raw(f'<rect x="{left:.1f}" y="{y:.1f}" width="{sec_w:.1f}" height="{h:.1f}" fill="url(#{pat})" fill-opacity="0.35"/>')
        if l.get("fluid"):
            c.rect(left, y, sec_w, h, fill=c.color(l["fluid"]), op=0.42)
        for gf in l.get("fluids", []):  # доли пласта сверху вниз: [{"to": 0.6, "fluid": "oil"}]
            c.rect(left, y + h * float(gf.get("from", 0)), sec_w, h * (float(gf["to"]) - float(gf.get("from", 0))), fill=c.color(gf["fluid"]), op=0.45)
        c.line(left, y, left + sec_w, y, stroke="#0F1419", sw=1.2)
        if well and l.get("invasion"):
            iw = float(l["invasion"]) * (sec_w * 0.5)
            for side in (-1, 1):
                x0 = wx - ww / 2 - iw if side < 0 else wx + ww / 2
                x0c = max(x0, left)
                c.rect(x0c, y, min(iw, left + sec_w - x0c) if side > 0 else (wx - ww / 2) - x0c, h, fill=c.color("filtrate"), op=0.72)
        tops.append((y, h))
        y += h
    if well:
        # ствол: профиль по диаметру слоёв (каверна > 1, сужение < 1)
        for (ty, th), l in zip(tops, layers):
            d = ww * float(l.get("caliper", 1.0))
            c.rect(wx - d / 2, ty, d, th, fill=c.color("mud"), op=1)
            if l.get("cake"):
                ck = float(l["cake"]) * 10
                for side in (-1, 1):
                    c.rect(wx - ww / 2 if side < 0 else wx + ww / 2 - ck, ty, ck, th, fill="#3B2F23", op=1)
        if well.get("tool") is not None:
            ty = top + float(well["tool"]) * H
            c.rect(wx - 6, top, 2, ty - top, fill="#BDC3C7")
            c.rect(wx - 7, ty - 16, 14, 32, fill=c.color("measurement"), rx=4)
        if well.get("label"):
            c.text(min(max(wx, left + text_w(well["label"], 16) / 2), left + sec_w), top - 10, well["label"], 16, c.muted, "middle")
    label_x = left + sec_w - 8
    for (ty, th), l in zip(tops, layers):
        if l.get("label"):
            lines = wrap(l["label"], sec_w * 0.62, 16, True)
            yy = ty + th / 2 + 6 - (len(lines) - 1) * 10
            for ln in lines:
                c.text(label_x, yy, ln, 16, c.ink, "end", True, halo=True)
                yy += 20
        if well and l.get("inv_label") and l.get("invasion"):
            for k, ln in enumerate(wrap(l["inv_label"], left + sec_w - (wx + ww / 2 + 6) - 4, 16)):
                c.text(wx + ww / 2 + 6, ty + 18 + 19 * k, ln, 16, c.ink, "start", halo=True)
    for k in p.get("contacts", []):
        ky = top + float(k["at"]) * H
        c.line(left, ky, left + sec_w, ky, stroke=c.color(k.get("color"), "#ECEFF4"), sw=2.2, dash=k.get("dash", "8,5"))
        c.text(left + 8, ky - 7 + float(k.get("dy", 0)), k["label"], 16, c.ink, "start", True, halo=True)
    for z in p.get("zones", []):
        y0, y1 = top + float(z["from"]) * H, top + float(z["to"]) * H
        c.rect(left, y0, sec_w, y1 - y0, fill="url(#hatch)", op=0.45)
        c.text(left + 8, (y0 + y1) / 2 + 6, z["label"], 16, c.ink, "start", True, halo=True)
    tx = left + sec_w + 10
    for t in tracks:
        tw = 84.0
        c.rect(tx, top, tw, H, fill="#0B1015", stroke="#3A4754")
        col = c.color(t.get("color"), "#F1C40F")
        c.text(tx + tw / 2, top - 10, t["name"], 16, col, "middle", True)
        vals = t["values"]
        lo, hi = float(t.get("min", 0)), float(t.get("max", 1))

        def px(v: float) -> float:
            return tx + 6 + (min(max(float(v), lo), hi) - lo) / (hi - lo) * (tw - 12)

        pts: list[tuple[float, float]] = []
        if t.get("profile"):  # пары (доля глубины, значение)
            pts = [(px(v), top + float(d) * H) for d, v in t["profile"]]
        else:
            for (ty, th), v in zip(tops, vals):
                e = min(5.0, th * 0.2)
                pts += [(px(v), ty + e), (px(v), ty + th - e)]
        c.poly(pts, stroke=col, sw=2.6, dash=t.get("dash"))
        if t.get("second"):
            pts2: list[tuple[float, float]] = []
            for (ty, th), v in zip(tops, t["second"]):
                e = min(5.0, th * 0.2)
                pts2 += [(px(v), ty + e), (px(v), ty + th - e)]
            c.poly(pts2, stroke=c.color(t.get("second_color"), "#E67E22"), sw=2.4, dash="6,4")
        for m in t.get("marks", []):
            c.line(px(m["v"]), top, px(m["v"]), top + H, stroke="#ECEFF4", sw=1.6, dash="5,4")
            c.text(px(m["v"]), top + H + 20, m["label"], 16, c.ink, "middle")
        tx += tw + 8
    return top + H + (28 if any(t.get("marks") for t in tracks) else 10)


_REEF = {
    # координаты в долях: x 0..1, y 0 — уровень дна, 1 — вершина
    "dome": {
        "core": [(0.34, 0), (0.40, 0.55), (0.46, 0.92), (0.50, 1), (0.54, 0.92), (0.60, 0.55), (0.66, 0)],
        "flank_l": [(0.16, 0), (0.30, 0.34), (0.46, 0.92), (0.40, 0.55), (0.34, 0)],
        "flank_r": [(0.84, 0), (0.70, 0.34), (0.54, 0.92), (0.60, 0.55), (0.66, 0)],
    },
    "flat": {
        "core": [(0.30, 0), (0.32, 0.80), (0.36, 0.9), (0.64, 0.9), (0.68, 0.80), (0.70, 0)],
        "flank_l": [(0.10, 0), (0.22, 0.4), (0.36, 0.9), (0.32, 0.80), (0.30, 0)],
        "flank_r": [(0.90, 0), (0.78, 0.4), (0.64, 0.9), (0.68, 0.80), (0.70, 0)],
    },
    "atoll": {
        "lagoon": [(0.34, 0), (0.36, 0.72), (0.64, 0.72), (0.66, 0)],
        "rim_l": [(0.22, 0), (0.24, 0.86), (0.28, 1), (0.33, 0.9), (0.36, 0.72), (0.34, 0)],
        "rim_r": [(0.78, 0), (0.76, 0.86), (0.72, 1), (0.67, 0.9), (0.64, 0.72), (0.66, 0)],
        "flank_l": [(0.04, 0), (0.14, 0.4), (0.28, 1), (0.24, 0.86), (0.22, 0)],
        "flank_r": [(0.96, 0), (0.86, 0.4), (0.72, 1), (0.76, 0.86), (0.78, 0)],
    },
    "system": {
        "front": [(0.06, -0.12), (0.20, 0.22), (0.40, 0.96), (0.36, 0.5), (0.32, 0)],
        "crest": [(0.32, 0), (0.36, 0.5), (0.40, 0.96), (0.45, 1), (0.50, 0.9), (0.52, 0.6), (0.50, 0)],
        "back": [(0.50, 0), (0.52, 0.6), (0.50, 0.9), (0.66, 0.66), (0.96, 0.56), (0.96, 0)],
    },
}
_REEF_NAMES = {"core": "ядро", "flank_l": "", "flank_r": "", "lagoon": "лагуна", "rim_l": "", "rim_r": "", "front": "предрифовый шлейф", "crest": "гребень", "back": "тыльная зона"}


def draw_reef(c: Canvas, p: dict) -> float:
    """Разрез рифовой постройки заданного типа; выделяется зона, о которой говорит шаг."""
    kind = p["kind"]
    if kind not in _REEF:
        raise SpecError(f"тип постройки {kind!r}")
    H = float(p.get("height", 230))
    top, left = 30.0, PAD + 4.0
    w = W - 2 * left
    base = top + H - 46
    amp = H - 96

    def xy(pt):
        return left + pt[0] * w, base - pt[1] * amp

    if p.get("sea", True):
        c.rect(left, top, w, base - top, fill=c.color("water"), op=0.10)
        c.line(left, top + 4, left + w, top + 4, stroke=c.color("water"), sw=2, dash="8,5")
        c.text(left + w - 4, top - 8, "уровень моря во время роста", 16, c.color("water"), "end")
    if p.get("seal"):
        cap = [xy(q) for q in p["seal_line"]] if p.get("seal_line") else None
        c.rect(left, top + 14, w, base - top - 14, fill=c.color("shale"), op=0.45)
        c.raw(f'<rect x="{left:.1f}" y="{top + 14:.1f}" width="{w:.1f}" height="{base - top - 14:.1f}" fill="url(#dash)" fill-opacity="0.4"/>')
        if cap:
            c.poly(cap, stroke="#ECEFF4")
    floor = [(left, base), (left + w, base), (left + w, base + 34), (left, base + 34)]
    if kind == "system":
        floor = [(left, base + 0.12 * amp), (left + 0.34 * w, base), (left + w, base), (left + w, base + 34), (left, base + 34)]
    c.poly(floor, fill=c.color("dense"), op=0.75, close=True)
    hl = set(p.get("highlight", []))
    names = {**_REEF_NAMES, **p.get("names", {})}
    for key, pts in _REEF[kind].items():
        group = key.split("_")[0]
        is_hl = key in hl or group in hl
        col = c.color("reef") if group in ("core", "rim", "crest") else c.color("limestone")
        c.poly([xy(q) for q in pts], fill=col, op=0.85 if group != "lagoon" else 0.55, stroke="#0F1419", sw=1.2, close=True)
        pat = "brick" if group in ("core", "rim", "crest") else "dots"
        if group in ("lagoon", "back"):
            pat = "dash"
        c.raw(f'<polygon points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in (xy(q) for q in pts))}" fill="url(#{pat})" fill-opacity="0.28"/>')
        if is_hl:
            c.poly([xy(q) for q in pts], fill=c.color("ok"), op=0.30, stroke=c.color("ok"), sw=4, close=True)
    if p.get("fill_to"):  # заполнение ловушки флюидом до контакта
        fy = base - float(p["fill_to"]) * amp
        c.line(left, fy, left + w, fy, stroke=c.color("water"), sw=2.2, dash="8,5")
        c.text(left + 4, fy - 7 if W > 480 else fy + 20, p.get("fill_label", "контакт"), 16, c.ink, "start", True, halo=True)
    for key, pts in _REEF[kind].items():
        lab = names.get(key) or ""
        if lab:
            cx = sum(q[0] for q in pts) / len(pts)
            cy = sum(q[1] for q in pts) / len(pts)
            x0, y0 = xy((cx, max(cy - 0.06, 0.12)))
            lines = wrap(lab, 150, 16, True)
            lx = min(max(x0, left + max(text_w(s, 16, True) for s in lines) / 2 + 2), left + w - max(text_w(s, 16, True) for s in lines) / 2 - 2)
            for i, ln in enumerate(lines):
                c.text(lx, y0 + i * 19, ln, 16, c.ink, "middle", True, halo=True)
    for wl in p.get("wells", []):
        wx = left + float(wl["x"]) * w
        c.line(wx, top - 2, wx, base - float(wl.get("bottom", 0.05)) * amp, stroke="#ECEFF4", sw=4)
        c.text(wx, top - 10, wl.get("label", "скв."), 16, c.ink, "middle", True, halo=True)
        if wl.get("to_x") is not None:
            c.line(wx, base - 0.6 * amp, left + float(wl["to_x"]) * w, base - 0.1 * amp, stroke="#ECEFF4", sw=3)
    if p.get("sides"):
        c.text(left, base + 56, p["sides"][0], 16, c.muted)
        c.text(left + w, base + 56, p["sides"][1], 16, c.muted, "end")
        return base + 64
    return base + 40


def draw_plan(c: Canvas, p: dict) -> float:
    """Вид сверху: зоны постройки и точки скважин."""
    kind = p["kind"]
    H = float(p.get("height", 250))
    if kind != "system":
        H = min(H, (W - 2 * PAD) / 2)
    cx, cy = W / 2, 12 + H / 2
    hl = set(p.get("highlight", []))

    def mark(x, y, lab):
        c.circle(x, y, 8, fill="#ECEFF4", stroke="#0F1419", sw=2)
        c.circle(x, y, 2.6, fill="#0F1419")
        if lab:
            fits = x + 13 + text_w(lab, 16, True) < W - 4
            c.text(x + 13 if fits else x, y + 6 if fits else y + 28, lab, 16, c.ink, "start" if fits else "middle", True, halo=True)

    def zone(rx, ry, col, on, op=0.8):
        c.raw(f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" fill="{col}" fill-opacity="{op}" stroke="{c.color("ok") if on else "#0F1419"}" stroke-width="{4 if on else 1.4}"/>')

    if kind == "dome":
        zone(H * 0.62, H * 0.46, c.color("limestone"), "flank" in hl, 0.6)
        zone(H * 0.34, H * 0.25, c.color("reef"), "core" in hl, 0.9)
        c.text(cx, cy - H * 0.33, p.get("outer", "склоны"), 16, c.ink, "middle", True, halo=True)
        c.text(cx, cy + 34, p.get("inner", "ядро"), 16, c.ink, "middle", True, halo=True)
        scale = (H * 0.62, H * 0.46)
    elif kind == "atoll":
        zone(H * 0.92, H * 0.48, c.color("limestone"), "flank" in hl, 0.6)
        zone(H * 0.74, H * 0.38, c.color("reef"), "rim" in hl, 0.9)
        zone(H * 0.44, H * 0.22, c.color("limestone"), "lagoon" in hl, 1)
        c.text(cx, cy - H * 0.27, p.get("outer", "кольцевой гребень"), 16, c.ink, "middle", True, halo=True)
        c.text(cx, cy + 6, p.get("inner", "лагуна"), 16, c.ink, "middle", True, halo=True)
        scale = (H * 0.92, H * 0.48)
    elif kind == "system":
        x0, x1 = 30.0, W - 30.0
        bands = [("front", c.color("limestone"), 0.14, 0.36, "предрифовый шлейф (к бассейну)"), ("crest", c.color("reef"), 0.36, 0.60, "гребень: вытянутая цепочка"), ("back", c.color("limestone"), 0.60, 0.88, "тыльная зона (к шельфу)")]
        for key, col, a, b, lab in bands:
            on = key in hl
            c.rect(x0, 12 + a * H, x1 - x0, (b - a) * H, fill=col, op=0.85 if key == "crest" else 0.6, stroke=c.color("ok") if on else "#0F1419", sw=4 if on else 1.2, rx=10)
            c.text(x0 + 12, 12 + a * H + 24, lab, 16, c.ink, "start", True, halo=True)
        scale = ((x1 - x0) / 2, H / 2)
    else:
        raise SpecError(f"план {kind!r}")
    for q in p.get("wells", []):
        mark(cx + float(q["x"]) * scale[0], cy + float(q["y"]) * scale[1], q.get("label", ""))
    return 12 + H + 8


def draw_flow(c: Canvas, p: dict) -> float:
    """Порядок действий или цепочка «причина → следствие» сверху вниз."""
    y = 14.0
    items = p["items"]
    colors = {"in": "#3498DB", "step": "#4DC4BF", "out": "#2ECC71", "warn": "#F0AD4E", "bad": "#F27979", "rule": "#F1C40F"}
    count = 0
    for i, it in enumerate(items):
        it = it if isinstance(it, dict) else {"text": it}
        col = colors.get(it.get("kind", "step"), "#4DC4BF")
        num = ""
        if p.get("numbered", True) and it.get("kind", "step") == "step":
            count += 1
            num = str(count)
        tw = W - 2 * PAD - 14 - (64 if num else 24)
        head_h = 21 if it.get("head") else 0
        h = max(_lines_h(it["text"], tw, FS) + head_h + 20, 52)
        c.rect(PAD, y, W - 2 * PAD, h, fill=c.sg["panel"], stroke=col, sw=2, rx=10)
        if num:
            c.circle(PAD + 32, y + h / 2, 17, fill=col)
            c.text(PAD + 32, y + h / 2 + 6, num, 18, "#0F1419", "middle", True)
        else:
            c.rect(PAD, y, 8, h, fill=col, rx=4)
        tx = PAD + (64 if num else 24)
        yy = y + 25
        if it.get("head"):
            c.text(tx, yy, it["head"], 16, col, "start", True)
            yy += 22
        c.text_block(tx, yy if head_h or h > 56 else y + h / 2 + 6, it["text"], tw, FS)
        y += h
        if i < len(items) - 1:
            c.line(W / 2, y + 2, W / 2, y + 20, stroke="#4DC4BF", sw=2.2, marker="ar")
            y += 24
    return y + 4


def draw_bars(c: Canvas, p: dict) -> float:
    """Диапазоны и значения с числами книги."""
    rows = p["rows"]
    unit = p.get("unit", "")
    lo = float(p.get("min", 0))
    hi = float(p.get("max") or max(float(r.get("hi", r.get("value", 0))) for r in rows) * 1.12)
    log = bool(p.get("log"))
    left, right = PAD, PAD
    pw = W - left - right
    y = 12.0

    def sx(v: float) -> float:
        if log:
            a, b = math.log10(max(lo, 1e-9)), math.log10(hi)
            return left + (math.log10(max(v, 1e-9)) - a) / (b - a) * pw
        return left + (v - lo) / (hi - lo) * pw

    palette = ["#4DC4BF", "#F1C40F", "#E67E22", "#9BD7F5", "#A98FD1", "#2ECC71"]
    for i, r in enumerate(rows):
        col = c.color(r.get("color"), palette[i % len(palette)])
        val = r.get("text") or (f'{_fmt(float(r["value"]))} {unit}' if "value" in r else f'{_fmt(float(r["lo"]))}–{_fmt(float(r["hi"]))} {unit}')
        room = pw - text_w(val, 17, True) - 16
        if room < 130:  # значение длинное: подпись и значение на разных строках
            y = c.text_block(left, y + 16, r["label"], pw, FS) + 22
        else:
            y = c.text_block(left, y + 16, r["label"], min(room, pw - 100), FS)
        y -= 10
        c.rect(left, y, pw, 24, fill=c.sg["panel"], rx=6)
        if "value" in r:
            c.rect(left, y, sx(float(r["value"])) - left, 24, fill=col, rx=6)
        else:
            c.rect(sx(float(r["lo"])), y, max(sx(float(r["hi"])) - sx(float(r["lo"])), 4), 24, fill=col, rx=6)
        c.text(left + pw, y - 9, val, 17, col, "end", True)
        y += 34
    if p.get("axis", True):
        for t in ([10**k for k in range(math.ceil(math.log10(max(lo, 1e-9))), math.floor(math.log10(hi)) + 1)] if log else _ticks(lo, hi, 5)):
            c.line(sx(t), y - 6, sx(t), y, stroke=c.muted, sw=1.4)
            c.text(min(max(sx(t), left + 8), left + pw - 14), y + 17, _fmt(t), 16, c.muted, "middle")
        c.text(left + pw / 2, y + 38, p.get("axis_label", unit), 16, c.muted, "middle")
        y += 46
    return y


def draw_compare(c: Canvas, p: dict) -> float:
    """Сопоставление: до трёх колонок, на узком рисунке — друг под другом."""
    cols = p["cols"]
    side = len(cols) == 2 and not p.get("stack") and W > 480
    gap = 14.0
    cw = (W - 2 * PAD - gap) / 2 if side else W - 2 * PAD
    x, y = PAD, 14.0
    max_h = 0.0
    boxes = []
    for col in cols:
        h = 18 + _lines_h(col["title"], cw - 28, 18, True) + sum(_lines_h("• " + ln, cw - 28, FS) + 5 for ln in col["lines"]) + 10
        boxes.append(h)
        max_h = max(max_h, h)
    for col, h in zip(cols, boxes):
        color = c.color(col.get("color"), "#4DC4BF")
        hh = max_h if side else h
        c.rect(x, y, cw, hh, fill=c.sg["panel"], stroke=color, sw=2, rx=10)
        yy = c.text_block(x + 14, y + 28, col["title"], cw - 28, 18, color, bold=True)
        for ln in col["lines"]:
            yy = c.text_block(x + 14, yy + 3, "• " + ln, cw - 28, FS) + 2
        if side:
            x += cw + gap
        else:
            y += hh + 12
    return (y + max_h if side else y - 12) + 6


def draw_tree(c: Canvas, p: dict) -> float:
    """Классификация: корень и ветви."""
    y = 12.0
    root_h = _lines_h(p["root"], W - 2 * PAD - 28, 18, True) + 18
    c.rect(PAD, y, W - 2 * PAD, root_h, fill="#153736", stroke="#4DC4BF", sw=2, rx=10)
    c.text_block(PAD + 14, y + 27, p["root"], W - 2 * PAD - 28, 18, bold=True)
    y += root_h
    trunk = PAD + 18
    y0 = y
    for ch in p["children"]:
        y += 14
        tw = W - PAD - (PAD + 46) - 28
        title_h = _lines_h(ch["title"], tw, FS, True)
        sub_h = sum(_lines_h(s, tw, 16) for s in ch.get("lines", []))
        h = title_h + sub_h + 18
        col = c.color(ch.get("color"), "#4DC4BF")
        c.rect(PAD + 46, y, W - 2 * PAD - 46, h, fill=c.sg["panel"], stroke=col, sw=2, rx=10)
        c.line(trunk, y + h / 2, PAD + 44, y + h / 2, stroke="#4DC4BF", sw=2.2, marker="ar")
        yy = c.text_block(PAD + 60, y + 25, ch["title"], tw, FS, col, bold=True)
        for s in ch.get("lines", []):
            yy = c.text_block(PAD + 60, yy, s, tw, 16, c.ink)
        last = y + h / 2
        y += h
    c.line(trunk, y0, trunk, last, stroke="#4DC4BF", sw=2.2)
    return y + 6


def draw_pores(c: Canvas, p: dict) -> float:
    """Поры между зёрнами: зёрна, вода-плёнка, флюид. Панели рядом показывают состояния."""
    panels = p["panels"]
    n = len(panels)
    gap = 14.0
    pw = (W - 2 * PAD - gap * (n - 1)) / n
    H = float(p.get("height", 190))
    top = 36.0
    for i, s in enumerate(panels):
        x0 = PAD + i * (pw + gap)
        c.text(x0 + pw / 2, top - 12, s.get("head", ""), 17, c.ink, "middle", True)
        cid = f"cp{i}"
        c.raw(f'<clipPath id="{cid}"><rect x="{x0:.1f}" y="{top}" width="{pw:.1f}" height="{H}" rx="8"/></clipPath><g clip-path="url(#{cid})">')
        c.rect(x0, top, pw, H, fill=c.color(s.get("fluid"), "#2E8B57"), op=0.9)
        step = pw / 3.0
        r = step * 0.5 * float(s.get("grain", 0.96))
        film = float(s.get("film", 0)) * r
        row = 0
        yy = top
        while yy < top + H + step:
            for k in range(-1, 5):
                gx = x0 + (k + (0.5 if row % 2 else 0.0)) * step
                if film:
                    c.circle(gx, yy, r + film, fill=c.color(s.get("film_color"), c.color("water")), op=1)
                c.circle(gx, yy, r, fill=c.color(s.get("lith"), "#E8C872"), stroke="#0F1419", sw=1.4)
            yy += step * 0.87
            row += 1
        for v in s.get("vugs", []):
            c.raw(f'<ellipse cx="{x0 + v[0] * pw:.1f}" cy="{top + v[1] * H:.1f}" rx="{v[2] * pw:.1f}" ry="{v[3] * H:.1f}" fill="{c.color(s.get("fluid"), "#2E8B57")}" stroke="#0F1419" stroke-width="1.6"/>')
        c.raw("</g>")
        c.rect(x0, top, pw, H, stroke="#ECEFF4", sw=1.6, rx=8)
        yb = top + H + 22
        for ln in s.get("lines", []):
            for w1 in wrap(ln, pw, 16):
                c.text(x0 + pw / 2, yb, w1, 16, c.ink, "middle")
                yb += 20
        s["_h"] = yb
    return max(s.pop("_h") for s in panels)


def draw_matrix(c: Canvas, p: dict) -> float:
    """Таблица признаков: строка — порода или случай, столбец — метод."""
    cols, rows = p["cols"], p["rows"]
    if W <= 480:
        y = 12.0
        tw = W - 2 * PAD - 28
        for r in rows:
            pairs = " · ".join(f"{a}\u00a0{b}" for a, b in zip(cols, r["cells"]) if b not in ("", "—"))
            h = _lines_h(r["label"], tw, 16, True) + _lines_h(pairs, tw, 16) + 16
            c.rect(PAD, y, W - 2 * PAD, h, fill=c.sg["panel"], stroke="#2B3642", rx=6)
            c.rect(PAD, y, 8, h, fill=c.color(r.get("lith"), "#4DC4BF"))
            yy = c.text_block(PAD + 18, y + 23, r["label"], tw, 16, bold=True)
            c.text_block(PAD + 18, yy, pairs, tw, 16)
            y += h + 8
        return y
    lw = float(p.get("label_w", 170))
    cw = (W - 2 * PAD - lw) / len(cols)
    head = max(_lines_h(s, cw - 6, 16, True) for s in cols) + 14
    y = 12.0
    c.rect(PAD, y, W - 2 * PAD, head, fill="#153736", rx=6)
    for j, s in enumerate(cols):
        lines = wrap(s, cw - 6, 16, True)
        yy = y + 22
        for ln in lines:
            c.text(PAD + lw + cw * (j + 0.5), yy, ln, 16, c.ink, "middle", True)
            yy += 20
    y += head
    mark_col = {"↑": "#F1C40F", "↓": "#9BD7F5", "—": "#AEB9C5"}
    for r in rows:
        h = max(_lines_h(r["label"], lw - 16, 16), max(_lines_h(v, cw - 8, 16) for v in r["cells"])) + 14
        c.rect(PAD, y, W - 2 * PAD, h, fill=c.sg["panel"], stroke="#2B3642")
        if r.get("lith"):
            c.rect(PAD, y, 8, h, fill=c.color(r["lith"]))
        c.text_block(PAD + 16, y + 22, r["label"], lw - 16, 16, bold=False)
        for j, v in enumerate(r["cells"]):
            yy = y + 22
            for ln in wrap(v, cw - 8, 16):
                c.text(PAD + lw + cw * (j + 0.5), yy, ln, 16, mark_col.get(v[:1], c.ink), "middle", v[:1] in mark_col)
                yy += 20
        y += h
    return y + 8


TEMPLATES: dict[str, Callable[[Canvas, dict], float]] = {
    "curves": draw_curves,
    "layers": draw_layers,
    "reef": draw_reef,
    "plan": draw_plan,
    "flow": draw_flow,
    "bars": draw_bars,
    "compare": draw_compare,
    "tree": draw_tree,
    "pores": draw_pores,
    "matrix": draw_matrix,
}


def merge_params(base: dict, over: dict | None) -> dict:
    """Параметры кадра поверх базовых; словарь vars дополняется, остальное заменяется."""
    over = over or {}
    out = {**base, **over}
    if "vars" in over:
        out["vars"] = {**base.get("vars", {}), **over["vars"]}
    return out


def phone_width(spec: dict) -> int:
    """Полотно для телефона: у разреза с несколькими кривыми оно шире, иначе кривые не поместятся."""
    n = len((spec.get("params") or {}).get("tracks") or [])
    return max(W_PHONE, 46 + 220 + PAD + 6 + 92 * n) if spec.get("template") == "layers" else W_PHONE


def _draw(spec: dict, sg: dict, params: dict | None) -> tuple[Canvas, float]:
    name = spec.get("template")
    if name not in TEMPLATES:
        raise SpecError(f"неизвестный шаблон {name!r}")
    c = Canvas(sg)
    try:
        return c, TEMPLATES[name](c, merge_params(spec.get("params", {}), params))
    except (KeyError, TypeError, IndexError) as e:
        raise SpecError(f"шаблон {name}: не хватает параметра ({e})") from e


def render_frames(spec: dict, sg: dict) -> list[tuple[str, str]]:
    """Кадры ползунка: [(подпись, SVG)]. Все кадры одного рисунка одной высоты, чтобы страница не прыгала."""
    drawn = []
    for f in spec.get("frames", []):
        over = dict(f.get("params") or {})
        if f.get("vars"):
            over["vars"] = {**over.get("vars", {}), **f["vars"]}
        drawn.append((str(f["label"]), *_draw(spec, sg, over)))
    top = max((h for _, _, h in drawn), default=0.0)
    return [(label, _finish(c, spec, top, 0.0)) for label, c, _ in drawn]


def render(spec: dict, sg: dict, params: dict | None = None) -> str:
    """SVG по спецификации; params поверх spec['params'] — для кадров с ползунком."""
    c, h = _draw(spec, sg, params)
    return _finish(c, spec, h, 0.0)
