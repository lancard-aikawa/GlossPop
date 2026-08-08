"""エントリ間の関係の解決と、相関図に渡すグラフの組み立て。

参照 (``Relation.to``) は **wiki の名前と同じ感覚**で書ける。``カテゴリ/slug`` の
ref でも、用語名そのままでもよい。ID を別に持たない代わりに、ここが揺れを吸収する:

- 正規化 (``normalize_link``) で空白と全角/半角の違いを潰す
- 改名・カテゴリ移動で捨てた ``former_refs`` も受ける (wiki のリダイレクト)
- 用語名で書かれたときは、**書いた側のカテゴリ → 同じ辞書 → 全体** の順に絞る

**絞りきれないときは黙ってどれかに寄せない。** 同じ用語名がカテゴリ違いで
併存できる以上、寄せた瞬間に相関図の辺と本文のリンク先が食い違う。
``Resolution.ambiguous`` に候補を全部入れて返し、UI に出させる。
"""

from __future__ import annotations

from collections.abc import Container
from dataclasses import dataclass, field

from .linker import entry_url
from .models import GLOBAL_SCOPE, Entry, Relation


@dataclass
class Resolution:
    """参照 1 件の解決結果。

    ``entry`` が ``None`` なら未解決。``ambiguous`` が空でなければ「候補は
    あるが 1 つに決まらない」（＝書き手がカテゴリまで書く必要がある）。
    """

    target: str
    entry: Entry | None = None
    ambiguous: list[Entry] = field(default_factory=list)

    @property
    def missing(self) -> bool:
        return self.entry is None

    @property
    def reason(self) -> str:
        if self.entry is not None:
            return ""
        if self.ambiguous:
            names = "、".join(e.path_label for e in self.ambiguous)
            return f"どれを指すか決まりません（{names}）。「カテゴリ/slug」で書いてください"
        return "まだ登録されていません"


def resolve(target: str, entries: list[Entry], *, origin: Entry | None = None) -> Resolution:
    """参照文字列 1 件を解決する。

    ``origin`` は参照を書いた側のエントリ。同カテゴリを優先するために使う。
    """
    key = (target or "").strip().casefold()
    if not key:
        return Resolution(target=target)

    # 1. ref そのもの。いまの ref を旧 ref より優先する
    #    （消えたエントリの旧名を、別のエントリが引き継いでいることがある）
    for e in entries:
        if e.ref.casefold() == key:
            return Resolution(target=target, entry=e)
    for e in entries:
        if any(r.casefold() == key for r in e.former_refs):
            return Resolution(target=target, entry=e)

    # 2. 用語名 / 別名。候補が複数なら書き手の文脈で絞る
    hits = [e for e in entries if any(s.casefold() == key for s in e.surfaces)]
    if not hits:
        return Resolution(target=target)
    if len(hits) == 1:
        return Resolution(target=target, entry=hits[0])

    for narrow in _narrowings(origin):
        picked = [e for e in hits if narrow(e)]
        if len(picked) == 1:
            return Resolution(target=target, entry=picked[0])
    return Resolution(target=target, ambiguous=hits)


def _narrowings(origin: Entry | None):
    """候補を絞る述語を、優先順に返す。"""
    if origin is not None:
        yield lambda e: e.scope == origin.scope and e.category == origin.category
        yield lambda e: e.scope == origin.scope
    yield lambda e: e.scope == GLOBAL_SCOPE


# --------------------------------------------------------------------------- #
# グラフ
# --------------------------------------------------------------------------- #

def relation_when(
    rel: Relation, origin: Entry, target: Entry | None,
) -> tuple[str, int | None, bool, bool]:
    """辺 1 本の作中の時刻。**書かれていなければ両端の語から採る**。

    返すのは ``(表示する文字列, 並べ替えの数, 語から採ったか, だいたいか)``。
    **「だいたいか」も一緒に返す** —— 文字列と印が別々の道を通ると、継いだ辺で
    片方だけ古い値になる（読む口は 1 つ、が崩れる）。
    **ここが唯一の読み口**（相関図と辞書ページが別々に決めると、同じ関係が
    図では並ぶのに一覧では時刻無し、という食い違いになる）。

    **関係に書いた ``when`` が最優先。** 「いつの関係か」は語そのものの時刻と
    ずれうる（徳川家康と織田信長の間柄は、どちらの生没とも別の時点で変わる）ので、
    書いてあればそれが正。

    **無ければ、両端のうち遅いほうを継ぐ。** 読む順の「両端が出そろうところ」
    (`timeline.annotate()` の ``max(a, b)``) と同じ考え方で、「本能寺の変 →
    山崎の戦い」が読めるようになるのは遅いほう（山崎）が起きた時点になる。

    **片方にしか書かれていなくても継ぐ。** ここだけ読む順と違う ——
    あちらは両端とも必ず位置を持つ（同じ文書に出てくる語だから）が、こちらの
    空欄は「**書いていない**」。両端そろって初めて継ぐ形にすると、**事件 →
    人物**の関係が全部時刻を失う（人物に生没を書く辞書のほうが珍しい）ので、
    事件に日付を 1 行書いても時系列には何も出てこない。

    **継ぐのは西暦として読める時刻だけ。** 読めない文字列まで配ると、語 1 つの
    書き間違いが**関係の本数だけ**「書いたのに読めない」に化けて数を狂わせる。
    語のほうの間違いは点検 (`doctor`) がその語 1 件として挙げる。
    """
    if rel.when:
        return rel.when, rel.when_at, False, rel.when_about
    ends = [e for e in (origin, target) if e is not None and e.when_at is not None]
    if not ends:
        return "", None, False, False
    latest = max(ends, key=lambda e: e.when_at)  # type: ignore[arg-type,return-value]
    return latest.when, latest.when_at, True, latest.when_about


