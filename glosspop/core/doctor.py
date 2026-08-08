"""辞書の点検。**壊れているものだけ**を挙げる。

参照は名前で書ける（ID を持たない）ぶん、書き間違いや相手の削除で静かに切れる。
`/api/graph` はカテゴリ単位でしか壊れを返さないので、辞書全体を横断して集める
受け皿がここ。「次に書くべきエントリの一覧」としても使う。

**正常なものを問題として出さない**のが方針。同じ用語名がカテゴリ違いで併存するのは
この辞書の狙いどおりの機能だし、関係が 1 本も無いエントリも普通にある。挙げるのは
「直さないと読み手に見えないもの」「黙って壊れているもの」だけにする。
"""

from __future__ import annotations

from collections.abc import Mapping

from . import relations
from .linker import entry_url
from .models import Entry

#: 重大度。error = 壊れている、warn = 直したほうがよい
ERROR = "error"
WARN = "warn"

#: 点検の種類。UI の説明文はここを引く（画面と実装で文言をずらさない）
CHECKS: dict[str, dict[str, str]] = {
    "broken_relation": {
        "label": "解決できない関係",
        "hint": "相手がまだ登録されていないか、名前が違います。押すとその語で登録できます。",
    },
    "ambiguous_relation": {
        "label": "どれを指すか決まらない関係",
        "hint": "同じ用語名が複数のカテゴリにあります。「カテゴリ/slug」まで書いてください。",
    },
    "self_relation": {
        "label": "自分自身への関係",
        "hint": "図に描けません。相手を書き直すか、関係を消してください。",
    },
    "no_summary": {
        "label": "要約が無い",
        "hint": "吹き出しに出る文が空です。本文にリンクが生えても中身が読めません。",
    },
    "empty_definition": {
        "label": "本文が空",
        "hint": "辞書ページに出す説明がありません。",
    },
    "two_map_shapes": {
        "label": "地図の形が 2 つ以上",
        "hint": "pin（点）・line（線）・area（領域）は 1 つだけ書いてください。"
                "いまは細かいほう（領域 → 線 → 点）が使われています。",
    },
    "shape_without_map": {
        "label": "座標だけ書かれている",
        "hint": "どの絵に置くかが無いので、地図には出ません。map: に絵の名前を書いてください。",
    },
    "map_without_image": {
        "label": "その名前の絵がありません",
        "hint": "絵を消しても用語は書き換えないので、置いていた語は地図から静かに消えます。"
                "絵を入れ直すか、map: を直してください。",
    },
    "map_point_outside": {
        "label": "座標が絵の外",
        "hint": "座標は絵の幅を 1 とした比です（x は 0〜1、y は 0 から絵の縦横比まで）。"
                "外に出た点は描かれても画面に出ません。",
    },
}

#: 点検の文言に出す形の名前。**書いた人が探す語**（frontmatter の項目名）を添える
_KIND_WORDS = {"point": "点 (pin)", "line": "線 (line)", "area": "領域 (area)"}


def _outside(points: list[list[float]], ratio: float | None = None) -> list[int]:
    """絵の外に出ている点の番号（1 始まり）。

    **x は 0〜1、y は 0 以上。** 座標は**絵の幅を 1 とした比**なので、
    **縦長の絵では y が 1 を超えるのが正常** —— y の上限は絵の縦横比
    （高さ ÷ 幅）でしか決まらない。

    その比は `core` からは読めない（絵の中身を知らない）ので、**呼ぶ側が絵から
    読んで渡す**。**渡されなければ上限は見ない** —— 決めようのない定数を置いて
    正常なものを問題として出すくらいなら、確実に外だと分かるものだけを挙げる
    （`maps` を渡されなければ絵の点検をしない、というのと同じ約束）。
    """
    return [
        i for i, point in enumerate(points, 1)
        if not 0.0 <= point[0] <= 1.0
        or point[1] < 0.0
        or (ratio is not None and point[1] > ratio)
    ]


def _issue(kind: str, severity: str, entry: Entry, detail: str, **extra) -> dict:
    return {
        "kind": kind,
        "label": CHECKS[kind]["label"],
        "hint": CHECKS[kind]["hint"],
        "severity": severity,
        "ref": entry.ref,
        "term": entry.term,
        "url": entry_url(entry),
        "path_label": entry.path_label,
        "detail": detail,
        **extra,
    }


