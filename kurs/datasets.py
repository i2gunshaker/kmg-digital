"""Загрузка и детерминированная нарезка открытых данных (Volve 15/9-F-1 B) без хранения сырых LAS в git."""
from __future__ import annotations

import hashlib
import json
import statistics
import tempfile
import urllib.request
from pathlib import Path

from .config import ROOT

VOLVE_COMMIT = "c293edf37c1b567b8dc9c81ea88f2cf288eab423"
VOLVE_BASE = f"https://raw.githubusercontent.com/andymcdgeo/spwla_volve/{VOLVE_COMMIT}/15_9-F-1B"
VOLVE_FILES = {
    "INPUT": {
        "url": f"{VOLVE_BASE}/WLC_PETRO_COMPUTED_INPUT_1.LAS",
        "sha256": "685b009dbf038ce0a547b6bcb249ad60f7583515c39df267e4218ecb98d1797c",
    },
    "OUTPUT": {
        "url": f"{VOLVE_BASE}/WLC_PETRO_COMPUTED_OUTPUT_1.LAS",
        "sha256": "f492c088cc8ad2ebb5c9e4bd1ee94dbf0ccab3e772be54d3903254d2a251ae4e",
    },
}

DEFAULT_WINDOWS = [
    {"id": "A", "label": "Интервал A (3265–3270 м)", "d_min": 3265.0, "d_max": 3270.0},
    {"id": "B", "label": "Интервал B (3305–3310 м)", "d_min": 3305.0, "d_max": 3310.0},
    {"id": "C", "label": "Интервал C (3325–3330 м)", "d_min": 3325.0, "d_max": 3330.0},
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_las_ascii(path: Path) -> tuple[list[str], list[dict[str, float | None]]]:
    """Читает секции ~C и ~A из LAS 2.0 без сторонних зависимостей."""
    curves: list[str] = []
    rows: list[dict[str, float | None]] = []
    null_val = -999.25
    section = ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = raw.strip()
        if not ln:
            continue
        if ln.startswith("~"):
            section = ln[1:2].upper()
            continue
        if ln.startswith("#"):
            continue
        if section == "W" and ln.upper().startswith("NULL"):
            try:
                null_val = float(ln.split(":", 1)[0].split(".", 1)[1].strip().split()[-1])
            except Exception:
                pass
        elif section == "C":
            mnem = ln.split(".", 1)[0].strip().split()[0]
            curves.append(mnem)
        elif section == "A":
            parts = ln.split()
            if len(parts) != len(curves):
                continue
            row: dict[str, float | None] = {}
            for name, tok in zip(curves, parts):
                val = float(tok)
                row[name] = None if abs(val - null_val) < 1e-6 else val
            rows.append(row)
    return curves, rows


def build_volve_slice(out_path: Path | None = None) -> Path:
    """Скачивает LAS Volve 15/9-F-1 B во временную папку, проверяет SHA-256 и сохраняет 3 окна в JSON."""
    out_path = out_path or ROOT / "domain/geology/datasets/volve_15_9_f1b.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        parsed: dict[str, dict[int, dict[str, float | None]]] = {}
        for kind, spec in VOLVE_FILES.items():
            dest = tmp_dir / f"{kind}.las"
            urllib.request.urlretrieve(spec["url"], dest)
            digest = _sha256(dest)
            if digest != spec["sha256"]:
                raise ValueError(f"SHA-256 mismatch for {kind}: {digest} != {spec['sha256']}")
            _, rows = parse_las_ascii(dest)
            by_dm: dict[int, dict[str, float | None]] = {}
            for r in rows:
                d = r.get("DEPTH")
                if d is not None:
                    by_dm[round(d * 10)] = r
            parsed[kind] = by_dm

    in_map = parsed["INPUT"]
    out_map = parsed["OUTPUT"]
    intervals = []
    for w in DEFAULT_WINDOWS:
        lo = round(w["d_min"] * 10)
        hi = round(w["d_max"] * 10)
        pts = []
        for dm in range(lo, hi):
            ir = in_map.get(dm)
            orr = out_map.get(dm)
            if not ir or not orr:
                continue
            pts.append(
                {
                    "depth_m": round(dm / 10.0, 1),
                    "GR": round(ir["GR"], 2) if ir.get("GR") is not None else None,
                    "RT": round(ir["RT"], 3) if ir.get("RT") is not None else None,
                    "NPHI": round(ir["NPHI"], 4) if ir.get("NPHI") is not None else None,
                    "RHOB": round(ir["RHOB"], 3) if ir.get("RHOB") is not None else None,
                    "PHIF": round(orr["PHIF"], 4) if orr.get("PHIF") is not None else None,
                    "VSH": round(orr["VSH"], 4) if orr.get("VSH") is not None else None,
                    "SW": round(orr["SW"], 4) if orr.get("SW") is not None else None,
                    "SAND_FLAG": int(round(orr["SAND_FLAG"])) if orr.get("SAND_FLAG") is not None else None,
                }
            )
        summary = {
            "count": len(pts),
            "median_GR": round(statistics.median(p["GR"] for p in pts if p["GR"] is not None), 2),
            "median_RT": round(statistics.median(p["RT"] for p in pts if p["RT"] is not None), 2),
            "median_PHIF": round(statistics.median(p["PHIF"] for p in pts if p["PHIF"] is not None), 3),
            "median_VSH": round(statistics.median(p["VSH"] for p in pts if p["VSH"] is not None), 3),
            "median_SW": round(statistics.median(p["SW"] for p in pts if p["SW"] is not None), 3),
            "sand_flag_1_count": sum(1 for p in pts if p["SAND_FLAG"] == 1),
        }
        intervals.append({**w, "summary": summary, "points": pts})

    payload = {
        "dataset_id": "data.volve_15_9_f1b",
        "well": "15/9-F-1 B",
        "field": "Volve (Норвегия)",
        "depth_reference": "MD RKB, м (шаг 0.1 м)",
        "license": "Equinor Volve Open Data License (на основе CC BY 4.0); правообладатели: Equinor, ExxonMobil Exploration & Production Norway AS, Bayerngas Norge AS",
        "sources": VOLVE_FILES,
        "operator_rule": {
            "name": "Правило выделения песчаника (Hugin, отчёт оператора Volve)",
            "condition": "PHIF > 0.10 и VSH < 0.50",
            "note": "Выделение коллектора (SAND_FLAG) само по себе не означает наличие нефти: водоносный песчаник тоже имеет SAND_FLAG=1, а плотный прослой с повышенным RT имеет PHIF < 0.10.",
        },
        "units": {
            "GR": "API",
            "RT": "Ом·м",
            "NPHI": "доля",
            "RHOB": "г/см³",
            "PHIF": "доля",
            "VSH": "доля",
            "SW": "доля",
        },
        "intervals": intervals,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return out_path


def load_volve_slice(domain_dir: Path) -> dict | None:
    p = domain_dir / "datasets" / "volve_15_9_f1b.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


if __name__ == "__main__":
    p = build_volve_slice()
    data = json.loads(p.read_text(encoding="utf-8"))
    print(f"Saved {p} ({p.stat().st_size} bytes)")
    for iv in data["intervals"]:
        print(iv["id"], iv["label"], iv["summary"])
