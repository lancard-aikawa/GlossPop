"""関係と語につく**作中の時刻**（`when`）の書き方。

**判明位置 (`reveal`) とは別の軸。** あちらは「読者がいつ読めるようになるか」で、
こちらは「作中でいつ起きたか」。同じ図に混ぜると、読み手には**どちらの順で
並んでいるのか分からない**（並べる軸は必ず画面に書く）。

**元号では並べられない。** 「天保三年」と「享保五年」のどちらが先かは、変換表を
持たないと決まらない —— 表は改元のたびに増えるし、作品ごとの独自の暦は端から
載っていない。**推測で並べると、間違った順序をそれらしく出す**ことになる。

**線は「変換表が要るか」。** 世紀・年代・算用数字の年月日は**算術**なので読む
（`16世紀` → 1501）。表が要るもの（元号・作中の暦）だけを拒む。ここを緩めて
元号を読み始めると、上の理由がそのまま壊れる。

だから**並べ替えに使うのは西暦だけ**にして、元号でも作中の暦でも**そのうしろに
そのまま書ける**ようにした:

    when: 1560-05-19 永禄三年五月十九日      # 先頭で並び、表示は全文
    when: 1560-05-19 10:30 払暁              # 時刻まで
    when: 1560                               # 年だけでもよい
    when: 1560年5月19日                      # 算用数字ならこの書き方も読む
    when: 永禄三年五月十九日                  # **並ばない**（数えて画面に出す）

**粗い書き方はその範囲の頭に置く。** ``1560`` が ``1560-01-01`` なのと同じ規則で、
``1560年代`` は 1560、``16世紀`` は 1501（世紀は 1501〜1600 の数え方）。
**幅は持たない** —— 範囲どうしの前後は決まらないので、持った瞬間に並べ替えが
定義できなくなる（幅を言いたいならうしろに文字で書ける）。

**「だいたい」の印は書けるが、位置は変えない。** ``約1560`` も ``1560ごろ`` も
``1560`` と同じところに並び、``is_about()`` が真を返すだけ。**別の時刻として
ずらさない** —— ずらす幅を決める根拠が無いし、ずらせば「書いていない値」で
並べたことになる。

**表示は書かれたまま。** 並べ替えのために読むのは先頭だけで、読んだ値で人の言葉を
置き換えたりはしない（`timeline.py` が `reveal` を上書きしないのと同じ約束）。

**読めなければ `None`。黙って寄せない。** 読めない時刻は時系列で「時刻が分からない」
の帯に入り、点検が「西暦で読めない」として挙げる（書いたのに並ばない、を画面に
出さないと気付けない）。
"""

from __future__ import annotations

import re

#: 「だいたい」の印。**頭にも後ろにも書ける**（`約1560` / `1560ごろ`）。
#: 位置は変えないので、ここを増やしても並びは動かない
_ABOUT_HEAD = re.compile(r"^\s*(?:約|およそ|ca\.|c\.|[~〜≈])\s*")
_ABOUT_TAIL = r"(?:頃|ごろ|ころ|くらい|前後|\?|？)"

#: 先頭の粗い書き方。**どちらもその範囲の頭に置く**（`1560` が `1560-01-01` と
#: 同じ扱いになるのと同じ規則）。世紀は 1501〜1600 を「16世紀」と数える
_CENTURY = re.compile(r"^\s*(?P<n>\d{1,2})\s*世紀")
_DECADE = re.compile(r"^\s*(?P<year>\d{1,4})\s*年代")

#: 算用数字の年月日。**元号は読まない**（漢数字なのでここには当たらない）
_JP_DATE = re.compile(
    r"""^\s*
    (?P<year>\d{1,4}) \s* 年
    (?: \s* (?P<month>\d{1,2}) \s* 月
      (?: \s* (?P<day>\d{1,2}) \s* 日 )?
    )?
    """,
    re.VERBOSE,
)

