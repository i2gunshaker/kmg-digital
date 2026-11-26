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

import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

from .config import BUILD, ROOT, Config


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


def synth(text: str, out: Path, *, backend: str | None = None, voice: str | None = None, lang: str = "ru") -> Path:
    """Синтез фразы в файл (mp3 у edge и elevenlabs, wav у silero). Кэш: тот же текст второй раз не синтезируется."""
    backend = backend or backend_name()
    en = lang == "en"
    out.parent.mkdir(parents=True, exist_ok=True)
    key = out.with_suffix(".txt")
    cache_tag = f"{backend}|{voice}|{lang}|{text}"
    if out.exists() and key.exists() and key.read_text(encoding="utf-8") == cache_tag:
        return out
    if backend == "edge":
        try:
            import edge_tts
        except ImportError as e:
            raise TTSError("edge-tts не установлен: pip install edge-tts") from e

        async def go() -> None:
            default_voice = "en-US-AndrewNeural" if en else "ru-RU-DmitryNeural"
            await edge_tts.Communicate(strip_stress(text), voice or default_voice, rate="-5%").save(str(out))

        asyncio.run(go())
    elif backend == "silero":
        try:
            import torch
        except ImportError as e:
            raise TTSError("Silero требует torch: pip install torch") from e
        if en:
            model, _ = torch.hub.load("snakers4/silero-models", "silero_tts", language="en", speaker="v3_en")
            model.save_wav(text=strip_stress(text), speaker=voice or "en_0", sample_rate=48000, audio_path=str(out))
        else:
            try:
                model, _ = torch.hub.load("snakers4/silero-models", "silero_tts", language="ru", speaker="v5_ru")
            except Exception:
                model, _ = torch.hub.load("snakers4/silero-models", "silero_tts", language="ru", speaker="v4_ru")
            # Silero: ударение — «+» перед ударной гласной
            plus = re.sub(r"([аеёиоуыэюяАЕЁИОУЫЭЮЯ])\u0301", lambda m: "+" + m.group(1), text)
            model.save_wav(text=plus, speaker=voice or "aidar", sample_rate=48000, audio_path=str(out))
    elif backend == "elevenlabs":
        import urllib.error
        import urllib.request

        # Голос по умолчанию: RU — Nikolay (проверен на геологических терминах), переопределяется ELEVENLABS_VOICE_ID;
        # EN — George, переопределяется ELEVENLABS_VOICE_ID_EN.
        if en:
            default_vid = os.environ.get("ELEVENLABS_VOICE_ID_EN") or DEFAULT_EL_VOICE_ID_EN
        else:
            default_vid = os.environ.get("ELEVENLABS_VOICE_ID") or "3EuKHIEZbSzrHGNmdYsx"
        vid = voice or default_vid
        language_code = "en" if en else "ru"
        model_id = os.environ.get("ELEVENLABS_MODEL_ID") or os.environ.get("ELEVENLABS_MODEL") or "eleven_v4"
        if model_id == "eleven_v4":
            payload: dict[str, Any] = {
                "text": strip_stress(text),
                "model_id": model_id,
                "language_code": language_code,
                "apply_text_normalization": "on",
                "voice_settings": {"stability": 0.55, "similarity_boost": 0.78},
            }
        else:
            payload = {
                "text": strip_stress(text),
                "model_id": model_id,
                "language_code": language_code,
                "voice_settings": {"stability": 0.55, "similarity_boost": 0.78, "speed": 0.95},
            }
        last_err: Exception | None = None
        for attempt in range(3):
            req = urllib.request.Request(
                f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
                data=json.dumps(payload).encode(),
                headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json", "Accept": "audio/mpeg"},
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    out.write_bytes(r.read())
                last_err = None
                break
            except urllib.error.HTTPError as e:  # тело ответа без ключа: код и текст ошибки сервиса
                body = e.read()[:300].decode("utf-8", "replace")
                if e.code == 400 and "language_code" in body and "language_code" in payload:
                    payload.pop("language_code", None)
                    last_err = e
                    continue
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    last_err = e
                    continue
                raise TTSError(f"ElevenLabs {e.code}: {body}") from e
        if last_err is not None:
            raise TTSError(f"ElevenLabs error: {last_err}") from last_err
    else:
        raise TTSError(f"неизвестный синтезатор {backend}")
    key.write_text(cache_tag, encoding="utf-8")
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


# ---------------------------------------------------------------- паспорта Remotion (Track 2)
DEFAULT_EL_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # George (premade, multilingual)
DEFAULT_EL_VOICE_ID_EN = "JBFqnCBsd6RMkjVDRZzb"  # George для lang="en"; переопределяется ELEVENLABS_VOICE_ID_EN
DEFAULT_EL_MODEL = "eleven_multilingual_v2"
SUPPORTED_BACKENDS = ("auto", "elevenlabs", "silero", "edge_tts", "mock")

_SYMBOL_EXPANSIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bVolve\b", re.I), "Вольве"),
    (re.compile(r"\bGR\b"), "джи-ар"),
    (re.compile(r"\bRT\b"), "эр-тэ"),
    (re.compile(r"\bPHIF\b"), "фи-эф"),
    (re.compile(r"\bNPHI\b"), "эн-пи-аш-ай"),
    (re.compile(r"\bRHOB\b"), "эр-эйч-о-би"),
    (re.compile(r"\bSW\b"), "эс-вэ"),
    (re.compile(r"\bSAND_FLAG\b"), "флаг песчаника"),
    (re.compile(r"(\d+)\s*:\s*(\d+)"), r"\1 к \2"),
    (re.compile(r"Ом\s*[·*]\s*м"), "ом-метр"),
    (re.compile(r"м²\s*/\s*с|м\^2\s*/\s*с"), "квадратных метров в секунду"),
    (re.compile(r"β\*"), "бета со звёздочкой"),
    (re.compile(r"ρ[пn]"), "ро-пэ"),
    (re.compile(r"k[пn]\*"), "ка-пэ со звёздочкой"),
    (re.compile(r"\bk[пn]\b"), "ка-пэ"),
    (re.compile(r"\bκ\b|(?<![а-яА-Яa-zA-Z])κ(?![а-яА-Яa-zA-Z])"), "каппа"),
    (re.compile(r"-Ei\(-x\)"), "минус интегральная показательная функция"),
    (re.compile(r"(\d)\s*%"), r"\1 процентов"),
]


