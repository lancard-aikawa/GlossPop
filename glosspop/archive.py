"""辞書の書き出しと取り込み（zip）。

**取り込みは「置き換え」。** 取り込んだ側の辞書は zip の中身そのものになり、
zip に無いエントリは消える。混ぜないのは、同じカテゴリの同じ用語が両側にあった
ときにどちらを採るかを決められないため —— 決めずに片方へ寄せると、**残ったほうが
正だと思い込んだまま気付かない**。置き換えなら「いま何があるか」が常に一意になる。

**消す前に必ず控えを取る。** このリポジトリで唯一、利用者のデータを消す操作なので、
バックアップを人の手順に任せない（`copy_data_root` も `move` も `installer` も
「元を消さない」で通してきた）。控えは書き出しと同じ zip で、`data/backups/` に残る。

対象は**全体の辞書とカテゴリマスターだけ**。フォルダの辞書 (`.glosspop`) は
フォルダごとコピーすれば運べるし、URL ごとの辞書 (`data/sites/`) は URL に紐づく。
どちらも含めないぶん、**置き換えで消える範囲が辞書 1 か所に収まる**。
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from . import categories, config, store
from .installer import InstallError, safe_members

#: zip の中の置き場所。展開時もこの名前で探す
GLOSSARY_PREFIX = "glossary/"
CATEGORIES_NAME = "categories.yaml"

#: 書き出したものだと分かる目印。中身も検証に使う
MANIFEST_NAME = "glosspop-export.json"

#: 取り込む zip の上限。辞書は文字ばかりなので、これを超えるものは別物とみなす
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024

#: 控えの置き場所（`DATA_ROOT` の下。保存先を移せば一緒に動く）
BACKUP_DIR_NAME = "backups"


class ArchiveError(Exception):
    pass


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_dir() -> Path:
    return Path(config.DATA_ROOT) / "data" / BACKUP_DIR_NAME


# --------------------------------------------------------------------------- #
# 書き出し
# --------------------------------------------------------------------------- #

def export_bytes() -> bytes:
    """全体の辞書とカテゴリマスターを zip にして返す。

    ファイルはそのまま入れる（Markdown のまま）。**解凍すればテキストエディタで
    読める**ことを保ったままにするため、独自形式にはしない。
    """
    root = config.GLOSSARY_DIR
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if root.exists():
            for path in sorted(root.glob("*/*.md")):
                rel = path.relative_to(root).as_posix()
                zf.writestr(f"{GLOSSARY_PREFIX}{rel}", path.read_bytes())
                count += 1
        if config.CATEGORIES_FILE.exists():
            zf.writestr(CATEGORIES_NAME, config.CATEGORIES_FILE.read_bytes())
        zf.writestr(
            MANIFEST_NAME,
            json.dumps(
                {
                    "app": "GlossPop",
                    "kind": "glossary",
                    "entries": count,
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    return buf.getvalue()


def export_name() -> str:
    return f"glosspop-glossary-{_stamp()}.zip"


def write_backup() -> Path:
    """いまの辞書を控えとして `data/backups/` に書き出す。書いた場所を返す。"""
    directory = backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"backup-{_stamp()}.zip"
    # 同じ秒に 2 回走っても上書きしない
    n = 2
    while path.exists():
        path = directory / f"backup-{_stamp()}-{n}.zip"
        n += 1
    path.write_bytes(export_bytes())
    return path


# --------------------------------------------------------------------------- #
# 取り込み（置き換え）
# --------------------------------------------------------------------------- #

def _entry_members(zf: zipfile.ZipFile, members: list[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
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


def inspect(data: bytes) -> dict:
    """取り込む前に中身を確かめる。``{entries, categories, has_manifest}``。

    **GlossPop が書き出したものかを見る。** アプリ本体の zip を取り込ませると
    辞書が空で置き換わるので、形が違うものはここで止める。
    """
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ArchiveError(f"zip が大きすぎます（{MAX_ARCHIVE_BYTES // 1024 // 1024} MB まで）")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ArchiveError("zip として読めません") from exc
    with zf:
        try:
            # 展開先の外に出る要素・シンボリックリンクは自分で弾く
            # （外から来た書庫をライブラリ任せにしない。installer と同じ規則）
            members = safe_members(zf, config.GLOSSARY_DIR.parent)
        except InstallError as exc:
            raise ArchiveError(str(exc)) from exc
        entries = _entry_members(zf, members)
        names = {m.filename for m in members}
        if not entries and MANIFEST_NAME not in names:
            raise ArchiveError(
                "GlossPop が書き出した zip ではないようです"
                f"（{GLOSSARY_PREFIX}<カテゴリ>/<用語>.md が入っていません）"
            )
        return {
            "entries": len(entries),
            "categories": len({Path(m.filename[len(GLOSSARY_PREFIX):]).parts[0] for m in entries}),
            "has_manifest": MANIFEST_NAME in names,
        }


def import_bytes(data: bytes) -> dict:
    """zip の中身で辞書を**置き換える**。控えを取ってから消す。

    置き換えはディレクトリごと入れ替える（2 回の rename）。1 ファイルずつ消して
    書くと、途中で失敗したときに**半分だけ新しい辞書**が残る。
    """
    info = inspect(data)
    backup = write_backup()

    root = config.GLOSSARY_DIR
    root.parent.mkdir(parents=True, exist_ok=True)
    incoming = root.parent / f"{root.name}.incoming"
    replaced = root.parent / f"{root.name}.replaced-{_stamp()}"
    if incoming.exists():
        shutil.rmtree(incoming, ignore_errors=True)

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = safe_members(zf, config.GLOSSARY_DIR.parent)
        entries = _entry_members(zf, members)
        for member in entries:
            rel = Path(member.filename[len(GLOSSARY_PREFIX):])
            target = incoming / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(member))
        incoming.mkdir(parents=True, exist_ok=True)   # 0 件の zip でも作る
        categories_data = zf.read(CATEGORIES_NAME) if CATEGORIES_NAME in {
            m.filename for m in members
        } else None

    leftover: str | None = None
    try:
        if root.exists():
            root.rename(replaced)
        incoming.rename(root)
    except OSError as exc:
        # 入れ替えに失敗したら書きかけを残さない。控えはもう取ってある
        shutil.rmtree(incoming, ignore_errors=True)
        if replaced.exists() and not root.exists():
            replaced.rename(root)
        raise ArchiveError(f"辞書を入れ替えられませんでした: {exc}") from exc

    if replaced.exists():
        shutil.rmtree(replaced, ignore_errors=True)
        if replaced.exists():
            leftover = str(replaced)      # 消せなくても黙らない

    if categories_data is not None:
        config.CATEGORIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.CATEGORIES_FILE.write_bytes(categories_data)

    # 保存先は変わらないので再起動は要らない。読み直しの合図だけ出す
    store.invalidate()
    categories.invalidate()
    return {
        "entries": info["entries"],
        "categories": info["categories"],
        "backup": str(backup),
        "leftover": leftover,
    }
