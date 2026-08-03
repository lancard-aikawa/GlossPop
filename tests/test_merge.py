"""割れてしまった同じものを 1 つにまとめる (`merge.py`)。

要点は 3 つ:

- **消える側の呼び方が残ること。** 用語名を別名に回さないと、本文でその表記が
  リンクにならず、名前で書かれた他エントリの関係も行き先を失う
- **参照側を書き換えないこと。** 消える側の ref は `former_refs` に積んで転送する
- **畳めないものを黙って寄せないこと。** 本文・要約は人が決めた値だけを採る
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from glosspop import merge, relations, store
from glosspop.app import app
from glosspop.models import EntryDraft


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def add(term, **kwargs):
    kwargs.setdefault("category", "登場人物")
    kwargs.setdefault("definition", f"{term}の説明。")
    return store.save(EntryDraft(term=term, **kwargs))


@pytest.fixture
def split():
    """同じ人物が 2 つに割れている状態。"""
    keep = add("主人", summary="猫の飼い主。", tags=["人物"], aliases=["先生"])
    drop = add("苦沙弥先生", summary="中学校の英語教師。", tags=["教師"])
    return keep, drop


class TestAliases:
    def test_the_dropped_term_becomes_an_alias(self, split):
        keep, drop = split
        merged = merge.apply(keep.ref, drop.ref)
        assert merged.term == "主人"
        assert "苦沙弥先生" in merged.aliases
        # 消える側の別名も引き継ぐ。残す側の用語名は別名にしない
        assert "先生" in merged.aliases
        assert "主人" not in merged.aliases

    def test_the_dropped_name_still_links_in_a_document(self, split):
        """まとめた結果、片方の呼び方だけ引けなくなっては意味がない。"""
        keep, drop = split
        merge.apply(keep.ref, drop.ref)
        hits = store.find_by_surface("苦沙弥先生")
        assert [e.ref for e in hits] == [keep.ref]

    def test_the_dropped_entry_is_gone(self, split):
        keep, drop = split
        merge.apply(keep.ref, drop.ref)
        assert store.get(drop.ref) is None
        assert len(store.load_all()) == 1


class TestForwarding:
    def test_the_old_ref_still_resolves(self, split):
        """**参照側は書き換えない。** 転送で解決し続けること。"""
        keep, drop = split
        other = add("吾輩", category="登場人物", relations=[{"to": drop.ref, "label": "飼い主"}])

        merge.apply(keep.ref, drop.ref)
        entries = store.load_all()
        res = relations.resolve(drop.ref, entries, origin=store.get(other.ref))
        assert res.entry is not None and res.entry.ref == keep.ref

    def test_a_reference_written_by_name_also_survives(self, split):
        keep, drop = split
        other = add("吾輩", relations=[{"to": "苦沙弥先生", "label": "飼い主"}])

        merge.apply(keep.ref, drop.ref)
        entries = store.load_all()
        res = relations.resolve("苦沙弥先生", entries, origin=store.get(other.ref))
        assert res.entry is not None and res.entry.ref == keep.ref

    def test_the_other_entry_file_is_untouched(self, split):
        keep, drop = split
        other = add("吾輩", relations=[{"to": drop.ref}])
        before = store.path_for_ref(other.ref).read_text(encoding="utf-8")

        merge.apply(keep.ref, drop.ref)
        assert store.path_for_ref(other.ref).read_text(encoding="utf-8") == before


class TestConflicts:
    def test_the_plan_lists_what_cannot_be_folded(self, split):
        keep, drop = split
        plan = merge.plan(keep.ref, drop.ref)
        fields = {c["field"]: c for c in plan["conflicts"]}
        assert fields["summary"]["keep"] == "猫の飼い主。"
        assert fields["summary"]["drop"] == "中学校の英語教師。"
        # リストは和集合で畳めるので衝突にしない
        assert set(plan["union"]["tags"]) == {"人物", "教師"}

    def test_the_chosen_value_wins(self, split):
        keep, drop = split
        merged = merge.apply(
            keep.ref, drop.ref, fields={"summary": "猫の飼い主で、中学校の英語教師。"}
        )
        assert merged.summary == "猫の飼い主で、中学校の英語教師。"

    def test_nothing_chosen_keeps_the_surviving_side(self, split):
        keep, drop = split
        merged = merge.apply(keep.ref, drop.ref)
        assert merged.summary == "猫の飼い主。"

    def test_an_empty_field_is_filled_from_the_dropped_side(self):
        keep = add("主人", reading="")
        drop = add("苦沙弥先生", reading="くしゃみせんせい")
        # 片側にしか無いものは衝突ではない。拾わないと情報が落ちる
        fields = [c["field"] for c in merge.plan(keep.ref, drop.ref)["conflicts"]]
        assert "reading" not in fields
        assert merge.apply(keep.ref, drop.ref).reading == "くしゃみせんせい"


class TestRelations:
    def test_relations_from_both_sides_are_kept(self):
        add("吾輩")
        add("迷亭")
        keep = add("主人", relations=[{"to": "吾輩", "label": "飼い主"}])
        drop = add("苦沙弥先生", relations=[{"to": "迷亭", "label": "友人"}])

        merged = merge.apply(keep.ref, drop.ref)
        assert {r.to for r in merged.relations} == {"吾輩", "迷亭"}

    def test_the_same_target_is_one_edge_and_flagged(self):
        """**同じ相手への関係は 1 本にする。** 2 本あると相関図に多重辺が出る。"""
        target = add("吾輩")
        keep = add("主人", relations=[{"to": "吾輩", "label": "飼い主"}])
        drop = add("苦沙弥先生", relations=[{"to": target.ref, "label": "主人"}])

        row = next(r for r in merge.plan(keep.ref, drop.ref)["relations"] if r["key"] == target.ref)
        # 片方が名前・片方が ref でも、解決してから突き合わせるので同じ組と分かる
        assert row["conflict"] is True
        merged = merge.apply(keep.ref, drop.ref)
        assert len(merged.relations) == 1
        assert merged.relations[0].label == "飼い主"        # 既定は残す側

    def test_the_dropped_side_can_be_chosen(self):
        target = add("吾輩")
        keep = add("主人", relations=[{"to": "吾輩", "label": "飼い主"}])
        drop = add("苦沙弥先生", relations=[{"to": target.ref, "label": "主人"}])

        merged = merge.apply(
            keep.ref, drop.ref, relations=[{"to": target.ref, "label": "主人"}]
        )
        assert [(r.to, r.label) for r in merged.relations] == [(target.ref, "主人")]

    def test_pointing_at_each_other_does_not_become_a_self_edge(self):
        keep = add("主人")
        drop = add("苦沙弥先生", relations=[{"to": "主人", "label": "同一人物"}])

        plan = merge.plan(keep.ref, drop.ref)
        assert any(r["self_reference"] for r in plan["relations"])
        assert any("自分への辺" in w for w in plan["warnings"])
        assert merge.apply(keep.ref, drop.ref).relations == []

    def test_a_self_edge_is_dropped_even_if_it_is_sent_back(self):
        """クライアントが送り返してきても残さない。"""
        keep = add("主人")
        drop = add("苦沙弥先生", relations=[{"to": "主人"}])
        merged = merge.apply(keep.ref, drop.ref, relations=[{"to": keep.ref, "label": "自分"}])
        assert merged.relations == []


class TestGuards:
    def test_merging_an_entry_with_itself_is_refused(self, split):
        keep, _ = split
        with pytest.raises(merge.MergeError, match="同じエントリ同士"):
            merge.apply(keep.ref, keep.ref)

    def test_a_missing_entry_is_refused(self, split):
        keep, _ = split
        with pytest.raises(merge.MergeError, match="見つかりません"):
            merge.apply(keep.ref, "登場人物/居ない")

    def test_crossing_categories_warns_but_is_allowed(self):
        keep = add("ソース", category="プログラミング")
        drop = add("ソース", category="料理")
        plan = merge.plan(keep.ref, drop.ref)
        assert any("カテゴリが違います" in w for w in plan["warnings"])
        merged = merge.apply(keep.ref, drop.ref)
        assert merged.category == "プログラミング"

    def test_crossing_scopes_lands_on_the_surviving_side(self, tmp_path):
        from glosspop import config

        folder = tmp_path / "小説"
        folder.mkdir()
        config.set_content_dir(folder)
        keep = add("主人")
        drop = add("苦沙弥先生", scope="local")

        plan = merge.plan(keep.ref, drop.ref)
        assert any("保存先が違います" in w for w in plan["warnings"])
        merged = merge.apply(keep.ref, drop.ref)
        assert merged.scope == "global"
        assert store.get(drop.ref) is None


class TestApi:
    def test_the_plan_endpoint_shows_both_sides(self, client, split):
        keep, drop = split
        res = client.get("/api/merge", params={"keep": keep.ref, "drop": drop.ref})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["keep"]["term"] == "主人"
        assert body["drop"]["term"] == "苦沙弥先生"
        assert "苦沙弥先生" in body["union"]["aliases"]

    def test_the_plan_lists_who_points_at_the_dropped_side(self, client, split):
        keep, drop = split
        add("吾輩", relations=[{"to": drop.ref, "label": "飼い主"}])
        body = client.get("/api/merge", params={"keep": keep.ref, "drop": drop.ref}).json()
        assert [b["term"] for b in body["backlinks"]] == ["吾輩"]

    def test_applying_returns_the_merged_entry(self, client, split):
        keep, drop = split
        res = client.post("/api/merge", json={
            "keep": keep.ref, "drop": drop.ref, "fields": {"summary": "まとめた要約。"},
        })
        assert res.status_code == 200, res.text
        assert res.json()["summary"] == "まとめた要約。"
        assert client.get(f"/api/entries/{drop.ref}").status_code == 404

    def test_a_missing_entry_is_a_404(self, client, split):
        keep, _ = split
        res = client.get("/api/merge", params={"keep": keep.ref, "drop": "登場人物/居ない"})
        assert res.status_code == 404

    def test_merging_with_itself_is_a_409(self, client, split):
        keep, _ = split
        res = client.post("/api/merge", json={"keep": keep.ref, "drop": keep.ref})
        assert res.status_code == 409


def test_duplicate_targets_are_collapsed():
    """**同じ行き先が 2 行来ても 1 本にする。**

    `relations` はクライアントから来るので、検証を通さずに書くと相関図に
    多重辺が出る（どちらが正かも決まらない）。
    """
    add("吾輩")
    keep = add("主人")
    drop = add("苦沙弥先生")
    merged = merge.apply(keep.ref, drop.ref, relations=[
        {"to": "吾輩", "label": "飼い主"},
        {"to": "吾輩", "label": "主人"},
    ])
    assert [(r.to, r.label) for r in merged.relations] == [("吾輩", "飼い主")]
