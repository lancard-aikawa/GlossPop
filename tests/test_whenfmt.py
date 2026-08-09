"""関係につく**作中の時刻** (`when`) の読み方。

**元号では並べられない**（「天保三年」と「享保五年」の前後は変換表を持たないと
決まらない）ので、並べ替えに使うのは**先頭の西暦だけ**。うしろには元号でも
作中の暦でも書けて、**表示は書かれたまま**。

ここで見張るのは 2 つ: **読めるものは必ず前後が決まること**と、
**読めないものを推測で並べないこと**（間違った順序をそれらしく出さない）。
"""

from __future__ import annotations

import pytest

from glosspop.core import whenfmt


class TestWhatCanBeSorted:
    @pytest.mark.parametrize("text", [
        "1560",
        "1560-05",
        "1560-05-19",
        "1560-05-19 10:30",
        "1560-05-19T10:30",
        "1560-05-19 10:30:15",
        "  1560-05-19  ",
    ])
    def test_a_western_date_at_the_head_is_read(self, text):
        assert whenfmt.sort_key(text) is not None

    def test_the_rest_of_the_line_is_free(self):
        """**うしろは何を書いてもよい**（元号・作中の暦・ひとこと）。"""
        assert whenfmt.sort_key("1560-05-19 永禄三年五月十九日") == \
            whenfmt.sort_key("1560-05-19")

    def test_a_time_after_the_date_still_counts(self):
        """日記のような使い方では**時刻まで**要る（空白でも T でも同じ）。"""
        assert whenfmt.sort_key("2026-08-08 09:30 朝の散歩") == \
            whenfmt.sort_key("2026-08-08T09:30")

    def test_they_come_out_in_order(self):
        times = [
            "1600-10-21 関ヶ原",
            "1560",
            "1560-05-19 10:30",
            "1560-05-19 09:00",
            "1560-05",
        ]
        assert sorted(times, key=whenfmt.sort_key) == [
            "1560",
            "1560-05",
            "1560-05-19 09:00",
            "1560-05-19 10:30",
            "1600-10-21 関ヶ原",
        ]

    def test_a_coarse_value_comes_first_within_the_year(self):
        """``1560`` は「その年のどこか」なので、年の頭に置く。"""
        assert whenfmt.sort_key("1560") < whenfmt.sort_key("1560-01-01")


class TestWhatIsNotGuessed:
    """**読めなければ `None`。** 推測で並べると、間違った順序をそれらしく出す。"""

    @pytest.mark.parametrize("text", [
        "",
        "   ",
        "永禄三年五月十九日",          # 元号は変換表が要る（持たない）
        "その年の夏",
        "15600519",                    # 区切りが無い（頭 4 桁を年と読まない）
        "1560-13-01",                  # 13 月
        "1560-05-32",                  # 32 日
        "1560-05-19 25:00",            # 25 時
        "0000-01-01",
        "-500",                        # 紀元前は扱わない
    ])
    def test_it_returns_none(self, text):
        assert whenfmt.sort_key(text) is None

    def test_none_is_not_zero(self):
        """0 を返すと「いちばん古い」として並んでしまう（黙って寄せない）。"""
        assert whenfmt.sort_key("永禄三年") is not whenfmt.sort_key("1")


class TestWhatIsShown:
    def test_the_written_string_is_kept(self):
        """**並べ替えのために読んだ値で人の言葉を置き換えない**（`reveal` と同じ）。"""
        assert whenfmt.written("  1560-05-19 永禄三年五月十九日 ") == \
            "1560-05-19 永禄三年五月十九日"

    def test_nothing_written_is_empty(self):
        assert whenfmt.written("") == ""
        assert whenfmt.written(None) == ""


class TestTheTimeOnTheTermItself:
    """**時刻は語にも書ける**（事件・出来事）。読む口は関係とまったく同じ。"""

    def test_the_term_reads_its_own_time_through_whenfmt(self):
        from glosspop.core.models import Entry

        entry = Entry(term="本能寺の変", when="1582-06-21 天正十年六月二日")
        assert entry.when_at == whenfmt.sort_key(entry.when)
        assert entry.when_at is not None

    def test_an_unreadable_time_is_none_not_zero(self):
        """0 に寄せると、いちばん古い出来事として並ぶ。"""
        from glosspop.core.models import Entry

        assert Entry(term="X", when="天保三年").when_at is None

    def test_a_range_is_not_a_time(self):
        """**期間は持たない。**`1467-1477` は西暦として読めない（月が 14 と 77 になる）。

        範囲を書きたいときは先頭を 1 点にして、うしろに文字で書く。
        """
        from glosspop.core.models import Entry

        assert Entry(term="応仁の乱", when="1467-1477").when_at is None
        assert Entry(term="応仁の乱", when="1467 応仁の乱（〜1477）").when_at is not None


