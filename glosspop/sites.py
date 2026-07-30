"""URL ごとのローカル辞書。

``https://docs.python.org/3/library/os.html`` を読んでいるとき、
``sites/docs.python.org/3/library/.glosspop/`` があればそれを使う。無ければ
``sites/docs.python.org/.glosspop/`` を見る……と上へ辿り、**いちばん長く
一致するもの**を使う（フォルダ版の「いちばん近い祖先」と同じ規則）。

URL のパスをそのままディレクトリ名にはできない（``..``・``:``・クエリ・
Windows の禁止文字）。``_safe_segment()`` で潰したうえで、組み立てた結果が
``SITES_DIR`` の外に出ていないことを最後に必ず検査する。
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import config

#: ディレクトリ名に使えない文字
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')

#: 1 セグメントの長さ上限（パス全体が長くなりすぎないように）
_MAX_SEGMENT = 60

#: 探索するセグメント数の上限
_MAX_SEGMENTS = 12


class SiteError(Exception):
    pass


def _safe_segment(value: str) -> str:
    value = unquote(value or "").strip()
    value = _UNSAFE.sub("-", value).strip(". ")
    return value[:_MAX_SEGMENT]


def split_target(target: str) -> list[str]:
    """URL または ``ドメイン/パス`` をディレクトリ名の並びにする。

    クエリと fragment は落とす（同じページの別パラメータで辞書を分けたくない）。
    """
    raw = (target or "").strip()
    if not raw:
        return []
    if "//" not in raw.split("?")[0]:
        raw = f"//{raw}"                      # スキーム無しでも解釈できるように
    parts = urlsplit(raw if "//" in raw else f"//{raw}")
    netloc = (parts.netloc or "").strip()
    # ホストらしくないもの (空白入り、ドットもポートも無い) は URL と見なさない
    if not netloc or any(c.isspace() for c in netloc):
        return []
    if "." not in netloc and not netloc.lower().startswith("localhost"):
        return []
    host = _safe_segment(netloc)
    if not host:
        return []
    segments = [host.lower()]
    for piece in (parts.path or "").split("/"):
        safe = _safe_segment(piece)
        if safe:
            segments.append(safe)
        if len(segments) >= _MAX_SEGMENTS:
            break
    return segments


def path_for(target: str) -> Path:
    """``ドメイン/パス`` に対応するディレクトリ。``sites/`` の外には出さない。"""
    segments = split_target(target)
    if not segments:
        raise SiteError(f"URL として読めません: {target}")
    base = config.SITES_DIR.resolve()
    path = base.joinpath(*segments).resolve()
    if base != path and base not in path.parents:
        raise SiteError(f"URL として読めません: {target}")
    return path


def prefix_of(path: Path) -> str:
    """ディレクトリから ``ドメイン/パス`` の表示用文字列に戻す。"""
    try:
        return path.resolve().relative_to(config.SITES_DIR.resolve()).as_posix()
    except (ValueError, OSError):
        return path.name


def site_root(url: str) -> Path | None:
    """その URL に効く辞書のルート。無ければ ``None``（勝手に作らない）。"""
    try:
        path = path_for(url)
    except SiteError:
        return None
    base = config.SITES_DIR.resolve()
    current = path
    while current == base or base in current.parents:
        if (current / config.LOCAL_DIR_NAME).is_dir():
            return current
        if current == base:
            break
        current = current.parent
    return None


def create(target: str) -> Path:
    """``ドメイン/パス`` に辞書を作る（既にあればそれを返す）。"""
    path = path_for(target)
    (path / config.LOCAL_DIR_NAME / "glossary").mkdir(parents=True, exist_ok=True)
    return path


def describe(url: str | None) -> dict:
    """UI 用: いまの URL に効いている辞書の情報。"""
    root = site_root(url) if url else None
    return {
        "url": url or "",
        "prefix": prefix_of(root) if root else "",
        "dir": str(root / config.LOCAL_DIR_NAME / "glossary") if root else "",
        "suggested_prefix": suggested_prefix(url) if url else "",
    }


def suggested_prefix(url: str) -> str:
    """作成フォームの初期値。ページ名らしい末尾は落として 1 つ上を提案する。"""
    segments = split_target(url)
    if not segments:
        return ""
    if len(segments) > 1 and "." in segments[-1]:
        segments = segments[:-1]      # os.html のようなページ名は含めない
    return "/".join(segments)