def check(entries: list[Entry], *, maps: Mapping[str, float | None] | None = None) -> dict:
    """辞書全体を点検して ``{issues, counts, checked}`` を返す。

    ``issues`` は重大度の高い順、同じ重大度なら登録順。

    ``maps`` は**置いてある絵**の ``<scope>/<名前>`` → **縦横比**（高さ ÷ 幅）。
    `core` は辞書の置き場所も絵の中身も知らないので、**呼ぶ側から引数で渡す**
    （`timeline` が `Linker` と `Document` を受けているのと同じ形）。
    **渡されなければ絵の点検はしない** ——「絵が 1 枚も無い」と「一覧をもらって
    いない」を混同すると、地図を使っている辞書で**全部の語に警告が出る**
    （そうなると誰も見なくなる）。

    比が ``None`` の絵（大きさを読めなかった絵）は**上限を見ないだけ**で、
    ほかの点検はそのまま効く。**読めないことを問題として挙げない** ——
    直しようがないものを挙げても、本物の壊れが埋もれるだけになる。
    """
    issues: list[dict] = []

    for entry in entries:
        for rel in entry.relations:
            res = relations.resolve(rel.to, entries, origin=entry)
            if res.entry is not None and res.entry.ref == entry.ref:
                issues.append(_issue(
                    "self_relation", WARN, entry,
                    f"「{rel.to}」は自分自身です", target=rel.to,
                ))
            elif res.ambiguous:
                issues.append(_issue(
                    "ambiguous_relation", ERROR, entry,
                    f"「{rel.to}」→ {res.reason}",
                    target=rel.to,
                    candidates=[
                        {"ref": e.ref, "path_label": e.path_label, "url": entry_url(e)}
                        for e in res.ambiguous
                    ],
                ))
            elif res.entry is None:
                issues.append(_issue(
                    "broken_relation", ERROR, entry,
                    f"「{rel.to}」が見つかりません",
                    target=rel.to,
                    # wiki の赤リンク。ここから登録に入れるようにする
                    create_url=f"/glossary?q={rel.to}",
                ))

        if not entry.summary.strip():
            issues.append(_issue("no_summary", WARN, entry, "summary が空です"))
        if not entry.definition.strip():
            issues.append(_issue("empty_definition", WARN, entry, "本文が空です"))
        # **黙って片方を選ばない。** `map_shape` は細かいほうを採るが、それは
        # 描くために決めているだけで、書いた人には見えない（＝ここで挙げる）
        if entry.map_shape_count > 1:
            written = "・".join(
                name for name, value in
                (("pin", entry.pin), ("line", entry.line), ("area", entry.area)) if value
            )
            issues.append(_issue(
                "two_map_shapes", WARN, entry, f"{written} が同時に書かれています",
            ))

        # **地図は「書いたのに出ない」が起きやすい。** どれも画面には何も出ない
        # ので、点検で言わないと気付く手段が無い。**逆に、`map` だけ書いて形が
        # まだ無いのは正常**（「この絵に置きたい」の置き待ち。図が数えて出す）。
        shape = entry.map_shape
        if shape is not None and not entry.map:
            issues.append(_issue(
                "shape_without_map", WARN, entry,
                f"{_KIND_WORDS[shape['kind']]} を書いていますが map がありません",
            ))
        if entry.map and maps is not None and f"{entry.scope}/{entry.map}" not in maps:
            issues.append(_issue(
                "map_without_image", WARN, entry,
                f"「{entry.map}」という絵がありません", target=entry.map,
            ))
        if shape is not None:
            # **上限は「その語が置かれている絵」の比**（絵ごとに違う）。
            # 絵の名前が無い / その絵が無いときは上限を見ない（上の 2 つが挙げる）
            outside = _outside(
                shape["points"],
                maps.get(f"{entry.scope}/{entry.map}") if maps and entry.map else None,
            )
            if outside:
                where = "・".join(f"{i} 点目" for i in outside[:5])
                more = f" ほか {len(outside) - 5} 点" if len(outside) > 5 else ""
                issues.append(_issue(
                    "map_point_outside", WARN, entry, f"{where}{more} が絵の外です",
                ))

    order = {ERROR: 0, WARN: 1}
    issues.sort(key=lambda i: order.get(i["severity"], 9))

    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue["kind"]] = counts.get(issue["kind"], 0) + 1
    return {
        "issues": issues,
        "counts": counts,
        "checked": len(entries),
        "errors": sum(1 for i in issues if i["severity"] == ERROR),
        "warnings": sum(1 for i in issues if i["severity"] == WARN),
    }
