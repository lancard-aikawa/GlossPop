"""関係が「その文書のどこで読めるようになるか」の計算。

守っているのは 4 つ:

* 位置は**両端が出そろうところ**（遅いほうの語の初出）
* 当たり方は自動リンクと同じ（素の部分一致に戻すと、リンクにならない語まで
  「出てくる」ことになる）
* 位置の言い方は章・ページ・行の順で、`Document.locate()` と同じ規則
* 出せなかった関係は**落とさず数える**（`undated`）
"""

from __future__ import annotations

from glosspop.core import timeline
from glosspop.core.documents import Document
from glosspop.core.linker import Linker
from glosspop.core.models import Entry


def entry(term: str, *, slug: str = "", aliases: list[str] | None = None) -> Entry:
    return Entry(term=term, category="登場人物", slug=slug or term, aliases=aliases or [])


def graph_of(*edges: tuple[str, str], nodes: list[str] | None = None) -> dict:
    names = nodes or sorted({n for edge in edges for n in edge})
    return {
        "nodes": [{"ref": f"登場人物/{n}", "term": n} for n in names],
        "edges": [
            {"from": f"登場人物/{a}", "to": f"登場人物/{b}", "label": ""}
            for a, b in edges
        ],
    }


CHAPTERS = Document(
    kind="html",
    text="",
    segments=[
        ("第一章", "吾輩は猫である。主人はいつも書斎にいる。"),
        ("第二章", "迷亭が訪ねてきた。"),
        ("第六章", "金田は主人を訪ねた。"),
    ],
)


def linker_for(*terms: str) -> Linker:
    return Linker([entry(t) for t in terms])


class TestWhereARelationBecomesReadable:
    def test_uses_the_later_of_the_two_first_appearances(self):
        """**両方が出そろうまで、その関係は読めない。**

        吾輩は第一章、金田は第六章。関係が読めるようになるのは第六章。
        """
        graph = graph_of(("吾輩", "金田"))
        timeline.annotate(graph, CHAPTERS, linker_for("吾輩", "金田"))
        assert graph["edges"][0]["at_label"] == "第六章"

    def test_a_pair_inside_one_chapter_stays_there(self):
        graph = graph_of(("吾輩", "主人"))
        timeline.annotate(graph, CHAPTERS, linker_for("吾輩", "主人"))
        assert graph["edges"][0]["at_label"] == "第一章"

    def test_nodes_carry_their_own_first_appearance(self):
        graph = graph_of(("吾輩", "金田"))
        timeline.annotate(graph, CHAPTERS, linker_for("吾輩", "金田"))
        assert {n["term"]: n["at_label"] for n in graph["nodes"]} == {
            "吾輩": "第一章", "金田": "第六章",
        }

    def test_orders_by_the_computed_number_not_by_the_label(self):
        """**並べ替えは計算値で。** 人が書いた文字列では「第6章」と「六章」が別物になる。"""
        graph = graph_of(("吾輩", "金田"), ("吾輩", "主人"), ("吾輩", "迷亭"))
        timeline.annotate(graph, CHAPTERS, linker_for("吾輩", "主人", "迷亭", "金田"))
        order = sorted(graph["edges"], key=lambda e: e["at"])
        assert [e["at_label"] for e in order] == ["第一章", "第二章", "第六章"]

    def test_falls_back_to_line_numbers_without_chapters(self):
        """章もページも無い文書では行番号。位置の言い方は `locate()` と同じ規則。"""
        doc = Document(kind="text", text="", segments=[("", "一行目\n二行目\n太郎と花子\n")])
        graph = graph_of(("太郎", "花子"))
        timeline.annotate(graph, doc, linker_for("太郎", "花子"))
        assert graph["edges"][0]["at_label"] == "L.3"

    def test_matches_the_way_the_linker_does(self):
        """別名で出てきても「出てきた」ことにする（本文でリンクになる語と揃える）。"""
        doc = Document(kind="html", text="", segments=[("序", "猫が来た。"), ("破", "苦沙弥先生。")])
        linker = Linker([entry("吾輩", aliases=["猫"]), entry("主人", aliases=["苦沙弥先生"])])
        graph = graph_of(("吾輩", "主人"))
        timeline.annotate(graph, doc, linker)
        assert graph["edges"][0]["at_label"] == "破"


class TestWhatCannotBePlaced:
    def test_counts_relations_it_cannot_place_instead_of_dropping_them(self):
        """**黙って欠けた図を出さない**（`hidden` / `outside` と同じ約束）。"""
        graph = graph_of(("吾輩", "居ない人"))
        timeline.annotate(graph, CHAPTERS, linker_for("吾輩", "居ない人"))
        assert graph["undated"] == 1
        assert graph["edges"][0]["at"] is None and graph["edges"][0]["at_label"] == ""
        assert len(graph["edges"]) == 1                 # 落としてはいない

    def test_says_zero_when_everything_could_be_placed(self):
        graph = graph_of(("吾輩", "主人"))
        timeline.annotate(graph, CHAPTERS, linker_for("吾輩", "主人"))
        assert graph["undated"] == 0

    def test_a_term_that_never_appears_has_no_position(self):
        graph = graph_of(nodes=["居ない人"])
        timeline.annotate(graph, CHAPTERS, linker_for("居ない人"))
        assert graph["nodes"][0]["at"] is None and graph["nodes"][0]["at_label"] == ""


class TestNothingIsStored:
    def test_the_reveal_string_is_left_alone(self):
        """**人が書いた文字列は上書きしない。** 並べ替えだけこちらの数でやる。"""
        graph = graph_of(("吾輩", "金田"))
        graph["edges"][0]["reveal"] = "六章のおわり"
        timeline.annotate(graph, CHAPTERS, linker_for("吾輩", "金田"))
        assert graph["edges"][0]["reveal"] == "六章のおわり"
        assert graph["edges"][0]["at_label"] == "第六章"

    def test_the_answer_follows_the_text(self, tmp_path):
        """本文を直せば次に読んだときの位置が変わる（保存していない証拠）。"""
        from glosspop.core import documents

        path = tmp_path / "章.txt"
        path.write_text("太郎だけの場面。\n\n花子が現れた。\n", encoding="utf-8")
        linker = linker_for("太郎", "花子")

        graph = graph_of(("太郎", "花子"))
        timeline.annotate(graph, documents.read_cached(path), linker)
        assert graph["edges"][0]["at_label"] == "L.3"

        path.write_text("太郎と花子が同時に出た。\n", encoding="utf-8")
        again = graph_of(("太郎", "花子"))
        timeline.annotate(again, documents.read_cached(path), linker)
        assert again["edges"][0]["at_label"] == "L.1"
