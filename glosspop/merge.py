"""割れてしまった同じものを、あとから 1 つにまとめる。

抽出のときは `ai.split_aliases()` が別の呼び方を拾って**新しいエントリを立てない**
ようにしてあるが、**すでに 2 つに割れているもの**は後からでないと直せない。
「主人」と「苦沙弥先生」が別エントリのままだと、本文のリンク先も相関図のノードも
二重になる。

## 自動では**見つけない**

「同じ人物かもしれない」を機械で判定しない。**カテゴリ違いの同名はこの辞書の
狙いどおりの機能**（「ソース」がプログラミングにも料理にもある）なので、判定を
入れると点検が正常なものを大量に挙げ、警告が誰にも読まれなくなる。まとめるのは
人が 2 つ選んだときだけ。

## 消える側は「別名」として残す

**残す側の別名に、消える側の用語名と別名を全部入れる。** これをしないと、本文中の
「苦沙弥先生」がリンクにならず、他エントリが名前で書いた関係も行き先を失う
（関係に ID は無く、名前で書けるのがこの辞書の仕様）。

参照側のファイルは**書き換えない**。消える側の ref は残す側の ``former_refs`` に
積むので、ref で書かれた関係は転送で解決し続ける（wiki のリダイレクトと同じ。
全エントリを書き換えて回る実装にすると、手で `mv` された経路が必ず漏れる）。

## 畳めないところは人に決めさせる

本文・要約・読みが両側にあって違うとき、**どちらを採るかは機械には決まらない**。
`plan()` が衝突を並べて返し、`apply()` は決まった値だけを受け取る。黙って
片方に寄せると、選ばなかったほうが**確認の機会も無いまま消える**。
"""

from __future__ import annotations

from .core import relations as rel_mod
from . import store
from .core.linker import entry_url
from .core.models import Entry, Relation, now_iso

#: 両側にあって食い違うと人に選ばせる項目。**リストは和集合で畳めるので入れない**
#: （別名・タグ・使用例は「両方入っていて困る」ことがない）
CONFLICT_FIELDS = ("reading", "summary", "definition", "source", "first_file", "first_locator")

#: 統合しても機械で畳める項目（和集合を採る）
UNION_FIELDS = ("aliases", "examples", "tags")


class MergeError(Exception):
    pass


def _require(ref: str, entries: list[Entry], what: str) -> Entry:
    for e in entries:
        if e.ref == ref:
            return e
    raise MergeError(f"{what}が見つかりません: {ref}")


def _rel_key(rel: Relation, origin: Entry, entries: list[Entry]) -> str:
    """関係の行き先を**解決後の ref** で表した鍵。

    ここが用語名のままだと、同じ相手への関係を「片方は名前、片方は ref」で
    書いてあるときに別物と数え、**同じ組に 2 本目の辺が生える**
    （`ai.existing_ref_pairs()` が用語名で照合して踏んだのと同じ話）。
    """
    res = rel_mod.resolve(rel.to, entries, origin=origin)
    return res.entry.ref if res.entry is not None else f"?{rel.to.casefold()}"


def _rel_view(rel: Relation | None, origin: Entry | None, entries: list[Entry]) -> dict | None:
    if rel is None or origin is None:
        return None
    res = rel_mod.resolve(rel.to, entries, origin=origin)
    return {
        **rel.model_dump(),
        "term": res.entry.term if res.entry is not None else rel.to,
        "path_label": res.entry.path_label if res.entry is not None else "未登録",
        "missing": res.entry is None,
    }


