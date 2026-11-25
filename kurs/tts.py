"""Озвучка: текст для диктора, синтез, громкость и проверка распознаванием.

1. Текст для озвучки готовит код, а не модель: числа и единицы словами с согласованием
   («1,5 МПа» → «одна целая пять десятых мегапаскаля», «5–25 %» → «от пяти до двадцати пяти процентов»),
   аббревиатуры — по доменному словарю произношения, ударения — по словарю ударений.
   Субтитры строятся из исходного проверенного текста с цифрами, а не из этого.
2. Синтез: edge-tts (по умолчанию), Silero (офлайн, если установлен torch), ElevenLabs (если есть ключ).
3. Громкость: ffmpeg loudnorm в два прохода до −16 LUFS.
4. Проверка: распознавание faster-whisper или mlx-whisper, WER по основам слов и сдвиг начала фраз.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


# ---------------------------------------------------------------- числа словами
_U = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять", "десять", "одиннадцать", "двенадцать",
      "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_U_F = {1: "одна", 2: "две"}
_T = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
_H = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]
# родительный падеж: «от пяти до двадцати пяти»
_U_G = ["", "одного", "двух", "трёх", "четырёх", "пяти", "шести", "семи", "восьми", "девяти", "десяти", "одиннадцати", "двенадцати",
        "тринадцати", "четырнадцати", "пятнадцати", "шестнадцати", "семнадцати", "восемнадцати", "девятнадцати"]
_U_GF = {1: "одной"}
_T_G = ["", "", "двадцати", "тридцати", "сорока", "пятидесяти", "шестидесяти", "семидесяти", "восьмидесяти", "девяноста"]
_H_G = ["", "ста", "двухсот", "трёхсот", "четырёхсот", "пятисот", "шестисот", "семисот", "восьмисот", "девятисот"]
_SCALES = [(10**9, ("миллиард", "миллиарда", "миллиардов"), False), (10**6, ("миллион", "миллиона", "миллионов"), False),
           (1000, ("тысяча", "тысячи", "тысяч"), True)]
_SCALES_G = {10**9: "миллиардов", 10**6: "миллионов", 1000: "тысяч"}


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """forms: (1, 2–4, 5+) — «метр, метра, метров»."""
    n = abs(n) % 100
    if 11 <= n <= 19:
        return forms[2]
    return forms[0] if n % 10 == 1 else forms[1] if 2 <= n % 10 <= 4 else forms[2]


def _chunk(n: int, fem: bool, gen: bool) -> list[str]:
    out = []
    h, rest = divmod(n, 100)
    if h:
        out.append((_H_G if gen else _H)[h])
    if rest >= 20:
        t, u = divmod(rest, 10)
        out.append((_T_G if gen else _T)[t])
        if u:
            out.append((_U_GF.get(u) if gen and fem else None) or (_U_F.get(u) if fem and not gen else None) or (_U_G if gen else _U)[u])
    elif rest:
        out.append((_U_GF.get(rest) if gen and fem else None) or (_U_F.get(rest) if fem and not gen else None) or (_U_G if gen else _U)[rest])
    return out


def int_words(n: int, fem: bool = False, gen: bool = False) -> str:
    if n == 0:
        return "нуля" if gen else "ноль"
    if n < 0:
        return "минус " + int_words(-n, fem, gen)
    out = []
    for size, forms, f in _SCALES:
        if n >= size:
            k, n = divmod(n, size)
            out += _chunk(k, f, gen)
            if gen:  # «одной тысячи», «двух тысяч», «одного миллиона», «пяти миллионов»
                out.append(forms[1] if k % 10 == 1 and k % 100 != 11 else _SCALES_G[size])
            else:
                out.append(plural(k, forms))
    out += _chunk(n, fem, gen)
    return " ".join(w for w in out if w)


_FRAC = {1: ("десятая", "десятых"), 2: ("сотая", "сотых"), 3: ("тысячная", "тысячных")}


def number_words(raw: str, fem: bool = False, gen: bool = False) -> tuple[str, str]:
    """(слова, форма единицы: 'one' | 'few' | 'many' | 'frac'). «1,5» → («одна целая пять десятых», 'frac')."""
    s = raw.replace(" ", "").replace("\u00a0", "").replace("−", "-").replace("–", "-")
    neg = s.startswith("-")
    s = s.lstrip("+-")
    if "," in s or "." in s:
        a, b = re.split(r"[.,]", s, maxsplit=1)
        b = b[:3]
        ia, ib = int(a or 0), int(b or 0)
        one_a, one_b = ia % 10 == 1 and ia % 100 != 11, ib % 10 == 1 and ib % 100 != 11
        if gen:  # «от нуля целых одной десятой»
            whole = int_words(ia, fem=True, gen=True) + " " + ("целой" if one_a else "целых")
            frac = int_words(ib, fem=True, gen=True) + " " + (_FRAC[len(b)][1] if not one_b else _FRAC[len(b)][0][:-2] + "ой")
        else:
            whole = int_words(ia, fem=True) + " " + ("целая" if one_a else "целых")
            frac = int_words(ib, fem=True) + " " + (_FRAC[len(b)][0] if one_b else _FRAC[len(b)][1])
        return ("минус " if neg else "") + whole + " " + frac, "frac"
    n = int(s or 0)
    form = "one" if plural(n, ("one", "few", "many")) == "one" else plural(n, ("one", "few", "many"))
    return ("минус " if neg else "") + int_words(n, fem, gen), form


# Единицы: (1, 2–4, 5+, род. ед. — после дроби), женский род
UNITS: dict[str, tuple[tuple[str, str, str, str], bool]] = {
    "%": (("процент", "процента", "процентов", "процента"), False),
    "°С": (("градус Цельсия", "градуса Цельсия", "градусов Цельсия", "градуса Цельсия"), False),
    "°C": (("градус Цельсия", "градуса Цельсия", "градусов Цельсия", "градуса Цельсия"), False),
    "Ом·м": (("ом-метр", "ом-метра", "ом-метров", "ом-метра"), False),
    "МПа": (("мегапаскаль", "мегапаскаля", "мегапаскалей", "мегапаскаля"), False),
    "кПа": (("килопаскаль", "килопаскаля", "килопаскалей", "килопаскаля"), False),
    "Па": (("паскаль", "паскаля", "паскалей", "паскаля"), False),
    "атм": (("атмосфера", "атмосферы", "атмосфер", "атмосферы"), True),
    "м²/с": (("квадратный метр в секунду", "квадратных метра в секунду", "квадратных метров в секунду", "квадратного метра в секунду"), False),
    "м3/сут": (("кубический метр в сутки", "кубических метра в сутки", "кубических метров в сутки", "кубического метра в сутки"), False),
    "м³/сут": (("кубический метр в сутки", "кубических метра в сутки", "кубических метров в сутки", "кубического метра в сутки"), False),
    "т/сут": (("тонна в сутки", "тонны в сутки", "тонн в сутки", "тонны в сутки"), True),
    "г/см³": (("грамм на кубический сантиметр", "грамма на кубический сантиметр", "граммов на кубический сантиметр", "грамма на кубический сантиметр"), False),
    "кг/м³": (("килограмм на кубический метр", "килограмма на кубический метр", "килограммов на кубический метр", "килограмма на кубический метр"), False),
    "мкм": (("микрометр", "микрометра", "микрометров", "микрометра"), False),
    "мм": (("миллиметр", "миллиметра", "миллиметров", "миллиметра"), False),
    "см": (("сантиметр", "сантиметра", "сантиметров", "сантиметра"), False),
    "км": (("километр", "километра", "километров", "километра"), False),
    "мД": (("миллидарси", "миллидарси", "миллидарси", "миллидарси"), False),
    "мВ": (("милливольт", "милливольта", "милливольт", "милливольта"), False),
    "сут": (("сутки", "суток", "суток", "суток"), False),
    "мин": (("минута", "минуты", "минут", "минуты"), True),
    "с": (("секунда", "секунды", "секунд", "секунды"), True),
    "ч": (("час", "часа", "часов", "часа"), False),
    "м": (("метр", "метра", "метров", "метра"), False),
    "т": (("тонна", "тонны", "тонн", "тонны"), True),
    "лет": (("год", "года", "лет", "года"), False),
}
_UNIT_RX = "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))
_NUM = r"[-−]?\d+(?:[.,]\d+)?"
_RANGE_RE = re.compile(rf"(?:от\s+)?({_NUM})\s*(?:–|—|-|до)\s*({_NUM})\s*({_UNIT_RX})?(?![\wа-яА-Я])")
_QTY_RE = re.compile(rf"({_NUM})\s*({_UNIT_RX})(?![\wа-яА-Я])")
_BARE_RE = re.compile(rf"(?<![\w.,])({_NUM})(?![\w.,]*\d)")


def _unit_word(unit: str, form: str) -> str:
    forms, _ = UNITS[unit]
    return {"one": forms[0], "few": forms[1], "many": forms[2], "frac": forms[3]}[form]


def say_numbers(text: str) -> str:
    """Числа и единицы словами для диктора."""
    s = re.sub(r"(\d)[\s\u00a0](\d{3})(?!\d)", r"\1\2", text)
    s = re.sub(r"(\d)\s*·\s*10\^?\{?([-−]?\d+)\}?", lambda m: f"{m.group(1)} на десять в степени {number_words(m.group(2))[0]}", s)

    def rng(m: re.Match) -> str:
        a, b, unit = m.group(1), m.group(2), m.group(3)
        fem = bool(unit) and UNITS[unit][1]
        wa, fa = number_words(a, fem, gen=True)
        wb, fb = number_words(b, fem, gen=True)
        tail = ""
        if unit:
            forms, _ = UNITS[unit]
            tail = " " + (forms[3] if fb == "frac" else forms[2] if unit not in ("сут",) else "суток")
        return f"от {wa} до {wb}{tail}"

    s = _RANGE_RE.sub(rng, s)

    def case_before(pos: int) -> str:
        """Падеж числа по предлогу перед ним: родительный после «до», «около», «более»…, винительный после «за», «через»."""
        prev = re.findall(r"[А-Яа-яЁё]+", s[max(0, pos - 20):pos].lower())
        w = prev[-1] if prev else ""
        if w in _GEN_PREP:
            return "gen"
        if w in ("за", "через", "на", "в", "под"):
            return "acc"
        return "nom"

    def qty(m: re.Match) -> str:
        unit = m.group(2)
        fem = UNITS[unit][1]
        case = case_before(m.start())
        w, form = number_words(m.group(1), fem, gen=case == "gen")
        forms = UNITS[unit][0]
        if case == "gen":
            word = forms[3] if form in ("one", "frac") else forms[2]
        else:
            word = _unit_word(unit, form)
            if case == "acc" and form == "one" and fem:  # «за одну секунду»
                w = re.sub(r"одна$", "одну", w)
                word = re.sub(r"а(?=\b| в| на)", "у", word, count=1)
        return f"{w} {word}"

    s = _QTY_RE.sub(qty, s)

    def bare(m: re.Match) -> str:
        fem = bool(re.match(r"\s*тысяч", s[m.end():m.end() + 8]))
        return number_words(m.group(1), fem, gen=case_before(m.start()) == "gen")[0]

    s = _BARE_RE.sub(bare, s)
    return s


_GEN_PREP = {"до", "от", "около", "свыше", "более", "менее", "больше", "меньше", "из", "без", "для", "порядка", "выше", "ниже", "у", "с"}


# ---------------------------------------------------------------- словари
def _table(path: Path) -> list[tuple[str, str]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] and not set(cells[0]) <= set("-: ") and cells[0] not in ("Пишется", "Слово"):
            rows.append((cells[0], cells[1]))
    return rows


def load_dicts(domain_dir: Path) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """(аббревиатуры → как читать, слово → слово с ударением U+0301)."""
    abbr = []
    for written, spoken in _table(domain_dir / "pronunciation.md"):
        for w in re.split(r",\s*", written):
            if w:
                abbr.append((w, spoken))
    stress = {w.lower(): s for w, s in _table(domain_dir / "stress_dictionary.md")}
    return sorted(abbr, key=lambda x: -len(x[0])), stress


def speech_text(text: str, domain_dir: Path, stress_marks: bool = True) -> str:
    """Текст для диктора: без LaTeX и markdown, числа словами, аббревиатуры по словарю, ударения."""
    abbr, stress = load_dicts(domain_dir)
    s = re.sub(r"\$+[^$]*\$+", " ", text or "")
    s = re.sub(r"[*_`#>\[\]]", "", s)
    s = say_numbers(s)
    for w, spoken in abbr:
        s = re.sub(rf"(?<![\wА-Яа-я]){re.escape(w)}(?![\wА-Яа-я])", spoken, s)
    if stress_marks:
        def mark(m: re.Match) -> str:
            w = m.group(0)
            st = stress.get(w.lower())
            if not st or "\u0301" in w:
                return w
            return st[:1].upper() + st[1:] if w[:1].isupper() else st
        s = re.sub(r"[А-Яа-яЁё]+", mark, s)
    return re.sub(r"\s{2,}", " ", s).strip()


def strip_stress(text: str) -> str:
    return text.replace("\u0301", "")


# ---------------------------------------------------------------- синтез
class TTSError(RuntimeError):
    pass


def backend_name() -> str:
    want = os.environ.get("KURS_TTS", "").lower()
    if want:
        return want
    if os.environ.get("ELEVENLABS_API_KEY"):
        return "elevenlabs"
    return "edge"


def synth(text: str, out: Path, *, backend: str | None = None, voice: str | None = None) -> Path:
    """Синтез фразы в файл (mp3 у edge и elevenlabs, wav у silero). Кэш: тот же текст второй раз не синтезируется."""
    backend = backend or backend_name()
    out.parent.mkdir(parents=True, exist_ok=True)
    key = out.with_suffix(".txt")
    if out.exists() and key.exists() and key.read_text(encoding="utf-8") == f"{backend}|{voice}|{text}":
        return out
    if backend == "edge":
        try:
            import edge_tts
        except ImportError as e:
            raise TTSError("edge-tts не установлен: pip install edge-tts") from e

        async def go() -> None:
            await edge_tts.Communicate(strip_stress(text), voice or "ru-RU-DmitryNeural", rate="-5%").save(str(out))

        asyncio.run(go())
    elif backend == "silero":
        try:
            import torch
        except ImportError as e:
            raise TTSError("Silero требует torch: pip install torch") from e
        model, _ = torch.hub.load("snakers4/silero-models", "silero_tts", language="ru", speaker="v4_ru")
        # Silero: ударение — «+» перед ударной гласной
        plus = re.sub(r"([аеёиоуыэюяАЕЁИОУЫЭЮЯ])\u0301", lambda m: "+" + m.group(1), text)
        model.save_wav(text=plus, speaker=voice or "aidar", sample_rate=48000, audio_path=str(out))
    elif backend == "elevenlabs":
        import urllib.error
        import urllib.request

        # Голос по умолчанию — стандартный мужской голос библиотеки ElevenLabs; модель multilingual_v2 читает по-русски.
        # Другой голос: ELEVENLABS_VOICE_ID в .env.
        vid = voice or os.environ.get("ELEVENLABS_VOICE_ID") or "pNInz6obpgDQGcFmaJgB"
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
            data=json.dumps({
                "text": strip_stress(text),
                "model_id": os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
                "language_code": "ru",
                "voice_settings": {"stability": 0.55, "similarity_boost": 0.75, "speed": 0.95},
            }).encode(),
            headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json", "Accept": "audio/mpeg"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                out.write_bytes(r.read())
        except urllib.error.HTTPError as e:  # тело ответа без ключа: код и текст ошибки сервиса
            body = e.read()[:300].decode("utf-8", "replace")
            if e.code == 400 and "language_code" in body:  # модель без выбора языка — повтор без него
                payload = json.loads(req.data)
                payload.pop("language_code", None)
                req.data = json.dumps(payload).encode()
                with urllib.request.urlopen(req, timeout=120) as r:
                    out.write_bytes(r.read())
            else:
                raise TTSError(f"ElevenLabs {e.code}: {body}") from e
    else:
        raise TTSError(f"неизвестный синтезатор {backend}")
    key.write_text(f"{backend}|{voice}|{text}", encoding="utf-8")
    return out


# ---------------------------------------------------------------- ffmpeg
def need(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise TTSError(f"не найден {tool}: установите ffmpeg")
    return path


def duration(path: Path) -> float:
    r = subprocess.run(
        [need("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(r.stdout.strip())


def loudnorm(src: Path, dst: Path, target: float = -16.0) -> dict:
    """Два прохода loudnorm: замер, затем нормализация с замеренными параметрами. Видео копируется без перекодирования."""
    ff = need("ffmpeg")
    flt = f"loudnorm=I={target}:TP=-1.5:LRA=11"
    r = subprocess.run([ff, "-hide_banner", "-i", str(src), "-af", flt + ":print_format=json", "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    if not m:
        raise TTSError("loudnorm: нет замера в выводе ffmpeg")
    j = json.loads(m.group(0))
    second = (
        f"{flt}:measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}"
        f":measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true"
    )
    subprocess.run(
        [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-c:v", "copy", "-af", second, "-ar", "48000", "-c:a", "aac", "-b:a", "160k", str(dst)],
        check=True,
    )
    return j


def measure_lufs(path: Path) -> float:
    r = subprocess.run([need("ffmpeg"), "-hide_banner", "-i", str(path), "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS", r.stderr)
    if not m:
        raise TTSError("ebur128: нет итоговой громкости")
    return float(m[-1])


# ---------------------------------------------------------------- распознавание
def transcribe(path: Path, language: str = "ru") -> list[dict]:
    """Слова с таймкодами [{word, start, end}]: faster-whisper, иначе mlx-whisper (Apple Silicon)."""
    model_name = os.environ.get("KURS_ASR_MODEL", "small")
    try:
        from faster_whisper import WhisperModel

        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segs, _ = model.transcribe(str(path), language=language, word_timestamps=True, vad_filter=False)
        return [{"word": w.word.strip(), "start": w.start, "end": w.end} for s in segs for w in (s.words or [])]
    except ImportError:
        pass
    try:
        import mlx_whisper

        res = mlx_whisper.transcribe(str(path), language=language, word_timestamps=True,
                                     path_or_hf_repo=os.environ.get("KURS_MLX_MODEL", "mlx-community/whisper-small-mlx"))
        return [{"word": w["word"].strip(), "start": w["start"], "end": w["end"]} for s in res["segments"] for w in s.get("words", [])]
    except ImportError as e:
        raise TTSError("нет распознавателя: pip install faster-whisper (или mlx-whisper на Apple Silicon)") from e


def _wstem(w: str) -> str:
    """Основа для сравнения с распознанным текстом: без двух последних букв («пяти» = «пять»)."""
    return w if len(w) <= 3 else w[: max(3, len(w) - 2)]


def _tokens(text: str) -> list[str]:
    t = strip_stress(text).lower().replace("ё", "е")
    t = re.sub(r"(?<![\w.,])\d+(?:[.,]\d+)?", lambda m: say_numbers(m.group(0)), t)
    return [_wstem(w) for w in re.findall(r"[a-zа-я]+", t)]


def wer(ref: str, hyp: str) -> float:
    """WER по основам слов: падежное окончание распознавателя ошибкой не считается."""
    r, h = _tokens(ref), _tokens(hyp)
    if not r:
        return 0.0 if not h else 1.0
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / len(r)


def sync_drift(lines: list[dict], words: list[dict]) -> float:
    """Наибольший сдвиг начала фразы сценария от распознанного начала, с. lines: [{t_start, speech}]."""
    toks = [(_wstem(re.sub(r"[^a-zа-я]", "", w["word"].lower().replace("ё", "е"))), w["start"]) for w in words]
    worst, k = 0.0, 0
    for ln in lines:
        first = _tokens(ln["speech"])[:3]
        if not first:
            continue
        hit = None
        for j in range(k, len(toks)):
            if toks[j][0] == first[0] and all(j + i < len(toks) and toks[j + i][0] == first[i] for i in range(1, min(2, len(first)))):
                hit = j
                break
        if hit is None:
            continue
        worst = max(worst, abs(toks[hit][1] - ln["t_start"]))
        k = hit + 1
    return round(worst, 2)
