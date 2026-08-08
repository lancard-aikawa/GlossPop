"""辞書の点検。

参照を名前で書ける（ID を持たない）ぶん、書き間違いや相手の削除で静かに切れる。
**正常なものを問題として出さない**のがここの難しいところで、テストの半分は
「出さないこと」を確かめている。
"""

from __future__ import annotations

import pytest

from glosspop import store
from glosspop.core import doctor
from glosspop.core.models import EntryDraft


def kinds(report: dict) -> list[str]:
    return [i["kind"] for i in report["issues"]]


@pytest.fixture
def healthy(add_entry):
    """点検に引っかからないエントリ 2 つ。"""
    a = add_entry("ジョバンニ", category="登場人物", summary="主人公。", definition="本文。")
    b = add_entry("カムパネルラ", category="登場人物", summary="友人。", definition="本文。")
    return a, b


def link(entry, to: str, **kwargs):
    return store.save(
        EntryDraft(
            term=entry.term,
            category=entry.category,
            summary=entry.summary,
            definition=entry.definition,
            relations=[{"to": to, **kwargs}],
        ),
        ref=entry.ref,
    )


class TestQuiet:
    """健全な辞書では黙っていること。"""

    def test_healthy_entries_produce_nothing(self, healthy):
        report = doctor.check(store.load_all())
        assert report["issues"] == []
        assert report["errors"] == 0 and report["warnings"] == 0
        assert report["checked"] == 2

    def test_a_resolvable_relation_is_not_an_issue(self, healthy):
        a, b = healthy
        link(a, b.ref, label="親友")
        assert kinds(doctor.check(store.load_all())) == []

    def test_same_term_in_two_categories_is_not_an_issue(self, add_entry):
        """カテゴリ違いの同名は狙いどおりの機能。問題として出さない。"""
        add_entry("ソース", category="料理", summary="調味料。", definition="本文。")
        add_entry("ソース", category="プログラミング", summary="原文。", definition="本文。")
        assert kinds(doctor.check(store.load_all())) == []

    def test_an_entry_without_relations_is_not_an_issue(self, healthy):
        assert kinds(doctor.check(store.load_all())) == []


class TestBrokenRelations:
    def test_reports_a_target_that_does_not_exist(self, healthy):
        a, _ = healthy
        link(a, "いない人", label="兄")
        report = doctor.check(store.load_all())
        assert kinds(report) == ["broken_relation"]
        issue = report["issues"][0]
        assert issue["severity"] == "error"
        assert issue["term"] == "ジョバンニ" and issue["target"] == "いない人"
        # 赤リンクから登録に入れる導線
        assert "いない人" in issue["create_url"]

    def test_reports_an_ambiguous_target(self, add_entry):
        add_entry("ソース", category="料理", summary="調味料。", definition="本文。")
        add_entry("ソース", category="プログラミング", summary="原文。", definition="本文。")
        origin = add_entry("メモ", category="日記", summary="覚書。", definition="本文。")
        link(origin, "ソース", label="参照")
        report = doctor.check(store.load_all())
        assert kinds(report) == ["ambiguous_relation"]
        assert len(report["issues"][0]["candidates"]) == 2

    def test_reports_a_self_relation(self, healthy):
        a, _ = healthy
        link(a, a.ref, label="自分")
        report = doctor.check(store.load_all())
        assert kinds(report) == ["self_relation"]
        assert report["issues"][0]["severity"] == "warn"

    def test_a_moved_target_is_not_broken(self, healthy):
        """転送 (former_refs) が効いていれば壊れていない。"""
        a, b = healthy
        link(a, b.ref, label="親友")
        store.move(b.ref, "主要人物")
        assert kinds(doctor.check(store.load_all())) == []


