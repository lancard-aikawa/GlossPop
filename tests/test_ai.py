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
