"""辞書エントリのデータモデル。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

UNCATEGORIZED = "未分類"

# Windows でファイル名に使えない文字 + 制御文字
_SLUG_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_SLUG_SPACE = re.compile(r"\s+")
_SLUG_DASHES = re.compile(r"-{2,}")
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def slugify(term: str) -> str:
    """用語からファイル名 / URL に使える slug を作る。

    日本語はそのまま残す (NTFS も URL も UTF-8 で扱える) ので、
    ``API 設計`` -> ``api-設計`` のように読める名前になる。
    """
    s = unicodedata.normalize("NFKC", term).strip().lower()
    s = _SLUG_ILLEGAL.sub("-", s)
    s = _SLUG_SPACE.sub("-", s)
    s = _SLUG_DASHES.sub("-", s).strip("-. ")
    if not s or s.upper() in _WIN_RESERVED:
        digest = hashlib.sha1(term.encode("utf-8")).hexdigest()[:10]
        s = f"{s}-{digest}" if s else f"term-{digest}"
    return s[:80]


def _clean_list(values: list[str] | None) -> list[str]:
    """空要素と重複を落とし、順序は保つ。"""
    out: list[str] = []
    for v in values or []:
        v = (v or "").strip()
        if v and v not in out:
            out.append(v)
    return out


class EntryBase(BaseModel):
    term: str
    reading: str = ""
    aliases: list[str] = Field(default_factory=list)
    category: str = UNCATEGORIZED
    subcategory: str = ""
    summary: str = ""
    definition: str = ""
    examples: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source: str = ""

    @field_validator("aliases", "examples", "related", "tags", mode="before")
    @classmethod
    def _normalize_lists(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return _clean_list([str(x) for x in v])  # type: ignore[union-attr]

    @field_validator("term", "reading", "category", "subcategory", "summary", "definition", "source", mode="before")
    @classmethod
    def _normalize_str(cls, v: object) -> str:
        return "" if v is None else str(v).strip()

    @field_validator("category")
    @classmethod
    def _default_category(cls, v: str) -> str:
        return v or UNCATEGORIZED


class EntryDraft(EntryBase):
    """AI 下書き / API 入力。"""


class Entry(EntryBase):
    """保存済みエントリ。slug はファイル名が正。"""

    slug: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @property
    def surfaces(self) -> list[str]:
        """本文中でリンク対象にする表記のリスト。"""
        return _clean_list([self.term, *self.aliases])

    @property
    def path_label(self) -> str:
        return f"{self.category} / {self.subcategory}" if self.subcategory else self.category
