"""Конфигурация конвейера: пути к материалам, главы, выбор LLM."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify_id(raw: str) -> str:
    """Приводит строку (в т.ч. кириллицу и пути с пробелами) к id вида ^[a-z0-9_.-]+$."""
    out = []
    for ch in (raw or "").lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif "a" <= ch <= "z" or "0" <= ch <= "9" or ch in "._-":
            out.append(ch)
        else:
            out.append("_")
    slug = re.sub(r"_+", "_", "".join(out)).strip("_.-")
    return slug or "chapter"


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


@dataclass
class Chapter:
    id: str
    prefix: str
    title: str
    set: str = "train"
    book: str | None = None
    lines: tuple[int, int] | None = None
    file: str | None = None
    page_print_offset: int | None = None
    language: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def build_dir(self) -> Path:
        d = BUILD / self.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def md_path(self) -> Path:
        """Вырезанная глава — единственный источник цитат и страниц."""
        return self.build_dir / f"{self.id}.md"

    @property
    def meta_path(self) -> Path:
        return self.build_dir / "chapter_meta.json"


def _extract_heading_title(text: str, fallback: str) -> str:
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("#"):
            title = s.lstrip("#").strip()
            if title:
                return title
    return fallback


class Config:
    def __init__(self, path: Path | None = None):
        path = path or ROOT / "config.yaml"
        self.raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.input_dir = Path(
            os.environ.get("KURS_INPUT_DIR", self.raw["input_dir"])
        ).expanduser()
        self.schemas_dir = self.input_dir / self.raw["schemas_dir"]
        self.domain_dir = ROOT / self.raw["domain_pack"]
        self.llm = dict(self.raw.get("llm", {}))
        self.chapters: dict[str, Chapter] = {}
        for cid, c in (self.raw.get("chapters") or {}).items():
            lines = tuple(c["lines"]) if c.get("lines") else None
            known = {"prefix", "title", "set", "book", "lines", "file", "page_print_offset", "language"}
            self.chapters[cid] = Chapter(
                id=cid,
                prefix=c.get("prefix", cid.split("_")[0]),
                title=c.get("title", cid),
                set=c.get("set", "train"),
                book=c.get("book"),
                lines=lines,
                file=c.get("file"),
                page_print_offset=c.get("page_print_offset"),
                language=c.get("language"),
                extra={k: v for k, v in c.items() if k not in known},
            )

    def chapter(self, cid: str) -> Chapter:
        if cid in self.chapters:
            return self.chapters[cid]
        # Незнакомая глава (тест): передаётся путём к md-файлу.
        p = Path(cid).expanduser()
        if p.exists() and p.is_file():
            from .units import detect_language

            text = p.read_text(encoding="utf-8")
            ch_id = slugify_id(p.stem)
            raw_prefix = ch_id.split("_")[0].split(".")[0].split("-")[0]
            prefix = re.sub(r"[^a-z0-9_]", "_", raw_prefix).strip("_") or "ch"
            lang = detect_language(text)
            title = _extract_heading_title(text, p.stem)
            ch = Chapter(
                id=ch_id,
                prefix=prefix,
                title=title,
                set="test",
                file=str(p.resolve()),
                language=lang,
            )
            meta = {
                "id": ch.id,
                "prefix": ch.prefix,
                "title": ch.title,
                "set": ch.set,
                "file": ch.file,
                "language": ch.language,
            }
            ch.meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            self.chapters[ch.id] = ch
            return ch
        # Повторное обращение по сохранённому id незнакомой главы (например, в `kurs check <id>`).
        meta_path = BUILD / cid / "chapter_meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            ch = Chapter(
                id=meta["id"],
                prefix=meta["prefix"],
                title=meta["title"],
                set=meta.get("set", "test"),
                file=meta.get("file"),
                language=meta.get("language"),
            )
            self.chapters[ch.id] = ch
            return ch
        md_fallback = BUILD / cid / f"{cid}.md"
        if md_fallback.exists():
            from .units import detect_language

            text = md_fallback.read_text(encoding="utf-8")
            prefix = re.sub(r"[^a-z0-9_]", "_", cid.split("_")[0]).strip("_") or "ch"
            ch = Chapter(
                id=cid,
                prefix=prefix,
                title=_extract_heading_title(text, cid),
                set="test",
                file=str(md_fallback.resolve()),
                language=detect_language(text),
            )
            self.chapters[ch.id] = ch
            return ch
        raise KeyError(f"Неизвестная глава: {cid}")

    def resolve(self, rel: str) -> Path:
        p = Path(rel).expanduser()
        return p if p.is_absolute() else self.input_dir / p
