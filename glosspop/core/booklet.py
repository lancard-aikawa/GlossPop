"""辞書を**読ませる 1 枚**にする（冊子）。

zip は**データの持ち運び**（同じ辞書を別の場所で開くためのもの）で、
**人に渡して読ませる形**が無かった。ここが作るのは 1 つの Markdown ——
GitHub でも、印刷でも、そのまま読める。

**並びは五十音**（画面の束ね方と同じ規則）。カテゴリ順にしないのは、冊子は
**引くもの**だから —— 通して読むときの順は、紙の辞書と同じであってほしい。

**リンクは張らない。** 1 枚に全部入っているので、探すのは目次と索引の仕事。
文書内リンクを張ると、**書き出し先（GitHub / エディタ / 印刷）で見出しの
アンカーの作られ方が違う**ので、**どこかで必ず切れる**（切れたリンクは
「その語が無い」に見える）。

**索引は呼ぶ側が渡す。** どの語がどこに出てくるかは本文を読まないと分からず、
`core` は文書の置き場所を知らない（`doctor` が絵の一覧を受け取るのと同じ形）。
**渡されなければ索引の節を出さない** —— 空の索引を載せると「1 語も出てこない」に
見える。
"""

from __future__ import annotations

from .models import Entry

#: 五十音の行と、そこに入る先頭の字。**画面の束ね方と同じ規則**（`glossary.js`）。
#: 濁点・半濁点・小さい字も同じ行に入れる（「が」は か行）
_ROWS: list[tuple[str, str]] = [
    ("あ", "あいうえおぁぃぅぇぉゔ"),
    ("か", "かきくけこがぎぐげごゕゖ"),
    ("さ", "さしすせそざじずぜぞ"),
    ("た", "たちつてとだぢづでどっ"),
    ("な", "なにぬねの"),
    ("は", "はひふへほばびぶべぼぱぴぷぺぽ"),
    ("ま", "まみむめも"),
    ("や", "やゆよゃゅょ"),
    ("ら", "らりるれろ"),
    ("わ", "わをんゎ"),
]
ROW_LATIN = "英字"
ROW_DIGIT = "数字"
#: かなで置けない語（漢字の見出しで読みが無い）。**黙って「あ」行に混ぜない**
ROW_NONE = "読みなし"
ROW_ORDER = [*(row for row, _ in _ROWS), ROW_LATIN, ROW_DIGIT, ROW_NONE]


def _row_of_text(text: str) -> str:
    """先頭の 1 字から行を決める。決まらなければ空。カタカナはひらがなに畳む。"""
    head = (text or "").strip()
    if not head:
        return ""
    first = head[0]
    code = ord(first)
    kana = chr(code - 0x60) if 0x30A1 <= code <= 0x30F6 else first
    for row, chars in _ROWS:
        if kana in chars:
            return row
    if first.isascii() and first.isalpha():
        return ROW_LATIN
    if first.isdigit():
        return ROW_DIGIT
    return ""


def row_of(entry: Entry) -> str:
    """その語をどの行に置くか。**読みが正、無ければ見出しそのもの。**"""
    return _row_of_text(entry.reading) or _row_of_text(entry.term) or ROW_NONE


def _sort_key(entry: Entry) -> tuple[str, str]:
    """読み（無ければ見出し）で並べる。同じなら ref で決め切る（並びを揺らさない）。"""
    return ((entry.reading or entry.term), entry.ref)


def _entry_block(entry: Entry) -> list[str]:
    """1 語ぶん。**書いてあるものだけ出す**（空の見出しを並べない）。"""
    head = entry.term + (f"（{entry.reading}）" if entry.reading else "")
    out = [f"### {head}", ""]

    facts = [entry.path_label]
    if entry.aliases:
        facts.append("別名: " + " / ".join(entry.aliases))
    if entry.tags:
        facts.append(" ".join(f"#{t}" for t in entry.tags))
    out += [" ｜ ".join(f for f in facts if f), ""]

    if entry.summary.strip():
        out += [entry.summary.strip(), ""]
    if entry.definition.strip():
        out += [entry.definition.strip(), ""]
    for example in entry.examples:
        out += [f"> {example}", ""]

    if entry.relations:
        out.append("関係:")
        for rel in entry.relations:
            # **向きは「自分から相手を見て」に固定**（画面と同じ読み方になる）
            arrow = "⇄" if rel.mutual else "→"
            words = " / ".join(w for w in (rel.label, rel.back) if w)
            bits = [f"{arrow} {rel.to}"]
            if words:
                bits.append(f"（{words}）")
            if rel.rank:
                bits.append(f"［{rel.rank}］")
            if rel.when:
                bits.append(f"作中: {rel.when}")
            if rel.reveal:
                bits.append(f"判明: {rel.reveal}")
            out.append("- " + " ".join(bits))
        out.append("")
    return out


def _index_block(occurrences: list[dict]) -> list[str]:
    """巻末索引。``occurrences`` は ``{"term", "files": [{"name", "first"}]}`` の並び。

    **出てこない語も載せる。** 索引でいちばん見たいのは「本文に無い語」なので、
    落とすと見る意味が消える（画面の索引と同じ約束）。
    """
    out = ["## 索引", "", "この辞書の語が、本文のどこに出てくるか。", ""]
    for item in occurrences:
        places = "、".join(
            f"{f['name']}{(' ' + f['first']) if f.get('first') else ''}"
            for f in item.get("files", [])
        )
        if item.get("more_files"):
            places += f"、ほか {item['more_files']} 文書"
        out.append(f"- {item['term']} — {places or '（本文に出てきません）'}")
    out.append("")
    return out


def build(
    entries: list[Entry],
    *,
    title: str = "用語辞書",
    generated: str = "",
    occurrences: list[dict] | None = None,
) -> str:
    """辞書 1 冊ぶんの Markdown を作る。

    ``generated`` は書き出した日時の文字列。**`core` では作らない**
    （時刻を持ち込むと同じ入力から同じ出力にならず、テストが書けなくなる）。
    """
    buckets: dict[str, list[Entry]] = {row: [] for row in ROW_ORDER}
    for entry in entries:
        buckets[row_of(entry)].append(entry)
    rows = [(row, sorted(buckets[row], key=_sort_key)) for row in ROW_ORDER if buckets[row]]

    out = [f"# {title}", ""]
    note = f"{len(entries)} 語"
    if generated:
        note += f" ／ {generated} 時点"
    out += [note, ""]

    if not rows:
        out += ["まだ用語が登録されていません。", ""]
        return "\n".join(out)

    # 目次。**リンクにしない**（書き出し先でアンカーの作られ方が違う）
    out += ["## 目次", ""]
    for row, items in rows:
        out.append(f"- **{row}**（{len(items)}）　" + "、".join(e.term for e in items))
    out.append("")

    for row, items in rows:
        out += [f"## {row}", ""]
        if row == ROW_NONE:
            # **責めない。** どうすれば行に並ぶかだけ書く（画面と同じ）
            out += ["読みが書かれていないので、五十音には並べていません。", ""]
        for entry in items:
            out += _entry_block(entry)

    if occurrences is not None:
        out += _index_block(occurrences)
    return "\n".join(out).rstrip() + "\n"
