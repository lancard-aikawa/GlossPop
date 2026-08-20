"""エントリ 1 件のファイル形式（YAML frontmatter + 本文）。

**「どこに置くか」は知らない。** 置き場所を決めるのは `store`（手元の 1 台）や
GlossPopApp（利用者ごとのディレクトリ）の仕事で、ここが持つのは**書式だけ**。

分けてあるのは、**この書式が 2 つのプロジェクトで同じでなければならない**から。
片方だけ項目を足すと、書き出した zip がもう片方で**読めるのに一部だけ落ちる**
という壊れ方をする（例外にならないぶん気付けない）。→ `docs/design-notes.md`

**`category` `slug` `scope` は frontmatter に書かない。** ディレクトリ名とファイル名が
正で、`entry_from_parts()` がパスの値で上書きする。書いてあっても無視される。

旧 `related` を書き出さないのも同じ考え方 —— 読み込み時に `relations` へ畳まれるので、
次に保存した時点で自動的に移行する。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from .models import GLOBAL_SCOPE, Entry

#: frontmatter に書き出すキーの順序。
#:
#: **`category` は入れない**（ディレクトリ名が正）。**旧 `related` も入れない**
#: （読み込み時に `relations` へ畳まれる）。
FM_KEYS = (
    "term",
    "reading",
    "aliases",
    "excludes",
    "subcategory",
    "summary",
    "examples",
    "when",
    "relations",
    "tags",
    "source",
    "first_file",
    "first_locator",
    "map",
    "pin",
    "line",
    "area",
    "former_refs",
    "created_at",
    "updated_at",
)


class EntryFileError(Exception):
    """frontmatter が読めない。

    `store.StoreError` はこれの別名なので、辞書まわりの失敗はどちらの名前でも
    捕まえられる（→ `store.py` の注記）。
    """


def parse_markdown(text: str) -> tuple[dict, str]:
    """frontmatter と本文に分割する。frontmatter が無ければ全体を本文とみなす。"""
    if not text.startswith("---"):
        return {}, text.strip()
    lines = text.splitlines()
    if lines[0].strip() != "---":
        return {}, text.strip()
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            raw = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1:]).strip()
            try:
                meta = yaml.safe_load(raw) or {}
            except yaml.YAMLError as exc:
                raise EntryFileError(f"frontmatter の YAML が壊れています: {exc}") from exc
            if not isinstance(meta, dict):
                raise EntryFileError("frontmatter がマッピングではありません")
            return meta, body
    # 閉じ `---` が無い → 全体を本文扱い
    return {}, text.strip()


def _plain(value: object) -> object:
    """yaml.safe_dump に渡せる素の値にする。

    ``relations`` は pydantic モデルのリストなので dict に落とし、そのうえで
    空の項目を落とす（``label: ''`` が全行に並ぶとファイルが読めなくなる）。
    """
    if isinstance(value, BaseModel):
        return {k: v for k, v in value.model_dump().items() if v not in ("", [], None)}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def dump_markdown(entry: Entry) -> str:
    meta = {}
    for key in FM_KEYS:
        value = getattr(entry, key)
        # 空文字 / 空リストは書かない (ファイルをノイズで埋めない)
        if value in ("", [], None):
            continue
        meta[key] = _plain(value)
    front = yaml.safe_dump(
        meta,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    ).rstrip()
    body = entry.definition.strip()
    return f"---\n{front}\n---\n\n{body}\n"


def entry_from_parts(text: str, *, category: str, slug: str, scope: str = GLOBAL_SCOPE) -> Entry:
    """ファイルの中身と「どこに在ったか」から `Entry` を作る。

    **パスを受け取らない形も要る** —— GlossPopApp は利用者ごとのディレクトリに置くので
    `category` / `slug` の出どころがこちらと違う。書式の解釈だけを共有したい。
    """
    meta, body = parse_markdown(text)
    meta = dict(meta)
    meta["definition"] = body
    meta["slug"] = slug
    meta["category"] = category  # ディレクトリ名が正
    meta["scope"] = scope        # 置き場所が正 (frontmatter には書かない)
    meta.setdefault("term", slug)
    return Entry.model_validate(meta)


def entry_from_file(path: Path, scope: str = GLOBAL_SCOPE) -> Entry:
    """`<カテゴリ>/<slug>.md` を読む。ディレクトリ名とファイル名が正。"""
    return entry_from_parts(
        path.read_text(encoding="utf-8"),
        category=path.parent.name,
        slug=path.stem,
        scope=scope,
    )