class TestContentChecks:
    def test_reports_a_missing_summary(self, add_entry):
        add_entry("冪等", category="プログラミング", definition="本文。")
        report = doctor.check(store.load_all())
        assert kinds(report) == ["no_summary"]

    def test_reports_an_empty_definition(self, add_entry):
        add_entry("冪等", category="プログラミング", summary="要約。")
        assert kinds(doctor.check(store.load_all())) == ["empty_definition"]

    def test_counts_group_by_kind(self, add_entry):
        add_entry("冪等", category="プログラミング")
        add_entry("結果整合性", category="プログラミング")
        report = doctor.check(store.load_all())
        assert report["counts"]["no_summary"] == 2
        assert report["counts"]["empty_definition"] == 2


class TestWhenChecks:
    """作中の時刻 (`when`)。**書いていないのが普通**なので、そこは黙ること。

    挙げるのは「書いたのに西暦として読めない」だけ —— そのぶんは時系列で
    「時刻が分からない」の帯に落ちるが、**画面には何も出ない**ので、
    ここで言わないと気付く手段が無い。
    """

    def entry(self, add_entry, **kwargs):
        add_entry("信長", category="人", summary="要約。", definition="本文。", **kwargs)
        add_entry("光秀", category="人", summary="要約。", definition="本文。")

    def test_nothing_is_said_when_there_is_no_time(self, add_entry):
        """**未入力は正常。** 全部の関係に時刻が付く辞書のほうが珍しい。"""
        self.entry(add_entry, relations=[{"to": "光秀", "label": "家臣"}])
        assert kinds(doctor.check(store.load_all())) == []

    def test_nothing_is_said_when_the_time_can_be_read(self, add_entry):
        self.entry(add_entry, relations=[
            {"to": "光秀", "label": "家臣", "when": "1582-06-21 天正十年六月二日"},
        ])
        assert kinds(doctor.check(store.load_all())) == []

    def test_an_era_name_alone_is_reported(self, add_entry):
        """元号だけでは前後が決まらない（変換表を持たない）。**黙って並べない。**"""
        self.entry(add_entry, relations=[
            {"to": "光秀", "label": "家臣", "when": "天正十年六月二日"},
        ])
        report = doctor.check(store.load_all())
        assert kinds(report) == ["unreadable_when"]
        assert "天正十年六月二日" in report["issues"][0]["detail"]
        assert report["issues"][0]["severity"] == "warn"

    def test_the_time_on_the_term_itself_is_read_the_same_way(self, add_entry):
        """語に書いた時刻も同じ規則で見る。**書いていないのは黙る。**"""
        add_entry("桶狭間の戦い", category="事件", summary="要約。", definition="本文。",
                  when="1560-06-12 永禄三年五月十九日")
        add_entry("応仁の乱", category="事件", summary="要約。", definition="本文。")
        assert kinds(doctor.check(store.load_all())) == []

    def test_an_unreadable_time_on_the_term_is_reported(self, add_entry):
        """**こちらのほうが被害が広い** —— 読めない時刻は関係へも継がれないので、
        その語に繋がる辺がまとめて時系列から落ちる。
        """
        add_entry("本能寺の変", category="事件", summary="要約。", definition="本文。",
                  when="天正十年六月二日")
        report = doctor.check(store.load_all())
        assert kinds(report) == ["unreadable_when"]
        assert "この語の時刻" in report["issues"][0]["detail"]


