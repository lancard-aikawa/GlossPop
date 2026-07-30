"""外部から取ってきた HTML を、読める・安全な断片に落とす。

やること:

* script / style / nav / header / footer などを中身ごと捨てる
* 残したタグ以外は開始・終了タグだけ落として中のテキストは残す
* 属性は href / src / alt / title だけ残し、相対 URL を絶対化する
* ``javascript:`` などのスキームを弾く
* ``<main>`` / ``<article>`` があればその中身だけを採る

依存を増やしたくないので標準ライブラリの HTMLParser だけで書いている。
完璧なサニタイザではないが、許可制なので未知のタグ・属性は必ず落ちる。
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

#: 残すタグ
KEEP_TAGS = frozenset({
    "p", "br", "hr", "div", "span", "section",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "dl", "dt", "dd",
    "blockquote", "pre", "code", "kbd", "samp", "var",
    "em", "strong", "b", "i", "u", "s", "del", "ins", "mark", "small", "sub", "sup",
    "a", "img", "figure", "figcaption",
    "ruby", "rb",  # ルビの親字は残す (読みは DROP_TREES で落とす)
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
})

#: 中身ごと捨てるタグ
DROP_TREES = frozenset({
    "script", "style", "noscript", "template", "svg", "math", "canvas",
    "iframe", "frame", "frameset", "object", "embed", "applet",
    "form", "input", "button", "select", "textarea", "label", "fieldset",
    "nav", "header", "footer", "aside", "menu", "dialog", "audio", "video",
    # ルビの読み。残すと「太郎たろう」のように地の文に混ざり、自動リンクの
    # 照合が壊れる（青空文庫の HTML 版や epub で必ず踏む）
    "rt", "rp",
})

#: 閉じタグを持たない要素。**HTML の void 要素を漏れなく挙げること。**
#:
#: ここに無い void 要素が DROP_TREES に入っていると、閉じタグを待ち続けて
#: **それ以降の本文が全部消える**。``<input>`` がそうで、検索ボックスのある
#: ページ（＝たいていの Web ページ）が空になっていた。
VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "frame", "hr", "img",
    "input", "link", "meta", "param", "source", "track", "wbr",
})

#: タグごとに残す属性
KEEP_ATTRS = {
    "a": ("href", "title"),
    "img": ("src", "alt", "title"),
}

_SAFE_SCHEMES = ("http", "https", "mailto", "data:image/")

#: 本文が入っていそうな順に探す
MAIN_CANDIDATES = ("main", "article")


def _safe_url(value: str, base: str, *, allow_relative: bool = False) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith("#"):
        return None  # ページ内アンカーは開いた文書では意味を持たないので落とす
    if value.startswith("//"):
        return None  # プロトコル相対は基準が無いと外部への遷移になる
    absolute = urljoin(base, value) if base else value
    scheme = urlsplit(absolute).scheme.lower()
    if scheme in ("http", "https", "mailto"):
        return absolute
    if absolute.lower().startswith("data:image/"):
        return absolute
    # 基準 URL が無い = ローカルの .html。リンク先の相対パスはビューアが
    # content の中を辿れるので残す (画像は配信する経路が無いので残さない)
    if allow_relative and not base and not scheme:
        return absolute
    return None


class _Cleaner(HTMLParser):
    def __init__(self, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self._drop_depth = 0
        self._drop_tag: str | None = None
        self._open: list[str] = []
        self.title = ""
        self._in_title = False
        # <main> / <article> の中身を別に貯めておき、あればそちらを採用する
        self._main_parts: list[str] | None = None
        self._main_tag: str | None = None
        self._main_depth = 0

    # -- 出力先 ------------------------------------------------------------
    def _emit(self, text: str) -> None:
        if self._main_parts is not None:
            self._main_parts.append(text)
        else:
            self.parts.append(text)

    # -- パーサコールバック ------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._drop_depth:
            if tag == self._drop_tag and tag not in VOID_TAGS:
                self._drop_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in DROP_TREES:
            if tag not in VOID_TAGS:
                self._drop_depth = 1
                self._drop_tag = tag
            return
        if tag == "base":
            for name, value in attrs:
                if name.lower() == "href" and value:
                    self.base_url = urljoin(self.base_url, value)
            return

        if self._main_parts is None and tag in MAIN_CANDIDATES:
            self._main_parts = []
            self._main_tag = tag
            self._main_depth = 1
            return
        if self._main_parts is not None and tag == self._main_tag:
            self._main_depth += 1

        if tag not in KEEP_TAGS:
            return

        rendered = [tag]
        for name, value in attrs:
            name = name.lower()
            if name not in KEEP_ATTRS.get(tag, ()):
                continue
            if name in ("href", "src"):
                url = _safe_url(value or "", self.base_url, allow_relative=(name == "href"))
                if url is None:
                    continue
                value = url
            rendered.append(f'{name}="{escape(value or "", quote=True)}"')
        if tag in VOID_TAGS:
            self._emit(f"<{' '.join(rendered)}>")
        else:
            self._emit(f"<{' '.join(rendered)}>")
            self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in VOID_TAGS or tag in KEEP_TAGS:
            self.handle_starttag(tag, attrs)
            if tag not in VOID_TAGS and self._open and self._open[-1] == tag:
                self._open.pop()
                self._emit(f"</{tag}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
            return
        if self._drop_depth:
            if tag == self._drop_tag:
                self._drop_depth -= 1
                if self._drop_depth == 0:
                    self._drop_tag = None
            return
        if self._main_parts is not None and tag == self._main_tag:
            self._main_depth -= 1
            if self._main_depth == 0:
                # main/article を閉じた: ここまでの中身を本文として確定する
                self.parts = self._main_parts
                self._main_parts = None
                self._main_tag = None
                self._finished_main = True
            return
        if tag not in KEEP_TAGS or tag in VOID_TAGS:
            return
        if tag in self._open:
            # 途中で閉じ忘れているタグをまとめて閉じる
            while self._open:
                open_tag = self._open.pop()
                self._emit(f"</{open_tag}>")
                if open_tag == tag:
                    break

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._drop_depth or not data:
            return
        self._emit(escape(data, quote=False))

    def close(self) -> str:  # type: ignore[override]
        super().close()
        if self._main_parts is not None:  # 閉じられていない main/article
            self.parts = self._main_parts
            self._main_parts = None
        while self._open:
            self.parts.append(f"</{self._open.pop()}>")
        return "".join(self.parts)


def clean_html(html: str, *, base_url: str = "") -> tuple[str, str]:
    """(本文 HTML, <title>) を返す。"""
    cleaner = _Cleaner(base_url)
    cleaner.feed(html or "")
    body = cleaner.close()
    return body.strip(), " ".join(cleaner.title.split())


def clean_fragment(html: str, *, base_url: str = "") -> str:
    """すでに掃除済みの断片をもう一度通す（冪等・クライアント経由の再検証用）。"""
    return clean_html(html, base_url=base_url)[0]