def load_stress_map(domain_dir: Path) -> dict[str, str]:
    """Читает domain/<domain>/stress_dictionary.md -> {слово_в_нижнем_регистре: слово_с_U0301}."""
    p = domain_dir / "stress_dictionary.md"
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for ln in p.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s.startswith("|") or s.startswith("|---") or "Слово" in s:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) >= 2 and cells[0] and cells[1]:
            out[cells[0].lower()] = cells[1]
    return out


def _apply_stress_dict(text: str, stress_map: dict[str, str]) -> str:
    if not stress_map:
        return text

    def _sub(m: re.Match[str]) -> str:
        tok = m.group(0)
        if "\u0301" in tok:
            return tok
        repl = stress_map.get(tok.lower())
        if not repl:
            return tok
        return repl.capitalize() if tok[:1].isupper() else repl

    return re.sub(r"[А-Яа-яЁё\u0301]+", _sub, text)


def _u0301_to_silero_plus(text: str) -> str:
    """Преобразует ударный знак U+0301 после гласной в формат Silero ('+' перед гласной)."""
    return re.sub(r"([АЕЁИОУЫЭЮЯаеёиоуыэюя])\u0301", r"+\1", text)


def strip_stress_marks(text: str) -> str:
    return (text or "").replace("\u0301", "").replace("+", "")


def normalize_for_speech(text: str, backend: str, domain_dir: Path, *, lang: str = "ru") -> tuple[str, str]:
    """Возвращает (текст_для_синтеза, чистый_произносимый_эталон_без_ударений)."""
    s = (text or "").strip()
    if lang == "en":
        # Английский текст: без русских раскрытий символов и без словаря ударений.
        s = s.replace("→", " leads to ").replace("≠", " is not equal to ")
        s = re.sub(r"\s+", " ", s).strip()
        return s, s
    for pat, repl in _SYMBOL_EXPANSIONS:
        s = pat.sub(repl, s)
    s = s.replace("→", " переходит в ").replace("≠", " не равно ")
    s = re.sub(r"\s+", " ", s).strip()

    stress_map = load_stress_map(domain_dir)
    stressed = _apply_stress_dict(s, stress_map)
    spoken_ref = strip_stress_marks(stressed)

    if backend == "silero":
        tts_text = _u0301_to_silero_plus(stressed)
    elif backend == "edge_tts":
        tts_text = stressed
    else:
        # ElevenLabs нестабильно читает combining U+0301, но отлично понимает букву ё и чистый текст.
        tts_text = spoken_ref
    return tts_text, spoken_ref


def _find_prediction_pause_index(script: list[dict]) -> int | None:
    """Находит индекс реплики, после которой стоит пауза предсказания (>= 1.0 с в сценарии)."""
    best_idx = None
    best_gap = 0.0
    for i in range(len(script) - 1):
        gap = float(script[i + 1]["t_start"]) - float(script[i]["t_end"])
        if gap >= 1.0 and gap > best_gap:
            best_gap = gap
            best_idx = i
    if best_idx is not None:
        return best_idx
    for i, ln in enumerate(script[:-1]):
        on_scr = (ln.get("on_screen") or "").lower()
        if "пауза" in on_scr or "предсказ" in ln.get("text", "").lower():
            return i
    return None


