"""見出しを機械で作る (`core.headline`)。

**外れた主張は、無いより悪い。** ここで見ているのは「言えることしか言わない」
ほうで、良い見出しが出ることではない —— 決め手が無いときに黙ること、書かれて
いない細かさを盛らないこと、カテゴリ名の意味を読まないこと、の 3 つ。
"""
from __future__ import annotations

from glosspop.core import headline
from glosspop.core.models import Entry, Relation


def entry(term: str, **kw) -> Entry:
    kw.setdefault("category", "事件")
    kw.setdefault("slug", term)
    return Entry(term=term, **kw)


def rel(to: str, **kw) -> Relation:
    return Relation(to=to, **kw)


# --- 同着 -------------------------------------------------------------

def test_同じ時刻に並んだ語を見出しにする():
    found = headline.same_time([
        entry("本能寺の変", when="1582-06-21 天正十年六月二日"),
        entry("伊賀越え", when="1582-06-21 天正十年六月二日"),
        entry("姉川の戦い", when="1570-07-30"),
    ])
    assert found is not None
    assert found.text == "同じ日に、2 つの事件"
    assert "どちらも 1582-06-21 天正十年六月二日" in found.note
    assert set(found.terms) == {"本能寺の変", "伊賀越え"}


def test_年しか書かれていなければ日とは言わない():
    """**書かれている桁だけを読む。** 盛ると、書いていない値を主張することになる。"""
    found = headline.same_time([entry("あ", when="1582"), entry("い", when="1582")])
    assert found is not None and found.text == "同じ年に、2 つの事件"


def test_月まで書かれていれば月と言う():
    found = headline.same_time([entry("あ", when="1582-06"), entry("い", when="1582-06")])
    assert found is not None and found.text == "同じ月に、2 つの事件"


def test_だいたいの書き方はころに落とす():
    """`16世紀` を「同じ年に」と言うと、1501 年ちょうどのように聞こえる。"""
    found = headline.same_time([entry("あ", when="16世紀"), entry("い", when="16世紀")])
    assert found is not None and found.text == "同じころに、2 つの事件"


def test_書かれ方が違えば束ねない():
    """並べ替えの値が同じでも、書かれ方が違えば別の帯（年表の帯と同じ規則）。"""
    assert headline.same_time([
        entry("あ", when="1582-06-21 天正十年六月二日"),
        entry("い", when="1582-06-21"),
    ]) is None


def test_1語では同着にならない():
    assert headline.same_time([entry("あ", when="1582"), entry("い", when="1570")]) is None


def test_カテゴリが揃っていなければ語と言う():
    """**カテゴリ名の意味は読まない。** 揃っているときだけ借りる。"""
    found = headline.same_time([
        entry("あ", when="1582", category="事件"),
        entry("い", when="1582", category="人"),
    ])
    assert found is not None and found.text == "同じ年に、2 つの語"


def test_同名のカテゴリでも辞書が違えば揃っていない():
    """鍵は `<scope><>カテゴリ` —— 名前だけで束ねない。"""
    found = headline.same_time([
        entry("あ", when="1582", category="事件"),
        entry("い", when="1582", category="事件", scope="local"),
    ])
    assert found is not None and found.text == "同じ年に、2 つの語"


def test_読めない時刻は数えない():
    assert headline.same_time([
        entry("あ", when="天保三年"), entry("い", when="天保三年"),
    ]) is None


# --- 最多 -------------------------------------------------------------

def test_いちばん多く繋がっている語を見出しにする():
    found = headline.most_linked([
        entry("信長", relations=[rel("光秀"), rel("秀吉")]),
        entry("光秀"), entry("秀吉"),
    ])
    assert found is not None
    assert found.text == "信長に、2 本の線が集まる"


def test_1位が決まらなければ出さない():
    """**黙ってどれかに寄せない**（`relations.resolve()` と同じ約束）。"""
    assert headline.most_linked([
        entry("あ", relations=[rel("い"), rel("う")]),
        entry("え", relations=[rel("お"), rel("か")]),
        entry("い"), entry("う"), entry("お"), entry("か"),
    ]) is None                            # あ も え も 2 本


def test_行き先の無い参照は数えない():
    """壊れた参照は点検の担当。ここで二重に出さない。"""
    assert headline.most_linked([entry("あ", relations=[rel("居ない語")])]) is None


def test_関係が1本では言うほどのことがない():
    assert headline.most_linked([entry("あ", relations=[rel("い")]), entry("い")]) is None


# --- 幅 ---------------------------------------------------------------

def test_幅は年に丸める():
    found = headline.span([
        entry("あ", when="1560-06-12 永禄三年五月十九日"),
        entry("い", when="1582-07-02 天正十年六月十三日"),
    ])
    assert found is not None and found.text == "1560 から 1582 まで"


def test_だいたいの書き方は丸めない():
    """`16世紀` を `1501` と書くと、書いていない値を見出しに出すことになる。"""
    found = headline.span([entry("あ", when="16世紀"), entry("い", when="1582")])
    assert found is not None and found.text == "16世紀 から 1582 まで"


def test_幅が無ければ出さない():
    assert headline.span([entry("あ", when="1582"), entry("い", when="1582")]) is None


# --- 選び方 -----------------------------------------------------------

def test_同着は最多より強い():
    entries = [
        entry("あ", when="1582", relations=[rel("い"), rel("う")]),
        entry("い", when="1582"), entry("う"),
    ]
    assert headline.pick(entries).kind == "同着"
    assert [h.kind for h in headline.candidates(entries)][:2] == ["同着", "最多"]


def test_作れなければNoneを返す():
    """呼ぶ側は辞書の名前に落ちる。**無理に何か言わない。**"""
    assert headline.pick([entry("あ"), entry("い")]) is None
    assert headline.candidates([]) == []
