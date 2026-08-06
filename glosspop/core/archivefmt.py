"""辞書の zip の**形**と、外から来た書庫を開くときの**安全規則**。

**「どこに置くか」は知らない。** 展開先を決めるのは `archive`（手元の 1 台）や
GlossPopApp（利用者ごとのディレクトリ）の仕事で、ここが持つのは
**中身の並べ方と、通してよいかの判断**だけ。

分けてあるのは、**この形が 2 つのプロジェクトで同じでなければならない**から。
片方だけ並べ方を変えると、書き出した zip がもう片方で「読めるのに空で置き換わる」
という壊れ方をする（`entryfile` と同じ話）。→ `docs/design-notes.md`

形はこれだけ:

```
glossary/<カテゴリ>/<slug>.md   … 辞書。**2 段と決まっている**
categories.yaml                 … カテゴリマスター（GlossPop だけが入れる）
glosspop-export.json            … 目印
```

**Markdown はそのまま入れる。** 解凍すればテキストエディタで読めることを保つため、
独自形式にしない。
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

#: zip の中の置き場所。展開時もこの名前で探す
GLOSSARY_PREFIX = "glossary/"
CATEGORIES_NAME = "categories.yaml"

#: 書き出したものだと分かる目印。中身も検証に使う
MANIFEST_NAME = "glosspop-export.json"

#: 取り込む zip の上限。辞書は文字ばかりなので、これを超えるものは別物とみなす
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


class ArchiveFormatError(Exception):
    """zip の形が違う / 開けない。"""


def safe_members(zf: zipfile.ZipFile, dest: Path) -> list[zipfile.ZipInfo]:
    """展開先の外に出る要素が無いことを確かめてから返す。

    ``zipfile`` も絶対パスと ``..`` は落とすが、**外から来た書庫をライブラリ任せに
    しない**。シンボリックリンクもここで弾く（Windows でも zip には入りうる）。
    """
    base = dest.resolve()
    members = []
    for info in zf.infolist():
        name = info.filename
        if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
            raise ArchiveFormatError(f"展開先の外を指す要素があります: {name}")
        # 上位 4 ビットがファイル種別。0xA000 = シンボリックリンク
        if (info.external_attr >> 16) & 0xF000 == 0xA000:
            raise ArchiveFormatError(f"シンボリックリンクは展開しません: {name}")
        resolved = (base / name).resolve()
        if resolved != base and base not in resolved.parents:
            raise ArchiveFormatError(f"展開先の外に出ます: {name}")
        members.append(info)
    if not members:
        raise ArchiveFormatError("zip が空です")
    return members


def entry_members(members: list[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
    """``glossary/<カテゴリ>/<slug>.md`` だけを拾う。

    深さも見る。**辞書は 2 段と決まっている**ので、それ以外は元の形が違う
    （＝別のものを取り込もうとしている）とみなして通さない。
    """
    out = []
    for info in members:
        if info.is_dir() or not info.filename.startswith(GLOSSARY_PREFIX):
            continue
        parts = Path(info.filename[len(GLOSSARY_PREFIX):]).parts
        if len(parts) == 2 and parts[1].lower().endswith(".md"):
            out.append(info)
    return out


def split_member(name: str) -> tuple[str, str]:
    """``glossary/人物/寒月.md`` → ``("人物", "寒月")``。"""
    parts = Path(name[len(GLOSSARY_PREFIX):]).parts
    return parts[0], parts[1][: -len(".md")]


def open_archive(
    data: bytes, dest: Path, *, max_bytes: int | None = None
) -> tuple[zipfile.ZipFile, list[zipfile.ZipInfo]]:
    """上限・zip として読めるか・展開先の外に出ないか、を通してから開く。

    返した `ZipFile` は呼ぶ側が閉じること（`with` に渡せる）。
    ``max_bytes`` を受け取るのは、**呼ぶ側が自分の上限を持てるようにする**ため
    （既定はここの値）。
    """
    limit = MAX_ARCHIVE_BYTES if max_bytes is None else max_bytes
    if len(data) > limit:
        raise ArchiveFormatError(f"zip が大きすぎます（{limit // 1024 // 1024} MB まで）")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ArchiveFormatError("zip として読めません") from exc
    try:
        members = safe_members(zf, dest)
    except ArchiveFormatError:
        zf.close()
        raise
    return zf, members


def inspect(data: bytes, dest: Path, *, max_bytes: int | None = None) -> dict:
    """取り込む前に中身を確かめる。``{entries, categories, has_manifest}``。

    **辞書の zip かを見る。** アプリ本体の zip を取り込ませると辞書が空で
    置き換わるので、形が違うものはここで止める。
    """
    zf, members = open_archive(data, dest, max_bytes=max_bytes)
    with zf:
        entries = entry_members(members)
        names = {m.filename for m in members}
        if not entries and MANIFEST_NAME not in names:
            raise ArchiveFormatError(
                "GlossPop が書き出した zip ではないようです"
                f"（{GLOSSARY_PREFIX}<カテゴリ>/<用語>.md が入っていません）"
            )
        return {
            "entries": len(entries),
            "categories": len({split_member(m.filename)[0] for m in entries}),
            "has_manifest": MANIFEST_NAME in names,
        }


def manifest_bytes(*, app: str, entries: int, created_at: str, partial: bool = False,
                   categories: list[str] | None = None) -> bytes:
    """目印。**`app` は書き出した側の名前**（受け取る側は形だけを見る）。"""
    return json.dumps(
        {
            "app": app,
            "kind": "glossary",
            "entries": entries,
            # 一部だけ書き出したことは中身にも残す（受け取った側が
            # 「これで全部」と思わないように）
            "partial": partial,
            "categories": sorted(categories or []),
            "created_at": created_at,
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
