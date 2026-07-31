"""エントリ間の関係 —— 参照の解決、転送 (旧 ref)、相関図。

ID を別に持たない設計なので、揺れを吸収するのはここ 1 か所だけ。
壊れると「相関図の辺が本文のリンク先と食い違う」という形で出る。
"""

from __future__ import annotations

import pytest

from glosspop import relations, store
from glosspop.models import Entry, EntryDraft, Relation


def rel(**kwargs) -> dict:
    return {"to": kwargs.pop("to"), **kwargs}


@pytest.fixture
def cast(add_entry):
    """同じカテゴリの登場人物 2 人。"""
    giovanni = add_entry("ジョバンニ", category="登場人物")
    campanella = add_entry("カムパネルラ", category="登場人物")
    return giovanni, campanella


# --------------------------------------------------------------------------- #
# Relation の正規化
# --------------------------------------------------------------------------- #

class TestRelationModel:
    def test_normalizes_spacing_around_the_separator(self):
        """「空白を足しただけで切れる」を防ぐのがこのモデルの主な仕事。"""
        assert Relation(to="  登場人物 / カムパネルラ  ").to == "登場人物/カムパネルラ"

    def test_collapses_runs_of_whitespace(self):
        assert Relation(to="銀河 　鉄道").to == "銀河 鉄道"

    def test_mutual_is_derived_from_back_not_stored_twice(self):
        """相互かどうかのフラグは持たない (二重に持つとずれる)。"""
        assert Relation(to="X", label="親友", back="親友").mutual is True
        assert Relation(to="X", label="片思い").mutual is False

    def test_rank_accepts_synonyms_and_rejects_junk(self):
        assert Relation(to="X", rank="上位").rank == "上"
        assert Relation(to="X", rank="同格").rank == "対等"
        assert Relation(to="X", rank="ふつう").rank == ""

    def test_duplicate_targets_collapse_to_one(self):
        entry = Entry(
            term="ジョバンニ",
            relations=[rel(to="X", label="親友"), rel(to="X", label="宿敵")],
        )
        assert [r.label for r in entry.relations] == ["親友"]

    def test_empty_target_is_dropped(self):
        assert Entry(term="X", relations=[rel(to="   ")]).relations == []


# --------------------------------------------------------------------------- #
# 参照の解決
# --------------------------------------------------------------------------- #

class TestResolve:
    def test_resolves_a_ref(self, cast):
        giovanni, campanella = cast
        res = relations.resolve(campanella.ref, store.load_all(), origin=giovanni)
        assert res.entry is not None and res.entry.ref == campanella.ref

    def test_resolves_a_bare_term_like_a_wiki_name(self, cast):
        giovanni, campanella = cast
        res = relations.resolve("カムパネルラ", store.load_all(), origin=giovanni)
        assert res.entry is not None and res.entry.ref == campanella.ref

    def test_resolves_an_alias(self, add_entry, cast):
        giovanni, _ = cast
        add_entry("ザネリ", category="登場人物", aliases=["いじめっ子"])
        res = relations.resolve("いじめっ子", store.load_all(), origin=giovanni)
        assert res.entry is not None and res.entry.term == "ザネリ"

    def test_prefers_the_writers_own_category(self, add_entry):
        """同じ用語名がカテゴリ違いで併存できる以上、書き手の文脈で絞る。"""
        cooking = add_entry("ソース", category="料理")
        add_entry("ソース", category="プログラミング")
        origin = add_entry("だし", category="料理")
        res = relations.resolve("ソース", store.load_all(), origin=origin)
        assert res.entry is not None and res.entry.ref == cooking.ref

    def test_reports_ambiguity_instead_of_guessing(self, add_entry):
        """絞りきれないときに黙ってどれかへ寄せない。"""
        add_entry("ソース", category="料理")
        add_entry("ソース", category="プログラミング")
        origin = add_entry("メモ", category="日記")
        res = relations.resolve("ソース", store.load_all(), origin=origin)
        assert res.entry is None
        assert {e.category for e in res.ambiguous} == {"料理", "プログラミング"}
        assert "カテゴリ/slug" in res.reason

    def test_missing_target_is_reported_not_raised(self, cast):
        giovanni, _ = cast
        res = relations.resolve("まだ書いていない人", store.load_all(), origin=giovanni)
        assert res.missing and "まだ登録されていません" in res.reason

    def test_ignores_surrounding_spaces(self, cast):
        giovanni, campanella = cast
        res = relations.resolve("  カムパネルラ ", store.load_all(), origin=giovanni)
        assert res.entry is not None and res.entry.ref == campanella.ref