def _node(entry: Entry, *, inside: bool) -> dict:
    return {
        "ref": entry.ref,
        "term": entry.term,
        "reading": entry.reading,
        "summary": entry.summary,
        "category": entry.category,
        "subcategory": entry.subcategory,
        "scope": entry.scope,
        "path_label": entry.path_label,
        "url": entry_url(entry),
        # **その語自体の作中の時刻。**辺が時刻を継ぐ元でもある（→ `relation_when`）
        "when": entry.when,
        "when_at": entry.when_at,
        # だいたいの時刻か（`16世紀` `約1560`）。**位置は変わらない** ——
        # 点が正確に見えないように印を出すためだけに返す
        "when_about": entry.when_about,
        # 地図の見せ方が使う。**点・線・領域を 1 つに畳んで渡す**（→ models）。
        # 3 通りの場合分けを描く側に持ち込まない
        "map": entry.map,
        "shape": entry.map_shape,
        # 絞り込みの外にいるが、辺の相手として出す必要があるノード
        "outside": not inside,
        "missing": False,
    }


def _missing_node(target: str) -> dict:
    return {
        "ref": f"?{target}",
        "term": target,
        "reading": "",
        "summary": "",
        "category": "",
        "subcategory": "",
        "scope": "",
        "path_label": "未登録",
        # wiki の赤リンク。押したらその語で登録に入れるよう検索へ飛ばす
        "url": f"/glossary?q={target}",
        "when": "",
        "when_at": None,
        "when_about": False,
        "map": "",
        "shape": None,
        "outside": True,
        "missing": True,
    }


def build_graph(
    entries: list[Entry],
    *,
    scope: str | None = None,
    category: str | None = None,
    spoilers: bool = False,
    only: Container[str] | None = None,
) -> dict:
    """相関図に渡す ``{nodes, edges, broken, hidden, outside}`` を作る。

    ``scope`` / ``category`` は図に載せる範囲。範囲外のエントリでも、辺の相手に
    なっていれば ``outside`` 付きのノードとして足す（辺が宙に浮かないように）。

    ``only`` は**ここに挙げた ref しか出さない**（「この文書に出てくる語だけ」）。
    こちらは相手を足さない —— 足すと**その文書に出てこない語が図に混ざる**ので、
    絞った意味が無くなる。落とした辺は数えて ``outside`` で返す
    （**黙って欠けた図を出さない**。``hidden`` と同じ約束）。

    ``spoilers=False`` のとき、``reveal`` が書かれた関係は **出さずに数だけ返す**。
    相関図は本文より先を一望させてしまうので、既定は伏せる側に倒す。
    どこで判明するかを機械で比較する手段が無い以上、「いつ」ではなく
    「判明位置が明記されているか」で切るのが、嘘をつかない唯一の線引き。
    """
    inside = [
        e for e in entries
        if (scope is None or e.scope == scope)
        and (category is None or e.category == category)
        and (only is None or e.ref in only)
    ]
    nodes: dict[str, dict] = {e.ref: _node(e, inside=True) for e in inside}
    edges: list[dict] = []
    broken: list[dict] = []
    hidden = 0
    outside = 0

    for entry in inside:
        # **番号は「伏せたものも数えた」位置。** 相関図から関係を直すとき、
        # 書き手のエントリの relations の何番目かがそのまま鍵になる。
        # 出した辺だけを数えると、判明位置つきを伏せたぶんだけずれて
        # **別の関係を書き換える**（黙って壊れるので必ず enumerate から取る）
        for index, rel in enumerate(entry.relations):
            if rel.reveal and not spoilers:
                hidden += 1
                continue
            res = resolve(rel.to, entries, origin=entry)
            if only is not None and (res.entry is None or res.entry.ref not in only):
                # 絞ったときは相手を足さない。足すと、その文書に出てこない語が
                # 図に混ざって「この文書の図」でなくなる（数だけ返す）
                outside += 1
                continue
            if res.entry is None:
                broken.append({
                    "from": entry.ref,
                    "from_term": entry.term,
                    "to": rel.to,
                    "reason": res.reason,
                    "candidates": [
                        {"ref": e.ref, "path_label": e.path_label, "url": entry_url(e)}
                        for e in res.ambiguous
                    ],
                })
                nodes.setdefault(f"?{rel.to}", _missing_node(rel.to))
                target_ref = f"?{rel.to}"
            else:
                target_ref = res.entry.ref
                nodes.setdefault(target_ref, _node(res.entry, inside=False))

            when, when_at, when_inherited, when_about = relation_when(rel, entry, res.entry)
            edges.append({
                "from": entry.ref,
                "to": target_ref,
                "label": rel.label,
                "back": rel.back,
                "rank": rel.rank,
                "mutual": rel.mutual,
                "reveal": rel.reveal,
                # **作中の時刻は 2 つ返す**（`timeline.annotate()` の `at` /
                # `at_label` と同じ形の裏返し）: 表示は人が書いた文字列そのまま、
                # 並べ替えは先頭の西暦から出した数。読めなければ `None` で、
                # **黙って寄せない**（時系列が「時刻が分からない」の帯に入れる）。
                # 関係に書かれていなければ両端の語から継ぐ（→ `relation_when`）
                "when": when,
                "when_at": when_at,
                # 継いだ値かどうか。**画面に出す** —— 書いていない時刻が並んで
                # いるのに黙っていると、「この関係に時刻を書いた覚えはない」に見える
                "when_inherited": when_inherited,
                "when_about": when_about,
                "missing": res.entry is None,
                # 直すのに要るもの: 書き手の何番目の関係か と、書かれている行き先。
                # ``to`` は解決後の ref なので、ファイルに書いてある文字列とは違う
                "index": index,
                "rel_to": rel.to,
            })

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "broken": broken,
        # 黙って伏せない: 何本隠したかは必ず返す
        "hidden": hidden,
        # 絞り込みの外を向いていて落とした辺。絞っていないときは必ず 0
        "outside": outside,
    }