class TestMapChecks:
    """地図は「書いたのに出ない」が起きやすい。**画面には何も出ない**ので、
    ここで言わないと気付く手段が無い。逆に、置き待ち（絵の名前だけ書いた語）と
    縦長の絵の座標は**正常**なので黙ること。
    """

    def entry(self, add_entry, term="田楽狭間", **kwargs):
        return add_entry(term, category="地", summary="要約。", definition="本文。", **kwargs)

    def test_coordinates_without_a_picture_name_are_reported(self, add_entry):
        """どの絵に置くかが無いと、その座標は**絶対に画面へ出ない**。"""
        self.entry(add_entry, pin=[0.4, 0.3])
        report = doctor.check(store.load_all())
        assert kinds(report) == ["shape_without_map"]
        assert report["issues"][0]["severity"] == "warn"

    def test_a_name_with_no_such_picture_is_reported(self, add_entry):
        """絵を消しても用語は書き換えない ＝ 静かに地図から消える。"""
        self.entry(add_entry, map="尾張", pin=[0.4, 0.3])
        report = doctor.check(store.load_all(), maps={})
        assert kinds(report) == ["map_without_image"]
        assert report["issues"][0]["target"] == "尾張"

    def test_nothing_is_said_when_the_picture_is_there(self, add_entry):
        self.entry(add_entry, map="尾張", pin=[0.4, 0.3])
        assert kinds(doctor.check(store.load_all(), maps={"global/尾張": None})) == []

    def test_without_the_list_the_picture_is_not_checked(self, add_entry):
        """**「1 枚も無い」と「一覧をもらっていない」を混同しない。**

        混同すると、地図を使っている辞書で全部の語に警告が出る（＝誰も見なくなる）。
        """
        self.entry(add_entry, map="尾張", pin=[0.4, 0.3])
        assert kinds(doctor.check(store.load_all())) == []

    def test_a_picture_name_without_coordinates_is_normal(self, add_entry):
        """「この絵に置きたい」の置き待ち。図が数えて出すので点検は黙る。"""
        self.entry(add_entry, map="尾張")
        assert kinds(doctor.check(store.load_all(), maps={"global/尾張": None})) == []

    def test_a_point_outside_the_picture_is_reported(self, add_entry):
        self.entry(add_entry, map="尾張", pin=[1.4, 0.3])
        report = doctor.check(store.load_all(), maps={"global/尾張": None})
        assert kinds(report) == ["map_point_outside"]
        assert "1 点目" in report["issues"][0]["detail"]

    def test_a_tall_picture_may_have_y_over_one(self, add_entry):
        """**座標は絵の幅を 1 とした比。** 縦長の絵では y が 1 を超えるのが正常。

        上限は**その絵の縦横比**でしか決まらない。比が分からない（``None``）ときは
        上限を見ない —— 決めようのない定数を置いて正常なものを問題として出さない。
        """
        self.entry(add_entry, map="尾張", line=[[0.1, 0.2], [0.3, 2.5]])
        assert kinds(doctor.check(store.load_all(), maps={"global/尾張": None})) == []

    def test_y_within_the_ratio_of_a_tall_picture_is_normal(self, add_entry):
        """比をもらえば上限が決まる。**縦長なら 1 を超えていても正常。**"""
        self.entry(add_entry, map="尾張", line=[[0.1, 0.2], [0.3, 2.5]])
        assert kinds(doctor.check(store.load_all(), maps={"global/尾張": 3.0})) == []

    def test_y_below_the_picture_is_reported_when_the_ratio_is_known(self, add_entry):
        """絵の下へはみ出した点は**描かれても画面に出ない**（黙って消える側）。"""
        self.entry(add_entry, map="尾張", line=[[0.1, 0.2], [0.3, 2.5]])
        report = doctor.check(store.load_all(), maps={"global/尾張": 0.7})
        assert kinds(report) == ["map_point_outside"]
        assert "2 点目" in report["issues"][0]["detail"]

    def test_the_ratio_of_another_picture_is_not_used(self, add_entry):
        """上限は**その語が置かれている絵**のもの。別の絵の比で切らない。"""
        self.entry(add_entry, map="尾張", line=[[0.1, 0.2], [0.3, 2.5]])
        maps = {"global/尾張": 3.0, "global/三河": 0.5}
        assert kinds(doctor.check(store.load_all(), maps=maps)) == []

    def test_a_negative_coordinate_is_outside(self, add_entry):
        self.entry(add_entry, map="尾張", area=[[0.1, -0.2], [0.3, 0.4], [0.5, 0.6]])
        report = doctor.check(store.load_all(), maps={"global/尾張": None})
        assert kinds(report) == ["map_point_outside"]


def test_errors_come_before_warnings(healthy):
    a, _ = healthy
    link(a, "いない人", label="兄")
    store.save(EntryDraft(term="無記入", category="登場人物"))
    report = doctor.check(store.load_all())
    assert report["issues"][0]["severity"] == "error"
    assert {i["severity"] for i in report["issues"][1:]} == {"warn"}
