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
from .core import relations
from .installer import InstallError, safe_members
from .core.models import GLOBAL_SCOPE, CategoryNameError

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

def export_bytes(only: list[str] | None = None) -> bytes:
    """全体の辞書とカテゴリマスターを zip にして返す。

    ファイルはそのまま入れる（Markdown のまま）。**解凍すればテキストエディタで
    読める**ことを保ったままにするため、独自形式にはしない。

    ``only`` にカテゴリ名を渡すと**そのカテゴリだけ**を入れる（1 カテゴリだけ人に
    渡す用途）。**取り込む側は何も変えなくてよい** —— 併合は「入っているものを
    足して上書きする」だけなので、中身が一部でもそのまま通る。決めるのは
    書き出す側だけ、という切り分けにしてある。
    """
    root = config.GLOSSARY_DIR
    picked = set(only) if only else None
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if root.exists():
            for path in sorted(root.glob("*/*.md")):
                if picked is not None and path.parent.name not in picked:
                    continue
                rel = path.relative_to(root).as_posix()
                zf.writestr(f"{GLOSSARY_PREFIX}{rel}", path.read_bytes())
                count += 1
        master = _export_categories(picked)
        if master is not None:
            zf.writestr(CATEGORIES_NAME, master)
        zf.writestr(
            MANIFEST_NAME,
            json.dumps(
                {
                    "app": "GlossPop",
                    "kind": "glossary",
                    "entries": count,
                    # 一部だけ書き出したことは中身にも残す（受け取った側が
                    # 「これで全部」と思わないように）
                    "partial": picked is not None,
                    "categories": sorted(picked) if picked is not None else [],
                    "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    return buf.getvalue()


def _export_categories(picked: set[str] | None) -> bytes | None:
    """zip に入れるカテゴリマスター。

    **全部書き出すときはファイルをそのまま。** 控え (`write_backup`) もここを
    通るので、書式ごと元に戻せる形を崩さない。一部だけのときは選んだぶんを
    組み立て直す（渡す相手に関係の無いカテゴリの説明と並びを送りつけない）。
    """
    if picked is None:
        if not config.CATEGORIES_FILE.exists():
            return None
        return config.CATEGORIES_FILE.read_bytes()
    items = [c for c in categories.load() if c.name in picked]
    if not items:
        return None
    return categories.dumps(items).encode("utf-8")


def export_name(only: list[str] | None = None) -> str:
    """書き出す zip の名前。

    **カテゴリ名はファイル名に入れない。** 空白も日本語も使えるので、
    ``Content-Disposition`` に載せると経路ごとに化ける。一部であることだけ示す。
    """
    part = "-part" if only else ""
    return f"glosspop-glossary{part}-{_stamp()}.zip"


def export_plan(only: list[str] | None = None) -> dict:
    """書き出す前の下見。**何語入るか**と、**行き先が外に出る関係が何本か**。

    一部だけ渡すと、渡した先で**相手の居ない関係**ができる（関係は名前で書くので
    保存はできるが、リンクにも図の辺にもならない）。押す前に数で見せておかないと、
    受け取った側が「関係が消えた」と読む。数えるのは**解決できている関係だけ** ——
    もともと壊れている参照は点検 (`/doctor`) の担当で、ここで二重に出さない。
    """
    picked = set(only) if only else None
    entries = [e for e in store.load_all() if e.scope == GLOBAL_SCOPE]
    inside = [e for e in entries if picked is None or e.category in picked]
    refs = {e.ref for e in inside}

    dangling: list[str] = []
    for entry in inside:
        for rel in entry.relations:
            target = relations.resolve(rel.to, entries, origin=entry).entry
            if target is not None and target.ref not in refs:
                dangling.append(f"{entry.term} → {target.term}")
    return {
        "entries": len(inside),
        "categories": sorted(picked) if picked is not None else sorted(
            {e.category for e in entries}
        ),
        "partial": picked is not None,
        "dangling": _cut(dangling),
        "dangling_count": len(dangling),
        "truncated": len(dangling) > MAX_REPORTED,
    }


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
# 控えを見る / 1 件だけ戻す / 片付ける
#
# 併合の衝突は「取り込む側が勝つ」の 1 行に決めてあり、上書きされた語は**控えに
# しか残らない**。戻すのに zip を手で開かせるのでは、その約束が半分しか果たせて
# いない（→ docs/open-questions.md にあった宿題）。
#
# **自動では消さない。** 溜まるのは事実だが、控えは「消える前の唯一の写し」なので
# 古いものを勝手に捨てると約束のほうが壊れる。合計の大きさを出して、消すかどうかは
# 人に決めさせる。
# --------------------------------------------------------------------------- #

def _backup_path(name: str) -> Path:
    """控えの名前からパスを作る。**外へ出る名前は通さない。**

    名前は画面から来るので、`..` や絶対パスをそのまま繋がない（外から来た書庫を
    ライブラリ任せにしないのと同じ規則）。組み立てた結果が控えの置き場所の中に
    あることを最後に必ず確かめる。
    """
    directory = backup_dir()
    if not name or "/" in name or "\\" in name or not name.lower().endswith(".zip"):
        raise ArchiveError(f"控えの名前が不正です: {name}")
    path = (directory / name).resolve()
    if path.parent != directory.resolve():
        raise ArchiveError(f"控えの名前が不正です: {name}")
    if not path.exists():
        raise ArchiveError(f"その控えはありません: {name}")
    return path


def _backup_order(name: str) -> tuple[str, str, int]:
    """新しい順に並べるための鍵。``backup-<日付>-<時刻>[-<連番>].zip`` を読む。

    **名前をそのまま並べ替えない。** 同じ秒に 2 回取ると ``-2`` が付き、文字列
    順では `-`（0x2D）が `.`（0x2E）より小さいので**古いほうが先に来る**。
    ファイルの時刻で並べるのも駄目 —— Windows の時計は 15ms ほどの粒度なので、
    続けて取った 2 つが同じ時刻になりうる（そこで競走するテストは書かない）。
    """
    stem = name[len("backup-"):-len(".zip")] if name.startswith("backup-") else name
    parts = stem.split("-")
    try:
        n = int(parts[2]) if len(parts) > 2 else 1
    except ValueError:
        n = 1
    return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "", n)


def _count_entries(path: Path) -> int:
    """控えに入っている用語の数。**中身は展開しない**（名前だけ数える）。"""
    try:
        with zipfile.ZipFile(path) as zf:
            return len(_entry_members(zf, zf.infolist()))
    except (zipfile.BadZipFile, OSError):
        return 0


def list_backups() -> dict:
    """控えの一覧（新しい順）。``{dir, total_bytes, items}``。

    **合計の大きさも返す。** 自動で消さない代わりに、溜まっていることが分かる
    ようにしておく（消すかどうかは人が決める）。
    """
    directory = backup_dir()
    items: list[dict] = []
    total = 0
    if directory.exists():
        for path in sorted(
            directory.glob("backup-*.zip"), key=lambda p: _backup_order(p.name), reverse=True
        ):
            stat = path.stat()
            total += stat.st_size
            items.append({
                "name": path.name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).astimezone()
                .isoformat(timespec="seconds"),
                "entries": _count_entries(path),
            })
    return {"dir": str(directory), "total_bytes": total, "items": items}


def backup_contents(name: str) -> dict:
    """控え 1 つの中身。``ref`` と、**いま手元にあるかどうか**を返す。

    手元にあるかを添えるのは、戻すときに**上書きになるのかどうか**を押す前に
    見せるため（1 件ずつ消す経路と同じで、控えは取らない代わりに先に見せる）。
    """
    path = _backup_path(name)
    root = config.GLOSSARY_DIR
    try:
        with zipfile.ZipFile(path) as zf:
            refs = sorted(_ref_of(m.filename) for m in _entry_members(zf, zf.infolist()))
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"控えを読めません: {name}") from exc
    return {
        "name": name,
        "count": len(refs),
        "entries": [
            {"ref": ref, "here": (root / f"{ref}.md").exists()}
            for ref in refs[:MAX_REPORTED]
        ],
        "truncated": len(refs) > MAX_REPORTED,
    }