class TestRoughAndApproximateTimes:
    """**分かっているところまでしか書けない**、が普通の使い方。

    正確な日付を捏造させると辞書が嘘をつくので、粗い書き方をそのまま受ける。
    見張るのは 3 つ: **粗いものは範囲の頭に置く**（`1560` が `1560-01-01` と
    同じ扱いなのと同じ規則）、**「だいたい」の印は位置を変えない**、
    **変換表が要るものは相変わらず読まない**（元号）。
    """

    @pytest.mark.parametrize("text,year", [
        ("16世紀", 1501),          # 1501〜1600 を 16 世紀と数える
        ("1世紀", 1),
        ("1560年代", 1560),
        ("1560年", 1560),
    ])
    def test_a_rough_time_lands_on_the_head_of_its_range(self, text, year):
        assert whenfmt.sort_key(text) == whenfmt.sort_key(str(year))

    @pytest.mark.parametrize("text", ["1560年5月19日", "1560 年 5 月 19 日"])
    def test_a_japanese_date_reads_like_the_iso_one(self, text):
        assert whenfmt.sort_key(text) == whenfmt.sort_key("1560-05-19")

    @pytest.mark.parametrize("text", ["約1560", "およそ1560", "~1560", "〜1560",
                                      "ca.1560", "1560ごろ", "1560頃", "1560?", "1560 ごろ"])
    def test_an_approximate_mark_does_not_move_the_position(self, text):
        """**印であって別の時刻ではない。** ずらす幅を決める根拠が無い。"""
        assert whenfmt.sort_key(text) == whenfmt.sort_key("1560")
        assert whenfmt.is_about(text) is True

    def test_a_century_and_a_decade_are_approximate_without_a_mark(self):
        assert whenfmt.is_about("16世紀") is True
        assert whenfmt.is_about("1560年代") is True
        assert whenfmt.is_about("1560") is False
        assert whenfmt.is_about("1560-05-19") is False

    def test_an_unreadable_time_is_not_approximate_either(self):
        """読めないものは「だいたい」ですらない（点検が挙げる側）。"""
        assert whenfmt.is_about("天正十年") is False

    @pytest.mark.parametrize("text", ["天正十年", "1560〜1570", "15600519", "0世紀"])
    def test_what_is_still_not_read(self, text):
        """**幅は持たない**（範囲どうしの前後が決まらない）。元号も相変わらず読まない。"""
        assert whenfmt.sort_key(text) is None


class TestCoarseningToTheYear:
    """**月日だけ落として年は残す。** 「西暦に直せないが年は分かる」の落としどころ。

    和暦・太陰暦の月日は換算表が無いと西暦に直せないので、直せないものを
    黙って持たせるより、**確かなところまで**に丸めるほうを採る。
    """

    def test_it_keeps_the_year_and_the_words_after_it(self):
        assert whenfmt.year_only("1582-06-02 天正十年六月二日") == "1582 天正十年六月二日"

    def test_the_japanese_form_is_coarsened_too(self):
        assert whenfmt.year_only("1582年6月2日 天正十年六月二日") == "1582 天正十年六月二日"

    def test_a_time_of_day_goes_with_the_day(self):
        assert whenfmt.year_only("1560-05-19 10:30 払暁") == "1560 払暁"

    def test_something_already_coarse_is_left_alone(self):
        for text in ["1560", "16世紀", "1560年代", "約1560"]:
            assert whenfmt.year_only(text) == text

    def test_an_unreadable_string_is_returned_as_written(self):
        """読めないものを触っても直らない（点検が挙げる側の話）。"""
        assert whenfmt.year_only("天正十年六月二日") == "天正十年六月二日"

    def test_it_never_makes_an_unreadable_string_readable(self):
        """**読めない部分を削って「読める時刻」に化けさせない。**

        13 月・32 日は `sort_key()` が読めないと言う（`_pack()` が範囲で弾く）。
        頭だけ読み直して月日を落とすと `1560` として並んでしまい、**書いていない
        値で並べた**ことになる。丸めてよいのは、丸める前から読めていたものだけ。
        """
        for text in ["1560年13月", "1560-05-32", "0年5月", "15600519"]:
            assert whenfmt.sort_key(text) is None
            assert whenfmt.year_only(text) == text
            assert whenfmt.sort_key(whenfmt.year_only(text)) is None

    def test_the_result_still_sorts(self):
        assert whenfmt.sort_key(whenfmt.year_only("1582-06-02 天正十年六月二日")) is not None