#: 先頭の西暦。**ここで切れること**（`_ENDS`）まで見る —— 見ないと
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
    """,
    re.VERBOSE,
)

#: 頭を読み終えたところ。**「だいたい」の印を挟んでから**切れていること
_ENDS = re.compile(rf"^\s*{_ABOUT_TAIL}?\s*(?:\s|$)")


def _pack(year: int, month: int = 0, day: int = 0,
          hour: int = 0, minute: int = 0, second: int = 0) -> int | None:
    """``YYYYMMDDhhmmss`` を 1 つの整数にする。**範囲の外は読めなかった扱い。**

    13 月や 32 日を「たぶんこの辺」と置くと、間違った順序をそれらしく出すことになる。
    """
    if not 1 <= year <= 9999:
        return None
    if not (0 <= month <= 12 and 0 <= day <= 31):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return (((((year * 100 + month) * 100 + day) * 100 + hour) * 100 + minute) * 100 + second)


def _read(text: str) -> tuple[int | None, bool]:
    """``(並べ替えの数, だいたいか)``。**読む口はここ 1 つ。**

    粗いものから順に試す。`1560年代` を先に見ないと `_JP_DATE` が `1560年` として
    読んでしまい、「年代」の 2 字が余って切れなくなる（＝読めない扱いになる）。
    """
    s = text or ""
    about = False
    head = _ABOUT_HEAD.match(s)
    if head:
        about = True
        s = s[head.end():]

    for pattern, kind in ((_CENTURY, "century"), (_DECADE, "decade"),
                          (_JP_DATE, "jp"), (_HEAD, "iso")):
        found = pattern.match(s)
        if found is None:
            continue
        rest = s[found.end():]
        tail = _ENDS.match(rest)
        if tail is None:
            continue
        if rest[:tail.end()].strip():
            about = True                     # 「ごろ」「?」などが続いていた
        parts = found.groupdict()
        if kind == "century":
            # 16世紀 = 1501〜1600。**範囲の頭**に置く（`1560` と同じ規則）
            n = int(parts["n"])
            at = _pack((n - 1) * 100 + 1) if n >= 1 else None
        else:
            at = _pack(
                int(parts["year"]),
                int(parts.get("month") or 0),
                int(parts.get("day") or 0),
                int(parts.get("hour") or 0),
                int(parts.get("minute") or 0),
                int(parts.get("second") or 0),
            )
        if at is None:
            return None, about
        return at, about
    return None, about


def sort_key(text: str) -> int | None:
    """並べ替えのための数。読めなければ ``None``（例外にはしない）。

    返すのは ``YYYYMMDDhhmmss`` を 1 つの整数にしたもの。**書かれていない
    ところは 0** なので、``1560`` は ``1560-01-01`` より前に来る
    （「その年のどこか」は年の頭に置く。同じ年の中で細かいほうが後）。
    """
    return _read(text)[0]


def is_about(text: str) -> bool:
    """**だいたいの時刻として書かれているか。** 位置は変わらない。

    「約」「ごろ」「?」を付けたもの、世紀、年代がこれにあたる。**印であって
    別の時刻ではない**ので、`sort_key()` は付けても付けなくても同じ数を返す
    （ずらす幅を決める根拠が無いし、ずらせば書いていない値で並べたことになる）。
    """
    at, about = _read(text)
    if at is None:
        return False
    if about:
        return True
    # 世紀と年代は、印が無くてもそれ自体が「だいたい」
    stripped = _ABOUT_HEAD.sub("", text or "")
    return bool(_CENTURY.match(stripped) or _DECADE.match(stripped))


def year_only(text: str) -> str:
    """頭の西暦を**年だけ**に丸める。**うしろは書かれたまま。**

    「月日は西暦に直せないが、年なら分かる」ときの落としどころ
    （``1582-06-02 天正十年六月二日`` → ``1582 天正十年六月二日``）。粗い書き方は
    範囲の頭に置く規則がそのまま効くので、年だけでも並ぶ。

    **読めない文字列はそのまま返す**（読めないものを触っても直らない）。世紀・年代は
    もともと年より粗いので変わらない。
    """
    s = text or ""
    # **`sort_key()` が読めないものには触らない。** ここで頭だけを読み直すと、
    # 読めない部分を削ったせいで**読める時刻に化ける**（`1560年13月` は 13 月が
    # あるので `sort_key()` は `None` なのに、月を落とせば `1560` として並ぶ）。
    # 書いていない値で並べたことになるので、判断は読む口 1 つに任せる
    if sort_key(s) is None:
        return s.strip()
    head = _ABOUT_HEAD.match(s)
    prefix, rest = (s[:head.end()], s[head.end():]) if head else ("", s)
    if _CENTURY.match(rest) or _DECADE.match(rest):
        return s.strip()
    for pattern in (_JP_DATE, _HEAD):
        found = pattern.match(rest)
        if found is None or _ENDS.match(rest[found.end():]) is None:
            continue
        year = int(found.groupdict()["year"])
        tail = rest[found.end():].strip()
        return f"{prefix.strip()}{year}{' ' + tail if tail else ''}".strip()
    return s.strip()


def written(text: str) -> str:
    """画面に出す文字列。**書かれたまま**（並べ替えのために読んだ値で置き換えない）。"""
    return (text or "").strip()
