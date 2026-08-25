"""レンダリング済み HTML に辞書リンクを差し込む。

タグと属性は触らず、テキストだけを書き換える。
``<pre>`` (コードブロック) と ``<a>`` (既存リンク) の中は無視する。

インラインの ``<code>`` は対象にする。日本語の技術文書では ```用語``` を
コードではなく強調のつもりで書くことが多く、そこがリンクにならないと
「登録したのにリンクされない」という見え方になるため。
コードそのものを載せるのは ``<pre>`` のほうなので、区別できる。

照合は「読者に見えるテキスト」に対して行う。``**冪**等`` のように強調で語が
分断されていても、インライン要素をまたいで一致させ、断片ごとにリンクを張る。
ブロック要素と ``<br>`` はまたがない (段落をまたいだ偶然の一致を防ぐため)。

同じ表記がカテゴリ違いで複数登録されていることがあるので、リンクは
「表記」を持たせておき、吹き出し側が表記から全件を引く。

大文字小文字は原則として区別しないが、**短い全大文字の略語だけは区別する**
(``_case_sensitive()``)。``MD`` が ``README.md`` に当たるのを防ぐため。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from html import escape
from urllib.parse import quote

from .models import Entry

_TAG_RE = re.compile(r"<[^>]*>", re.S)
_TAG_NAME_RE = re.compile(r"^<\s*(/?)\s*([A-Za-z][A-Za-z0-9]*)")

#: この要素の内側ではリンクを作らない。
#: ``<pre>`` はコードブロック全体を覆うので、``<pre><code>`` の中も自動的に外れる。
SKIP_TAGS = frozenset({"a", "pre", "script", "style", "textarea"})

#: この要素をまたぐテキストは「つながっている」とみなす。
#: ``<br>`` は視覚的な改行なので意図的に外している。
INLINE_TAGS = frozenset({
    "a", "abbr", "b", "bdi", "bdo", "big", "cite", "code", "data", "dfn", "del",
    "em", "font", "i", "ins", "kbd", "mark", "q", "rb", "rp", "rt", "ruby", "s", "samp",
    "small", "span", "strong", "sub", "sup", "time", "tt", "u", "var", "wbr",
})

#: 前後の境界チェックが必要な文字クラス (英数字と _)
_WORDISH = re.compile(r"[0-9A-Za-z_]")
_LOOKBEHIND = r"(?<![0-9A-Za-z_])"
_LOOKAHEAD = r"(?![0-9A-Za-z_])"

#: **短い全大文字の略語だけ大文字小文字を区別する。**
#:
#: 照合は原則として大文字小文字を無視するが、``MD`` (Markdown の別名) のような
#: 短い略語はそのままだと ``README.md`` の拡張子に当たる。境界チェックは
#: 「英数字以外なら通す」ので ``.`` が境界として通ってしまい、README を開くと
#: 拡張子が軒並みリンクになった。``ML`` ``DB`` など同じ形の別名を登録するたびに
#: 同じことが起きる（インラインコードを対象に加えたことで表面化した。コード片には
#: 拡張子やオプション名が多い）。
#:
#: 略語は本文でも大文字で書かれるので、ここだけ区別すれば実害なく潰せる。
#: 代償は ``API`` を ``api`` と小文字で書いた箇所が当たらなくなること。
#:
#: **長さで切っているので ``HTML`` は対象外**（``.html`` には当たったまま）。
#: 伸ばすならこの定数だけを変える。上限を外すと ``NASA`` のような全大文字の
#: 固有名詞まで区別することになるので、必要になってから広げる。
_ACRONYM_MAX_LEN = 3


def _case_sensitive(variant: str) -> bool:
    """この表記は大文字小文字を区別して照合するか。

    ``isupper()`` は小文字を含めば偽になるので、``&amp;`` に展開された
    エスケープ済みの表記 (``_variants()``) が巻き込まれることはない。
    """
    return (
        len(variant) <= _ACRONYM_MAX_LEN
        and variant.isascii()
        and variant.isalnum()
        and variant.isupper()
    )

# セグメントの種類
_TEXT = "text"
_TAG = "tag"


def entry_url(entry: Entry) -> str:
    # ref はローカル辞書だと ".local/" が付くので、ref から組み立てる
    return "/glossary/" + "/".join(quote(part) for part in entry.ref.split("/"))


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
    if _case_sensitive(variant):
        # 全体は IGNORECASE で組むので、ここだけスコープ付きで打ち消す
        pat = f"(?-i:{pat})"
    if _WORDISH.match(variant[0]):
        pat = _LOOKBEHIND + pat
    if _WORDISH.match(variant[-1]):
        pat = pat + _LOOKAHEAD
    return pat


#: trie の終端の目印。1 文字のキーとは絶対に衝突しない値にする
_END = object()


def _fold(ch: str) -> str:
    """trie のキーに使う 1 文字。

    全体を IGNORECASE で組むので、``A`` と ``a`` は同じ枝にまとめないと
    **同じ位置で 2 つの枝が両方成立**し、どちらが選ばれるかが並び順まかせになる
    （長い表記が勝つ、という約束が崩れる）。長さの変わる畳み込み（``ß`` → ``ss``）
    だけは 1 文字に収まらないので、そのときは畳まない。
    """
    low = ch.lower()
    return low if len(low) == 1 else ch


def _trie(variants: list[str]) -> str:
    """表記の並びを、前方一致でまとめた 1 本の正規表現にする。

    ``用語0001|用語0002|…`` と並べると、照合は**本文の長さ × 候補数**で効く。
    共通の先頭をまとめれば候補数の効きが消える（実測: 3000 語・2120 字で
    40.8 ms → 計測不能）。

    **境界チェックは枝ごとに置く。** 先頭側 (`_LOOKBEHIND`) は最初の 1 文字で
    決まるので枝の根に、末尾側 (`_LOOKAHEAD`) は表記ごとに違うので**終端に**置く。
    """
    roots: dict[bool, dict] = {}
    for variant in variants:
        node = roots.setdefault(bool(_WORDISH.match(variant[0])), {})
        for ch in variant:
            node = node.setdefault(_fold(ch), {})
        node[_END] = True

    parts = []
    for behind in sorted(roots, reverse=True):      # 出力を毎回同じにする
        body = _branch(roots[behind], "")
        parts.append((_LOOKBEHIND + body) if behind else body)
    return "|".join(parts)


def _branch(node: dict, came_from: str) -> str:
    """trie の 1 節点を正規表現にする。

    **続きのある枝を先に、ここで終わる枝を後に置く。** 正規表現の選択は書いた順に
    試すので、この順序がそのまま「同じ位置では長い表記が勝つ」になる。
    """
    alts = [
        re.escape(ch) + _branch(node[ch], ch)
        for ch in sorted(k for k in node if k is not _END)
    ]
    if node.get(_END):
        # ここで終わる表記の末尾チェック。来た 1 文字で決まる
        alts.append(_LOOKAHEAD if came_from and _WORDISH.match(came_from) else "")
    if not alts:
        return ""
    return alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"


def _compile(variants: list[str]) -> "re.Pattern[str] | None":
    """表記の集合から照合用の正規表現を作る。

    大文字小文字を区別する表記（3 文字以下の全大文字 ASCII）は ``(?-i:…)`` で
    囲む必要があり、木に混ぜられないので並べたまま残す。**数は少ないので、
    候補数で効く走査コストにはならない。**
    """
    if not variants:
        return None
    folded: list[str] = []
    exact: list[str] = []
    for variant in variants:
        (exact if _case_sensitive(variant) else folded).append(variant)
    parts = []
    if folded:
        parts.append(_trie(folded))
    # 木より後ろに置く。木のほうが長い表記を持ちうるので、先に試させる
    parts.extend(_pattern_for(v) for v in sorted(exact, key=len, reverse=True))
    return re.compile("|".join(parts), re.IGNORECASE)


class _Segment:
    """HTML を「タグ」と「テキスト」に切ったときの 1 片。"""

    __slots__ = ("kind", "text", "linkable", "name")

    def __init__(self, kind: str, text: str, *, linkable: bool = False, name: str | None = None) -> None:
        self.kind = kind
        self.text = text
        self.linkable = linkable
        self.name = name


def _tokenize(html: str) -> list[_Segment]:
    segments: list[_Segment] = []
    skip_depth = 0
    pos = 0
    for m in _TAG_RE.finditer(html):
        chunk = html[pos:m.start()]
        if chunk:
            segments.append(_Segment(_TEXT, chunk, linkable=skip_depth == 0))
        tag = m.group(0)
        name, closing, selfclose = _tag_info(tag)
        segments.append(_Segment(_TAG, tag, name=name))
        if name in SKIP_TAGS and not selfclose:
            skip_depth = max(0, skip_depth - 1) if closing else skip_depth + 1
        pos = m.end()
    tail = html[pos:]
    if tail:
        segments.append(_Segment(_TEXT, tail, linkable=skip_depth == 0))
    return segments


def _runs(segments: Sequence[_Segment]) -> list[list[int]]:
    """連続して「読者に見えるテキスト」になるセグメント番号のまとまりを返す。

    インライン要素はまたぐが、ブロック要素・``<br>``・リンク不可テキストで切る。
    """
    runs: list[list[int]] = []
    current: list[int] = []

    def flush() -> None:
        nonlocal current
        if current:
            runs.append(current)
            current = []

    for i, seg in enumerate(segments):
        if seg.kind == _TEXT:
            if seg.linkable:
                current.append(i)
            else:
                flush()
        elif seg.name is None or seg.name not in INLINE_TAGS:
            # コメント・DOCTYPE・ブロック要素・<br> はすべて区切り
            flush()
    flush()
    return runs


class _Group:
    """ひとつの表記に紐づくエントリ群。"""

    __slots__ = ("surface", "entries", "_refs")

    def __init__(self, surface: str) -> None:
        self.surface = surface
        self.entries: list[Entry] = []
        self._refs: set[str] = set()

    def add(self, entry: Entry) -> None:
        if entry.ref not in self._refs:
            self._refs.add(entry.ref)
            self.entries.append(entry)

    def remaining(self, skip: frozenset[str]) -> list[Entry]:
        return [e for e in self.entries if e.ref not in skip]


class Linker:
    """辞書エントリ集合から自動リンカを組み立てる。"""

    def __init__(self, entries: Sequence[Entry]) -> None:
        self._groups: dict[str, _Group] = {}
        variants: list[str] = []
        for entry in entries:
            for surface in entry.surfaces:
                for variant in _variants(surface):
                    if not variant:
                        continue
                    key = variant.casefold()
                    group = self._groups.get(key)
                    if group is None:
                        group = _Group(surface)
                        self._groups[key] = group
                        self._groups.setdefault(variant.lower(), group)
                        variants.append(variant)
                    group.add(entry)

        # 「当てない表記」(`excludes`) を**エントリの付かない群**として混ぜる。
        #
        # 木は「同じ位置では長い表記が勝つ」ので、`読み込み` を枝として持たせれば
        # その位置では `読み` ではなくこちらが選ばれ、群にエントリが無いので
        # `annotate()` も `_hits()` も**そのまま素通しする**（どちらも既に
        # 「エントリの無い群は飛ばす」を通っている）。新しい照合規則は増えない。
        #
        # **表記の登録を全部終えてから回す。** 先に混ぜると、除外と同じ文字列を
        # 用語や別名に持つエントリが現れたときに**その語がリンクにならない**
        # （用語が除外に負ける）。あとから回せば `self._groups` に既にある鍵は
        # 飛ばすので、実在する表記のほうが必ず勝つ。
        for entry in entries:
            for surface in entry.exclusions:
                for variant in _variants(surface):
                    if not variant:
                        continue
                    key = variant.casefold()
                    if key in self._groups:
                        continue
                    blank = _Group(surface)
                    self._groups[key] = blank
                    self._groups.setdefault(variant.lower(), blank)
                    variants.append(variant)

        # 最長一致優先: 同じ開始位置では長い表記が勝つ。
        # 木にまとめる側はその順序を枝の並びで持つ (`_branch`)
        variants.sort(key=len, reverse=True)
        self._re = _compile(variants)

    def __bool__(self) -> bool:
        return self._re is not None

    def finditer(self, text: str):
        """**素のテキスト**の上で表記を探す。``annotate()`` と同じ規則で当たる。

        ``annotate()`` は HTML を走査するが、本文をそのまま探したい場面
        （「この語が出てくる文書」）もある。規則を書き分けると、**リンクにならない
        語を「出てくる」と言う**ようになるので、同じ正規表現から出す。
        """
        if self._re is None or not text:
            return
        for m in self._re.finditer(text):
            # 「当てない表記」で当たったぶんはここで落とす。**`annotate()` が
            # リンクにしない位置を、こちらが「出てくる」と言ってはいけない**
            # （`?ref=` の出現探し `app._entry_finder` はこの口を直に使う）。
            group = self._group_for(m.group(0))
            if group is not None and group.entries:
                yield m

    def _group_for(self, matched: str) -> "_Group | None":
        """当たった文字列から群を引く。**引き方はここ 1 か所。**"""
        return self._groups.get(matched.casefold()) or self._groups.get(matched.lower())

    def _hits(self, text: str):
        """``(文字位置, エントリ)`` を**出てくる順**に流す。

        「出てくるか」を知りたい側 (`entries_in`) と「どこで出てくるか」を
        知りたい側 (`first_positions`) の**共通の出どころ**。片方に別の照合を
        書くと、リンクにならない語を「出てくる」と言うようになる。
        """
        for m in self.finditer(text):
            group = self._group_for(m.group(0))
            if group is None:
                continue
            for entry in group.entries:
                yield m.start(), entry

    def entries_in(self, text: str) -> list[Entry]:
        """**素のテキスト**に出てくるエントリを初出順で返す。

        `annotate()` から「リンクを差し込む」を抜いたもので、当たり方は同じ。
        別に照合を書くと**リンクにならない語を「出てくる」と言う**ようになるので、
        ここも `finditer()` と同じ正規表現から出す（`?ref=` の出現探しと同じ話）。

        HTML ではなく素のテキストを見るので、`SKIP_TAGS`（`pre` や既存のリンクの
        内側）の除外は掛からない。文書に**出てくるか**を知りたい側の口で、
        本文にリンクを貼る側の口ではない。
        """
        hits: dict[str, Entry] = {}
        for _, entry in self._hits(text):
            hits.setdefault(entry.ref, entry)
        return list(hits.values())

    def first_positions(self, text: str) -> dict[str, int]:
        """``ref`` → **その語が最初に出てくる文字位置**。

        当たり方は `entries_in()` とまったく同じ（同じ `_hits()` から出す）。
        時系列 (`timeline.py`) が「その関係を読めるようになる位置」を出すのに使う。
        """
        out: dict[str, int] = {}
        for start, entry in self._hits(text):
            out.setdefault(entry.ref, start)
        return out

    def occurrences(self, text: str) -> dict[str, dict[str, int]]:
        """``ref`` → ``{"count": 出てくる回数, "first": 最初の文字位置}``。

        索引（語がどこに何回出てくるか）が使う。**1 回の走査で全語ぶん**返すので、
        語ごとに探し直さない —— 3000 語の辞書で語の数だけ本文を読み直すのは
        現実的でない（`_image_index()` が 1 回の走査で URL を作るのと同じ判断）。

        当たり方は `entries_in()` / `first_positions()` とまったく同じ
        （同じ `_hits()` から出す）。**別に照合を書かないこと** —— 書くと
        「本文でリンクにならない語を索引に載せる」ようになる。
        """
        out: dict[str, dict[str, int]] = {}
        for start, entry in self._hits(text):
            found = out.get(entry.ref)
            if found is None:
                out[entry.ref] = {"count": 1, "first": start}
            else:
                found["count"] += 1
        return out

    def annotate(
        self,
        html: str,
        *,
        first_only: bool = False,
        skip_refs: Iterable[str] = (),
        counts: dict[str, int] | None = None,
    ) -> tuple[str, list[Entry]]:
        """HTML にリンクを差し込み (書き換え後 HTML, 出現したエントリ) を返す。

        エントリは初出順。``first_only`` なら各表記の最初の出現だけをリンクする。
        ``skip_refs`` は無視するエントリ (辞書ページで自分自身を貼らない用)。

        ``counts`` に辞書を渡すと ``ref`` → **その HTML に出てくる回数**を書き込む。
        **数える口を別に作らないため**にここで受けている —— `occurrences()` は
        素のテキストに当てるものなので、レンダリング済みの HTML を渡すと
        タグや属性の中まで数える。別に書くと**一覧に出る語と数が食い違う**
        （同じ辞書なのに違うことを言う図、と同じ壊れ方）。

        **`first_only` でも本当の回数を数える。** あれは「最初の 1 回だけリンクする」
        という**見せ方**の指定で、その語が何回出てくるかは変わらない。
        """
        if self._re is None or not html:
            return html, []

        skip = frozenset(skip_refs)
        segments = _tokenize(html)
        hits: dict[str, Entry] = {}
        seen_surfaces: set[str] = set()
        # セグメント番号 -> [(開始, 終了, 開始タグ)]
        inserts: dict[int, list[tuple[int, int, str]]] = {}

        for run in _runs(segments):
            flat = "".join(segments[i].text for i in run)
            if not flat.strip():
                continue
            # (flat 上の開始位置, セグメント番号, 長さ)
            layout: list[tuple[int, int, int]] = []
            cursor = 0
            for i in run:
                length = len(segments[i].text)
                layout.append((cursor, i, length))
                cursor += length

            for m in self._re.finditer(flat):
                group = self._group_for(m.group(0))
                if group is None:
                    continue
                entries = group.remaining(skip)
                if not entries:
                    continue
                # **数えるのは `first_only` の判定より前。** あとに置くと、
                # 最初の 1 回だけリンクする設定のときに全部 1 になる
                if counts is not None:
                    for e in entries:
                        counts[e.ref] = counts.get(e.ref, 0) + 1
                if first_only and group.surface in seen_surfaces:
                    continue

                start, end = m.start(), m.end()
                pieces = [
                    (base, idx, length)
                    for base, idx, length in layout
                    if base < end and base + length > start
                ]
                if not pieces:
                    continue
                seen_surfaces.add(group.surface)
                for e in entries:
                    hits.setdefault(e.ref, e)

                open_tag = self._open_tag(group, entries, split=len(pieces) > 1)
                for base, idx, length in pieces:
                    lo = max(start, base) - base
                    hi = min(end, base + length) - base
                    inserts.setdefault(idx, []).append((lo, hi, open_tag))

        return self._rebuild(segments, inserts), list(hits.values())

    # ------------------------------------------------------------------ #

    def _open_tag(self, group: _Group, entries: list[Entry], *, split: bool) -> str:
        href = entry_url(entries[0]) if len(entries) == 1 else f"/glossary?q={quote(group.surface)}"
        # 分断された断片は左右の余白を消して、見た目を 1 続きに保つ
        classes = "gloss-link gloss-split" if split else "gloss-link"
        attrs = [
            f'class="{classes}"',
            f'href="{href}"',
            f'data-gloss="{escape(group.surface)}"',
            f'data-count="{len(entries)}"',
        ]
        if len(entries) > 1:
            attrs.append(f'title="{escape(group.surface)} — {len(entries)} 件の意味があります"')
        return f"<a {' '.join(attrs)}>"

    @staticmethod
    def _rebuild(segments: Sequence[_Segment], inserts: dict[int, list[tuple[int, int, str]]]) -> str:
        out: list[str] = []
        for i, seg in enumerate(segments):
            spans = inserts.get(i)
            if seg.kind != _TEXT or not spans:
                out.append(seg.text)
                continue
            text = seg.text
            cursor = 0
            for lo, hi, open_tag in sorted(spans):
                if lo < cursor:
                    continue  # 重なり (正規表現は非重複なので通常起きない)
                out.append(text[cursor:lo])
                out.append(open_tag)
                out.append(text[lo:hi])
                out.append("</a>")
                cursor = hi
            out.append(text[cursor:])
        return "".join(out)
