"""コマンドライン: サーバ起動と、Claude スキルから叩く辞書操作。

Claude スキル (``/gloss-add``) からは JSON を stdin で渡す形を使う。
シェルのクォートで日本語や改行が壊れないのでいちばん安全。

    echo '{"term":"用語", ...}' | glosspop add --json -
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import categories, config, merge, store, watchdog
from .core.models import (
    GLOBAL_SCOPE,
    LOCAL_SCOPE,
    SCOPES,
    CategoryNameError,
    EntryDraft,
    normalize_category,
)


def _resolve(target: str):
    """slug / ref / 用語名のどれでもエントリを引けるようにする。"""
    entry = store.get(target)
    if entry is not None:
        return [entry]
    hits = store.find_by_surface(target)
    if hits:
        return hits
    return [e for e in store.load_all() if e.slug == target]


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _read_text_arg(value: str | None) -> str:
    """``-`` なら stdin から読む。``@path`` ならファイルから読む。"""
    if value is None:
        return ""
    if value == "-":
        return sys.stdin.read()
    if value.startswith("@"):
        with open(value[1:], encoding="utf-8") as fh:
            return fh.read()
    return value


def _emit(data: object) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _apply_folder(args: argparse.Namespace) -> int:
    """``--folder`` を「開いているフォルダ」として反映する。

    ローカル辞書はビューアが**開いているフォルダ**から祖先方向に探して決めるが、
    一度きりの CLI にはその状態が無い。ここで ``config.set_content_dir()`` を
    通しておけば、``store.glossary_dir(LOCAL_SCOPE)`` から先はサーバとまったく
    同じ経路になる（CLI 用の探索を別に書くと、必ず規則がずれる）。
    """
    raw = getattr(args, "folder", None)
    if not raw:
        return 0
    path = Path(raw).expanduser()
    if not path.is_dir():
        print(f"フォルダがありません: {path}", file=sys.stderr)
        return 2
    config.set_content_dir(path.resolve())
    return 0


def _announce_local(action: str) -> int:
    """ローカル辞書の場所を stderr に出す。使えなければ 2 を返す。

    **黙って別の場所に書かない。** 祖先の ``.glosspop`` が使われることがあり
    （1 巻 2 巻で共有する仕組み）、``--folder`` を省けば既定の content フォルダに
    なる。ビューアが場所を画面に出しているのと同じ理由でここでも出す。
    """
    directory = store.glossary_dir(LOCAL_SCOPE)
    if directory is None or not store.local_available():
        print(
            f"ローカル辞書が使えないので{action}できません。"
            "--folder でフォルダを指定してください。",
            file=sys.stderr,
        )
        return 2
    print(f"ローカル辞書: {directory}", file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- #
# サブコマンド
# --------------------------------------------------------------------------- #

def _wait_started(server, timeout: float = 30.0, sleep=None) -> bool:
    """**自分の**サーバが listen し始めるまで待つ。

    ポートが開いたかで見ないこと —— 前のサーバがまだ終わりきっていない間に開き
    直すと（生存確認で終わるまで 20 秒ほどある）、**そのポートは「開いている」**。
    そこで窓を出すと、**もうすぐ死ぬ古いサーバに向いた窓**が開く。こちらが bind に
    失敗したことも同時に分かる（`started` が立たない）。
    """
    import time

    sleep = sleep or time.sleep
    for _ in range(int(timeout / 0.05)):
        if getattr(server, "started", False):
            return True
        sleep(0.05)
    return False


def _open_window_later(args: argparse.Namespace, url: str, server=None) -> None:
    """サーバが listen したら専用ウィンドウを開く（別スレッド）。

    **窓の寿命はプロセスでは追えない。** 起動した ``msedge.exe`` はブラウザ本体を
    別プロセスで生んですぐ終了するので、こちらの ``Popen`` を ``wait()`` しても
    「窓が閉じた」ことにはならない（一度これで「窓は開いたままサーバだけ落ちる」を
    作った）。追えるのは**ページの側からの合図**だけなので、そちらは
    ``watchdog.py`` が引き受ける。

    **コンソール窓は `glosspopw.exe` の側で無い**（`console=False` で作ってある）ので、
    ここで隠す細工は要らない。以前は `console=True` の 1 本きりで、窓が開いた時点で
    `FreeConsole` して離脱していたが、**親にコンソールが無くなると子（claude）が
    自分の窓を作る**という副作用があり、変則をやめて 2 本立てにした。
    """
    import threading

    from . import appwindow

    def run() -> None:
        ok = (
            _wait_started(server) if server is not None
            else appwindow.wait_until_ready(args.host, args.port)
        )
        if not ok:
            print(
                "サーバを起動できませんでした（ポートが使われている？）。"
                "ウィンドウは開きません。",
                file=sys.stderr,
            )
            return
        if appwindow.open_window(url) is None:
            print("アプリモードで開けるブラウザが無いので既定のブラウザで開きました。", file=sys.stderr)

    threading.Thread(target=run, daemon=True).start()


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    reload = args.reload
    if reload and config.FROZEN:
        # 凍結後はソースが _internal に展開されているだけなので監視しても意味が無く、
        # reloader の再起動で exe が二重起動する
        print("--reload は exe 版では使えません。無視します。", file=sys.stderr)
        reload = False

    open_window = getattr(args, "open", False)

    config.ensure_dirs()
    url = f"http://{args.host}:{args.port}/"
    print(f"GlossPop: {url}", file=sys.stderr)
    print(f"  辞書   : {config.GLOSSARY_DIR}", file=sys.stderr)
    print(f"  カテゴリ: {config.CATEGORIES_FILE}", file=sys.stderr)
    print(f"  content: {config.CONTENT_DIR}", file=sys.stderr)
    print(f"  claude : {config.CLAUDE_BIN or '(見つかりません — AI 下書きは無効)'}", file=sys.stderr)

    if reload:
        target = "glosspop.app:app"   # reloader はプロセスを作り直すので文字列でないと渡せない
    else:
        from .app import app as target  # 文字列 import は exe 版で解決できない

    if reload:
        # reloader はプロセスを作り直すので uvicorn 側に任せる（開発用）。
        # Server を掴めないので、窓を開けるなら従来どおりポートで待つ
        if open_window:
            watchdog.arm()
            _open_window_later(args, url)
        uvicorn.run(
            target,
            host=args.host,
            port=args.port,
            reload=True,
            log_level=args.log_level,
        )
        return 0

    # **`uvicorn.run()` ではなく Server を自分で持つ。** 生存確認が途絶えたときに
    # 止める先が要り、窓を出す合図にも「自分が listen したか」が要る
    # （`uvicorn.run()` はどちらの手段もくれない）
    server = uvicorn.Server(
        uvicorn.Config(target, host=args.host, port=args.port, log_level=args.log_level)
    )
    if open_window:
        # **ページからの合図を数え始めてから窓を開ける。** 逆にすると、開くのが
        # 速かったときの最初の合図を落とす
        watchdog.arm()
        print("  窓を閉じるとサーバも終わります。", file=sys.stderr)
        watchdog.watch(lambda: setattr(server, "should_exit", True))
        _open_window_later(args, url, server)
    server.run()
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    if args.json:
        data = json.loads(_read_text_arg(args.json))
        if not isinstance(data, dict):
            print("--json はオブジェクトを渡してください", file=sys.stderr)
            return 2
    else:
        if not args.term:
            print("--term か --json のどちらかが必要です", file=sys.stderr)
            return 2
        data = {
            "term": args.term,
            "reading": args.reading or "",
            "aliases": _split(args.aliases),
            "category": args.category or "",
            "subcategory": args.subcategory or "",
            "summary": args.summary or "",
            "definition": _read_text_arg(args.definition),
            "examples": _split(args.examples),
            "related": _split(args.related),
            "tags": _split(args.tags),
            "source": args.source or "",
        }

    # 明示された --scope が JSON の値より優先（指定 > 下書きに載っていた値）
    if getattr(args, "scope", None):
        data["scope"] = args.scope

    draft = EntryDraft.model_validate(data)
    if draft.scope == LOCAL_SCOPE and _announce_local("登録"):
        return 2
    # store.save() が使う形に揃えてから引く。揃えないと、保存側が正規化した結果と
    # 衝突するのに「無い」と判定して StoreError で落ちる
    category = normalize_category(draft.category or "未分類")
    # 同名でもカテゴリが違えば別エントリ。衝突判定は同一カテゴリ・同一スコープ内だけ
    # (--json で scope を渡せるので、グローバル決め打ちで引かない)
    existing = store.find_in_category(category, draft.term, draft.scope)

    if existing is not None and not args.update:
        others = [e for e in store.find_by_surface(draft.term) if e.ref != existing.ref]
        hint = (
            f"（別カテゴリの同名: {', '.join(e.ref for e in others)}）" if others else ""
        )
        print(
            f"「{draft.term}」はカテゴリ「{category}」に既に登録されています {hint}。"
            "上書きするなら --update を、別カテゴリに入れるなら --category を変えてください。",
            file=sys.stderr,
        )
        _emit({"status": "exists", "ref": existing.ref, "path": str(store.path_for_ref(existing.ref))})
        return 1

    entry = store.save(draft, ref=existing.ref if existing is not None else None)
    _emit(
        {
            "status": "updated" if existing is not None else "created",
            "ref": entry.ref,
            "slug": entry.slug,
            "term": entry.term,
            "category": entry.category,
            "subcategory": entry.subcategory,
            "path": str(store.path_for_ref(entry.ref)),
            "url": f"/glossary/{entry.ref}",
        }
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    entries = store.load_all()
    if args.category:
        entries = [e for e in entries if e.category == args.category]
    if args.json:
        _emit(
            [
                {
                    "ref": e.ref,
                    "slug": e.slug,
                    "term": e.term,
                    "aliases": e.aliases,
                    "category": e.category,
                    "subcategory": e.subcategory,
                    "summary": e.summary,
                }
                for e in entries
            ]
        )
        return 0
    if not entries:
        print("(辞書は空です)")
        return 0
    for e in entries:
        alias = f" ({', '.join(e.aliases)})" if e.aliases else ""
        print(f"[{e.path_label}] {e.term}{alias}  -- {e.summary or '(要約なし)'}")
    return 0


def _pick_one(target: str, category: str | None) -> object | None:
    hits = _resolve(target)
    if category:
        hits = [e for e in hits if e.category == category]
    if not hits:
        print(f"見つかりません: {target}", file=sys.stderr)
        return None
    if len(hits) > 1:
        print(
            f"「{target}」は {len(hits)} 件あります。--category で絞ってください: "
            + ", ".join(e.ref for e in hits),
            file=sys.stderr,
        )
        return None
    return hits[0]


def cmd_show(args: argparse.Namespace) -> int:
    entry = _pick_one(args.target, args.category)
    if entry is None:
        return 1
    if args.json:
        _emit({**entry.model_dump(), "ref": entry.ref})
    else:
        sys.stdout.write(store.path_for_ref(entry.ref).read_text(encoding="utf-8"))
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    entry = _pick_one(args.target, args.category)
    if entry is None or not store.delete(entry.ref):
        return 1
    _emit({"status": "deleted", "ref": entry.ref, "term": entry.term})
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    """割れてしまった同じものを 1 つにまとめる。

    **既定は下見だけ**（`--yes` で実行）。消える側の本文が要らないと決めるのは
    人なので、何がどうなるかを見せてから通す。畳めない項目は既定で残す側を採る
    ので、選び分けたいときはビューアの確認画面を使うこと。
    """
    keep = _pick_one(args.keep, args.keep_category)
    drop = _pick_one(args.drop, args.drop_category)
    if keep is None or drop is None:
        return 1
    try:
        plan = merge.plan(keep.ref, drop.ref)
    except merge.MergeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not args.yes:
        _emit({"status": "preview", **plan})
        print(
            "下見だけです。実行するには --yes を付けてください"
            "（衝突した項目は残す側を採ります）",
            file=sys.stderr,
        )
        return 0
    merged = merge.apply(keep.ref, drop.ref)
    _emit({
        "status": "merged",
        "ref": merged.ref,
        "term": merged.term,
        "aliases": merged.aliases,
        "dropped": drop.ref,
    })
    return 0


def cmd_move(args: argparse.Namespace) -> int:
    """カテゴリ・保存先を移す。どちらか片方でも両方でも指定できる。

    保存先をまたげるのは ``store.move()`` だけ（``save()`` は ref の位置を正とする）
    ので、辞書間の移し替えはこの経路に寄せてある。
    """
    if not args.to and not args.to_scope:
        print("--to（移動先カテゴリ）か --to-scope（移動先の辞書）が必要です", file=sys.stderr)
        return 2
    entry = _pick_one(args.target, args.category)
    if entry is None:
        return 1
    if args.to_scope == LOCAL_SCOPE and _announce_local("移動"):
        return 2
    moved = store.move(entry.ref, args.to, scope=args.to_scope)
    _emit(
        {
            "status": "moved",
            "from": entry.ref,
            "ref": moved.ref,
            "scope": moved.scope,
            "path": str(store.path_for_ref(moved.ref)),
        }
    )
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    """別のフォルダのデータをいまの保存先へ引き継ぐ。

    新しい版を隣に展開して起動すると、辞書は旧フォルダに残ったままで消えたように
    見える。UI を開かずに済ませたいときと、スクリプトから回したいとき用。
    """
    source = Path(args.source).expanduser()
    if not source.is_dir():
        print(f"フォルダがありません: {source}", file=sys.stderr)
        return 1
    target = Path(config.DATA_ROOT)
    try:
        report = config.copy_data_root(source, target)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"{source} -> {target}")
    print(f"  複製: {len(report['copied'])} 件")
    if report["cache_skipped"]:
        print(f"  キャッシュ {report['cache_skipped']} 件は運びません（作り直されます）")
    for item in report["skipped"]:
        print(f"  複製できず: {item['path']} ({item['reason']})", file=sys.stderr)
    print("元のデータは残しています。問題が無ければ手で片付けてください。")
    return 1 if report["skipped"] else 0


def cmd_categories(args: argparse.Namespace) -> int:
    """カテゴリマスターを見る / 編集する。

    **どの操作もスコープを取る。** `--folder` を付けると一覧にフォルダの辞書の
    カテゴリも出るので、スコープを渡せないと**同名のグローバル側を消す**ことになる。
    マスターは辞書ごとにあるので、`--add` もフォルダの辞書に対して使える。
    """
    scope = getattr(args, "scope", None) or GLOBAL_SCOPE
    if args.add:
        if scope == LOCAL_SCOPE and _announce_local("カテゴリの登録"):
            return 2
        category = categories.ensure(args.add, description=args.description or "", scope=scope)
        _emit({"status": "ensured", "scope": scope, **category.model_dump()})
        return 0
    if args.rename:
        old, new = args.rename
        if scope == LOCAL_SCOPE and _announce_local("改名"):
            return 2
        moved = store.rename_category(old, new, scope)
        _emit({"status": "renamed", "from": old, "to": new, "scope": scope, "moved_entries": moved})
        return 0
    if args.remove:
        if scope == LOCAL_SCOPE and _announce_local("削除"):
            return 2
        store.delete_category(args.remove, scope)
        _emit({"status": "deleted", "name": args.remove, "scope": scope})
        return 0
    _emit(store.category_tree())
    return 0


# --------------------------------------------------------------------------- #
# パーサ
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="glosspop", description="GlossPop 辞書ビューア")
    sub = p.add_subparsers(dest="command", required=True)

    def add_folder_option(parser: argparse.ArgumentParser) -> None:
        """ローカル辞書を触るコマンドに付ける。

        指定すると「そのフォルダを開いている」状態と同じになり、
        ``<DIR>` から祖先方向でいちばん近い ``.glosspop/glossary`` が対象になる。
        """
        parser.add_argument(
            "--folder",
            metavar="DIR",
            help="このフォルダのローカル辞書 (.glosspop/glossary) も対象にする",
        )

    def add_serve_options(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=8765)
        parser.add_argument("--reload", action="store_true")
        parser.add_argument("--log-level", default="info")

    s = sub.add_parser("serve", help="ビューアを起動する（ブラウザは自分で開く）")
    add_serve_options(s)
    s.set_defaults(func=cmd_serve, open=False)

    w = sub.add_parser("app", help="ビューアを起動して専用ウィンドウで開く")
    add_serve_options(w)
    w.set_defaults(func=cmd_serve, open=True)

    a = sub.add_parser("add", help="辞書に用語を登録する")
    a.add_argument("--json", metavar="SPEC", help="エントリ全体を JSON で渡す ('-' で stdin, '@path' でファイル)")
    a.add_argument("--term")
    a.add_argument("--reading", help="読み (かな)")
    a.add_argument("--aliases", help="カンマ区切りの別名")
    a.add_argument("--category")
    a.add_argument("--subcategory")
    a.add_argument("--summary", help="吹き出しに出す 1〜2 文")
    a.add_argument("--definition", help="本文 Markdown ('-' で stdin, '@path' でファイル)")
    a.add_argument("--examples", help="カンマ区切りの使用例")
    # 旧名。EntryBase が relations へ畳むので、指定すると向きも一言も無い関係になる
    a.add_argument("--related", help="カンマ区切りの関連語（relations に取り込まれる）")
    a.add_argument("--tags", help="カンマ区切りのタグ")
    a.add_argument("--source", help="出典 (ファイル名など)")
    a.add_argument("--update", action="store_true", help="既存エントリを上書きする")
    a.add_argument(
        "--scope",
        choices=SCOPES,
        help=f"保存先の辞書 (既定 {GLOBAL_SCOPE} = 全体)。local は --folder と併せて使う",
    )
    add_folder_option(a)
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="登録済みの用語を一覧する")
    l.add_argument("--category")
    l.add_argument("--json", action="store_true")
    add_folder_option(l)
    l.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="用語の Markdown を表示する")
    sh.add_argument("target", help="用語名 / slug / カテゴリ/slug")
    sh.add_argument("--category", help="同名が複数あるときの絞り込み")
    sh.add_argument("--json", action="store_true")
    add_folder_option(sh)
    sh.set_defaults(func=cmd_show)

    r = sub.add_parser("rm", help="用語を削除する")
    r.add_argument("target", help="用語名 / slug / カテゴリ/slug")
    r.add_argument("--category", help="同名が複数あるときの絞り込み")
    add_folder_option(r)
    r.set_defaults(func=cmd_rm)

    mv = sub.add_parser("move", help="用語を別カテゴリ / 別の辞書へ移す")
    mv.add_argument("target", help="用語名 / slug / カテゴリ/slug")
    mv.add_argument("--to", help="移動先カテゴリ")
    mv.add_argument("--to-scope", choices=SCOPES, help="移動先の辞書 (global / local)")
    mv.add_argument("--category", help="同名が複数あるときの絞り込み (移動元)")
    add_folder_option(mv)
    mv.set_defaults(func=cmd_move)

    mg = sub.add_parser("merge", help="割れてしまった同じものを 1 つにまとめる")
    mg.add_argument("keep", help="残す側。用語名 / slug / カテゴリ/slug")
    mg.add_argument("drop", help="まとめる側（消える）。用語名 / slug / カテゴリ/slug")
    mg.add_argument("--keep-category", help="残す側の絞り込み (同名が複数あるとき)")
    mg.add_argument("--drop-category", help="まとめる側の絞り込み (同名が複数あるとき)")
    mg.add_argument("--yes", action="store_true",
                    help="下見ではなく実行する（付けないと何も変えない）")
    add_folder_option(mg)
    mg.set_defaults(func=cmd_merge)

    c = sub.add_parser("categories", help="カテゴリマスターを見る / 編集する")
    c.add_argument("--add", metavar="NAME", help="カテゴリを登録する (用語ゼロでも可)")
    c.add_argument("--description", help="--add に付ける説明")
    c.add_argument("--rename", nargs=2, metavar=("OLD", "NEW"), help="カテゴリ名を変える")
    c.add_argument("--remove", metavar="NAME", help="空のカテゴリを削除する")
    c.add_argument(
        "--scope",
        choices=SCOPES,
        help=f"--add / --rename / --remove の対象辞書 (既定 {GLOBAL_SCOPE})。local は --folder と併せて使う",
    )
    add_folder_option(c)
    c.set_defaults(func=cmd_categories)

    m = sub.add_parser("migrate", help="別のフォルダのデータを引き継ぐ（元は消さない）")
    m.add_argument("--from", dest="source", required=True, metavar="DIR",
                   help="引き継ぎ元（旧バージョンのフォルダ）")
    m.set_defaults(func=cmd_migrate)

    return p


def _use_utf8_when_piped() -> None:
    """パイプ越しの入出力を UTF-8 に固定する。

    凍結した exe はコンソールのコードページ (日本語 Windows なら CP932) で書くので、
    ``glosspop add --json -`` の出力を他のツールが受けると壊れた文字列になる。
    コンソールへ直接出す場合は既定のまま（UTF-8 で書くと CP932 のコンソールが化ける）。

    **stdin も同じ。** ここを外すと ``sys.stdin.read()`` がロケール (CP932) で復号し、
    UTF-8 で流し込んだ日本語がサロゲートに化けたまま**そのまま保存される**。
    ``echo '{"term":"冪等"}' | glosspop add --json -`` はスキルが使う経路そのものなので、
    黙って壊れた見出し語のエントリができる。ファイル入力 (``@path``) は最初から
    UTF-8 決め打ちで読んでいるので、揃えるのが正しい。
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass  # 差し替えられたストリーム等では何もしない


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _use_utf8_when_piped()
    # 隠しコマンド: フォルダ選択ダイアログの子プロセス。exe には python が無いので
    # 自分自身を再実行して使う (picker.py 参照)。ヘルプには出さない
    if argv and argv[0] == "__pick-folder":
        from .picker import run_dialog

        # UTF-8 のバイト列で返す。テキストで書くと exe ではコンソールの
        # コードページで符号化され、日本語を含むパスが壊れる
        sys.stdout.buffer.write(run_dialog(argv[1] if len(argv) > 1 else "").encode("utf-8"))
        sys.stdout.buffer.flush()
        return 0

    args = build_parser().parse_args(argv)
    # 辞書を読む前に「開いているフォルダ」を決める。あとから差し替えると
    # store のキャッシュが前のフォルダの辞書を掴んだままになる
    rc = _apply_folder(args)
    if rc:
        return rc
    try:
        for line in store.ensure_ready():
            print(f"旧レイアウトを移行しました: {line}", file=sys.stderr)
        return args.func(args)
    except (store.StoreError, CategoryNameError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"JSON を読めません: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
