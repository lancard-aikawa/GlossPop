"""AI 抽出まわり。claude CLI は呼ばず、プロンプト生成と応答の後処理だけを見る。"""

from __future__ import annotations

import pytest

from glosspop import ai

TEXT = "この API は結果整合性を前提にしている。冪等な操作ならリトライしても安全だ。"


def test_prompt_lists_known_terms_to_skip():
    prompt = ai.build_extract_prompt(TEXT, exclude=["冪等", "ソース"], limit=5)
    assert "すでに辞書にある語" in prompt
    assert "冪等" in prompt and "ソース" in prompt
    assert TEXT in prompt


def test_prompt_without_known_terms_has_no_skip_block():
    assert "すでに辞書にある語" not in ai.build_extract_prompt(TEXT, exclude=[])


def test_parse_candidates_accepts_fenced_json():
    raw = '説明文\n```json\n[{"term": "結果整合性"}]\n```\n'
    assert ai.parse_candidates(raw) == [{"term": "結果整合性"}]


def test_parse_candidates_accepts_bare_array():
    assert ai.parse_candidates('[{"term": "冪等"}, "ゴミ"]') == [{"term": "冪等"}]


def test_parse_candidates_rejects_garbage():
    with pytest.raises(ai.AIError):
        ai.parse_candidates("JSON ではない返事")


class TestFilterCandidates:
    """AI の申告をそのまま信じない部分。"""

    def test_drops_terms_not_present_in_the_document(self):
        raw = [{"term": "結果整合性"}, {"term": "存在しない語"}]
        kept, dropped = ai.filter_candidates(raw, TEXT, limit=10)
        assert [c["term"] for c in kept] == ["結果整合性"]
        assert dropped[0]["term"] == "存在しない語"
        assert "見つからない" in dropped[0]["reason"]

    def test_drops_already_registered_terms(self, add_entry):
        add_entry("冪等", category="プログラミング")
        raw = [{"term": "冪等"}, {"term": "結果整合性"}]
        kept, dropped = ai.filter_candidates(raw, TEXT, limit=10)
        assert [c["term"] for c in kept] == ["結果整合性"]
        assert "登録済み" in dropped[0]["reason"]

    def test_dedupes_case_insensitively(self):
        raw = [{"term": "API"}, {"term": "api"}]
        kept, _ = ai.filter_candidates(raw, TEXT, limit=10)
        assert [c["term"] for c in kept] == ["API"]

    def test_respects_the_limit(self):
        raw = [{"term": "API"}, {"term": "結果整合性"}, {"term": "冪等"}]
        kept, dropped = ai.filter_candidates(raw, TEXT, limit=2)
        assert len(kept) == 2
        assert "枠" in dropped[0]["reason"]

    def test_keeps_the_reported_context(self):
        raw = [{"term": "冪等", "why": "設計の前提", "context": "冪等な操作ならリトライしても安全だ。"}]
        kept, _ = ai.filter_candidates(raw, TEXT, limit=10)
        assert kept[0]["why"] == "設計の前提"
        assert kept[0]["context"].startswith("冪等な操作")


#: 人物と専門用語が混ざった文書。種別ごとの枠を確かめるのに使う
MIXED = (
    "ジョバンニは活版所で働いていた。カムパネルラは黙っていた。"
    "この API は結果整合性を前提にしており、冪等な操作ならリトライできる。"
)