def restore_entry(name: str, ref: str) -> dict:
    """控えから**1 件だけ**辞書へ書き戻す。

    ファイルの中身をそのまま書く（`store.save()` を通さない）。控えは「消える前の
    写し」なので、**書いてあったとおりに戻る**のが筋 —— 保存し直すと本文の整形や
    `updated_at` が変わる。カテゴリはディレクトリが正なので、マスターには
    次の `load()` が拾わせる。
    """
    path = _backup_path(name)
    with zipfile.ZipFile(path) as zf:
        members = {
            _ref_of(m.filename): m
            for m in _entry_members(zf, safe_members(zf, config.GLOSSARY_DIR.parent))
        }
        member = members.get(ref)
        if member is None:
            raise ArchiveError(f"その控えに入っていません: {ref}")
        data = zf.read(member)

    target = config.GLOSSARY_DIR / f"{ref}.md"
    # 控えの中は `<カテゴリ>/<slug>.md` の 2 段と確かめてあるが、組み立てた先が
    # 辞書の外に出ていないことは最後にもう一度見る
    if target.resolve().parent.parent != config.GLOSSARY_DIR.resolve():
        raise ArchiveError(f"戻し先が不正です: {ref}")
    overwritten = target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    store.invalidate()
    categories.invalidate()
    return {"ref": ref, "overwritten": overwritten}


def delete_backup(name: str) -> None:
    """控えを 1 つ捨てる。**古いものを自動で消す口は作らない**（人が決める）。"""
    _backup_path(name).unlink()


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
