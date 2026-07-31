"""辞書エントリのデータモデル。"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator, model_validator

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


#: 関係の上下。**すべて「自分から見て相手 (``to``) がどうか」** で書く。
#: 向きの基準を 1 つに固定しないと、書く側も読む側も毎回迷う。
RANK_UP = "上"
RANK_DOWN = "下"
RANK_EVEN = "対等"
RANKS = (RANK_UP, RANK_DOWN, RANK_EVEN)

#: 揺れやすい書き方を吸収する（AI も人も「上位」「同格」などと書く）
_RANK_ALIASES = {
    "上位": RANK_UP, "上下": RANK_UP, "親": RANK_UP, "師": RANK_UP,
    "下位": RANK_DOWN, "子": RANK_DOWN, "弟子": RANK_DOWN,
    "同格": RANK_EVEN, "同等": RANK_EVEN, "水平": RANK_EVEN,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_link(text: str) -> str:
    """関係の参照先を正規化する。

    「空白を足しただけで切れる」を防ぐのがここの仕事。全角/半角の揺れ (NFC)、
    前後と連続する空白、``/`` の周りの空白を潰す。casefold はしない
    （表示にそのまま使うため。照合側で無視する）。
    """
    s = unicodedata.normalize("NFC", text or "").strip()
    s = _SLUG_SPACE.sub(" ", s)
    return re.sub(r"\s*/\s*", "/", s)


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


class Relation(BaseModel):
    """このエントリから見た、他のエントリとの関係。

    **全フィールドが「自分 → ``to``」の向きで書かれている。** 逆向きの一言は
    ``back`` に入れ、``back`` が空なら一方的な関係とみなす（矢印は片方だけ）。
    専用の「相互/一方的」フラグを置かないのは、二重に持つとずれるため。

    ``to`` は ``カテゴリ/slug`` の ref でも用語名でも書ける（wiki の名前と同じ
    感覚で手書きできるように）。解決は ``relations.resolve()`` が行う。
    """

    to: str
    label: str = ""          # 自分 → to の一言 ("親友" "師")
    back: str = ""           # to → 自分 の一言。空なら一方的
    rank: str = ""           # 上 | 下 | 対等 (to が自分から見てどうか)。空なら未指定
    reveal: str = ""         # この関係が判明する位置 ("第6章")。相関図のネタバレ抑止に使う

    @field_validator("to", mode="before")
    @classmethod
    def _normalize_to(cls, v: object) -> str:
        return normalize_link("" if v is None else str(v))

    @field_validator("label", "back", "reveal", mode="before")
    @classmethod
    def _normalize_text(cls, v: object) -> str:
        return "" if v is None else str(v).strip()

    @field_validator("rank", mode="before")
    @classmethod
    def _normalize_rank(cls, v: object) -> str:
        s = str(v or "").strip()
        s = _RANK_ALIASES.get(s, s)
        return s if s in RANKS else ""

    @property
    def mutual(self) -> bool:
        return bool(self.back)


class EntryBase(BaseModel):
    term: str
    reading: str = ""
    aliases: list[str] = Field(default_factory=list)
    category: str = UNCATEGORIZED
    subcategory: str = ""
    summary: str = ""
    definition: str = ""
    examples: list[str] = Field(default_factory=list)
    #: 他のエントリとの関係（向き・上下・一言）。相関図と辞書ページに出す。
    #: 旧 ``related``（向きも一言も無いただの名前の並び）はここに吸収する
    relations: list[Relation] = Field(default_factory=list)
    #: 改名・カテゴリ移動で捨てた古い ref。wiki のリダイレクトと同じ役割で、
    #: 参照側を書き換えずに済ませるために持つ (relations.resolve() が見る)
    former_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source: str = ""
    #: 初出の位置。小説の人物辞書などで「どこで出てきた語か」を残すために使う
    first_file: str = ""        # content ルートからの相対パス
    first_locator: str = ""     # 表示用の位置 ("L.42" / "p.42" / "第3章" など)

    @model_validator(mode="before")
    @classmethod
    def _absorb_related(cls, data: object) -> object:
        """旧 ``related`` を ``relations`` に畳む。

        「この語と繋がっている語」を 2 か所に書けると、どちらに書くか毎回迷ううえ、
        ``related`` に書いたぶんは相関図に出ない。入り口で 1 つにまとめる。

        ファイル・API・CLI のどの経路から来ても通るように、ここ 1 か所でやる。
        既存の ``relations`` が正で、同じ行き先なら ``Relation`` の検証が潰す。
        """
        if not isinstance(data, dict):
            return data
        legacy = data.get("related")
        if not legacy:
            return data
        data = dict(data)
        names = [legacy] if isinstance(legacy, str) else list(legacy)
        data["relations"] = [
            *(data.get("relations") or []),
            *({"to": str(n)} for n in names if str(n).strip()),
        ]
        data.pop("related", None)
        return data

    @field_validator("aliases", "examples", "tags", mode="before")
    @classmethod
    def _normalize_lists(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return _clean_list([str(x) for x in v])  # type: ignore[union-attr]

    @field_validator("former_refs", mode="before")
    @classmethod
    def _normalize_former(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return _clean_list([normalize_link(str(x)) for x in v])  # type: ignore[union-attr]

    @field_validator("relations", mode="after")
    @classmethod
    def _clean_relations(cls, v: list[Relation]) -> list[Relation]:
        """行き先が空のものを落とし、同じ行き先は最初の 1 件だけ残す。

        同じ相手への関係を 2 行書けると、相関図に多重辺が出て、どちらが
        正なのかも決まらない。入り口で 1 本にしておく。
        """
        out: list[Relation] = []
        seen: set[str] = set()
        for rel in v:
            key = rel.to.casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(rel)
        return out

    @field_validator(
        "term", "reading", "category", "subcategory", "summary", "definition",
        "source", "first_file", "first_locator",
        mode="before",
    )
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
    def all_refs(self) -> list[str]:
        """いまの ref と、過去に名乗っていた ref。参照解決はこの全部を受ける。"""
        return _clean_list([self.ref, *self.former_refs])

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