def _run_ffmpeg(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def _align_chars_to_words(text: str, chars: list[str], starts: list[float], ends: list[float]) -> list[dict]:
    """Преобразует посимвольные таймкоды ElevenLabs в список слов с start/end."""
    words: list[dict] = []
    buf: list[str] = []
    w_start: float | None = None
    w_end: float = 0.0
    for ch, st, en in zip(chars, starts, ends):
        if ch.isspace():
            if buf:
                words.append({"word": "".join(buf), "start": round(w_start or 0.0, 3), "end": round(w_end, 3)})
                buf = []
                w_start = None
        else:
            if w_start is None:
                w_start = float(st)
            w_end = float(en)
            buf.append(ch)
    if buf:
        words.append({"word": "".join(buf), "start": round(w_start or 0.0, 3), "end": round(w_end, 3)})
    if not words and text.strip():
        dur = float(ends[-1]) if ends else max(1.5, len(text) * 0.065)
        words.append({"word": text.strip(), "start": 0.0, "end": round(dur, 3)})
    return words


def _even_words(text: str, dur: float) -> list[dict]:
    toks = [t for t in text.split() if t]
    if not toks:
        return []
    step = dur / len(toks)
    return [
        {"word": t, "start": round(i * step, 3), "end": round((i + 1) * step, 3)}
        for i, t in enumerate(toks)
    ]


def _make_silence_wav(path: Path, dur_sec: float, sr: int = 44100) -> None:
    _run_ffmpeg([
        "-f", "lavfi",
        "-i", f"anullsrc=r={sr}:cl=mono",
        "-t", f"{dur_sec:.3f}",
        "-acodec", "pcm_s16le",
        str(path),
    ])


def _two_pass_loudnorm(in_wav: Path, out_wav: Path, out_mp3: Path) -> dict[str, float]:
    """Двухпроходный loudnorm ровно до -16 LUFS, TP -1.5 dBTP."""
    p1 = subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-i", str(in_wav),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    m = re.search(r"\{\s*\"input_i\".*?\}", p1.stderr, re.S)
    if not m:
        _run_ffmpeg(["-i", str(in_wav), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100", "-ac", "1", str(out_wav)])
        stats = {}
    else:
        stats = json.loads(m.group(0))
        flt = (
            "loudnorm=I=-16:TP=-1.5:LRA=11:"
            f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
            f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}:linear=true:print_format=json"
        )
        p2 = _run_ffmpeg(["-i", str(in_wav), "-af", flt, "-ar", "44100", "-ac", "1", str(out_wav)])
        m2 = re.search(r"\{\s*\"input_i\".*?\}", p2.stderr, re.S)
        stats = json.loads(m2.group(0)) if m2 else stats

    _run_ffmpeg(["-i", str(out_wav), "-codec:a", "libmp3lame", "-b:a", "192k", str(out_mp3)])
    # Если 2-й проход не вернул output_i, измерим итоговый файл напрямую.
    if "output_i" not in stats:
        pm = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-i", str(out_wav), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
            capture_output=True, text=True, check=True,
        )
        mm = re.search(r"\{\s*\"input_i\".*?\}", pm.stderr, re.S)
        if mm:
            st = json.loads(mm.group(0))
            return {"measured_lufs": float(st["input_i"]), "true_peak_dbtp": float(st["input_tp"])}
    return {
        "measured_lufs": float(stats.get("output_i", -16.0)),
        "true_peak_dbtp": float(stats.get("output_tp", -1.5)),
    }


def _synth_mock_wav(text: str, out_wav: Path, target_dur: float) -> float:
    sr = 44100
    dur = max(0.8, target_dur)
    n_samples = int(sr * dur)
    frames = bytearray()
    freq = 210.0 + (sum(ord(c) for c in text) % 90)
    for i in range(n_samples):
        t = i / sr
        env = min(1.0, t / 0.05) * min(1.0, max(0.0, (dur - t) / 0.05))
        mod = 0.65 + 0.35 * math.sin(2.0 * math.pi * 3.5 * t)
        val = int(11000 * env * mod * math.sin(2.0 * math.pi * freq * t))
        frames += struct.pack("<h", max(-32767, min(32767, val)))
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(frames))
    return dur


