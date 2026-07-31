"""辞書の点検。**壊れているものだけ**を挙げる。

参照は名前で書ける（ID を持たない）ぶん、書き間違いや相手の削除で静かに切れる。
`/api/graph` はカテゴリ単位でしか壊れを返さないので、辞書全体を横断して集める
受け皿がここ。「次に書くべきエントリの一覧」としても使う。

**正常なものを問題として出さない**のが方針。同じ用語名がカテゴリ違いで併存するのは
この辞書の狙いどおりの機能だし、関係が 1 本も無いエントリも普通にある。挙げるのは
「直さないと読み手に見えないもの」「黙って壊れているもの」だけにする。
"""

from __future__ import annotations

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
}


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


def check(entries: list[Entry]) -> dict:
    """辞書全体を点検して ``{issues, counts, checked}`` を返す。

    ``issues`` は重大度の高い順、同じ重大度なら登録順。
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
