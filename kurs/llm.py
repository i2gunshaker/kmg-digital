"""Переходник к языковой модели: ответ строго по JSON-схеме, кэш на диске, журнал и предел расхода.

Роли: `senior` — граф узлов, задания, маршрутизация; `fast` — карточки узлов.
Модели и цены заданы в config.yaml и заменяются без правки кода:
  KURS_LLM_BACKEND   anthropic | openai
  KURS_LLM_PROFILE   cheap | mid | max — набор моделей из config.yaml
  KURS_LLM_MODEL     одна модель на все роли
  OPENAI_BASE_URL    совместимый сервер (например, контур компании)
  KURS_LLM_BUDGET    сколько разрешено потратить за этот запуск, $ (по умолчанию 0: платных вызовов нет)
  KURS_LLM_TOTAL     общий предел за всё время, $ (иначе llm.total_usd)
  KURS_LLM_OFFLINE=1 только кэш: сетевой вызов запрещён
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import jsonschema

from .config import BUILD, Config


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


class OfflineMiss(LLMError):
    pass


class SchemaTooLarge(LLMError):
    """Сервер не смог построить грамматику по схеме: просим JSON словами и проверяем сами."""


# Замер по журналу: 1,95–2,19 знака русского текста с формулами на токен; берём меньше, чтобы прогноз был с запасом.
CHARS_PER_TOKEN = 1.9


@dataclass
class Usage:
    input: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "input": self.input,
            "cache_write": self.cache_write,
            "cache_read": self.cache_read,
            "output": self.output,
        }


@dataclass
class Reply:
    text: str
    model: str
    usage: Usage
    stop: str = ""  # почему ответ негоден: обрезан или отказ; расход всё равно учитывается


def log_total(log_path: Path) -> float:
    """Сколько уже потрачено за всё время по журналу вызовов, $."""
    if not log_path.exists():
        return 0.0
    total = 0.0
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            total += float(json.loads(line).get("cost_usd") or 0.0)
        except (ValueError, AttributeError):
            continue
    return total


class Wallet:
    """Кошелёк запуска: один на все главы и потоки.

    Держит два предела сразу — на этот запуск и общий за всё время (по журналу). Перед вызовом место под его цену
    бронируется, поэтому параллельные запросы не выходят за предел. Платный запуск на машине может быть только один."""

    def __init__(self, run_limit: float, total_limit: float, log_path: Path):
        self.run_limit = run_limit
        self.total_limit = total_limit
        self.log_path = log_path
        self.before = log_total(log_path)
        self.spent = 0.0
        self.reserved = 0.0
        self._lock = threading.Lock()
        self._file = None

    def _hold(self) -> None:
        if self._file is not None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.log_path.parent / ".llm.lock", "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            f.close()
            raise BudgetExceeded("другой платный запуск уже идёт; дождитесь его, чтобы пределы не сложились") from e
        self._file = f
        self.before = log_total(self.log_path) - self.spent

    def release(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    def reserve(self, label: str, price: float) -> None:
        if price <= 0:  # бесплатная модель (локальный сервер): пределы ни при чём
            return
        with self._lock:
            if self.run_limit <= 0:
                raise BudgetExceeded(f"{label}: платные вызовы выключены; разрешить — --max-usd N (этот вызов около ${price:.3f})")
            self._hold()
            need = self.spent + self.reserved + price
            if need > self.run_limit:
                raise BudgetExceeded(
                    f"{label}: потрачено ${self.spent:.3f} из ${self.run_limit:.2f} на запуск, на вызов нужно около ${price:.3f}"
                )
            if self.before + need > self.total_limit:
                raise BudgetExceeded(
                    f"{label}: за всё время потрачено ${self.before + self.spent:.2f} при общем пределе ${self.total_limit:.2f}; "
                    "чтобы продолжить, поднимите llm.total_usd в config.yaml"
                )
            self.reserved += price

    def settle(self, price: float, actual: float) -> None:
        with self._lock:
            if price > 0:
                self.reserved = max(0.0, self.reserved - price)
            self.spent += actual


# transport(model, system, user, schema | None, effort, max_tokens) -> Reply
Transport = Callable[[str, list[str], str, "dict | None", str, int], Reply]


def strict_schema(schema: dict) -> dict:
    """Приводит схему к строгому виду: все поля обязательны, лишних полей нет."""
    if not isinstance(schema, dict):
        return schema
    out = {k: v for k, v in schema.items()}
    if out.get("type") == "object":
        props = {k: strict_schema(v) for k, v in out.get("properties", {}).items()}
        out["properties"] = props
        out["required"] = list(props)
        out["additionalProperties"] = False
    if out.get("type") == "array" and "items" in out:
        out["items"] = strict_schema(out["items"])
    if "anyOf" in out:
        out["anyOf"] = [strict_schema(s) for s in out["anyOf"]]
    return out


_LOST_ESCAPES = {"\x08": "\\b", "\x0c": "\\f", "\r": "\\r", "\t": "\\t"}


def restore_latex(value: Any) -> Any:
    """Возвращает обратную черту командам LaTeX, съеденным при разборе JSON («\\frac» → перевод страницы + «rac»)."""
    if isinstance(value, str):
        return re.sub("[\x08\x0c\r\t](?=[A-Za-z])", lambda m: _LOST_ESCAPES[m.group(0)], value)
    if isinstance(value, list):
        return [restore_latex(v) for v in value]
    if isinstance(value, dict):
        return {k: restore_latex(v) for k, v in value.items()}
    return value


def parse_json(text: str) -> Any:
    """Разбирает ответ; вокруг объекта может стоять ограждение ```json, а в строках — одиночные черты LaTeX."""
    a, b = text.find("{"), text.rfind("}")
    if a == -1 or b <= a:
        raise ValueError("в ответе нет JSON-объекта")
    body = text[a : b + 1]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return json.loads(re.sub(r'\\\\|\\(?!["\\/bfnrtu])', lambda m: "\\\\", body))


# ---------------------------------------------------------------- бэкенды
def _b64(path: Path) -> tuple[str, str]:
    import base64

    media = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower(), "image/png")
    return media, base64.b64encode(path.read_bytes()).decode("ascii")


def _anthropic_content(user: str, images: list[Path] | None) -> Any:
    if not images:
        return user
    blocks: list[dict[str, Any]] = []
    for p in images:
        media, data = _b64(p)
        blocks.append({"type": "text", "text": f"Кадр {p.name}:"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
    return blocks + [{"type": "text", "text": user}]


def _openai_content(user: str, images: list[Path] | None) -> Any:
    if not images:
        return user
    parts: list[dict[str, Any]] = []
    for p in images:
        media, data = _b64(p)
        parts.append({"type": "text", "text": f"Кадр {p.name}:"})
        parts.append({"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}})
    return parts + [{"type": "text", "text": user}]


def _anthropic_transport() -> Transport:
    import anthropic

    client = anthropic.Anthropic(max_retries=6, timeout=1200.0)

    def call(model: str, system: list[str], user: str, schema: dict | None, effort: str, max_tokens: int, images: list[Path] | None = None) -> Reply:
        out_cfg: dict[str, Any] = {"effort": effort}
        if schema is not None:
            out_cfg["format"] = {"type": "json_schema", "schema": schema}
        blocks: list[dict[str, Any]] = [{"type": "text", "text": s} for s in system if s]
        if blocks:
            # Общая часть (правила и глава) одинакова у всех запросов прохода — её и кэшируем.
            blocks[-1]["cache_control"] = {"type": "ephemeral"}
        try:
            extra: dict[str, Any] = {}
            if not model.startswith("claude-haiku"):  # у Haiku запасной модели на сервере нет
                extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
            with client.beta.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=blocks,
                messages=[{"role": "user", "content": _anthropic_content(user, images)}],
                output_config=out_cfg,
                **extra,
            ) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise LLMError("ключ API не принят (401): проверьте ANTHROPIC_API_KEY в .env") from e
        except anthropic.PermissionDeniedError as e:
            raise LLMError(f"у ключа нет доступа к модели {model} (403)") from e
        except anthropic.NotFoundError as e:
            raise LLMError(f"модель {model} не найдена (404)") from e
        except anthropic.BadRequestError as e:
            if "grammar is too large" in str(e.message) or "schema is too complex" in str(e.message).lower():
                raise SchemaTooLarge(str(e.message)[:200]) from e
            raise LLMError(f"запрос отклонён (400): {e.message}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"ошибка API ({e.status_code}): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"нет связи с API: {type(e).__name__}") from e

        stop = ""  # оборванный ответ тоже оплачен: ошибка поднимается после записи расхода
        if msg.stop_reason == "refusal":
            stop = f"модель отказалась отвечать (категория: {msg.stop_details.category if msg.stop_details else None})"
        elif msg.stop_reason == "max_tokens":
            stop = f"ответ обрезан на {max_tokens} токенах"
        text = "".join(b.text for b in msg.content if b.type == "text")
        u = msg.usage
        return Reply(
            text=text,
            model=msg.model,
            usage=Usage(
                input=u.input_tokens or 0,
                cache_write=u.cache_creation_input_tokens or 0,
                cache_read=u.cache_read_input_tokens or 0,
                output=u.output_tokens or 0,
            ),
            stop=stop,
        )

    return call


def _openai_transport() -> Transport:
    import openai

    client = openai.OpenAI(max_retries=6, timeout=1200.0)

    def call(model: str, system: list[str], user: str, schema: dict | None, effort: str, max_tokens: int, images: list[Path] | None = None) -> Reply:
        fmt: dict[str, Any] = {"type": "json_object"}
        if schema is not None:
            fmt = {"type": "json_schema", "json_schema": {"name": "result", "strict": True, "schema": schema}}
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "\n\n".join(s for s in system if s)},
                    {"role": "user", "content": _openai_content(user, images)},
                ],
                response_format=fmt,
                max_completion_tokens=max_tokens,
            )
        except openai.AuthenticationError as e:
            raise LLMError("ключ API не принят (401): проверьте OPENAI_API_KEY в .env") from e
        except openai.APIStatusError as e:
            raise LLMError(f"ошибка API ({e.status_code}): {e.message}") from e
        except openai.APIConnectionError as e:
            raise LLMError(f"нет связи с API: {type(e).__name__}") from e
        choice = resp.choices[0]
        stop = f"ответ обрезан на {max_tokens} токенах" if choice.finish_reason == "length" else ""
        if getattr(choice.message, "refusal", None):
            stop = "модель отказалась отвечать"
        u = resp.usage
        cached = 0
        if u and u.prompt_tokens_details:
            cached = u.prompt_tokens_details.cached_tokens or 0
        return Reply(
            text=choice.message.content or "",
            model=resp.model,
            usage=Usage(
                input=(u.prompt_tokens - cached) if u else 0,
                cache_read=cached,
                output=u.completion_tokens if u else 0,
            ),
            stop=stop,
        )

    return call


# ---------------------------------------------------------------- клиент
class LLM:
    def __init__(
        self,
        cfg: Config,
        *,
        scope: str = "",
        transport: Transport | None = None,
        cache_dir: Path | None = None,
        log_path: Path | None = None,
        budget: float | None = None,
        wallet: Wallet | None = None,
    ):
        self.cfg = cfg
        self.scope = scope
        self.opts = cfg.llm
        self.backend = self._pick_backend(transport is not None)
        self._transport = transport
        self._real = transport is None
        self.cache_dir = cache_dir or BUILD / ".cache" / "llm"
        self.log_path = log_path or BUILD / "llm_usage.jsonl"
        if wallet is None:
            run = budget if budget is not None else float(os.environ.get("KURS_LLM_BUDGET") or 0)
            total = float(os.environ.get("KURS_LLM_TOTAL") or self.opts.get("total_usd", 20.0))
            wallet = Wallet(run, total, self.log_path)
        self.wallet = wallet
        self.offline = os.environ.get("KURS_LLM_OFFLINE") == "1"
        self.spent = 0.0
        self.calls = 0
        self.cache_hits = 0
        self._by_words: set[str] = set()
        self._lock = threading.Lock()

    # -- настройка
    def _pick_backend(self, has_transport: bool) -> str:
        backend = os.environ.get("KURS_LLM_BACKEND") or self.opts.get("backend", "auto")
        if backend == "auto":
            if os.environ.get("ANTHROPIC_API_KEY"):
                return "anthropic"
            if os.environ.get("OPENAI_API_KEY"):
                return "openai"
            # Без ключа остаётся работа из кэша: имя бэкенда нужно только для ключа кэша.
            return "anthropic"
        if backend not in ("anthropic", "openai"):
            raise LLMError(f"неизвестный бэкенд LLM: {backend}")
        return backend

    def model_for(self, role: str) -> str:
        forced = os.environ.get("KURS_LLM_MODEL")
        if forced:
            return forced
        models = (self.opts.get("models") or {}).get(self.backend) or {}
        if role not in models:
            raise LLMError(f"в config.yaml нет модели для роли {role} ({self.backend})")
        return models[role]

    def describe(self) -> str:
        return f"{self.backend}: senior={self.model_for('senior')}, fast={self.model_for('fast')}"

    def cost(self, model: str, usage: Usage) -> float:
        price = (self.opts.get("prices") or {}).get(model)
        if not price:
            raise LLMError(f"у модели {model} нет цены в config.yaml (llm.prices): без неё расход не проверить")
        usd = (
            usage.input * price["input"]
            + usage.cache_write * price.get("cache_write", price["input"])
            + usage.cache_read * price.get("cache_read", price["input"])
            + usage.output * price["output"]
        ) / 1_000_000
        long = price.get("long")  # длинный запрос у некоторых моделей дороже
        if long and usage.input + usage.cache_write + usage.cache_read > long["over"]:
            usd *= long["times"]
        return usd

    def forecast(self, model: str, system: list[str], user: str, schema: dict, max_tokens: int, images: int = 0) -> float:
        """Цена вызова до вызова: вход по длине текста (и ~1600 токенов на кадр), выход по верхней границе обычного ответа."""
        chars = sum(map(len, system)) + len(user) + len(json.dumps(schema, ensure_ascii=False))
        return self.cost(model, Usage(cache_write=int(chars / CHARS_PER_TOKEN) + 1600 * images, output=min(max_tokens, 12000)))

    # -- вызов
    def _key(self, model: str, system: list[str], user: str, schema: dict, effort: str, images: list[Path] | None = None) -> str:
        rec: dict[str, Any] = {"b": self.backend, "m": model, "s": system, "u": user, "j": schema, "e": effort}
        if images:  # без картинок ключ прежний: старый кэш остаётся в силе
            rec["i"] = [hashlib.sha256(p.read_bytes()).hexdigest() for p in images]
        blob = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def json(
        self,
        role: str,
        system: list[str],
        user: str,
        schema: dict,
        *,
        label: str,
        effort: str | None = None,
        max_tokens: int = 64000,
        images: list[Path] | None = None,
    ) -> dict:
        """Ответ модели по схеме. Одинаковый запрос берётся с диска и ничего не стоит. images — PNG/JPEG для модели со зрением."""
        schema = strict_schema(schema)
        model = self.model_for(role)
        effort = effort or (self.opts.get("effort") or {}).get(role, "medium")
        key = self._key(model, system, user, schema, effort, images)
        path = self.cache_dir / key[:2] / f"{key}.json"
        if path.exists():
            rec = json.loads(path.read_text(encoding="utf-8"))
            with self._lock:
                self.cache_hits += 1
            self._log(label, rec["model"], Usage(**rec["usage"]), 0.0, 0.0, True)
            return rec["data"]
        allow = self.opts.get("allow")
        if self._real and allow and model not in allow:
            raise LLMError(f"{label}: модель {model} запрещена, разрешены только: {', '.join(allow)} (llm.allow в config.yaml)")
        if self.offline:
            raise OfflineMiss(f"{label}: ответа нет в кэше, а сетевые вызовы запрещены (KURS_LLM_OFFLINE=1)")
        price = self.forecast(model, system, user, schema, max_tokens, len(images or []))
        with self._lock:
            if self._transport is None:
                if price > 0 and self.wallet.run_limit <= 0:
                    self.wallet.reserve(label, price)  # отказ до создания клиента: ключ не читается
                env = "ANTHROPIC_API_KEY" if self.backend == "anthropic" else "OPENAI_API_KEY"
                if not os.environ.get(env):
                    raise LLMError(f"нет ключа {env}: добавьте его в .env")
                self._transport = _anthropic_transport() if self.backend == "anthropic" else _openai_transport()

        last: Exception | None = None
        schema_id = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
        note = ""
        for _ in range(3):
            by_words = schema_id in self._by_words
            text_schema = (
                "\n\nВерни один JSON-объект без пояснений и без ограждений, строго по этой схеме "
                "(обратные черты в LaTeX удваивай):\n" + json.dumps(schema, ensure_ascii=False)
                if by_words
                else ""
            )
            self.wallet.reserve(label, price)
            cost = 0.0
            t0 = time.monotonic()
            try:
                args = (model, system, user + note + text_schema, None if by_words else schema, effort, max_tokens)
                reply = self._transport(*args, images=images) if images else self._transport(*args)
                cost = self.cost(model, reply.usage)
            except SchemaTooLarge:
                self._by_words.add(schema_id)
                continue
            finally:
                self.wallet.settle(price, cost)
            dt = time.monotonic() - t0
            with self._lock:
                self.spent += cost
                self.calls += 1
            self._log(label, reply.model, reply.usage, cost, dt, False)
            if reply.stop:
                raise LLMError(f"{label}: {reply.stop}")
            try:
                data = restore_latex(parse_json(reply.text))
                jsonschema.validate(data, schema)
            except (ValueError, jsonschema.ValidationError) as e:
                last = e
                msg = e.message if isinstance(e, jsonschema.ValidationError) else str(e)
                note = f"\n\nПрошлый ответ не разобран: {msg[:300]}. Верни корректный JSON."
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(
                json.dumps(
                    {"label": label, "model": reply.model, "usage": reply.usage.as_dict(), "data": data},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            tmp.replace(path)
            return data
        raise LLMError(f"{label}: ответ не прошёл проверку по схеме ({str(last)[:200]})")

    def _log(self, label: str, model: str, usage: Usage, cost: float, dt: float, cached: bool) -> None:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "scope": self.scope,
            "label": label,
            "model": model,
            **usage.as_dict(),
            "cost_usd": round(cost, 5),
            "seconds": round(dt, 1),
            "from_cache": cached,
        }
        with self._lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