def resolved_relations(entry: Entry, entries: list[Entry]) -> list[dict]:
    """1 エントリの関係を、辞書ページに出せる形にして返す。

    解決できたものは相手の URL を、できなかったものは理由を付ける。
    """
    out: list[dict] = []
    for rel in entry.relations:
        res = resolve(rel.to, entries, origin=entry)
        when, when_at, when_inherited, when_about = relation_when(rel, entry, res.entry)
        item = {
            **rel.model_dump(),
            # **相関図と同じ口から採る**（`relation_when`）。ここだけ素の `rel.when`
            # を出すと、図には並ぶのに一覧では時刻が無い、という食い違いになる
            "when": when,
            "when_at": when_at,
            "when_inherited": when_inherited,
            "when_about": when_about,
            "mutual": rel.mutual,
            "missing": res.missing,
            "reason": res.reason,
            "candidates": [
                {"ref": e.ref, "path_label": e.path_label, "url": entry_url(e)}
                for e in res.ambiguous
            ],
        }
        if res.entry is not None:
            item["ref"] = res.entry.ref
            item["url"] = entry_url(res.entry)
            item["term"] = res.entry.term
            item["path_label"] = res.entry.path_label
        else:
            item["ref"] = ""
            item["url"] = f"/glossary?q={rel.to}"
            item["term"] = rel.to
            item["path_label"] = "未登録"
        out.append(item)
    return out


def backlinks(entry: Entry, entries: list[Entry]) -> list[dict]:
    """このエントリを指している側の関係。

    関係は片側にしか書かないので、書かれていない側の辞書ページでは
    これを出さないと関係が見えない（＝両側に書きたくなって二重管理になる）。
    """
    out: list[dict] = []
    for other in entries:
        if other.ref == entry.ref:
            continue
        for rel in other.relations:
            res = resolve(rel.to, entries, origin=other)
            if res.entry is None or res.entry.ref != entry.ref:
                continue
            when, _, when_inherited, when_about = relation_when(rel, other, res.entry)
            out.append({
                "when": when,
                "when_inherited": when_inherited,
                "when_about": when_about,
                "ref": other.ref,
                "term": other.term,
                "url": entry_url(other),
                "path_label": other.path_label,
                # 表示は「相手 → 自分」の向きに直す。back が無ければ一方的
                "label": rel.back,
                "incoming": rel.label,
                "mutual": rel.mutual,
                "rank": _flip_rank(rel.rank),
                "reveal": rel.reveal,
            })
    return out


def _flip_rank(rank: str) -> str:
    return {"上": "下", "下": "上"}.get(rank, rank)
