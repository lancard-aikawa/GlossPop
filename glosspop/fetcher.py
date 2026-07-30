"""URL を取ってきてビューアで開ける形にする。

ブラウザから直接 fetch すると CORS で落ちるので、サーバ側で取得する。
ローカル専用ツールなので localhost や社内ホストも許可する（そこを読むために
使うことがあるため）。ガードはスキーム・サイズ・タイムアウト・リダイレクト数。
"""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

import httpx

from . import config
from .htmlclean import clean_html

MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdown")
TEXT_SUFFIXES = (".txt", ".text", ".log", ".rst", ".csv")


class FetchError(RuntimeError):
    pass


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
                encoding = response.encoding or "utf-8"
    except httpx.TooManyRedirects as exc:
        raise FetchError("リダイレクトが多すぎます") from exc
    except httpx.TimeoutException as exc:
        raise FetchError(f"{config.FETCH_TIMEOUT:.0f} 秒でタイムアウトしました") from exc
    except httpx.HTTPStatusError as exc:
        raise FetchError(f"取得できません: HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise FetchError(f"取得できません: {exc}") from exc

    try:
        text = raw.decode(encoding, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")

    kind = _kind_for(content_type, urlsplit(final_url).path)
    title = ""
    if kind == "html":
        text, title = clean_html(text, base_url=final_url)
        if not text.strip():
            raise FetchError("本文を抽出できませんでした（JavaScript で描画するページかもしれません）")

    return {"url": final_url, "title": title, "kind": kind, "text": text}