def _synth_elevenlabs_segment(
    client: Any,
    text: str,
    voice_id: str,
    model_id: str,
    out_mp3: Path,
    *,
    lang: str = "ru",
) -> tuple[Path, list[dict]]:
    from elevenlabs import VoiceSettings

    kwargs: dict[str, Any] = {
        "text": text,
        "model_id": model_id,
        "output_format": "mp3_44100_128",
        "seed": 42,
    }
    if model_id == "eleven_v4":
        kwargs["voice_settings"] = VoiceSettings(stability=0.55, similarity_boost=0.78)
        kwargs["language_code"] = "en" if lang == "en" else "ru"
        kwargs["apply_text_normalization"] = "on"
    else:
        kwargs["voice_settings"] = VoiceSettings(stability=0.55, similarity_boost=0.78, style=0.12, use_speaker_boost=True)

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = client.text_to_speech.convert_with_timestamps(voice_id, **kwargs)
            audio_bytes = base64.b64decode(resp.audio_base_64)
            out_mp3.write_bytes(audio_bytes)
            align = resp.alignment
            words = []
            if align and align.characters:
                words = _align_chars_to_words(
                    text,
                    list(align.characters),
                    list(align.character_start_times_seconds),
                    list(align.character_end_times_seconds),
                )
            return out_mp3, words
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            if "429" in msg or "rate" in msg or "50" in msg or "timeout" in msg:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    assert last_err is not None
    raise last_err


_SILERO_CACHE: dict[str, Any] = {}


