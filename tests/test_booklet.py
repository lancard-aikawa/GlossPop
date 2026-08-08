"""辞書を**読ませる 1 枚**にする（冊子）。

zip は**データの持ち運び**で、**人に渡して読ませる形**が無かった。ここで見張るのは
3 つ: **五十音の並び**（画面の束ね方と同じ規則）、**書いてあるものだけ出す**、
**索引は渡されたときだけ**（空の索引を載せると「1 語も出てこない」に見える）。
"""

from __future__ import annotations

from glosspop.core import booklet
from glosspop.core.models import Entry


def mk(term, *, reading="", slug=None, category="場所", **kwargs) -> Entry:
    return Entry(term=term, reading=reading, slug=slug or term, category=category, **kwargs)


class TestTheOrder:
    def test_it_goes_in_kana_order(self):
        text = booklet.build([
            mk("田楽狭間", reading="でんがくはざま"),
            mk("活版所", reading="かっぱんじょ"),
            mk("あさひ", reading="あさひ"),
        ])
        heads = [line for line in text.splitlines() if line.startswith("## ")]
        assert heads == ["## 目次", "## あ", "## か", "## た"]

    def test_katakana_is_folded_and_a_missing_reading_still_places_kana(self):
        """**カタカナはひらがなに畳む。** 読みが無くても見出しがかななら置ける。"""
        text = booklet.build([mk("ジョバンニ", slug="giovanni")])
        assert "## さ" in text and booklet.ROW_NONE not in text

    def test_a_kanji_head_without_a_reading_is_kept_apart(self):
        """**黙って「あ」行に混ぜない。** どうすれば並ぶかだけ書く（責めない）。"""
        text = booklet.build([mk("活版所")])
        assert f"## {booklet.ROW_NONE}" in text
        assert "読みが書かれていないので" in text

    def test_the_same_reading_is_still_ordered(self):
        """同じ読みでも並びを決め切る（開くたびに入れ替わらない）。"""
        pair = [mk("甲", reading="こう", slug="b"), mk("交", reading="こう", slug="a")]
        assert booklet.build(pair) == booklet.build(list(reversed(pair)))


class TestWhatIsWritten:
    def test_only_what_is_filled_in_comes_out(self):
        text = booklet.build([mk("活版所", reading="かっぱんじょ")])
        assert "### 活版所（かっぱんじょ）" in text
        assert "別名" not in text and "関係:" not in text and ">" not in text

    def test_everything_written_comes_out(self):
        text = booklet.build([mk(
            "ジョバンニ", reading="じょばんに", category="登場人物",
            aliases=["ジョバン二"], tags=["銀河"],
            summary="活版所で働く少年。", definition="主人公。",
            examples=["ジョバンニは活版所にいた。"],
            relations=[{
                "to": "カムパネルラ", "label": "親友", "back": "親友",
                "rank": "対等", "when": "1560-06-12", "reveal": "第6章",
            }],
        )])
        for piece in ["別名: ジョバン二", "#銀河", "活版所で働く少年。", "主人公。",
                      "> ジョバンニは活版所にいた。", "⇄ カムパネルラ", "（親友 / 親友）",
                      "作中: 1560-06-12", "判明: 第6章"]:
            assert piece in text, piece

    def test_the_time_on_the_term_itself_comes_out(self):
        """事件の日付は**語のほう**にある。関係だけ出していると冊子から静かに落ちる。"""
        text = booklet.build([mk(
            "本能寺の変", reading="ほんのうじのへん", category="事件",
            when="1582-06-21 天正十年六月二日", summary="要約。",
        )])
        assert "作中: 1582-06-21 天正十年六月二日" in text

    def test_the_head_says_how_many(self):
        text = booklet.build([mk("あ", reading="あ")], generated="2026-08-08")
        assert "1 語" in text and "2026-08-08" in text

    def test_an_empty_dictionary_says_so(self):
        assert "まだ用語が登録されていません" in booklet.build([])

    def test_the_toc_lists_every_term(self):
        text = booklet.build([mk("活版所", reading="かっぱんじょ"), mk("あさひ", reading="あさひ")])
        toc = text.split("## 目次")[1].split("## あ")[0]
        assert "あさひ" in toc and "活版所" in toc


class TestTheIndex:
    """巻末索引は**渡されたときだけ**（`core` は本文の置き場所を知らない）。"""

    def test_no_index_unless_given(self):
        assert "## 索引" not in booklet.build([mk("あ", reading="あ")])

    def test_it_lists_where_each_term_appears(self):
        text = booklet.build([mk("あ", reading="あ")], occurrences=[
            {"term": "あ", "files": [{"name": "一巻.txt", "first": "L.3"}], "more_files": 0},
        ])
        assert "## 索引" in text and "- あ — 一巻.txt L.3" in text

    def test_a_term_that_never_appears_is_still_listed(self):
        """**出てこない語こそ見たい**（落とすと索引を見る意味が消える）。"""
        text = booklet.build([mk("あ", reading="あ")], occurrences=[
            {"term": "あ", "files": [], "more_files": 0},
        ])
        assert "（本文に出てきません）" in text

    def test_the_files_that_were_cut_are_counted(self):
        text = booklet.build([mk("あ", reading="あ")], occurrences=[
            {"term": "あ", "files": [{"name": "一巻.txt", "first": ""}], "more_files": 4},
        ])
        assert "ほか 4 文書" in text