# --------------------------------------------------------------------------- #
# 転送 (旧 ref) —— wiki のリダイレクト
# --------------------------------------------------------------------------- #

class TestFormerRefs:
    def test_move_records_the_old_ref(self, cast):
        _, campanella = cast
        moved = store.move(campanella.ref, "主要人物")
        assert campanella.ref in moved.former_refs

    def test_a_relation_written_with_the_old_ref_still_resolves(self, add_entry, cast):
        """**参照側を書き換えない。** 移動しても古い ref が生き続ける。"""
        giovanni, campanella = cast
        old_ref = campanella.ref
        store.save(
            EntryDraft(
                term=giovanni.term,
                category=giovanni.category,
                relations=[rel(to=old_ref, label="親友", back="親友")],
            ),
            ref=giovanni.ref,
        )
        store.move(old_ref, "主要人物")

        entries = store.load_all()
        origin = store.get(giovanni.ref)
        assert origin is not None
        res = relations.resolve(old_ref, entries, origin=origin)
        assert res.entry is not None and res.entry.category == "主要人物"

    def test_the_current_ref_wins_over_someone_elses_old_ref(self, add_entry):
        """旧 ref を別のエントリが今の名前として使っていたら、今のほうを採る。"""
        first = add_entry("ソース", category="料理")
        old_ref = first.ref
        store.move(old_ref, "調味料")
        second = add_entry("ソース", category="料理")   # 同じ場所を新しい語が埋める

        res = relations.resolve(old_ref, store.load_all())
        assert res.entry is not None and res.entry.ref == second.ref

    def test_category_change_through_save_also_records_it(self, cast):
        _, campanella = cast
        old_ref = campanella.ref
        updated = store.save(
            EntryDraft(term=campanella.term, category="主要人物"), ref=old_ref
        )
        assert old_ref in updated.former_refs

    def test_former_refs_survive_an_update_that_omits_them(self, cast):
        """部分的な更新で転送情報が消えないこと（ファイルの値が正）。"""
        _, campanella = cast
        moved = store.move(campanella.ref, "主要人物")
        again = store.save(
            EntryDraft(term=moved.term, category=moved.category, summary="更新"),
            ref=moved.ref,
        )
        assert again.former_refs == moved.former_refs


# --------------------------------------------------------------------------- #
# 保存と読み出し
# --------------------------------------------------------------------------- #

class TestPersistence:
    def test_relations_round_trip_through_the_file(self, cast):
        giovanni, campanella = cast
        saved = store.save(
            EntryDraft(
                term=giovanni.term,
                category=giovanni.category,
                relations=[rel(to=campanella.ref, label="親友", back="親友", rank="対等")],
            ),
            ref=giovanni.ref,
        )
        store.invalidate()
        again = store.get(saved.ref)
        assert again is not None
        assert again.relations[0].to == campanella.ref
        assert again.relations[0].back == "親友"
        assert again.relations[0].rank == "対等"

    def test_empty_fields_are_not_written_to_the_file(self, cast):
        giovanni, campanella = cast
        saved = store.save(
            EntryDraft(
                term=giovanni.term,
                category=giovanni.category,
                relations=[rel(to=campanella.ref, label="親友")],
            ),
            ref=giovanni.ref,
        )
        text = store.path_for_ref(saved.ref).read_text(encoding="utf-8")
        assert "label: 親友" in text
        assert "back:" not in text and "reveal:" not in text


# --------------------------------------------------------------------------- #
# グラフ
# --------------------------------------------------------------------------- #

