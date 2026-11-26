"""Сводка метрик по главе: деньги и человеко-минуты на узел и на минуту видео, доля ручного труда, минуты ментора."""
from __future__ import annotations

import csv

from kurs.metrics import OFFICIAL, economics, economics_section, write_official


def row(stage, usd, pm=0.0, secs=0, machine=1.0, mentor=0.0):
    return {"chapter": "demo", "stage": stage, "nodes": 10, "llm_tokens_in": 1, "llm_tokens_out": 1, "money_usd": usd,
            "person_minutes": pm, "mentor_minutes": mentor, "video_seconds": secs, "manual_share": 0, "machine_minutes": machine}


def test_economics_per_node_and_video_minute(tmp_path):
    rows = [row("layer", 0.5, pm=6, machine=9), row("course", 0.1, machine=1), row("video", 0.06, pm=3, secs=120, machine=0, mentor=4)]
    e = economics(rows)[0]
    assert abs(e["usd_per_node"] - 0.066) < 1e-9 and e["pm_per_node"] == 0.9
    assert abs(e["usd_per_video_min"] - 0.03) < 1e-9 and e["pm_per_video_min"] == 1.5
    assert abs(e["manual_share"] - 9 / 19) < 1e-9 and e["mentor"] == 4
    md = economics_section(rows)
    assert "| `demo` | 10 | 0.66 | 0.066 |" in md and "| 2.0 | 0.030 | 1.5 |" in md
    # служебное поле не попадает в таблицу организаторов
    out = write_official(rows, tmp_path / "metrics.csv")
    assert next(csv.reader(out.open(encoding="utf-8"))) == OFFICIAL


def test_no_video_gives_dash():
    assert "| 0.0 | — | — |" in economics_section([row("layer", 0.2)])
