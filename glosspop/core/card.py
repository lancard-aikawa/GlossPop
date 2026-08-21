"""**カード**（メタ画像と公開ページの 1 枚）に載せる中身を作る。

ここが作るのは**データだけ**で、絵にするのはブラウザ側（SVG → PNG は
`graph-export.js` が既に持っている道をそのまま使う。サーバに画像ライブラリは
無く、**足すと `glosspop.spec` とビルド確認が付いてくる**）。`core` なので
辞書の置き場所も出力先も知らない —— 辞書の名前は呼ぶ側が渡す。

守っていること 3 つ:

- **何語まで載るかをここで決めない。** 入る数は実際の字幅で決まるので、
  測れる側（描く側）が切って、**落とした数を出す**。ここに「23 語」のような
  定数を置くと、用語名の長い辞書で**黙って溢れ**、短い辞書では無駄に余る
  （実測: 平均 3.5 字なら 30px で 23 個入るが、字数が変われば動く）
- **並びは繋がりの多い順。** 切られるのは後ろからなので、**切られて困るものが
  先に来る**ようにする（同数は読み順。入力順に依らせない）
- **伏せる約束はカードでも同じ。** 判明位置つきの関係は既定で数えない
  （`build_graph(spoilers=False)` と同じ既定）。カードは相関図よりも人目に
  付くところへ出るので、**ここだけ緩めると伏せている意味が無い**
"""

from __future__ import annotations

from dataclasses import dataclass

from . import headline, relations
from .models import Entry


@dataclass(frozen=True)
class Card:
    """カード 1 枚ぶんの中身。

    ``kind`` が空なら**見出しを作れなかった**ということで、``title`` は辞書の
    名前に落ちている（**無理に何か言わない**、の結果）。
    """

    name: str
    title: str
    note: str = ""
    kind: str = ""
    terms: tuple[str, ...] = ()
    total: int = 0
    links: int = 0


def _order(entries: list[Entry], counts: dict[str, int]) -> list[Entry]:
    """繋がりの多い順 → 読み（無ければ用語名）→ ref。**入力順に依らせない。**"""
    return sorted(
        entries,
        key=lambda e: (-counts.get(e.ref, 0), e.reading or e.term, e.ref),
    )


def build(entries: list[Entry], *, name: str, spoilers: bool = False) -> Card:
    """カードの中身を作る。**語が 1 つも無くても落ちない。**"""
    ready = list(entries)
    counts = relations.link_counts(ready, spoilers=spoilers)
    found = headline.pick(ready, spoilers=spoilers)
    return Card(
        name=name,
        title=found.text if found else name,
        note=found.note if found else "",
        kind=found.kind if found else "",
        terms=tuple(e.term for e in _order(ready, counts)),
        total=len(ready),
        # 1 本を両端で数えているので half にする（`link_counts` の約束）
        links=sum(counts.values()) // 2,
    )