class TestExtractKinds:
    """**何を抜き出すかを先に決める**部分。

    種別を分けずに頼むと AI は語義説明のできる語ばかり挙げ、登場人物が
    まるごと落ちる。枠を独立させたことが効いているかを見る。
    """

    def test_prompt_asks_for_each_kind_with_its_own_quota(self):
        prompt = ai.build_extract_prompt(MIXED, limit=12)
        for key in ai.DEFAULT_KINDS:
            assert f"`{key}`" in prompt
            assert ai.EXTRACT_KINDS[key]["label"] in prompt
        # 枠の振り替えを明示的に禁じていること (ここが緩むと人物が消える)
        assert "振り替えないこと" in prompt

    def test_prompt_only_mentions_the_requested_kinds(self):
        prompt = ai.build_extract_prompt(MIXED, kinds=["person"], limit=6)
        assert ai.EXTRACT_KINDS["person"]["label"] in prompt
        # 頼んでいない種別の説明は載せない（"`term`" はスキーマのキー名と
        # 衝突するのでラベルで見る）
        assert ai.EXTRACT_KINDS["term"]["label"] not in prompt

    def test_quota_splits_the_limit_without_exceeding_it(self):
        quota = ai.allocate_quota(12, ["person", "proper", "term"])
        assert quota == {"person": 4, "proper": 4, "term": 4}
        assert sum(ai.allocate_quota(10, ["person", "proper", "term"]).values()) == 10

    def test_one_kind_cannot_eat_another_kinds_quota(self):
        """人物を先に並べられても専門用語の枠は残る（順序で切らない）。"""
        raw = [
            *({"term": t, "kind": "person"} for t in ["ジョバンニ", "カムパネルラ"]),
            {"term": "結果整合性", "kind": "term"},
        ]
        kept, dropped = ai.filter_candidates(
            raw, MIXED, limit=3, kinds=["person", "term"]
        )
        terms = [c["term"] for c in kept]
        assert "結果整合性" in terms          # 人物 2 件のあとでも残る
        assert len(kept) == 3

    def test_a_kind_over_quota_is_dropped_with_a_reason(self):
        raw = [
            {"term": "ジョバンニ", "kind": "person"},
            {"term": "カムパネルラ", "kind": "person"},
            {"term": "結果整合性", "kind": "term"},
        ]
        kept, dropped = ai.filter_candidates(
            raw, MIXED, limit=2, kinds=["person", "term"]
        )
        assert [c["term"] for c in kept] == ["ジョバンニ", "結果整合性"]
        assert dropped[0]["term"] == "カムパネルラ"
        assert "人物" in dropped[0]["reason"]

    def test_unknown_kind_goes_to_a_bucket_with_room(self):
        raw = [{"term": "冪等", "kind": "なにか"}]
        kept, _ = ai.filter_candidates(raw, MIXED, limit=6, kinds=["person", "term"])
        assert kept[0]["kind"] == "person"      # 先に空いている枠へ

    def test_candidates_carry_the_kind_and_its_scope_hint(self):
        raw = [
            {"term": "ジョバンニ", "kind": "person"},
            {"term": "結果整合性", "kind": "term"},
        ]
        kept, _ = ai.filter_candidates(raw, MIXED, limit=6, kinds=["person", "term"])
        by_term = {c["term"]: c for c in kept}
        assert by_term["ジョバンニ"]["kind_label"] == "人物・組織"
        # 人物はこのフォルダの辞書向き、一般の専門用語は全体の辞書向き
        assert by_term["ジョバンニ"]["scope_hint"] == "local"
        assert by_term["結果整合性"]["scope_hint"] == "global"

    def test_kept_candidates_are_grouped_by_requested_kind_order(self):
        raw = [
            {"term": "結果整合性", "kind": "term"},
            {"term": "ジョバンニ", "kind": "person"},
        ]
        kept, _ = ai.filter_candidates(raw, MIXED, limit=6, kinds=["person", "term"])
        assert [c["term"] for c in kept] == ["ジョバンニ", "結果整合性"]

    def test_scope_block_leans_on_the_kind(self):
        block = ai.build_scope_block("銀河鉄道の夜", "person")
        assert "人物・組織" in block and "`local`" in block
        assert "人物・組織" not in ai.build_scope_block("銀河鉄道の夜")


DOCS = [
    ("a.md", "サーキットブレーカーで止める。指数バックオフで待つ。"),
    ("sub/b.md", "サーキットブレーカーは有効だ。"),
    ("c.md", "無関係な文章。"),
]


