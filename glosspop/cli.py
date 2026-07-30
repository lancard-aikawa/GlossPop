"""コマンドライン: サーバ起動と、Claude スキルから叩く辞書操作。

Claude スキル (``/gloss-add``) からは JSON を stdin で渡す形を使う。
シェルのクォートで日本語や改行が壊れないのでいちばん安全。

    echo '{"term":"用語", ...}' | glosspop add --json -
"""

from __future__ import annotations

import argparse
import json
import sys

from . import config, store
from .models import EntryDraft


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

def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    config.ensure_dirs()
    print(f"GlossPop: http://{args.host}:{args.port}/", file=sys.stderr)
    print(f"  辞書   : {config.GLOSSARY_DIR}", file=sys.stderr)
    print(f"  content: {config.CONTENT_DIR}", file=sys.stderr)
    print(f"  claude : {config.CLAUDE_BIN or '(見つかりません — AI 下書きは無効)'}", file=sys.stderr)
    uvicorn.run(
        "glosspop.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
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
    existing = store.find_by_surface(draft.term)

    if existing is not None and not args.update:
        print(
            f"「{draft.term}」は既に登録されています (slug: {existing.slug})。"
            "上書きするなら --update を付けてください。",
            file=sys.stderr,
        )
        _emit({"status": "exists", "slug": existing.slug, "path": str(store.path_for(existing.slug))})
        return 1

    entry = store.save(draft, slug=existing.slug if existing is not None else None)
    _emit(
        {
            "status": "updated" if existing is not None else "created",
            "slug": entry.slug,
            "term": entry.term,
            "category": entry.category,
            "subcategory": entry.subcategory,
            "path": str(store.path_for(entry.slug)),
            "url": f"/glossary/{entry.slug}",
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


def cmd_show(args: argparse.Namespace) -> int:
    entry = store.get(args.target) or store.find_by_surface(args.target)
    if entry is None:
        print(f"見つかりません: {args.target}", file=sys.stderr)
        return 1
    if args.json:
        _emit(entry.model_dump())
    else:
        sys.stdout.write(store.path_for(entry.slug).read_text(encoding="utf-8"))
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    entry = store.get(args.target) or store.find_by_surface(args.target)
    if entry is None or not store.delete(entry.slug):
        print(f"見つかりません: {args.target}", file=sys.stderr)
        return 1
    _emit({"status": "deleted", "slug": entry.slug, "term": entry.term})
    return 0


def cmd_categories(_args: argparse.Namespace) -> int:
    _emit(store.category_tree())
    return 0


# --------------------------------------------------------------------------- #
# パーサ
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="glosspop", description="GlossPop 辞書ビューア")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="ビューアを起動する")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--reload", action="store_true")
    s.add_argument("--log-level", default="info")
    s.set_defaults(func=cmd_serve)

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
    a.add_argument("--related", help="カンマ区切りの関連語")
    a.add_argument("--tags", help="カンマ区切りのタグ")
    a.add_argument("--source", help="出典 (ファイル名など)")
    a.add_argument("--update", action="store_true", help="既存エントリを上書きする")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="登録済みの用語を一覧する")
    l.add_argument("--category")
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="用語の Markdown を表示する")
    sh.add_argument("target", help="slug または用語名")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(func=cmd_show)

    r = sub.add_parser("rm", help="用語を削除する")
    r.add_argument("target", help="slug または用語名")
    r.set_defaults(func=cmd_rm)

    c = sub.add_parser("categories", help="カテゴリ構成を JSON で出す")
    c.set_defaults(func=cmd_categories)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except store.StoreError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"JSON を読めません: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
