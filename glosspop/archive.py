"""辞書の書き出しと取り込み（zip）。

取り込み方は 2 つある。

**置き換え (`replace`)** — 辞書は zip の中身そのものになり、zip に無いエントリは
消える。「いま何があるか」が常に一意になるのが利点。

**併合 (`merge`)** — zip にしか無い語を足し、**両方にあって中身が違うものは
取り込む側で上書きする**。手元にしか無い語は消えない。別の PC で書いた辞書を
持ち込む用途はこちら。

## 併合で「取り込む側を優先」にした理由

どちらを採るかは機械には決まらない。`updated_at` の新しいほうを採る手もあるが、
**時計がずれている PC が 1 台あると静かに古いほうが勝つ**（しかも勝ったほうが
正だと思い込んだまま気付かない）。1 件ずつ選ばせる手は、統合 (`merge.py`) では
成立するが**100 語が衝突する併合では人が捌けない**。

そこで規則は 1 つだけにした ——「取り込む側が勝つ」。**代わりに控えを必ず取り、
上書きした語を全部名前で返す**。規則が単純なら、消えたものを控えから拾い直せる。
黙って消える箇所をゼロにするのが、選ばせないことの引き換え。

## 消す前に必ず控えを取る

利用者のデータを丸ごと消しうる唯一の操作なので、バックアップを人の手順に
任せない（`copy_data_root` も `move` も `installer` も「元を消さない」で通してきた）。
控えは書き出しと同じ zip で、`data/backups/` に残る。**併合でも取る** ——
上書きされた語はそこにしか残らない。

対象は**全体の辞書とカテゴリマスターだけ**。フォルダの辞書 (`.glosspop`) は
フォルダごとコピーすれば運べるし、URL ごとの辞書 (`data/sites/`) は URL に紐づく。
どちらも含めないぶん、**取り込みで変わる範囲が辞書 1 か所に収まる**。
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
from .models import CategoryNameError

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


#: 取り込み方。`replace` は zip の中身そのものに、`merge` は足して上書きする
MODES = ("replace", "merge")

#: 報告に名前を並べる上限。**打ち切ったことは `truncated` で必ず返す**
#: （黙って切ると「これで全部」と読まれる）
MAX_REPORTED = 200


def _ref_of(member_name: str) -> str:
    rel = member_name[len(GLOSSARY_PREFIX):]
    return Path(rel).with_suffix("").as_posix()


def plan(data: bytes, mode: str = "merge") -> dict:
    """取り込む前の下見。**何が増えて何が上書きされるか**を数える。

    置き換えでは「消える語」も出す —— いちばん怖いのがそれなので、押す前に
    件数と名前を見せる。
    """
    _check_mode(mode)
    info = inspect(data)
    root = config.GLOSSARY_DIR
    here = (
        {p.relative_to(root).with_suffix("").as_posix(): p for p in root.glob("*/*.md")}
        if root.exists()
        else {}
    )

    added: list[str] = []
    updated: list[str] = []
    incoming_refs: set[str] = set()
    unchanged = 0
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in _entry_members(zf, safe_members(zf, root.parent)):
            ref = _ref_of(member.filename)
            incoming_refs.add(ref)
            current = here.get(ref)
            if current is None:
                added.append(ref)
            elif current.read_bytes() == zf.read(member):
                unchanged += 1
            else:
                updated.append(ref)

    # **消えるのは置き換えのときだけ。** 併合は手元にしか無い語をそのまま残す
    removed = [ref for ref in here if ref not in incoming_refs] if mode == "replace" else []
    return {
        "mode": mode,
        "entries": info["entries"],
        "categories": info["categories"],
        "added": _cut(added),
        "updated": _cut(updated),
        "removed": _cut(removed),
        "added_count": len(added),
        "updated_count": len(updated),
        "removed_count": len(removed),
        "unchanged": unchanged,
        "truncated": max(len(added), len(updated), len(removed)) > MAX_REPORTED,
    }


def _cut(names: list[str]) -> list[str]:
    return sorted(names)[:MAX_REPORTED]


def _check_mode(mode: str) -> str:
    if mode not in MODES:
        raise ArchiveError(f"不明な取り込み方です: {mode}")
    return mode


def import_bytes(data: bytes, mode: str = "replace") -> dict:
    """zip の中身を取り込む。**控えを取ってから**入れ替える。

    ``replace`` は zip の中身そのものに、``merge`` は zip にしか無い語を足して
    **両方にあるものを取り込む側で上書き**する（手元にしか無い語は残る）。

    どちらも**ディレクトリごと入れ替える**（2 回の rename）。併合でも完成形を
    別の場所に作ってから入れ替えるのは、1 ファイルずつ書くと途中で失敗したときに
    **半分だけ新しい辞書**が残るため。
    """
    _check_mode(mode)
    report = plan(data, mode)
    backup = write_backup()

    root = config.GLOSSARY_DIR
    root.parent.mkdir(parents=True, exist_ok=True)
    incoming = root.parent / f"{root.name}.incoming"
    replaced = root.parent / f"{root.name}.replaced-{_stamp()}"
    if incoming.exists():
        shutil.rmtree(incoming, ignore_errors=True)

    # 併合は「いまの辞書の複製」から始める。手元にしか無い語はこれで残り、
    # 入れ替えの原子性も置き換えと同じまま保てる
    if mode == "merge" and root.exists():
        shutil.copytree(root, incoming)

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = safe_members(zf, config.GLOSSARY_DIR.parent)
        for member in _entry_members(zf, members):
            rel = Path(member.filename[len(GLOSSARY_PREFIX):])
            target = incoming / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(member))    # 取り込む側が勝つ
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
        _write_categories(categories_data, mode)

    # 保存先は変わらないので再起動は要らない。読み直しの合図だけ出す
    store.invalidate()
    categories.invalidate()
    return {**report, "backup": str(backup), "leftover": leftover}


def _write_categories(data: bytes, mode: str) -> None:
    """カテゴリマスターを書く。併合では**手元の並びを保ったまま**足す。

    置き換えは zip のものをそのまま。併合で丸ごと差し替えると、**手元にしか
    無いカテゴリが順序と説明だけ失う**（ディレクトリは残るので次の `load()` で
    名前順に復活し、決めた並びが黙って崩れる）。

    衝突したカテゴリの扱いはエントリと同じ「取り込む側が勝つ」。ただし
    **サブカテゴリは和集合**にする —— あれは値ではなく「先出しの候補」なので、
    消して得るものが無い。
    """
    config.CATEGORIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if mode == "replace":
        config.CATEGORIES_FILE.write_bytes(data)
        return

    mine = list(categories.load())
    incoming = _parse_categories(data)
    by_name = {c.name: c for c in mine}
    order = [c.name for c in mine] + [c.name for c in incoming if c.name not in by_name]
    for other in incoming:
        current = by_name.get(other.name)
        if current is None:
            by_name[other.name] = other
            continue
        current.description = other.description or current.description
        current.subcategories = list(
            dict.fromkeys([*current.subcategories, *other.subcategories])
        )
    categories.write([by_name[name] for name in order])


def _parse_categories(data: bytes) -> list[categories.Category]:
    """zip の中のマスターを読む。**壊れていても取り込み自体は止めない。**

    エントリはもう入れ替わっているので、ここで例外を投げると「用語は入ったのに
    エラーが出た」になる。ディレクトリから作り直せるぶん、諦めるのはこちら。
    """
    try:
        return categories.parse(data.decode("utf-8"), name=CATEGORIES_NAME)
    except (UnicodeDecodeError, CategoryNameError):
        return []