class TestCombineDocuments:
    def test_puts_a_header_per_file(self):
        combined, used, skipped = ai.combine_documents(DOCS)
        assert "### a.md" in combined and "### sub/b.md" in combined
        assert used == ["a.md", "sub/b.md", "c.md"]
        assert skipped == []

    def test_reports_what_did_not_fit(self):
        combined, used, skipped = ai.combine_documents(DOCS, per_file=10, total=15)
        assert used == ["a.md", "sub/b.md"]
        assert skipped == ["c.md"]     # 黙って切らない
        assert len(combined) < 60

    def test_skips_empty_files(self):
        _, used, _ = ai.combine_documents([("empty.md", "  "), ("a.md", "本文")])
        assert used == ["a.md"]


class TestExtractFromDocuments:
    async def _extract(self, monkeypatch, response: str, **kwargs):
        monkeypatch.setattr(ai, "_run_claude", lambda prompt: response)
        return await ai.extract_terms_from_documents(DOCS, **kwargs)

    @pytest.mark.anyio
    async def test_sorts_by_how_many_files_mention_the_term(self, monkeypatch):
        res = await self._extract(
            monkeypatch, '[{"term": "指数バックオフ"}, {"term": "サーキットブレーカー"}]'
        )
        # 2 ファイルに出る語を先に出す (AI が返した順ではない)
        assert [c["term"] for c in res["candidates"]] == ["サーキットブレーカー", "指数バックオフ"]
        first = res["candidates"][0]
        assert first["files"] == ["a.md", "sub/b.md"]
        assert first["file_count"] == 2 and first["count"] == 2
        assert first["source"] == "a.md"

    @pytest.mark.anyio
    async def test_matches_against_the_whole_text_not_just_the_prompt(self, monkeypatch):
        # プロンプトには頭しか載せないが、後ろに出てくる語も採用してよい
        docs = [("long.md", "x" * 5000 + "サーキットブレーカー")]
        monkeypatch.setattr(ai, "_run_claude", lambda prompt: '[{"term": "サーキットブレーカー"}]')
        res = await ai.extract_terms_from_documents(docs)
        assert [c["term"] for c in res["candidates"]] == ["サーキットブレーカー"]

    @pytest.mark.anyio
    async def test_no_documents_is_an_error(self, monkeypatch):
        monkeypatch.setattr(ai, "_run_claude", lambda prompt: "[]")
        with pytest.raises(ai.AIError):
            await ai.extract_terms_from_documents([])


@pytest.fixture
def anyio_backend():
    return "asyncio"


NOVEL = (
    "第一章\n"
    "駅前は雨だった。\n"
    "そこに立っていたのが太郎だった。\n"
    "太郎は傘を持っていなかった。\n"
    "\n"
    "第十章\n"
    "太郎の正体は敵の間諜だった。\n"
)


class TestSpoilerControl:
    def test_locator_points_at_the_first_line(self):
        assert ai.locator_of(NOVEL, "太郎") == "L.3"
        assert ai.locator_of(NOVEL, "花子") == ""

    def test_context_stops_after_the_first_appearance(self):
        out = ai.context_up_to_first(NOVEL, "太郎", lead=100, trail=20)
        assert "駅前は雨だった" in out          # 初出までの流れは渡す
        assert "正体は敵の間諜" not in out       # その後の展開は渡さない

    def test_context_is_cut_at_the_paragraph_break(self):
        # trail が長くても、初出を含む段落の先までは渡さない
        out = ai.context_up_to_first(NOVEL, "太郎", lead=100, trail=4000)
        assert "傘を持っていなかった" in out
        assert "第十章" not in out and "間諜" not in out

    def test_context_is_bounded_before_the_first_appearance(self):
        # 長い前置きを丸ごと渡さない (小説 1 冊を送りつけない)
        text = "あ" * 5000 + "太郎が現れた。"
        out = ai.context_up_to_first(text, "太郎", lead=100, trail=10)
        assert len(out) < 200 and "太郎" in out

    def test_missing_term_yields_nothing(self):
        assert ai.context_up_to_first(NOVEL, "花子") == ""

    def test_prompt_forbids_spoilers_when_asked(self):
        prompt = ai.build_prompt("太郎", "初出の場面", spoiler="first")
        assert "ネタバレの禁止" in prompt
        assert "ここまでしか読んでいない" in prompt

    def test_prompt_is_unrestricted_by_default(self):
        assert "ネタバレの禁止" not in ai.build_prompt("太郎", "文脈")
