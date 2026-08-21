"""辞書から**見出し**を機械で作る。

メタ画像の掴みを担っているのは**見出しが主張であること**だった —— 実測で、絵を
一切変えずに見出しだけを名前 (`戦国時代`) から主張 (`同じ日に、2 つの事件`) に
替えたら、目次に見えていた 1 枚が読ませるものになった。ここが作るのはその主張で、
**辞書に書かれていることからしか作らない**。

守っていること 3 つ:

- **言い切れないものは出さない。** 決め手が無ければ ``None`` を返し、呼ぶ側は
  辞書の名前に落ちる。**外れた主張は、無いより悪い**（読みや作中の時刻で
  「分からないものは空で返させる」と決めたのと同じ判断）
- **カテゴリ名は引用するだけで、意味を読まない。**「事件」「人」といった名前は
  辞書ごとに違うので、当てにすると静かに効かなくなる（時系列の主従判定が
  *同じかどうか*しか見ないのと同じ規則）。**同じカテゴリに揃っているときだけ**
  その名前を借り、揃っていなければ「語」と言う
- **時刻の細かさを盛らない。** 年しか書かれていないものを「同じ日」と言わない。
  細かさは ``whenfmt.sort_key()`` の返り値から**書かれている桁だけ**を読む
  （時刻を読む口は ``whenfmt`` 1 か所、という約束をここでも通す）。
  「だいたい」の印が付いているものは「ころ」に落とす —— ``16世紀`` を
  1501 年ちょうどのように言わないため

表示は**書かれたまま**（``whenfmt.written()``）。並べ替えのために読んだ数で
置き換えると、`1560年代` が `1560` になって書いていない値を見出しに出すことになる。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import relations, whenfmt
from .models import Entry

#: 「同じ時」に何語並べば同着と言えるか。1 語では並んでいない
LEAST_TIE = 2

#: 「いちばん多く繋がっている」と言うのに要る本数。1 本では言うほどのことがない
LEAST_LINKS = 2

#: カテゴリが揃っていないときの呼び方。**カテゴリ名の意味は読まない**
ANY_KIND = "語"


@dataclass(frozen=True)
class Headline:
    """見出し 1 件。``text`` が主張、``note`` がその根拠。"""

    kind: str
    text: str
    note: str = ""
    terms: tuple[str, ...] = ()


def _unit(key: int, *, about: bool) -> str:
    """``sort_key()`` の値から、書かれている細かさを読む。**0 は「書かれていない」。**

    月に 0 は無いので、0 を「未記入」と読んで差し支えない（``whenfmt._pack()`` が
    13 月や 32 日を読めなかった扱いにしているのと同じ前提）。
    """
    if about:
        return "ころ"
    if key // 1_000_000 % 100:
        return "日"
    if key // 100_000_000 % 100:
        return "月"
    return "年"


def _kind_of(entries: list[Entry]) -> str:
    """揃っているカテゴリ名。揃っていなければ「語」。

    鍵は ``<scope><>カテゴリ`` —— **名前だけで束ねない**（同名が全体とフォルダの
    両方にありうる、という一覧・時系列と同じ話）。
    """
    keys = {f"{e.scope}<>{e.category}" for e in entries}
    return entries[0].category if len(keys) == 1 else ANY_KIND


def same_time(entries: list[Entry]) -> Headline | None:
    """**同じ時刻に並んだ語**を見出しにする。

    束ねるのは「並べ替えの値も、書かれ方も同じ」ものだけ（年表の帯と同じ規則）。
    人は同じ日を 2 通りに書けるので、寄せると**書いていない文字列が見出しに出る**。
    """
    bands: dict[tuple[int, str], list[Entry]] = {}
    for entry in entries:
        at = whenfmt.sort_key(entry.when)
        if at is None:
            continue
        bands.setdefault((at, whenfmt.written(entry.when)), []).append(entry)

    ties = [(key, found) for key, found in bands.items() if len(found) >= LEAST_TIE]
    if not ties:
        return None
    # 多いものを、同数なら古いほうを。**入力順に依らせない**
    (at, written), found = max(ties, key=lambda pair: (len(pair[1]), -pair[0][0]))
    about = any(whenfmt.is_about(e.when) for e in found)
    names = [e.term for e in found]
    unit = _unit(at, about=about)
    if len(found) == LEAST_TIE:
        note = f"{names[0]}と{names[1]} —— どちらも {written}"
    else:
        note = f"{written} に {len(found)} 件（{names[0]}・{names[1]} ほか）"
    return Headline(
        kind="同着",
        text=f"同じ{unit}に、{len(found)} つの{_kind_of(found)}",
        note=note,
        terms=tuple(names),
    )


def most_linked(entries: list[Entry], *, spoilers: bool = False) -> Headline | None:
    """**いちばん多く繋がっている語**を見出しにする。

    数えるのは `relations.link_counts()` **1 か所**（既定では判明位置つきを
    数えない）。**同数で 1 位が決まらないときは出さない** —— 黙ってどれかに
    寄せると、図と見出しが違うことを言う。
    """
    if not entries:
        return None
    links = relations.link_counts(entries, spoilers=spoilers)
    ranked = sorted(links.values(), reverse=True)
    if not ranked or ranked[0] < LEAST_LINKS:
        return None
    if len(ranked) > 1 and ranked[0] == ranked[1]:
        return None                       # 1 位が決まらない
    top = max(entries, key=lambda e: links[e.ref])
    return Headline(
        kind="最多",
        text=f"{top.term}に、{links[top.ref]} 本の線が集まる",
        note=f"{len(entries)} 語のうち、いちばん多く繋がっている",
        terms=(top.term,),
    )


def _edge(at: int, text: str) -> str:
    """幅の端を短く言う。**丸めて嘘になるものは書かれたまま。**

    見出しは 1 行に収まらないと読まれないので、``1560-06-12 永禄三年五月十九日``
    は ``1560`` まで落とす（``whenfmt.year_only()`` と同じ「頭の西暦だけ」）。
    ただし**「だいたい」の書き方は落とさない** —— ``16世紀`` を ``1501`` と
    書くと、書いていない値を見出しに出すことになる（時系列で世紀を白抜きの
    目盛りにしているのと同じ理由）。
    """
    return whenfmt.written(text) if whenfmt.is_about(text) else str(at // 10 ** 10)


def span(entries: list[Entry]) -> Headline | None:
    """**作中の時刻の幅**を見出しにする。"""
    dated = [(whenfmt.sort_key(e.when), e) for e in entries if whenfmt.sort_key(e.when)]
    if len(dated) < LEAST_TIE:
        return None
    dated.sort(key=lambda pair: pair[0])
    (low, first), (high, last) = dated[0], dated[-1]
    head, tail = _edge(low, first.when), _edge(high, last.when)
    if head == tail:
        return None                       # 幅が無い
    return Headline(
        kind="幅",
        text=f"{head} から {tail} まで",
        note=f"作中の時刻が書かれている {len(dated)} 語の幅",
        terms=(first.term, last.term),
    )


#: 強い順。**上から順に試して、最初に出たものを採る**
RULES = (same_time, most_linked, span)


def candidates(entries: list[Entry], *, spoilers: bool = False) -> list[Headline]:
    """作れた見出しを**強い順**に返す。

    ``spoilers`` は関係を見る規則にだけ効く（語の時刻は伏せる対象ではない ——
    `reveal` は関係に書くもので、`when` は並べるだけで何も伏せない）。
    """
    ready = list(entries)
    found = [
        same_time(ready),
        most_linked(ready, spoilers=spoilers),
        span(ready),
    ]
    return [head for head in found if head is not None]


def pick(entries: list[Entry], *, spoilers: bool = False) -> Headline | None:
    """いちばん強い見出し。**1 つも作れなければ ``None``。**"""
    found = candidates(entries, spoilers=spoilers)
    return found[0] if found else None
