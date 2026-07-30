"""Markdown / プレーンテキスト → HTML。

``html: False`` にしているのは安全側に倒すためだけでなく、生 HTML が混ざると
:mod:`glosspop.linker` のタグ走査が想定外の構造を踏む可能性を消すため。
"""

from __future__ import annotations

import re
from html import escape

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.front_matter import front_matter_plugin

def _make_md(*, breaks: bool) -> MarkdownIt:
    return (
        MarkdownIt(
            "commonmark",
            {
                "html": False,
                "linkify": True,
                "typographer": False,
                "xhtmlOut": False,  # <br> / <hr> を HTML5 の書き方で出す
                "breaks": breaks,
            },
        )
        .enable(["table", "strikethrough"])
        .use(front_matter_plugin)
        .use(anchors_plugin, max_level=4, permalink=False)
    )


#: 表示するソース文書用。標準の CommonMark どおり、単一改行は無視する
_md = _make_md(breaks=False)

#: 辞書本文用。1 文 1 行で書かれた本文をそのまま改行して見せる
_md_soft = _make_md(breaks=True)

_BLANK_LINE = re.compile(r"\n\s*\n")
_H1 = re.compile(r"^#\s+(.+?)\s*$", re.M)

#: 句点のあとがこれらの文字なら文の途中とみなして改行しない
_NOT_AFTER = "）)」』】〉》］]｝}、。！？!?…・"
_SENTENCE_END = "。！？"
_LIST_MARKER = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+)")

#: これより短い行は分割しない (すでに読める長さなので触らない)
SOFTEN_THRESHOLD = 60


def md_to_html(text: str) -> str:
    return _md.render(text or "")


def _split_sentences(line: str) -> list[str]:
    """行を文単位に切る。インラインコード (`...`) の中の句点は無視する。"""
    pieces: list[str] = []
    buf: list[str] = []
    in_code = False
    for i, ch in enumerate(line):
        buf.append(ch)
        if ch == "`":
            in_code = not in_code
            continue
        if in_code or ch not in _SENTENCE_END:
            continue
        nxt = line[i + 1] if i + 1 < len(line) else ""
        if nxt and nxt not in _NOT_AFTER:
            pieces.append("".join(buf))
            buf = []
    if buf:
        pieces.append("".join(buf))
    return pieces


def soften_paragraphs(text: str, *, max_len: int = SOFTEN_THRESHOLD) -> str:
    """長い段落を 1 文 1 行に割る。

    AI が生成した本文は 1 段落に 5〜6 文を詰め込みがちで、そのまま出すと
    読みづらい壁になる。コードフェンス・見出し・表は触らない。
    すでに 1 文 1 行になっている本文に対しては何も変えない (冪等)。
    """
    lines_out: list[str] = []
    fence: str | None = None

    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.lstrip()

        if fence is not None:
            lines_out.append(line)
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            lines_out.append(line)
            continue
        if len(line) <= max_len or stripped.startswith("#") or "|" in line:
            lines_out.append(line)
            continue

        pieces = _split_sentences(stripped)
        if len(pieces) <= 1:
            lines_out.append(line)
            continue

        indent = line[: len(line) - len(stripped)]
        marker = _LIST_MARKER.match(stripped)
        # 箇条書きの続きは中身の列に揃える (次の項目と誤読されないように)
        cont = indent + " " * len(marker.group(0)) if marker else indent
        lines_out.append(indent + pieces[0].rstrip())
        lines_out.extend(cont + p.strip() for p in pieces[1:])

    return "\n".join(lines_out)


def definition_to_html(text: str) -> str:
    """辞書本文を HTML にする。単一改行は <br> になる。"""
    return _md_soft.render(soften_paragraphs(text or ""))


def plain_to_html(text: str) -> str:
    """素のテキストを段落 HTML にする。空行で段落、単一改行は <br>。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    blocks = []
    for block in _BLANK_LINE.split(text):
        if not block.strip():
            continue
        lines = [escape(line) for line in block.split("\n")]
        blocks.append("<p>" + "<br>\n".join(lines) + "</p>")
    return "\n".join(blocks)


def render_source(
    text: str, *, kind: str = "auto", filename: str | None = None, base_url: str = ""
) -> str:
    """``kind`` は ``markdown`` / ``text`` / ``html`` / ``auto``。

    ``html`` は取得済みの Web ページ。クライアント経由で戻ってくるので、
    表示前にもう一度サニタイザを通す。
    """
    if kind == "auto":
        name = (filename or "").lower()
        kind = "markdown" if name.endswith((".md", ".markdown", ".mdown")) else "text"
    if kind == "html":
        from .htmlclean import clean_fragment  # 循環 import を避けて遅延読み込み

        return clean_fragment(text or "", base_url=base_url)
    return md_to_html(text) if kind == "markdown" else plain_to_html(text)


def guess_title(text: str, *, fallback: str = "") -> str:
    """先頭の見出し、無ければ最初の非空行を題として使う。"""
    m = _H1.search(text or "")
    if m:
        return m.group(1).strip()
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:80]
    return fallback
