"""「当てない表記」(`excludes`) —— 複合語や活用の中に当たるのを止める。

**規則では解けないので語ごとに持つ**、という判断の根拠は `models.EntryBase` の
注記にある（`samples/坊っちゃん` の *うらなり先生* を壊した実測）。ここでは
その判断が壊れていないこと、つまり **他の語の照合には一切効かないこと**を見る。
"""
from __future__ import annotations

import pytest

from glosspop.core.linker import Linker
from glosspop.core.models import Entry


def entry(term: str, **kw) -> Entry:
    kw.setdefault("category", "テスト")
    kw.setdefault("slug", term)
    return Entry(term=term, **kw)


def linked(entries, html: str) -> str:
    return Linker(entries).annotate(html)[0]


def surfaces_of(entries, text: str) -> list[str]:
    return [m.group(0) for m in Linker(entries).finditer(text)]


def test_複合語の中には当てない():
    e = entry("読み", excludes=["読み込み", "読み直す"])
    out = linked([e], "<p>読みを埋める / 読み込みを待つ / 読み直す</p>")
    assert out.count('class="gloss-link"') == 1
    assert ">読み<" in out


def test_除外した位置は出現としても数えない():
    """`finditer` と `annotate` は同じ規則から出る（→ `_hits` の注記）。"""
    e = entry("読み", excludes=["読み込み"])
    assert surfaces_of([e], "読み込みだけの本文") == []
    assert surfaces_of([e], "読みだけの本文") == ["読み"]


def test_活用も止められる():
    e = entry("控え", excludes=["控える", "控えて", "控えた"])
    out = linked([e], "<p>控えを取る。取るのは控えるとき</p>")
    assert out.count('class="gloss-link"') == 1


def test_最初の1回だけリンクの枠を除外が奪わない():
    """密度を下げる設定と併用したとき、誤爆に 1 回ぶんを取られない。

    これが**実際に起きた壊れ方**（`読み方` が唯一のリンクを取り、正しい
    箇所が一度もリンクにならなかった）。
    """
    e = entry("読み", excludes=["読み方"])
    out = Linker([e]).annotate("<p>読み方の話。あとで読みを埋める</p>", first_only=True)[0]
    assert out.count('class="gloss-link"') == 1
    assert "読み方" in out and 'gloss-link">読み方' not in out


def test_実在する表記のほうが除外に勝つ():
    """除外は**エントリの登録を全部終えてから**混ぜる（→ `Linker.__init__`）。

    順番を戻すと、他のエントリの用語名が黙ってリンクにならなくなる。
    """
    a = entry("読み", excludes=["読み込み"])
    b = entry("読み込み", slug="読み込み")
    out = linked([a, b], "<p>読み込みを待つ</p>")
    assert out.count('class="gloss-link"') == 1
    assert ">読み込み<" in out


@pytest.mark.parametrize("bad", ["まったく別の語", "読", ""])
def test_自分の表記を含まない除外は捨てる(bad):
    """どこにも当たらない枝を木に増やさない（`Entry.exclusions`）。"""
    e = entry("読み", excludes=[bad])
    assert e.exclusions == []


def test_別名にも効く():
    e = entry("読み仮名", aliases=["読み"], excludes=["読み込み"])
    assert surfaces_of([e], "読み込みと読み仮名") == ["読み仮名"]


def test_除外を書いていない辞書は何も変わらない():
    """**既存の辞書を壊さない**のがこの機能の前提（→ うらなり先生の実測）。"""
    e = entry("うらなり")
    assert surfaces_of([e], "うらなり先生とうらなり") == ["うらなり", "うらなり"]