def plan(keep_ref: str, drop_ref: str) -> dict:
    """統合の下見。**何がどうなるかを全部返す**（黙って消える箇所を作らない）。

    返すのは 3 つ:

    - ``conflicts`` … 両側に値があって食い違う項目。人が選ぶ
    - ``relations`` … 行き先ごとに寄せた関係。両側にあるものは ``conflict``
    - ``union`` … 機械で畳める項目の結果（別名・使用例・タグ）
    """
    if keep_ref == drop_ref:
        raise MergeError("同じエントリ同士はまとめられません")
    entries = store.load_all()
    keep = _require(keep_ref, entries, "残す側")
    drop = _require(drop_ref, entries, "まとめる側")

    conflicts = []
    for field in CONFLICT_FIELDS:
        a, b = getattr(keep, field), getattr(drop, field)
        if a and b and a != b:
            conflicts.append({"field": field, "keep": a, "drop": b})

    rows = _relation_plan(keep, drop, entries)
    return {
        "keep": _entry_view(keep),
        "drop": _entry_view(drop),
        "conflicts": conflicts,
        "relations": rows,
        "union": {
            # 用語名は「残す側を残し、消える側は別名に回す」で決まっているので
            # 選ばせない。選択制にすると、本文のリンクが片方だけ切れる形を作れる
            "aliases": _merged_aliases(keep, drop),
            "examples": _union(keep.examples, drop.examples),
            "tags": _union(keep.tags, drop.tags),
        },
        "backlinks": _incoming(drop, entries),
        "warnings": _warnings(keep, drop, rows),
    }


def _entry_view(entry: Entry) -> dict:
    return {
        "ref": entry.ref,
        "term": entry.term,
        "reading": entry.reading,
        "aliases": entry.aliases,
        "summary": entry.summary,
        "definition": entry.definition,
        "examples": entry.examples,
        "tags": entry.tags,
        "source": entry.source,
        "first_file": entry.first_file,
        "first_locator": entry.first_locator,
        "path_label": entry.path_label,
        "scope": entry.scope,
        "url": entry_url(entry),
    }


def _merged_aliases(keep: Entry, drop: Entry) -> list[str]:
    """残す側の別名 + **消える側の用語名** + 消える側の別名。

    消える側の用語名を落とすと、本文でその表記がリンクにならなくなる
    （まとめた結果、片方の呼び方だけ引けなくなるのでは意味がない）。
    残す側の用語名と同じものは `Entry` 側で落ちる。
    """
    return _union(keep.aliases, [drop.term, *drop.aliases], drop_values=[keep.term])


def _union(*groups, drop_values: list[str] | None = None) -> list[str]:
    banned = {v.casefold() for v in (drop_values or [])}
    out: list[str] = []
    for group in groups:
        for value in group:
            value = (value or "").strip()
            if not value or value.casefold() in banned:
                continue
            if not any(value.casefold() == o.casefold() for o in out):
                out.append(value)
    return out


def _relation_plan(keep: Entry, drop: Entry, entries: list[Entry]) -> list[dict]:
    """行き先ごとに、残す側と消える側の関係を並べる。

    **鍵は解決後の ref。** 順序は残す側が先で、消える側にしか無いものを後ろに足す。
    統合後に自分自身を指してしまう関係（互いを指し合っていた場合）は
    ``self_reference`` を立てて既定で外す —— 自分への辺は相関図で意味を成さない。
    """
    keep_by: dict[str, Relation] = {}
    for r in keep.relations:
        keep_by.setdefault(_rel_key(r, keep, entries), r)
    drop_by: dict[str, Relation] = {}
    for r in drop.relations:
        drop_by.setdefault(_rel_key(r, drop, entries), r)

    out: list[dict] = []
    for key in [*keep_by, *(k for k in drop_by if k not in keep_by)]:
        mine, theirs = keep_by.get(key), drop_by.get(key)
        gone = key in (keep.ref, drop.ref)
        out.append({
            "key": key,
            "keep": _rel_view(mine, keep, entries),
            "drop": _rel_view(theirs, drop, entries),
            "conflict": bool(mine and theirs and mine.model_dump() != theirs.model_dump()),
            # 互いを指し合っていた関係。まとめると自分への辺になるので落とす
            "self_reference": gone,
        })
    return out