def _synth_silero_segment(text: str, lang: str, out_wav: Path) -> Path:
    import torch

    key = "ru" if lang != "en" else "en"
    if key not in _SILERO_CACHE:
        model_id = "v5_ru" if key == "ru" else "v3_en"
        speaker = "aidar" if key == "ru" else "en_0"
        loaded = torch.hub.load(
            repo_or_dir="snakers4/silero-models",
            model="silero_tts",
            language=key,
            speaker=model_id,
            trust_repo=True,
        )
        model = loaded[0] if isinstance(loaded, tuple) else loaded
        _SILERO_CACHE[key] = (model, speaker)
    model, speaker = _SILERO_CACHE[key]
    audio = model.apply_tts(text=text, speaker=speaker, sample_rate=48000)
    samples = audio.cpu().numpy()
    pcm = (samples.clip(-1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(pcm)
    return out_wav


def _synth_edge_segment(text: str, lang: str, out_mp3: Path) -> Path:
    import asyncio
    import edge_tts

    voice = "ru-RU-DmitryNeural" if lang != "en" else "en-US-AndrewNeural"

    async def _run() -> None:
        comm = edge_tts.Communicate(text, voice)
        await comm.save(str(out_mp3))

    asyncio.run(_run())
    time.sleep(1.6)  # защита от лимита Microsoft при серийных запросах
    return out_mp3


_RU_NUM_WORDS = {
    "ноль": "0", "нуля": "0", "нулю": "0",
    "один": "1", "одна": "1", "одно": "1", "одну": "1", "одной": "1", "одному": "1", "одного": "1",
    "два": "2", "две": "2", "двух": "2",
    "три": "3", "трех": "3",
    "четыре": "4", "четырех": "4",
    "пять": "5", "пяти": "5",
    "шесть": "6", "шести": "6",
    "десять": "10", "десяти": "10",
    "пятнадцать": "15", "пятнадцати": "15",
    "семнадцать": "17", "семнадцати": "17",
    "двадцать": "20", "двадцати": "20",
    "сто": "100", "ста": "100",
    "двести": "200", "двухсот": "200",
    "тысяча": "1000", "тысячи": "1000", "тысяче": "1000", "тысячу": "1000",
}
_RU_UNIT_NORM = {
    "процент": "процентов", "процента": "процентов", "процентов": "процентов",
    "процентах": "процентов", "процентам": "процентов",
    "десятая": "десятых", "десятой": "десятых", "десятых": "десятых", "десятый": "десятых",
}


def _normalize_words_for_wer(text: str) -> list[str]:
    s = strip_stress_marks(text).lower().replace("ё", "е")
    s = s.replace("%", " процентов ")
    s = re.sub(r"(\d+),(\d+)", r"\1 \2", s)
    s = re.sub(r"[^a-zа-я0-9]+", " ", s)
    mapped = [_RU_UNIT_NORM.get(t, _RU_NUM_WORDS.get(t, t)) for t in s.split() if t]
    merged: list[str] = []
    i = 0
    while i < len(mapped):
        if mapped[i] == "20" and i + 1 < len(mapped) and mapped[i + 1] == "5":
            merged.append("25")
            i += 2
            continue
        if mapped[i] == "целых" and merged and merged[-1] == "0":
            i += 1
            continue
        if mapped[i] == "десятых" and len(merged) >= 2 and merged[-2] == "0" and merged[-1].isdigit():
            i += 1
            continue
        merged.append(mapped[i])
        i += 1
    return merged


def compute_wer(ref_text: str, hyp_text: str) -> float:
    """Классический word error rate на уровне слов (расстояние Левенштейна / длина эталона)."""
    r = _normalize_words_for_wer(ref_text)
    h = _normalize_words_for_wer(hyp_text)
    if not r:
        return 0.0
    dp = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev = dp[0]
        dp[0] = i
        for j, hw in enumerate(h, 1):
            cur = dp[j]
            if rw == hw:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = cur
    return round(dp[-1] / len(r), 4)


def run_whisper_check(wav_path: Path, expected_ref: str, timings: list[dict], lang: str = "ru") -> dict:
    """Обратная проверка разборчивости и сдвига локальным распознавателем: mlx-whisper на Mac, иначе faster-whisper."""
    try:
        import mlx_whisper

        res = mlx_whisper.transcribe(
            str(wav_path),
            path_or_hf_repo=os.environ.get("KURS_MLX_MODEL", "mlx-community/whisper-small-mlx"),
            language=lang,
            word_timestamps=True,
        )
        hyp = (res.get("text") or "").strip()
        asr_words = [(float(w["start"]), float(w["end"])) for seg in res.get("segments") or [] for w in seg.get("words") or []]
    except ImportError:
        words = transcribe(wav_path, language=lang)  # faster-whisper; нет и его — TTSError
        hyp = " ".join(w["word"] for w in words).strip()
        asr_words = [(float(w["start"]), float(w["end"])) for w in words]
    wer = compute_wer(expected_ref, hyp)
    max_drift = 0.0
    if asr_words and timings:
        first_asr_start = asr_words[0][0]
        last_asr_end = asr_words[-1][1]
        max_drift = max(
            abs(first_asr_start - (timings[0]["words"][0]["start"] if timings[0]["words"] else timings[0]["t_start"])),
            abs(last_asr_end - (timings[-1]["words"][-1]["end"] if timings[-1]["words"] else timings[-1]["t_end"])),
        )
    return {
        "wer": wer,
        "max_sync_drift_sec": round(max_drift, 3),
        "asr_transcript": hyp,
    }


def _format_vtt_ts(sec: float) -> str:
    ms = int(round(max(0.0, sec) * 1000))
    hh, rem = divmod(ms, 3_600_000)
    mm, rem = divmod(rem, 60_000)
    ss, ms = divmod(rem, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}"


CAPTION_MAX_WORDS = 7  # стандарт подачи: не больше 7–8 слов на экране
CAPTION_MIN_SEC = 1.5  # и не меньше 1,5 с на подпись
_CAPTION_BREAK = re.compile(r"[.,;:!?…»)]$")
_CAPTION_CLITIC = set("в во и а но на с со по к ко о об у за из от до не ни при для без над под что как the a an of in on at to and or".split())


def _is_word(tok: str) -> bool:
    return bool(re.search(r"\w", tok))


def caption_chunks(words: list[dict], t_end: float, max_words: int = CAPTION_MAX_WORDS, min_sec: float = CAPTION_MIN_SEC) -> list[dict]:
    """Режет реплику на подписи для экрана: не больше max_words слов, разрыв по знаку препинания, где можно.

    Подпись висит от своего первого слова до первого слова следующей (последняя — до конца реплики).
    Подпись короче min_sec сливается с соседней, если вместе выходит не больше max_words слов.
    """
    toks = [{"word": strip_stress_marks(str(w["word"])).strip(), "start": float(w["start"])} for w in words]
    toks = [w for w in toks if w["word"]]
    if not toks:
        return []
    n = len(toks)
    wc = [0] * (n + 1)  # число настоящих слов (без тире) до позиции
    for i, w in enumerate(toks):
        wc[i + 1] = wc[i] + _is_word(w["word"])
    target = wc[n] / max(1, math.ceil(wc[n] / max_words))
    best: list[tuple[float, int]] = [(0.0, -1)] + [(float("inf"), -1)] * n
    for j in range(1, n + 1):
        for i in range(j):
            k = wc[j] - wc[i]
            if k == 0 or k > max_words or best[i][0] == float("inf"):
                continue
            cost = best[i][0] + 2 + 0.15 * (k - target) ** 2
            if j < n and not _CAPTION_BREAK.search(toks[j - 1]["word"]):
                cost += 4  # разрыв посреди фразы
            if j < n and toks[j - 1]["word"].lower() in _CAPTION_CLITIC:
                cost += 4  # предлог или союз висит в конце подписи
            if not _is_word(toks[i]["word"]):
                cost += 3  # подпись с тире в начале
            if cost < best[j][0]:
                best[j] = (cost, i)
    cuts, j = [], n
    while j > 0:
        cuts.append((best[j][1], j))
        j = best[j][1]
    groups = [toks[i:j] for i, j in reversed(cuts)]

    def dur(gi: int) -> float:
        end = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else t_end
        return end - groups[gi][0]["start"]

    def words_in(g: list[dict]) -> int:
        return sum(_is_word(w["word"]) for w in g)

    changed = True
    while changed and len(groups) > 1:
        changed = False
        for gi in range(len(groups)):
            if dur(gi) >= min_sec:
                continue
            nb = [x for x in (gi - 1, gi + 1) if 0 <= x < len(groups) and words_in(groups[x]) + words_in(groups[gi]) <= max_words]
            if nb:
                o = min(nb, key=lambda x: words_in(groups[x]))
                a, b = sorted((gi, o))
                groups[a:b + 1] = [groups[a] + groups[b]]
                changed = True
                break
    return [
        {"text": " ".join(w["word"] for w in g), "start": round(g[0]["start"], 3),
         "end": round(groups[gi + 1][0]["start"] if gi + 1 < len(groups) else t_end, 3)}
        for gi, g in enumerate(groups)
    ]


def _write_vtt(timings: list[dict], out_vtt: Path) -> None:
    lines = ["WEBVTT", ""]
    idx = 0
    for seg in timings:
        caps = seg.get("captions") or [{"start": seg["t_start"], "end": seg["t_end"], "text": strip_stress_marks(seg["spoken_ref"])}]
        for c in caps:
            idx += 1
            lines.append(str(idx))
            lines.append(f"{_format_vtt_ts(c['start'])} --> {_format_vtt_ts(c['end'])}")
            lines.append(c["text"])
            lines.append("")
    out_vtt.write_text("\n".join(lines), encoding="utf-8")


def synthesize_passport(
    cfg: Config,
    passport_path: Path,
    out_dir: Path,
    *,
    backend: str = "auto",
    voice_id: str | None = None,
    model_id: str | None = None,
    max_chars: int = 8000,
    lang: str = "ru",
    skip_asr: bool = False,
    fixed_timeline: bool = False,
) -> dict[str, Any]:
    """Озвучивает паспорт целиком, склеивает с паузой предсказания 1.5 с, нормирует -16 LUFS и проверяет ASR."""
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"Неизвестный backend: {backend}")

    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    script = passport.get("script") or []
    if not script:
        raise ValueError("Пустой script в паспорте")

    if lang == "en" and voice_id is None:
        resolved_voice = os.environ.get("ELEVENLABS_VOICE_ID_EN") or DEFAULT_EL_VOICE_ID_EN
    else:
        resolved_voice = voice_id or os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_EL_VOICE_ID
    resolved_model = model_id or os.environ.get("ELEVENLABS_MODEL_ID") or DEFAULT_EL_MODEL
    chosen = backend
    if chosen == "auto":
        chosen = "elevenlabs" if os.environ.get("ELEVENLABS_API_KEY") else "silero"

    total_chars = sum(len(ln.get("text", "")) for ln in script)
    if chosen == "elevenlabs" and total_chars > max_chars:
        raise RuntimeError(f"Лимит --max-chars={max_chars} превышен: в сценарии {total_chars} символов")

    cache_dir = BUILD / ".cache" / "tts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    pause_idx = _find_prediction_pause_index(script)
    segment_entries: list[dict[str, Any]] = []

    def _run_with_backend(use_backend: str) -> list[dict[str, Any]]:
        el_client = None
        if use_backend == "elevenlabs":
            from elevenlabs import ElevenLabs

            key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
            if not key:
                raise RuntimeError("ELEVENLABS_API_KEY отсутствует")
            el_client = ElevenLabs(api_key=key)
            sub = el_client.user.get().subscription
            rem = int(sub.character_limit) - int(sub.character_count)
            if rem < total_chars:
                raise RuntimeError(f"Недостаточно кредитов ElevenLabs: осталось {rem}, нужно {total_chars}")

        entries: list[dict[str, Any]] = []
        for idx, line in enumerate(script):
            tts_text, spoken_ref = normalize_for_speech(line["text"], use_backend, cfg.domain_dir, lang=lang)
            planned_dur = max(1.2, float(line["t_end"]) - float(line["t_start"]))
            meta_tag = f"{use_backend}|{resolved_model}|{resolved_voice}|{lang}|{tts_text}"
            digest = hashlib.sha256(meta_tag.encode("utf-8")).hexdigest()[:24]
            ext = "mp3" if use_backend in ("elevenlabs", "edge_tts") else "wav"
            cached_audio = cache_dir / f"{digest}.{ext}"
            cached_meta = cache_dir / f"{digest}.json"

            if cached_audio.exists() and cached_meta.exists():
                meta = json.loads(cached_meta.read_text(encoding="utf-8"))
                words = meta.get("words", [])
                dur = float(meta["duration"])
            else:
                if use_backend == "mock":
                    dur = _synth_mock_wav(tts_text, cached_audio, planned_dur)
                    words = _even_words(spoken_ref, dur)
                elif use_backend == "elevenlabs":
                    _, words = _synth_elevenlabs_segment(
                        el_client, tts_text, resolved_voice, resolved_model, cached_audio, lang=lang
                    )
                    dur = _ffprobe_duration(cached_audio)
                    if not words:
                        words = _even_words(spoken_ref, dur)
                elif use_backend == "silero":
                    _synth_silero_segment(tts_text, lang, cached_audio)
                    dur = _ffprobe_duration(cached_audio)
                    words = _even_words(spoken_ref, dur)
                else:
                    _synth_edge_segment(tts_text, lang, cached_audio)
                    dur = _ffprobe_duration(cached_audio)
                    words = _even_words(spoken_ref, dur)

                cached_meta.write_text(
                    json.dumps({"duration": dur, "words": words, "spoken_ref": spoken_ref, "tts_text": tts_text}, ensure_ascii=False),
                    encoding="utf-8",
                )

            entries.append({
                "index": idx,
                "audio_path": cached_audio,
                "duration": dur,
                "words": words,
                "spoken_ref": spoken_ref,
                "tts_text": tts_text,
                "on_screen": line.get("on_screen", ""),
                "claim_id": line.get("claim_id"),
                "is_analogy": bool(line.get("is_analogy", False)),
                # последняя фраза не обрывается: финальное состояние держится на экране
                "pause_after_sec": 1.5 if idx == pause_idx else (0.25 if idx < len(script) - 1 else 1.2),
            })
        return entries

    # Попытка с отказоустойчивым переключением ВСЕГО ролика на резервный движок.
    fallback_chain = [chosen]
    if backend == "auto":
        for cand in ("silero", "edge_tts"):
            if cand not in fallback_chain:
                fallback_chain.append(cand)

    actual_backend = chosen
    last_exc: Exception | None = None
    for cand in fallback_chain:
        try:
            segment_entries = _run_with_backend(cand)
            actual_backend = cand
            break
        except Exception as exc:
            last_exc = exc
            if cand == fallback_chain[-1]:
                raise
    if not segment_entries and last_exc:
        raise last_exc

    # Склейка всех сегментов и точных пауз в единый PCM WAV и 2-проходный loudnorm (-16 LUFS).
    master_wav = out_dir / "master.wav"
    master_mp3 = out_dir / "master.mp3"
    with tempfile.TemporaryDirectory() as tmp:
        tdir = Path(tmp)
        concat_list: list[Path] = []
        cursor = 0.0
        for idx, ent in enumerate(segment_entries):
            seg_wav = tdir / f"seg_{idx:03d}.wav"
            filters = []
            tempo = 1.0
            if fixed_timeline:
                line = script[idx]
                slot = line["t_end"] - line["t_start"]
                # Оставляем финальный кадр для чтения результата.
                hold = min(1.2, slot * 0.15) if idx == len(script) - 1 else 0.0
                tempo = ent["duration"] / (slot - hold)
                filters = ["-af", f"atempo={tempo:.9f},apad,atrim=duration={slot:.9f}"]
                next_start = script[idx + 1]["t_start"] if idx + 1 < len(script) else line["t_end"]
                ent["pause_after_sec"] = next_start - line["t_end"]
            _run_ffmpeg(["-i", str(ent["audio_path"]), *filters, "-ar", "44100", "-ac", "1", "-acodec", "pcm_s16le", str(seg_wav)])
            real_dur = _ffprobe_duration(seg_wav)
            ent["t_start"] = round(cursor, 3)
            ent["t_end"] = round(cursor + real_dur, 3)
            ent["shifted_words"] = [
                {"word": w["word"], "start": round(cursor + w["start"] / tempo, 3), "end": round(cursor + min(real_dur, w["end"] / tempo), 3)}
                for w in ent["words"]
            ]
            concat_list.append(seg_wav)
            cursor += real_dur
            if ent["pause_after_sec"] > 0:
                sil_wav = tdir / f"sil_{idx:03d}.wav"
                _make_silence_wav(sil_wav, ent["pause_after_sec"], sr=44100)
                if idx == pause_idx:
                    ent["pause_window"] = [round(cursor, 3), round(cursor + ent["pause_after_sec"], 3)]
                concat_list.append(sil_wav)
                cursor += ent["pause_after_sec"]

        raw_mix = tdir / "raw_mix.wav"
        list_txt = tdir / "concat.txt"
        list_txt.write_text("\n".join(f"file '{p.as_posix()}'" for p in concat_list), encoding="utf-8")
        _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_txt), "-c", "copy", str(raw_mix)])
        loud_stats = _two_pass_loudnorm(raw_mix, master_wav, master_mp3)

    total_duration = round(_ffprobe_duration(master_wav), 3)
    timings = [
        {
            "index": ent["index"],
            "t_start": ent["t_start"],
            "t_end": ent["t_end"],
            "on_screen": ent["on_screen"],
            "claim_id": ent["claim_id"],
            "is_analogy": ent["is_analogy"],
            "spoken_ref": ent["spoken_ref"],
            "pause_after_sec": ent["pause_after_sec"],
            "pause_window": ent.get("pause_window"),
            "words": ent["shifted_words"],
            "captions": caption_chunks(ent["shifted_words"], ent["t_end"]) or [
                {"text": strip_stress_marks(ent["spoken_ref"]), "start": ent["t_start"], "end": ent["t_end"]}],
        }
        for ent in segment_entries
    ]
    vtt_path = out_dir / "subtitles.vtt"
    _write_vtt(timings, vtt_path)
    timings_path = out_dir / "timings.json"
    timings_path.write_text(json.dumps({"total_duration_sec": total_duration, "segments": timings}, ensure_ascii=False, indent=2), encoding="utf-8")

    # Обратная проверка mlx-whisper на финальном звуке (-16 LUFS)
    full_ref = " ".join(ent["spoken_ref"] for ent in segment_entries)
    asr_missing = ""
    if skip_asr or actual_backend == "mock":
        asr_info = {"wer": 0.0, "max_sync_drift_sec": 0.0, "asr_transcript": full_ref, "asr_skipped": True}
    else:
        try:
            asr_info = run_whisper_check(master_wav, full_ref, timings, lang=lang)
        except TTSError as e:  # нет распознавателя на этой машине: ролик не теряем, проверка речи — «warn»
            asr_missing = str(e)
            asr_info = {"wer": 0.0, "max_sync_drift_sec": 0.0, "asr_transcript": "", "asr_skipped": True}

    # Обновим копию паспорта фактическими таймкодами и статусом аудиопроверки.
    for line, ent in zip(passport["script"], segment_entries):
        line["t_start"] = ent["t_start"]
        line["t_end"] = ent["t_end"]
    if not fixed_timeline and len(passport.get("frames") or []) == len(segment_entries):
        for fr, ent in zip(passport["frames"], segment_entries):
            fr["t"] = round((ent["t_start"] + ent["t_end"]) / 2, 2)
    passport["node_card"]["duration_sec"] = total_duration
    ac = passport.setdefault("audio_check", {})
    ac.update({
        "backend": actual_backend,
        "model_id": resolved_model if actual_backend == "elevenlabs" else actual_backend,
        "voice_id": resolved_voice if actual_backend == "elevenlabs" else actual_backend,
        "measured_lufs": loud_stats["measured_lufs"],
        "true_peak_dbtp": loud_stats["true_peak_dbtp"],
        "wer": asr_info["wer"],
        "max_sync_drift_sec": asr_info["max_sync_drift_sec"],
        "prediction_pause_sec": 1.5 if pause_idx is not None else 0.0,
        "asr_transcript": asr_info["asr_transcript"],
        "status": "ok" if (abs(loud_stats["measured_lufs"] + 16.0) <= 1.5 and asr_info["wer"] <= 0.10 and not asr_missing) else "warn",
    })
    if asr_missing:
        ac["asr_skipped"] = True
        ac["asr_note"] = f"разборчивость не проверена: {asr_missing}"
    updated_passport = out_dir / "passport.json"
    updated_passport.write_text(json.dumps(passport, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "backend": actual_backend,
        "master_wav": master_wav,
        "master_mp3": master_mp3,
        "timings_json": timings_path,
        "subtitles_vtt": vtt_path,
        "passport_json": updated_passport,
        "total_duration_sec": total_duration,
        "measured_lufs": loud_stats["measured_lufs"],
        "wer": asr_info["wer"],
        "max_sync_drift_sec": asr_info["max_sync_drift_sec"],
        "pause_window": next((e.get("pause_window") for e in segment_entries if e.get("pause_window")), None),
        "resolved_voice_id": resolved_voice,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kurs.tts")
    ap.add_argument("passport", help="путь к passport.json")
    ap.add_argument("--out", required=True, help="папка для мастер-аудио и субтитров")
    ap.add_argument("--backend", default="auto", choices=SUPPORTED_BACKENDS)
    ap.add_argument("--voice-id", help="ID голоса (для ElevenLabs)")
    ap.add_argument("--model-id", help="модель (для ElevenLabs, напр. eleven_multilingual_v2)")
    ap.add_argument("--max-chars", type=int, default=8000)
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--skip-asr", action="store_true")
    args = ap.parse_args(argv)

    cfg = Config()
    res = synthesize_passport(
        cfg,
        Path(args.passport),
        Path(args.out),
        backend=args.backend,
        voice_id=args.voice_id,
        model_id=args.model_id,
        max_chars=args.max_chars,
        lang=args.lang,
        skip_asr=args.skip_asr,
    )
    print(json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in res.items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
