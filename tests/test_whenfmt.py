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