def _incoming(drop: Entry, entries: list[Entry]) -> list[dict]:
    """消える側を指している他エントリ。**書き換えないので、転送で残ることを示す。**"""
    return [
        {"ref": item["ref"], "term": item["term"], "path_label": item["path_label"]}
        for item in rel_mod.backlinks(drop, entries)
    ]


def _warnings(keep: Entry, drop: Entry, plan_rows: list[dict]) -> list[str]:
    out = []
    if any(row["self_reference"] for row in plan_rows):
        out.append("互いを指していた関係は、まとめると自分への辺になるので外します")
    if keep.scope != drop.scope:
        out.append(
            f"保存先が違います（残す側は{'このフォルダ' if keep.is_local else '全体'}の辞書）。"
            f"まとめた結果は残す側に置かれます"
        )
    if keep.category != drop.category:
        out.append(
            f"カテゴリが違います（{drop.path_label} → {keep.path_label}）。"
            "別のカテゴリに同じ名前を置けるのは仕様なので、本当に同じものか確かめてください"
        )
    return out


def apply(
    keep_ref: str,
    drop_ref: str,
    *,
    fields: dict | None = None,
    relations: list[dict] | None = None,
) -> Entry:
    """統合を実行する。**残す側を書いてから、消える側を消す。**

    ``fields`` は衝突した項目の決着（``{"definition": "..."}``）。**渡されなかった
    項目は残す側の値**。``relations`` は行き先ごとに採ると決めた関係の並びで、
    ``None`` なら「残す側優先、消える側にしか無いものは引き継ぐ」で畳む。

    書き込みと削除は ``store.write()`` が 1 つの操作としてやる（書いてから消す）。
    """
    if keep_ref == drop_ref:
        raise MergeError("同じエントリ同士はまとめられません")
    entries = store.load_all()
    keep = _require(keep_ref, entries, "残す側")
    drop = _require(drop_ref, entries, "まとめる側")

    values = dict(fields or {})
    merged = {
        field: values.get(field) or getattr(keep, field) or getattr(drop, field)
        for field in CONFLICT_FIELDS
    }
    chosen = (
        [Relation(**r) for r in relations]
        if relations is not None
        else _default_relations(keep, drop, entries)
    )
    # 統合したあと自分自身を指す関係は、どう指定されても残さない
    # （クライアントが送り返してくることがあるし、自分への辺は図で意味を成さない）
    mine = {keep.ref, drop.ref}
    chosen = [r for r in chosen if _rel_key(r, keep, entries) not in mine]

    # **`model_copy` ではなく検証を通す。** `relations` はクライアントから来るので、
    # 同じ行き先が 2 行あっても素通りしてしまう（`_clean_relations` が走らない）。
    # そのまま書くと相関図に多重辺が出る —— 入り口で 1 本にするのが約束
    updated = Entry.model_validate({
        **keep.model_dump(),
        **merged,
        "aliases": _merged_aliases(keep, drop),
        "examples": _union(keep.examples, drop.examples),
        "tags": _union(keep.tags, drop.tags),
        "relations": chosen,
        # **消える側の ref を転送先として積む。** 参照側は書き換えない
        "former_refs": _union(keep.former_refs, drop.all_refs, drop_values=[keep.ref]),
        # 古いほうの作成日時を採る（まとめても「いつからある語か」は変わらない）
        "created_at": min(keep.created_at, drop.created_at),
        "updated_at": now_iso(),
    })
    return store.write(updated, replacing=drop.ref)


def _default_relations(keep: Entry, drop: Entry, entries: list[Entry]) -> list[Relation]:
    """既定の畳み方: 残す側を優先し、消える側にしか無いものを後ろに足す。"""
    out: list[Relation] = []
    for item in _relation_plan(keep, drop, entries):
        if item["self_reference"]:
            continue
        source = item["keep"] or item["drop"]
        out.append(Relation(**{k: source[k] for k in ("to", "label", "back", "rank", "reveal")}))
    return out
