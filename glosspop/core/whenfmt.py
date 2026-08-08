"""関係につく**作中の時刻**（`when`）の書き方。

**判明位置 (`reveal`) とは別の軸。** あちらは「読者がいつ読めるようになるか」で、
こちらは「作中でいつ起きたか」。同じ図に混ぜると、読み手には**どちらの順で
並んでいるのか分からない**（並べる軸は必ず画面に書く）。

**元号では並べられない。** 「天保三年」と「享保五年」のどちらが先かは、変換表を
持たないと決まらない —— 表は改元のたびに増えるし、作品ごとの独自の暦は端から
載っていない。**推測で並べると、間違った順序をそれらしく出す**ことになる。

だから**並べ替えに使うのは西暦だけ**にして、元号でも作中の暦でも**そのうしろに
そのまま書ける**ようにした:

    when: 1560-05-19 永禄三年五月十九日      # 先頭で並び、表示は全文
    when: 1560-05-19 10:30 払暁              # 時刻まで
    when: 1560                               # 年だけでもよい
    when: 永禄三年五月十九日                  # **並ばない**（数えて画面に出す）

**表示は書かれたまま。** 並べ替えのために読むのは先頭だけで、読んだ値で人の言葉を
置き換えたりはしない（`timeline.py` が `reveal` を上書きしないのと同じ約束）。

**読めなければ `None`。黙って寄せない。** 読めない時刻は時系列で「時刻が分からない」
の帯に入り、点検が「西暦で読めない」として挙げる（書いたのに並ばない、を画面に
出さないと気付けない）。
"""

from __future__ import annotations

import re

#: 先頭の西暦。**ここで切れること**（`(?=\s|$)`）まで見る —— 見ないと
#: `15600519` の頭 4 桁を年として読んでしまう（推測はしない、が崩れる）
_HEAD = re.compile(
    r"""^\s*
    (?P<year>\d{1,4})
    (?: - (?P<month>\d{1,2})
      (?: - (?P<day>\d{1,2}) )?
    )?
    (?: [T\ ] (?P<hour>\d{1,2}) : (?P<minute>\d{2})
      (?: : (?P<second>\d{2}) )?
    )?
    (?=\s|$)
    """,
    re.VERBOSE,
)


def sort_key(text: str) -> int | None:
    """並べ替えのための数。読めなければ ``None``（例外にはしない）。

    返すのは ``YYYYMMDDhhmmss`` を 1 つの整数にしたもの。**書かれていない
    ところは 0** なので、``1560`` は ``1560-01-01`` より前に来る
    （「その年のどこか」は年の頭に置く。同じ年の中で細かいほうが後）。

    **範囲の外は読めなかった扱い。** 13 月や 32 日を「たぶんこの辺」と置くと、
    間違った順序をそれらしく出すことになる。
    """
    found = _HEAD.match(text or "")
    if found is None:
        return None
    parts = found.groupdict()
    year = int(parts["year"])
    month = int(parts["month"] or 0)
    day = int(parts["day"] or 0)
    hour = int(parts["hour"] or 0)
    minute = int(parts["minute"] or 0)
    second = int(parts["second"] or 0)
    if not 1 <= year <= 9999:
        return None
    if not (0 <= month <= 12 and 0 <= day <= 31):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return (((((year * 100 + month) * 100 + day) * 100 + hour) * 100 + minute) * 100
            + second)


def written(text: str) -> str:
    """画面に出す文字列。**書かれたまま**（並べ替えのために読んだ値で置き換えない）。"""
    return (text or "").strip()
