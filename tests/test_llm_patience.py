"""Переходник ждёт, когда сервер просит подождать, и не бросает главу."""
import json

import pytest

from kurs import llm as llm_mod
from kurs.config import Config
from kurs.llm import LLM, LLMError, RateLimited, Reply, Usage

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def client(tmp_path, monkeypatch, fails):
    monkeypatch.delenv("KURS_LLM_OFFLINE", raising=False)
    naps = []
    monkeypatch.setattr(llm_mod.time, "sleep", naps.append)
    left = [fails]

    def transport(model, system, user, schema, effort, max_tokens, images=None):
        if left[0]:
            left[0] -= 1
            raise RateLimited("сервер занят (429)", 7)
        return Reply(json.dumps({"ok": True}), model, Usage(input=10, output=5))

    return LLM(Config(), transport=transport, cache_dir=tmp_path / "c", log_path=tmp_path / "u.jsonl", budget=1.0), naps


def test_waits_out_rate_limit(tmp_path, monkeypatch):
    llm, naps = client(tmp_path, monkeypatch, 5)
    assert llm.json("fast", ["s"], "u", SCHEMA, label="x") == {"ok": True}
    assert len(naps) == 5 and all(7 <= n <= 9 for n in naps) and llm.calls == 1


def test_gives_up_after_patience(tmp_path, monkeypatch):
    monkeypatch.setenv("KURS_LLM_PATIENCE", "1")
    llm, naps = client(tmp_path, monkeypatch, 5)
    with pytest.raises(LLMError, match="ждать больше нельзя"):
        llm.json("fast", ["s"], "u", SCHEMA, label="x")
    assert naps == [] and llm.spent == 0
