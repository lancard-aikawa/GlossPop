"""URL を取ってきてビューアで開ける形にする。

ブラウザから直接 fetch すると CORS で落ちるので、サーバ側で取得する。
ローカル専用ツールなので localhost や社内ホストも許可する（そこを読むために
使うことがあるため）。ガードはスキーム・サイズ・タイムアウト・リダイレクト数。
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

import httpx

from . import config, documents
from .htmlclean import clean_html

MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdown")
TEXT_SUFFIXES = (".txt", ".text", ".log", ".rst", ".csv")

#: ``<meta charset>`` を探す範囲。宣言は head の先頭にしか書けない。
#: 全体に正規表現を当てないのは、青空文庫の 200KB 級のページで無駄に走らせないため。
META_SCAN_BYTES = 4096

_META_CHARSET_RE = re.compile(rb"""charset\s*=\s*["']?\s*([\w.:+-]+)""", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


class FetchError(RuntimeError):
    pass


def _meta_charset(raw: bytes) -> str | None:
    """HTML が自分で名乗っている文字コードを読む。

    **ヘッダに charset が無いページはここでしか分からない。** 青空文庫がそうで
    (``Content-Type: text/html`` だけ、中身は Shift_JIS)、httpx の
    ``response.encoding`` は charset が無いと utf-8 に倒れるため、そのまま使うと
    **化けたまま「読めてしまう」**。ファイル経路で既に踏んだ罠と同じもの
    (→ ``documents.decode``)。
    """
    found = _META_CHARSET_RE.search(raw[:META_SCAN_BYTES])
    return found.group(1).decode("ascii", "ignore") if found else None


def _visible_text(html: str) -> str:
    """タグを外して残る文字。**中身があるかの判定はこちらで行う。**

    SPA は空の div だけを返すことがある（note.com の実測が
    ``<div><div><div></div>…`` の 89 文字）。``strip()`` だけで見ると「文字がある
    ので本文が取れた」と判定してしまい、**エラーも出さずに真っ白な画面を出す**。
    """
    return _TAG_RE.sub("", html).strip()


def _kind_for(content_type: str, path: str) -> str:
    ctype = (content_type or "").split(";")[0].strip().lower()
    lowered = unquote(path).lower()
    if lowered.endswith(MARKDOWN_SUFFIXES) or ctype in ("text/markdown", "text/x-markdown"):
        return "markdown"
    if ctype in ("text/html", "application/xhtml+xml"):
        return "html"
    if lowered.endswith(TEXT_SUFFIXES) or ctype.startswith("text/"):
        return "text"
    if ctype in ("application/json", "application/xml", "text/xml"):
        return "text"
    if not ctype:
        return "html"
    raise FetchError(f"表示できない種類です: {ctype}")


def _read_limited(response: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > config.FETCH_MAX_BYTES:
            raise FetchError(
                f"サイズが上限 ({config.FETCH_MAX_BYTES // (1024 * 1024)} MB) を超えました"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def fetch(url: str) -> dict:
    """``{url, title, kind, text}`` を返す。kind は markdown / text / html。"""
    url = (url or "").strip()
    if not url:
        raise FetchError("URL を入力してください")
    if "://" not in url:
        url = "https://" + url

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FetchError(f"http / https のみ開けます（指定: {parts.scheme or '不明'}）")
    if not parts.netloc:
        raise FetchError(f"URL が不正です: {url}")

    try:
        with httpx.Client(
            follow_redirects=True,
            max_redirects=config.FETCH_MAX_REDIRECTS,
            timeout=config.FETCH_TIMEOUT,
            headers={
                "User-Agent": config.FETCH_USER_AGENT,
                "Accept": "text/html,text/markdown,text/plain;q=0.9,*/*;q=0.5",
            },
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                raw = _read_limited(response)
                final_url = str(response.url)
                content_type = response.headers.get("content-type", "")
                # ``encoding`` ではなく ``charset_encoding``。前者は charset が
                # 無いとき utf-8 を返すので、「宣言が無い」ことが分からなくなる。
                declared = response.charset_encoding
    except httpx.TooManyRedirects as exc:
        raise FetchError("リダイレクトが多すぎます") from exc
    except httpx.TimeoutException as exc:
        raise FetchError(f"{config.FETCH_TIMEOUT:.0f} 秒でタイムアウトしました") from exc
    except httpx.HTTPStatusError as exc:
        raise FetchError(f"取得できません: HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise FetchError(f"取得できません: {exc}") from exc

    text = documents.decode(raw, declared=declared or _meta_charset(raw))

    kind = _kind_for(content_type, urlsplit(final_url).path)
    title = ""
    if kind == "html":
        text, title = clean_html(text, base_url=final_url)
        if not _visible_text(text):
            raise FetchError("本文を抽出できませんでした（JavaScript で描画するページかもしれません）")

    return {"url": final_url, "title": title, "kind": kind, "text": text}
