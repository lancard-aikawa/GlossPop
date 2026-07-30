"""AI 抽出まわり。claude CLI は呼ばず、プロンプト生成と応答の後処理だけを見る。"""

from __future__ import annotations

import pytest

from glosspop import ai

TEXT = "この API は結果整合性を前提にしている。冪等な操作ならリトライしても安全だ。"


def test_prompt_lists_known_terms_to_skip():
    prompt = ai.build_extract_prompt(TEXT, exclude=["冪等", "ソース"], limit=5)
    assert "すでに辞書にある語" in prompt
    assert "冪等" in prompt and "ソース" in prompt
    assert "5 件まで" in prompt
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
        assert "上限" in dropped[0]["reason"]

    def test_keeps_the_reported_context(self):
        raw = [{"term": "冪等", "why": "設計の前提", "context": "冪等な操作ならリトライしても安全だ。"}]
        kept, _ = ai.filter_candidates(raw, TEXT, limit=10)
        assert kept[0]["why"] == "設計の前提"
        assert kept[0]["context"].startswith("冪等な操作")


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
