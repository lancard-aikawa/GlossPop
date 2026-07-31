"""コマンドライン: サーバ起動と、Claude スキルから叩く辞書操作。

Claude スキル (``/gloss-add``) からは JSON を stdin で渡す形を使う。
シェルのクォートで日本語や改行が壊れないのでいちばん安全。

    echo '{"term":"用語", ...}' | glosspop add --json -
"""

from __future__ import annotations

import argparse
import json
import sys

from . import categories, config, store
from .models import CategoryNameError, EntryDraft, normalize_category


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


# --------------------------------------------------------------------------- #
# サブコマンド
# --------------------------------------------------------------------------- #

def _open_window_later(args: argparse.Namespace, url: str) -> None:
    """サーバが listen したら専用ウィンドウを開く（別スレッド）。

    **窓の寿命は追えない。** 起動した ``msedge.exe`` はブラウザ本体を別プロセスで
    生んですぐ終了するので、こちらの ``Popen`` を ``wait()`` しても「窓が閉じた」
    ことにはならない（一度これで「窓は開いたままサーバだけ落ちる」を作った）。
    サーバを止めるのは Ctrl+C。
    """
    import threading

    from . import appwindow

    def run() -> None:
        if not appwindow.wait_until_ready(args.host, args.port):
            print("サーバの起動を確認できませんでした。ウィンドウは開きません。", file=sys.stderr)
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

    if open_window:
        _open_window_later(args, url)

    uvicorn.run(
        target,
        host=args.host,
        port=args.port,
        reload=reload,
        log_level=args.log_level,
    )
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

    draft = EntryDraft.model_validate(data)
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


def cmd_move(args: argparse.Namespace) -> int:
    entry = _pick_one(args.target, args.category)
    if entry is None:
        return 1
    moved = store.move(entry.ref, args.to)
    _emit({"status": "moved", "from": entry.ref, "ref": moved.ref, "path": str(store.path_for_ref(moved.ref))})
    return 0


def cmd_categories(args: argparse.Namespace) -> int:
    if args.add:
        category = categories.ensure(args.add, description=args.description or "")
        _emit({"status": "ensured", **category.model_dump()})
        return 0
    if args.rename:
        old, new = args.rename
        moved = store.rename_category(old, new)
        _emit({"status": "renamed", "from": old, "to": new, "moved_entries": moved})
        return 0
    if args.remove:
        store.delete_category(args.remove)
        _emit({"status": "deleted", "name": args.remove})
        return 0
    _emit(store.category_tree())
    return 0


# --------------------------------------------------------------------------- #
# パーサ
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="glosspop", description="GlossPop 辞書ビューア")
    sub = p.add_subparsers(dest="command", required=True)

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
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="登録済みの用語を一覧する")
    l.add_argument("--category")
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="用語の Markdown を表示する")
    sh.add_argument("target", help="用語名 / slug / カテゴリ/slug")
    sh.add_argument("--category", help="同名が複数あるときの絞り込み")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(func=cmd_show)

    r = sub.add_parser("rm", help="用語を削除する")
    r.add_argument("target", help="用語名 / slug / カテゴリ/slug")
    r.add_argument("--category", help="同名が複数あるときの絞り込み")
    r.set_defaults(func=cmd_rm)

    mv = sub.add_parser("move", help="用語を別カテゴリへ移す")
    mv.add_argument("target", help="用語名 / slug / カテゴリ/slug")
    mv.add_argument("--to", required=True, help="移動先カテゴリ")
    mv.add_argument("--category", help="同名が複数あるときの絞り込み (移動元)")
    mv.set_defaults(func=cmd_move)

    c = sub.add_parser("categories", help="カテゴリマスターを見る / 編集する")
    c.add_argument("--add", metavar="NAME", help="カテゴリを登録する (用語ゼロでも可)")
    c.add_argument("--description", help="--add に付ける説明")
    c.add_argument("--rename", nargs=2, metavar=("OLD", "NEW"), help="カテゴリ名を変える")
    c.add_argument("--remove", metavar="NAME", help="空のカテゴリを削除する")
    c.set_defaults(func=cmd_categories)

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
