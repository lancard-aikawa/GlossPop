"""カードの中身 (`core.card`)。

見ているのは**並びと数の約束**だけ —— 絵にするのはブラウザ側で、何語入るかも
そちらが測って決める（ここに定数を置かない、という判断そのものをテストにできる
形が無いので、代わりに**全語を順番付きで返す**ことを見る）。
"""
from __future__ import annotations

from glosspop.core import card
from glosspop.core.models import Entry, Relation


def entry(term: str, **kw) -> Entry:
    kw.setdefault("category", "人")
    kw.setdefault("slug", term)
    return Entry(term=term, **kw)


def rel(to: str, **kw) -> Relation:
    return Relation(to=to, **kw)


def test_見出しが作れれば主張が題になる():
    got = card.build([
        entry("本能寺の変", when="1582-06-21"),
        entry("伊賀越え", when="1582-06-21"),
    ], name="戦国時代")
    assert got.title == "同じ日に、2 つの人"
    assert got.kind == "同着"
    assert "どちらも 1582-06-21" in got.note


def test_作れなければ辞書の名前に落ちる():
    """**無理に何か言わない。** 落ちたことは `kind` が空であることで分かる。"""
    got = card.build([entry("あ"), entry("い")], name="銀河鉄道")
    assert (got.title, got.kind, got.note) == ("銀河鉄道", "", "")
    assert got.name == "銀河鉄道"


def test_並びは繋がりの多い順():
    """切られるのは後ろなので、**切られて困るものが先に来る**。"""
    got = card.build([
        entry("端"),
        entry("中心", relations=[rel("端"), rel("脇")]),
        entry("脇"),
    ], name="辞書")
    assert got.terms[0] == "中心"


def test_同数なら読みの順():
    got = card.build([entry("蜜柑", reading="みかん"), entry("林檎", reading="りんご")],
                     name="辞書")
    assert got.terms == ("蜜柑", "林檎")


def test_関係は両端で二重に数えない():
    got = card.build([entry("あ", relations=[rel("い")]), entry("い")], name="辞書")
    assert (got.total, got.links) == (2, 1)


def test_行き先の無い参照は数えない():
    got = card.build([entry("あ", relations=[rel("居ない語")])], name="辞書")
    assert got.links == 0


def test_判明位置つきの関係は既定で数えない():
    """カードは相関図より人目に付く。**ここだけ緩めない。**"""
    entries = [entry("あ", relations=[rel("い"), rel("う", reveal="第6章")]),
               entry("い"), entry("う")]
    assert card.build(entries, name="辞書").links == 1
    assert card.build(entries, name="辞書", spoilers=True).links == 2


def test_語が無くても落ちない():
    got = card.build([], name="からっぽ")
    assert (got.title, got.total, got.links, got.terms) == ("からっぽ", 0, 0, ())
