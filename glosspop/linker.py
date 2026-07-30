"""レンダリング済み HTML に辞書リンクを差し込む。

タグと属性は触らず、テキストノードだけを書き換える。
``<a> <code> <pre> <script> <style> <textarea> <kbd> <samp>`` の中は無視する
(コードサンプルや既存リンクを壊さないため)。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from html import escape
from urllib.parse import quote

from .models import Entry

_TAG_RE = re.compile(r"<[^>]*>", re.S)
_TAG_NAME_RE = re.compile(r"^<\s*(/?)\s*([A-Za-z][A-Za-z0-9]*)")

#: この要素の内側ではリンクを作らない
SKIP_TAGS = frozenset({"a", "code", "pre", "script", "style", "textarea", "kbd", "samp"})

#: 前後の境界チェックが必要な文字クラス (英数字と _)
_WORDISH = re.compile(r"[0-9A-Za-z_]")
_LOOKBEHIND = r"(?<![0-9A-Za-z_])"
_LOOKAHEAD = r"(?![0-9A-Za-z_])"


def _tag_info(tag: str) -> tuple[str | None, bool, bool]:
    """(タグ名, 閉じタグか, 自己終了か) を返す。タグでなければ名前が None。"""
    m = _TAG_NAME_RE.match(tag)
    if not m:
        return None, False, False
    return m.group(2).lower(), m.group(1) == "/", tag.rstrip().endswith("/>")


def _variants(surface: str) -> list[str]:
    """本文 HTML 中に現れうる表記のバリエーション。

    markdown レンダラは ``& < > "`` をエスケープするので、生の用語だけでは
    ``A&B`` のような語にマッチしない。
    """
    out = [surface]
    for v in (escape(surface, quote=False), escape(surface, quote=True)):
        if v not in out:
            out.append(v)
    return out


def _pattern_for(variant: str) -> str:
    pat = re.escape(variant)
    if _WORDISH.match(variant[0]):
        pat = _LOOKBEHIND + pat
    if _WORDISH.match(variant[-1]):
        pat = pat + _LOOKAHEAD
    return pat


class Linker:
    """辞書エントリ集合から自動リンカを組み立てる。"""

    def __init__(self, entries: Sequence[Entry]) -> None:
        self._by_slug: dict[str, Entry] = {e.slug: e for e in entries}
        self._lookup: dict[str, Entry] = {}
        variants: list[str] = []
        for entry in entries:
            for surface in entry.surfaces:
                for variant in _variants(surface):
                    if not variant:
                        continue
                    keys = {variant.casefold(), variant.lower()}
                    if any(k in self._lookup for k in keys):
                        continue  # 先に登録されたエントリを優先 (先勝ち)
                    for k in keys:
                        self._lookup[k] = entry
                    variants.append(variant)
        # 最長一致優先: 同じ開始位置では長い表記が勝つ
        variants.sort(key=len, reverse=True)
        self._re = re.compile("|".join(_pattern_for(v) for v in variants), re.IGNORECASE) if variants else None

    def __bool__(self) -> bool:
        return self._re is not None

    def annotate(
        self,
        html: str,
        *,
        first_only: bool = False,
        skip_slugs: Iterable[str] = (),
    ) -> tuple[str, list[Entry]]:
        """HTML にリンクを差し込み (書き換え後 HTML, 出現したエントリ) を返す。

        エントリは初出順。``first_only`` なら各用語の最初の出現だけをリンクする。
        ``skip_slugs`` は無視するエントリ (辞書ページで自分自身を貼らない用)。
        """
        if self._re is None or not html:
            return html, []

        skip = frozenset(skip_slugs)
        hits: dict[str, int] = {}
        parts: list[str] = []
        skip_depth = 0
        pos = 0

        for m in _TAG_RE.finditer(html):
            chunk = html[pos:m.start()]
            if chunk:
                parts.append(chunk if skip_depth else self._sub(chunk, hits, first_only, skip))
            tag = m.group(0)
            parts.append(tag)
            name, closing, selfclose = _tag_info(tag)
            if name in SKIP_TAGS and not selfclose:
                skip_depth = max(0, skip_depth - 1) if closing else skip_depth + 1
            pos = m.end()

        tail = html[pos:]
        if tail:
            parts.append(tail if skip_depth else self._sub(tail, hits, first_only, skip))

        entries = [self._by_slug[s] for s in hits if s in self._by_slug]
        return "".join(parts), entries

    # ------------------------------------------------------------------ #

    def _sub(self, text: str, hits: dict[str, int], first_only: bool, skip: frozenset[str]) -> str:
        def repl(m: re.Match[str]) -> str:
            surface = m.group(0)
            entry = self._lookup.get(surface.casefold()) or self._lookup.get(surface.lower())
            if entry is None or entry.slug in skip:
                return surface
            if first_only and entry.slug in hits:
                return surface
            hits[entry.slug] = hits.get(entry.slug, 0) + 1
            return (
                f'<a class="gloss-link" href="/glossary/{quote(entry.slug)}"'
                f' data-gloss="{escape(entry.slug)}"'
                f' data-term="{escape(entry.term)}">{surface}</a>'
            )

        return self._re.sub(repl, text)  # type: ignore[union-attr]
