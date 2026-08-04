"""関係が**その文書のどこで読めるようになるか**を計算する。

相関図の 4 つめの見せ方（時系列）が使う。「第一章では吾輩と主人だけ、第六章で
金田家が繋がる」を読ませるための位置で、``reveal``（判明位置）を軸にした図の
背骨にあたる。

守っていることが 4 つある:

**順序は計算する。保存しない。** 保存しないので**ずれようがなく**、版管理も
補正も要らない。外のエディタで本文を書き換えれば次に開いたときの図が変わるだけ
（`read_cached()` が読み直すので速度も問題にならない）。

**位置は「両端が出そろうところ」。** 2 語が近くに並ぶ窓を探す作りにはしない ——
窓の幅という決めようのない定数が要るうえ、**どの組にも必ず定義できる**という
性質が消える。読み手にとってその関係が読めるようになるのは、遅いほうの語が
初めて出てきた時点なので、これで言っていることと合う。
（「近くに並ぶ場面」を探すのは `ai.cooccurrence_context()` の仕事。あちらは
AI に渡す本文を選ぶためのもので、目的が違う。）

**文字位置は表に出さない。** 返すのは並べ替えのための数と、表示用の文字列
（章名 / ページ / 行番号）だけ。位置の言い方は `Document.locate_at()` 1 か所。

**位置の出せない関係は落とさず数える** (``undated``)。黙って欠けた図を出さない、
という ``hidden`` / ``outside`` と同じ約束。
"""

from __future__ import annotations

from .documents import Document
from .linker import Linker


def annotate(graph: dict, doc: Document, linker: Linker) -> dict:
    """``build_graph()`` の結果に、この文書での位置を書き足して返す。

    ノードには初出、辺には**両端が出そろう位置**が入る:

    - ``at`` — 並べ替えのための数（``plain`` の中の文字位置）。出せなければ ``None``
    - ``at_label`` — 画面に出す位置の文字列（章名 / ``p.42`` / ``L.42``）

    **``reveal`` は上書きしない。** 人が書いた文字列は人の言葉のまま出し、
    並べ替えだけこちらの数でやる（「第6章」と「六章」を機械で比べようとすると、
    書き方の違いが別物になる）。
    """
    first = linker.first_positions(doc.plain)
    labels: dict[int, str] = {}

    def label_of(index: int) -> str:
        if index not in labels:
            labels[index] = doc.locate_at(index)
        return labels[index]

    for node in graph.get("nodes", []):
        at = first.get(node["ref"])
        node["at"] = at
        node["at_label"] = label_of(at) if at is not None else ""

    undated = 0
    for edge in graph.get("edges", []):
        a = first.get(edge["from"])
        b = first.get(edge["to"])
        if a is None or b is None:
            # 片方でも出てこなければ、その関係は本文からは読めない。
            # 絞り込み (`only=`) を通っていれば普通は起きないが、黙って捨てない
            edge["at"] = None
            edge["at_label"] = ""
            undated += 1
            continue
        at = max(a, b)
        edge["at"] = at
        edge["at_label"] = label_of(at)

    graph["undated"] = undated
    return graph
