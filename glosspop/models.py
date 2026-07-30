"""辞書エントリのデータモデル。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator

UNCATEGORIZED = "未分類"

#: 辞書の置き場所。global = data/glossary、local = 開いているフォルダの .glosspop/
GLOBAL_SCOPE = "global"
LOCAL_SCOPE = "local"
SCOPES = (GLOBAL_SCOPE, LOCAL_SCOPE)

#: ローカルエントリの ref に付ける接頭辞。
#: カテゴリ名は「.」で始められない (normalize_category) ので実名と衝突しない
LOCAL_PREFIX = ".local"

# Windows でファイル名に使えない文字 + 制御文字
_SLUG_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_SLUG_SPACE = re.compile(r"\s+")
_SLUG_DASHES = re.compile(r"-{2,}")
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

#: カテゴリ名はディレクトリ名になるので、どの OS でも作れる文字だけに絞る。
#: FS で禁止 (< > : " / \ | ? *)、制御文字、URL で意味を持つ (# %) を弾く。
_CATEGORY_FORBIDDEN = re.compile(r'[<>:"/\\|?*#%\x00-\x1f\x7f]')
CATEGORY_MAX_LEN = 40


class CategoryNameError(ValueError):
    """カテゴリ名がディレクトリ名として使えない。"""


def normalize_category(name: str) -> str:
    """カテゴリ名を検証して正規化する。

    Windows / macOS / Linux のどれでもディレクトリを作れて、URL に載せても
    壊れない形だけを通す。macOS が NFD で返してくる濁点を揃えるため NFC 正規化する。
    """
    name = unicodedata.normalize("NFC", name or "").strip()
    if not name:
        raise CategoryNameError("カテゴリ名を入力してください")
    if len(name) > CATEGORY_MAX_LEN:
        raise CategoryNameError(f"カテゴリ名は {CATEGORY_MAX_LEN} 文字までです（{len(name)} 文字）")

    bad = sorted(set(_CATEGORY_FORBIDDEN.findall(name)))
    if bad:
        shown = " ".join(c if c.isprintable() else f"U+{ord(c):04X}" for c in bad)
        raise CategoryNameError(f"カテゴリ名に使えない文字が含まれています: {shown}")
    if name.startswith(".") or name.endswith("."):
        raise CategoryNameError("カテゴリ名の先頭と末尾に「.」は使えません")
    if name.upper().split(".")[0] in _WIN_RESERVED:
        raise CategoryNameError(f"「{name}」は Windows の予約名なので使えません")
    return name


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

    #: どちらの辞書に入れるか。保存先を決めるだけで、ファイルには書かない
    scope: str = GLOBAL_SCOPE

    @field_validator("scope", mode="before")
    @classmethod
    def _normalize_scope(cls, v: object) -> str:
        value = str(v or "").strip() or GLOBAL_SCOPE
        return value if value in SCOPES else GLOBAL_SCOPE


class Entry(EntryBase):
    """保存済みエントリ。

    保存先は ``<辞書ルート>/<category>/<slug>.md``。ディレクトリ名が
    ``category``、ファイル名が ``slug`` の正であり、frontmatter には書かない。
    ``scope`` も同じくパス（どちらの辞書にあるか）が正。
    同じ用語名でもカテゴリが違えば別エントリとして併存できる。
    """

    slug: str = ""
    scope: str = GLOBAL_SCOPE
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @property
    def ref(self) -> str:
        """エントリを一意に指す ID。URL でもそのまま使う。

        グローバルは ``カテゴリ/slug``、ローカルは ``.local/カテゴリ/slug``。
        グローバル側の形を変えないので、既存の URL と CLI はそのまま通る。
        """
        return make_ref(self.scope, self.category, self.slug)

    @property
    def is_local(self) -> bool:
        return self.scope == LOCAL_SCOPE

    @property
    def surfaces(self) -> list[str]:
        """本文中でリンク対象にする表記のリスト。"""
        return _clean_list([self.term, *self.aliases])

    @property
    def path_label(self) -> str:
        """UI に出す所在。ローカル辞書は先頭に印を付ける。

        吹き出しでローカルとグローバルの同名エントリが並ぶので、
        カテゴリ名だけだとどちらの意味か見分けられない。
        """
        base = f"{self.category} / {self.subcategory}" if self.subcategory else self.category
        return f"📁 {base}" if self.is_local else base


def make_ref(scope: str, category: str, slug: str) -> str:
    prefix = f"{LOCAL_PREFIX}/" if scope == LOCAL_SCOPE else ""
    return f"{prefix}{category}/{slug}"


def split_ref(ref: str) -> tuple[str, str, str]:
    """ref を ``(scope, カテゴリ, slug)`` に分解する。

    区切りが無ければ ``CategoryNameError``。
    """
    rest = (ref or "").strip()
    scope = GLOBAL_SCOPE
    if rest.startswith(f"{LOCAL_PREFIX}/"):
        scope = LOCAL_SCOPE
        rest = rest[len(LOCAL_PREFIX) + 1:]
    category, sep, slug = rest.rpartition("/")
    if not sep or not category or not slug:
        raise CategoryNameError(f"不正な参照です: {ref!r}（「カテゴリ/slug」の形で指定してください）")
    return scope, category, slug