def _link(term_entry, target, **kwargs):
    return store.save(
        EntryDraft(
            term=term_entry.term,
            category=term_entry.category,
            relations=[rel(to=target, **kwargs)],
        ),
        ref=term_entry.ref,
    )


class TestGraph:
    def test_builds_nodes_and_edges(self, cast):
        giovanni, campanella = cast
        _link(giovanni, campanella.ref, label="親友", back="親友", rank="対等")
        g = relations.build_graph(store.load_all(), category="登場人物")
        assert {n["term"] for n in g["nodes"]} == {"ジョバンニ", "カムパネルラ"}
        assert len(g["edges"]) == 1
        assert g["edges"][0]["mutual"] is True
        assert g["edges"][0]["rank"] == "対等"

    def test_unresolved_targets_become_red_link_nodes(self, cast):
        giovanni, _ = cast
        _link(giovanni, "まだ書いていない人", label="兄")
        g = relations.build_graph(store.load_all(), category="登場人物")
        missing = [n for n in g["nodes"] if n["missing"]]
        assert [n["term"] for n in missing] == ["まだ書いていない人"]
        assert g["broken"][0]["to"] == "まだ書いていない人"

    def test_a_partner_outside_the_filter_is_still_drawn(self, add_entry, cast):
        giovanni, _ = cast
        teacher = add_entry("先生", category="その他")
        _link(giovanni, teacher.ref, label="生徒", back="教師", rank="上")
        g = relations.build_graph(store.load_all(), category="登場人物")
        outside = [n for n in g["nodes"] if n["outside"]]
        assert [n["term"] for n in outside] == ["先生"]   # 辺が宙に浮かない

    def test_reveal_is_hidden_by_default_but_counted(self, cast):
        """相関図は本文より先を一望させる。判明位置つきの関係は既定で伏せる。"""
        giovanni, campanella = cast
        _link(giovanni, campanella.ref, label="実は兄弟", reveal="第6章")
        g = relations.build_graph(store.load_all(), category="登場人物")
        assert g["edges"] == []
        assert g["hidden"] == 1            # 黙って消さない

    def test_reveal_is_shown_when_asked(self, cast):
        giovanni, campanella = cast
        _link(giovanni, campanella.ref, label="実は兄弟", reveal="第6章")
        g = relations.build_graph(store.load_all(), category="登場人物", spoilers=True)
        assert [e["label"] for e in g["edges"]] == ["実は兄弟"]
        assert g["hidden"] == 0


class TestBacklinks:
    def test_the_side_that_did_not_write_it_still_sees_it(self, cast):
        """関係は片側にしか書かない。書かれていない側は逆引きで見せる。"""
        giovanni, campanella = cast
        _link(giovanni, campanella.ref, label="親友", back="親友")
        entries = store.load_all()
        target = store.get(campanella.ref)
        assert target is not None
        links = relations.backlinks(target, entries)
        assert [b["term"] for b in links] == ["ジョバンニ"]
        assert links[0]["label"] == "親友"

    def test_rank_is_flipped_for_the_other_side(self, add_entry, cast):
        giovanni, _ = cast
        teacher = add_entry("先生", category="登場人物")
        _link(giovanni, teacher.ref, label="師", back="弟子", rank="上")
        target = store.get(teacher.ref)
        assert target is not None
        assert relations.backlinks(target, store.load_all())[0]["rank"] == "下"


class TestResolvedRelations:
    def test_gives_the_entry_page_a_url_or_a_reason(self, cast):
        giovanni, campanella = cast
        store.save(
            EntryDraft(
                term=giovanni.term,
                category=giovanni.category,
                relations=[
                    rel(to=campanella.ref, label="親友"),
                    rel(to="いない人", label="兄"),
                ],
            ),
            ref=giovanni.ref,
        )
        entry = store.get(giovanni.ref)
        assert entry is not None
        out = relations.resolved_relations(entry, store.load_all())
        assert out[0]["missing"] is False and out[0]["term"] == "カムパネルラ"
        assert out[1]["missing"] is True and out[1]["reason"]
