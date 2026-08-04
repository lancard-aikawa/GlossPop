"""AI 抽出まわり。claude CLI は呼ばず、プロンプト生成と応答の後処理だけを見る。"""

from __future__ import annotations

import json

import pytest

from glosspop import ai, config

TEXT ="この API は結果整合性を前提にしている。冪等な操作ならリトライしても安全だ。"


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


class TestTimeouts:
    """**持ち時間は出す件数から見積もる。**

    所要時間は出力トークン数（思考を含む）にほぼ比例し、関係 20 本ぶんで約 140 秒
    （遅い日は 270 秒）。固定の 180 秒では吸収できずに落ちた（実際に踏んだ）。
    """

    def test_more_relations_get_more_time(self):
        assert ai.relations_timeout(20) > ai.relations_timeout(10)
        # 実測 15 本 271 秒。20 本を頼んだら少なくともそれ以上は待てること
        assert ai.relations_timeout(20) >= 271

    def test_never_below_the_configured_default(self, monkeypatch):
        monkeypatch.setattr(config, "CLAUDE_TIMEOUT", 600)
        assert ai.relations_timeout(1) == 600      # 明示された指定は下回らない

    def test_never_above_the_ceiling(self, monkeypatch):
        monkeypatch.setattr(config, "CLAUDE_TIMEOUT_MAX", 300)
        assert ai.relations_timeout(999) == 300

    def test_extraction_scales_too(self):
        # 語数の上限は API で 40 まで許している。既定のままだと溢れる
        assert ai.extract_timeout(40) > config.CLAUDE_TIMEOUT

    def test_the_timeout_is_reported_in_the_error(self, monkeypatch):
        import subprocess

        monkeypatch.setattr(config, "CLAUDE_BIN", "claude")

        def boom(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=kw["timeout"])

        monkeypatch.setattr(subprocess, "run", boom)
        with pytest.raises(ai.AIError, match="500 秒"):
            ai._generate("お願い", timeout=500)


class TestSplitAliases:
    """同じ人物が呼び方ごとに別エントリへ割れるのを止める部分。"""

    NOVEL = "苦沙弥先生は書斎にいる。吾輩はその膝に乗った。"

    def test_pulls_out_another_name_for_an_existing_entry(self, add_entry):
        add_entry("主人", category="登場人物")
        raw = [{"term": "苦沙弥先生", "alias_of": "主人", "why": "同じ人物"}]
        rest, aliases = ai.split_aliases(raw, self.NOVEL)
        assert rest == []
        assert aliases[0]["term"] == "苦沙弥先生"
        assert aliases[0]["alias_of"] == "主人"
        assert aliases[0]["ref"].endswith("主人")

    def test_keeps_it_as_a_normal_candidate_when_the_target_is_unknown(self):
        raw = [{"term": "苦沙弥先生", "alias_of": "いない人"}]
        rest, aliases = ai.split_aliases(raw, self.NOVEL)
        assert rest == raw and aliases == []

    def test_skips_a_name_that_is_already_an_alias(self, add_entry):
        add_entry("主人", category="登場人物", aliases=["苦沙弥先生"])
        raw = [{"term": "苦沙弥先生", "alias_of": "主人"}]
        rest, aliases = ai.split_aliases(raw, self.NOVEL)
        assert rest == [] and aliases == []

    def test_drops_a_name_that_is_not_in_the_document(self, add_entry):
        # 本文に無い表記を別名にしてもリンクにならない（候補語と同じ規則）
        add_entry("主人", category="登場人物")
        raw = [{"term": "珍野苦沙弥", "alias_of": "主人"}]
        rest, aliases = ai.split_aliases(raw, self.NOVEL)
        assert aliases == [] and rest == raw

    def test_leaves_other_candidates_alone(self, add_entry):
        add_entry("主人", category="登場人物")
        raw = [{"term": "苦沙弥先生", "alias_of": "主人"}, {"term": "吾輩"}]
        rest, aliases = ai.split_aliases(raw, self.NOVEL)
        assert [r["term"] for r in rest] == ["吾輩"]
        assert len(aliases) == 1


DOCS = [
    ("a.md", "サーキットブレーカーで止める。指数バックオフで待つ。"),
    ("sub/b.md", "サーキットブレーカーは有効だ。"),
    ("c.md", "無関係な文章。"),
]


class TestSampleText:
    """長い本文は**全体から間引く**。頭だけ渡すと後の章が一度も届かない。"""

    def test_short_text_is_passed_through(self):
        assert ai.sample_text("短い本文", 100) == "短い本文"

    def test_long_text_keeps_the_beginning_and_the_end(self):
        body = "書き出しの語" + "あ" * 20000 + "結びの語"
        out = ai.sample_text(body, 4000)
        assert len(out) <= 4000 + 200        # 印のぶんだけ超える
        assert "書き出しの語" in out          # 書き出しは必ず入れる
        assert "結びの語" in out              # 結びも届く（頭だけ切らない）
        assert ai.GAP_MARK in out             # 飛んだことを黙らない

    def test_windows_are_spread_over_the_whole_document(self):
        # 章ごとに違う語を置き、冒頭だけでなく後半からも採れていることを見る
        body = "\n".join(f"第{i}章の語" + "あ" * 2000 for i in range(10))
        out = ai.sample_text(body, 4000)
        found = [i for i in range(10) if f"第{i}章の語" in out]
        assert 0 in found
        assert max(found) >= 7                # 後半の章にも窓が立つ

    def test_head_only_slicing_would_have_missed_the_tail(self):
        body = "あ" * 5000 + "終章の語"
        assert "終章の語" not in body[:1000]           # 直前の実装ならここで落ちていた
        assert "終章の語" in ai.sample_text(body, 1000)


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

    def test_a_lone_long_document_gets_the_whole_budget(self):
        # 長編 1 冊だけのフォルダで冒頭 3000 字しか読めない、を防ぐ
        combined, _, _ = ai.combine_documents([("novel.epub", "あ" * 100_000)])
        assert len(combined) > ai.PER_FILE_CHARS * 3


class TestExtractFromDocuments:
    async def _extract(self, monkeypatch, response: str, **kwargs):
        monkeypatch.setattr(ai, "_generate", lambda prompt, **_: response)
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
        monkeypatch.setattr(ai, "_generate", lambda prompt, **_: '[{"term": "サーキットブレーカー"}]')
        res = await ai.extract_terms_from_documents(docs)
        assert [c["term"] for c in res["candidates"]] == ["サーキットブレーカー"]

    @pytest.mark.anyio
    async def test_no_documents_is_an_error(self, monkeypatch):
        monkeypatch.setattr(ai, "_generate", lambda prompt: "[]")
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


# --------------------------------------------------------------------------- #
# 関係の下書き
#
# 関係のデータ構造だけあっても 1 本ずつ手で書くことになり、図が空のまま終わる。
# ここが埋める側。**AI の申告をそのまま信じない**部分を主に見る。
# --------------------------------------------------------------------------- #

@pytest.fixture
def cast(add_entry):
    giovanni = add_entry("ジョバンニ", category="登場人物", summary="主人公。")
    campanella = add_entry("カムパネルラ", category="登場人物", summary="友人。")
    return giovanni, campanella


class TestRelationsPrompt:
    def test_lists_only_the_target_terms(self, cast):
        from glosspop import store
        prompt = ai.build_relations_prompt(store.load_all(), "本文")
        assert "ジョバンニ" in prompt and "カムパネルラ" in prompt
        # 用語を作る作業ではないと明示する（AI は平気で新しい名前を足す）
        assert "一覧に無い語を勝手に足さないこと" in prompt

    def test_forbids_reveal_when_only_the_first_scene_is_given(self, cast):
        from glosspop import store
        entries = store.load_all()
        first = ai.build_relations_prompt(entries, "本文", spoiler="first")
        assert "ネタバレの禁止" in first
        assert "**必ず空文字にすること**" in first
        full = ai.build_relations_prompt(entries, "本文", spoiler="full")
        assert "ネタバレの禁止" not in full

    def test_lists_pairs_that_already_have_a_relation(self, cast):
        from glosspop import store
        from glosspop.models import EntryDraft
        giovanni, campanella = cast
        store.save(
            EntryDraft(term=giovanni.term, category=giovanni.category,
                       relations=[{"to": campanella.ref, "label": "親友"}]),
            ref=giovanni.ref,
        )
        entries = store.load_all()
        prompt = ai.build_relations_prompt(entries, "本文", existing=ai.existing_pairs(entries, entries))
        assert "すでに書かれている関係" in prompt
        assert "ジョバンニ—カムパネルラ" in prompt


class TestStyle:
    """文体（口調）の指定。**効く範囲を毎回書き添えているか**を見張る。"""

    def test_no_block_when_nothing_is_set(self):
        assert ai.style() == ""
        assert "文体（口調）" not in ai.build_prompt("冪等")

    def test_the_setting_reaches_the_draft_prompt(self):
        config.save_settings({"ai_style": "講談調で"})
        prompt = ai.build_prompt("冪等")
        assert "文体（口調）" in prompt and "講談調で" in prompt

    def test_the_environment_wins_over_the_setting(self, monkeypatch):
        config.save_settings({"ai_style": "設定ファイル側"})
        monkeypatch.setenv(ai.STYLE_ENV, "環境変数側")
        assert ai.style() == "環境変数側"
        assert ai.describe_style()["style_source"] == "env"

    def test_a_long_指定_is_cut(self, monkeypatch):
        monkeypatch.setenv(ai.STYLE_ENV, "あ" * (ai.STYLE_MAX_CHARS + 50))
        assert len(ai.style()) == ai.STYLE_MAX_CHARS

    def test_the_draft_prompt_protects_the_headword(self):
        """**用語名やカテゴリを崩されると候補が丸ごと消える**（照合で落ちる）。"""
        config.save_settings({"ai_style": "講談調で"})
        prompt = ai.build_prompt("冪等")
        assert "`summary` `definition` `examples` の中身だけ" in prompt
        assert "`term`" in prompt and "`category`" in prompt

    def test_the_relations_prompt_protects_both_sides(self, cast):
        """``from`` / ``to`` が崩れると `filter_relations()` が全部落とす。"""
        from glosspop import store
        config.save_settings({"ai_style": "予告状風に"})
        prompt = ai.build_relations_prompt(store.load_all(), "本文")
        assert "予告状風に" in prompt
        assert "`label` `back` の中身だけ" in prompt
        assert "`from` `to` `rank` `reveal`" in prompt

    def test_extraction_is_left_alone(self):
        """抽出が返すのは**本文の表記そのまま**なので、文体を混ぜない。"""
        config.save_settings({"ai_style": "講談調で"})
        assert "文体（口調）" not in ai.build_extract_prompt(TEXT, limit=5)


class TestRewrite:
    """登録済みの語の書き直し。**文体を変えたあとに使う経路。**"""

    def test_the_current_text_is_handed_over(self):
        prompt = ai.build_prompt("冪等", current="何度実行しても同じ結果になること。")
        assert "いまの説明" in prompt
        assert "何度実行しても同じ結果になること。" in prompt

    def test_it_forbids_inventing_facts(self):
        """渡した説明が唯一の情報源になりうる（選択テキストも文書も手元に無い）。"""
        prompt = ai.build_prompt("冪等", current="何度実行しても同じ結果になること。")
        assert "書かれている事実は変えないでください" in prompt
        assert "一般論に置き換えたりしないこと" in prompt

    def test_nothing_is_added_without_it(self):
        assert "いまの説明" not in ai.build_prompt("冪等")

    def test_the_style_still_applies(self):
        config.save_settings({"ai_style": "講談調で"})
        prompt = ai.build_prompt("冪等", current="何度実行しても同じ結果になること。")
        assert "講談調で" in prompt and "いまの説明" in prompt


class TestFolderStyle:
    """フォルダごとの文体。**口調は作品につく**ので、辞書と同じ場所に置く。"""

    @pytest.fixture
    def folder(self, tmp_path):
        config.set_content_dir(tmp_path)
        yield tmp_path
        config.set_content_dir(None)

    def _write(self, root, text):
        (root / ".glosspop").mkdir(parents=True, exist_ok=True)
        (root / ".glosspop" / "style.md").write_text(text, encoding="utf-8")

    def test_the_folder_wins_over_the_global_setting(self, folder):
        config.save_settings({"ai_style": "全体の口調"})
        self._write(folder, "この作品の口調")
        assert ai.style() == "この作品の口調"
        assert ai.describe_style()["style_source"] == "folder"

    def test_it_falls_back_to_the_global_setting(self, folder):
        config.save_settings({"ai_style": "全体の口調"})
        assert ai.style() == "全体の口調"

    def test_the_environment_still_wins(self, folder, monkeypatch):
        self._write(folder, "この作品の口調")
        monkeypatch.setenv(ai.STYLE_ENV, "環境変数側")
        assert ai.style() == "環境変数側"

    def test_an_ancestor_folder_is_used(self, folder):
        """1 巻 2 巻を分けていても、作品フォルダに 1 つ置けば効く（辞書と同じ）。"""
        self._write(folder, "作品の口調")
        volume = folder / "第1巻"
        volume.mkdir()
        config.set_content_dir(volume)
        assert ai.style() == "作品の口調"
        info = ai.describe_style()
        # **どこのものが効いているかを画面に出せること**（黙って遠い口調を効かせない）
        assert info["style_folder_is_ancestor"] is True
        assert info["style_folder_label"] == folder.name

    def test_an_edit_outside_the_app_is_picked_up(self, folder):
        """覚え込まない —— エディタで直接書き換えてよいのがこの辞書の売り。"""
        self._write(folder, "はじめの口調")
        assert ai.style() == "はじめの口調"
        self._write(folder, "書き換えた口調")
        assert ai.style() == "書き換えた口調"

    def test_saving_creates_the_file_only_when_asked(self, folder):
        ai.describe_style()                       # 読むだけでは作らない
        assert not (folder / ".glosspop").exists()
        ai.save_style("local", "講談調で")
        assert (folder / ".glosspop" / "style.md").read_text(encoding="utf-8").strip() == "講談調で"

    def test_saving_an_empty_style_removes_the_file(self, folder):
        self._write(folder, "講談調で")
        ai.save_style("local", "")
        assert not (folder / ".glosspop" / "style.md").exists()
        assert (folder / ".glosspop").is_dir()    # 辞書が入っているかもしれない

    def test_a_hand_written_file_over_the_limit_is_cut(self, folder):
        self._write(folder, "あ" * (ai.STYLE_MAX_CHARS + 50))
        assert len(ai.style()) == ai.STYLE_MAX_CHARS
        # 画面で「超えた分は使わない」と言えるよう、生の値も返す
        assert len(ai.describe_style()["style_folder"]) == ai.STYLE_MAX_CHARS + 50

    def test_it_reaches_the_prompt(self, folder):
        self._write(folder, "怪盗の予告状のように")
        assert "怪盗の予告状のように" in ai.build_prompt("冪等")


class TestFilterRelations:
    def _filter(self, raw, **kwargs):
        from glosspop import store
        entries = store.load_all()
        kwargs.setdefault("scope", entries)
        kwargs.setdefault("limit", 10)
        return ai.filter_relations(raw, entries, **kwargs)

    def test_resolves_both_ends_to_refs(self, cast):
        giovanni, campanella = cast
        kept, _ = self._filter([
            {"from": "ジョバンニ", "to": "カムパネルラ", "label": "親友", "back": "親友"}
        ])
        assert kept[0]["from_ref"] == giovanni.ref
        assert kept[0]["to_ref"] == campanella.ref
        assert kept[0]["mutual"] is True

    def test_drops_a_term_that_is_not_registered(self, cast):
        kept, dropped = self._filter([
            {"from": "ジョバンニ", "to": "知らない人", "label": "兄"}
        ])
        assert kept == []
        assert "登録された用語ではありません" in dropped[0]["reason"]

    def test_drops_a_self_relation(self, cast):
        kept, dropped = self._filter([
            {"from": "ジョバンニ", "to": "ジョバンニ", "label": "自分"}
        ])
        assert kept == [] and dropped[0]["reason"] == "自分自身への関係"

    def test_drops_a_pair_that_already_has_a_relation(self, cast):
        from glosspop import store
        from glosspop.models import EntryDraft
        giovanni, campanella = cast
        store.save(
            EntryDraft(term=giovanni.term, category=giovanni.category,
                       relations=[{"to": campanella.ref, "label": "親友"}]),
            ref=giovanni.ref,
        )
        # 向きを逆にしても同じ組とみなすこと（2 本目の辺を生やさない）
        kept, dropped = self._filter([
            {"from": "カムパネルラ", "to": "ジョバンニ", "label": "親友"}
        ])
        assert kept == [] and dropped[0]["reason"] == "すでに関係が書かれています"

    def test_drops_the_same_pair_twice(self, cast):
        kept, dropped = self._filter([
            {"from": "ジョバンニ", "to": "カムパネルラ", "label": "親友"},
            {"from": "カムパネルラ", "to": "ジョバンニ", "label": "親友"},
        ])
        assert len(kept) == 1
        assert dropped[0]["reason"] == "同じ組が 2 回挙がりました"

    def test_strips_reveal_when_not_allowed(self, cast):
        kept, _ = self._filter(
            [{"from": "ジョバンニ", "to": "カムパネルラ", "label": "実は兄弟", "reveal": "第9章"}],
            allow_reveal=False,
        )
        assert kept[0]["reveal"] == ""

    def test_normalizes_rank(self, cast):
        kept, _ = self._filter([
            {"from": "ジョバンニ", "to": "カムパネルラ", "label": "弟子", "rank": "上位"}
        ])
        assert kept[0]["rank"] == "上"

    def test_respects_the_limit(self, add_entry, cast):
        add_entry("ザネリ", category="登場人物")
        kept, dropped = self._filter([
            {"from": "ジョバンニ", "to": "カムパネルラ", "label": "親友"},
            {"from": "ジョバンニ", "to": "ザネリ", "label": "同級生"},
        ], limit=1)
        assert len(kept) == 1 and "上限" in dropped[0]["reason"]

    def test_keeps_only_the_focused_entry(self, cast, add_entry):
        """用語ページからの下書きは、その語が端に居る関係だけを通す。"""
        giovanni, _ = cast
        add_entry("ザネリ", category="登場人物")
        kept, dropped = self._filter(
            [
                {"from": "ジョバンニ", "to": "カムパネルラ", "label": "親友"},
                {"from": "カムパネルラ", "to": "ザネリ", "label": "級友"},
            ],
            focus=giovanni,
        )
        assert [k["to_term"] for k in kept] == ["カムパネルラ"]
        assert "ジョバンニ" in dropped[0]["reason"]

    def test_the_focused_name_beats_someone_elses_alias(self, add_entry):
        """**焦点の表記は焦点**。他のエントリが同じ別名を持っていても落とさない。

        「寒月」のページから頼んだのに、別エントリ「水島」が別名として「寒月」を
        持っているせいで `resolve` が決めきれず、**その語の関係が 1 本も残らなかった**
        （同じ人物が 2 エントリに割れている辞書では普通に起きる）。どのエントリの
        話かは画面で決まっているので、そこだけはこちらが知っている。
        """
        kangetsu = add_entry("寒月", category="登場人物")
        add_entry("水島", category="登場人物", aliases=["寒月"])
        add_entry("主人", category="登場人物")
        kept, dropped = self._filter(
            [{"from": "寒月", "to": "主人", "label": "門下生", "back": "師"}],
            focus=kangetsu,
        )
        assert [k["from_ref"] for k in kept] == [kangetsu.ref], dropped
        # 焦点を渡さないときは今までどおり「決まりません」で落ちる（勝手に寄せない）
        assert not self._filter(
            [{"from": "寒月", "to": "主人", "label": "門下生"}]
        )[0]


class TestDraftRelations:
    @pytest.mark.anyio
    async def test_needs_two_entries(self, add_entry, monkeypatch):
        add_entry("ジョバンニ", category="登場人物")
        from glosspop import store
        monkeypatch.setattr(ai, "_generate", lambda prompt: "[]")
        with pytest.raises(ai.AIError):
            await ai.draft_relations(store.load_all(), [("a.md", "本文")])

    @pytest.mark.anyio
    async def test_returns_validated_relations(self, cast, monkeypatch):
        from glosspop import store
        # 関係の下書きは持ち時間を指定して呼ぶので、差し替えも timeout を受ける
        monkeypatch.setattr(ai, "_generate", lambda prompt, **_: json.dumps([
            {"from": "ジョバンニ", "to": "カムパネルラ", "label": "親友", "back": "親友"},
            {"from": "ジョバンニ", "to": "存在しない人", "label": "兄"},
        ]))
        docs = [("a.md", "ジョバンニとカムパネルラは友達だった。")]
        result = await ai.draft_relations(store.load_all(), docs)
        assert [r["to_term"] for r in result["relations"]] == ["カムパネルラ"]
        assert len(result["dropped"]) == 1

    @pytest.mark.anyio
    async def test_first_scene_mode_only_sends_the_first_appearance(self, cast, monkeypatch):
        """全文を渡すと、後で明かされる関係が図に出る。"""
        from glosspop import store
        seen = {}

        def capture(prompt, **_):
            seen["prompt"] = prompt
            return "[]"

        monkeypatch.setattr(ai, "_generate", capture)
        docs = [("a.md", "ジョバンニが出た。\n\nカムパネルラが出た。\n\n実は二人は兄弟だった。")]
        await ai.draft_relations(store.load_all(), docs, spoiler="first")
        assert "実は二人は兄弟だった" not in seen["prompt"]

    @pytest.mark.anyio
    async def test_first_scene_mode_finds_the_scene_through_an_alias(self, add_entry, monkeypatch):
        """本文が別の呼び方しかしていない人物でも、初出の場面を取れること。"""
        from glosspop import store
        add_entry("主人", category="登場人物", aliases=["苦沙弥先生"])
        add_entry("吾輩", category="登場人物")
        seen = {}
        monkeypatch.setattr(ai, "_generate", lambda p, **_: seen.setdefault("prompt", p) and "[]")

        docs = [("a.md", "吾輩は猫である。\n苦沙弥先生は書斎にいる。")]
        await ai.draft_relations(store.load_all(), docs, spoiler="first")
        assert "苦沙弥先生は書斎にいる" in seen["prompt"]

    @pytest.mark.anyio
    async def test_full_mode_sends_the_scene_where_the_terms_meet(self, cast, monkeypatch):
        """関係が書いてあるのは 2 人が並ぶ場面。冒頭ではない。"""
        from glosspop import store
        seen = {}
        monkeypatch.setattr(ai, "_generate", lambda p, **_: seen.setdefault("prompt", p) and "[]")

        docs = [("a.md", "無関係な前置き。" * 3000 + "ジョバンニとカムパネルラは親友だった。")]
        await ai.draft_relations(store.load_all(), docs, spoiler="full")
        assert "ジョバンニとカムパネルラは親友だった" in seen["prompt"]


class TestFirstSceneContext:
    """初出モードで渡す本文。**全員ぶんが入ること**が要。

    前は初出窓をそのまま繋いで頭から切っていたため、19 語で 52% が落ち、
    後ろに並んだ主人・吾輩・迷亭・黒 が丸ごと消えていた（実測）。関係は 2 語が
    揃って初めて書けるので、片方が消えた時点でその関係は絶対に出てこない。
    """

    def _cast(self, add_entry, n):
        return [add_entry(f"人物{i}", category="登場人物") for i in range(n)]

    def _docs(self, n):
        # 1 人あたり長い初出窓ができるように、間に詰め物を置く
        body = "".join(f"{'あ' * 3000}人物{i}が現れた。" for i in range(n))
        return [("novel.txt", body)]

    def test_nobody_is_dropped_when_the_budget_is_tight(self, add_entry):
        from glosspop import store
        self._cast(add_entry, 12)
        text = ai.first_scene_context(self._docs(12), store.load_all(), budget=6000)
        for i in range(12):
            assert f"### 人物{i} の初出" in text, f"人物{i} の場面が落ちた"

    def test_it_stays_within_the_budget(self, add_entry):
        from glosspop import store
        self._cast(add_entry, 12)
        text = ai.first_scene_context(self._docs(12), store.load_all(), budget=6000)
        assert len(text) <= 6000            # 見出しのぶんも数える

    def test_it_never_leaves_the_first_scene(self, add_entry):
        """切り詰め方を変えても、渡すのは初出の場面の中だけ（ネタバレの約束）。"""
        from glosspop import store
        self._cast(add_entry, 4)
        docs = self._docs(4)
        entries = store.load_all()
        text = ai.first_scene_context(docs, entries, budget=4000)
        for entry in entries:
            scene = ai._first_context(docs, entry.surfaces)
            body = text.split(f"### {entry.term} の初出\n", 1)[1].split("\n\n### ", 1)[0]
            assert body in scene

    def test_it_keeps_the_part_where_the_others_appear(self, add_entry):
        """切るなら、相手の名前が並んでいるところを残す（関係が読めるのはそこ）。"""
        from glosspop import store
        add_entry("太郎", category="登場人物")
        add_entry("花子", category="登場人物")
        # 初出窓の前半は無関係、後半に 2 人が並ぶ
        docs = [("a.txt", "無関係な前置き。" * 300 + "花子は太郎に声をかけた。")]
        text = ai.first_scene_context(docs, store.load_all(), budget=800)
        assert "花子は太郎に声をかけた" in text


class TestCooccurrenceContext:
    """関係の下書きに渡す本文の選び方。"""

    def _entries(self):
        from glosspop import store
        return store.load_all()

    def test_picks_the_window_where_two_terms_appear_together(self, cast):
        filler = "遠い前置き。" * 500
        docs = [("a.md", filler + "ジョバンニはカムパネルラに声をかけた。" + filler)]
        out = ai.cooccurrence_context(docs, self._entries())
        assert "ジョバンニはカムパネルラに声をかけた" in out

    def test_ignores_places_where_only_one_term_appears(self, cast):
        docs = [("a.md", "ジョバンニだけがいる場面。" * 200)]
        assert ai.cooccurrence_context(docs, self._entries()) == ""

    def test_counts_aliases_as_the_same_entry(self, add_entry):
        add_entry("主人", category="登場人物", aliases=["苦沙弥先生"])
        add_entry("吾輩", category="登場人物")
        docs = [("a.md", "余談。" * 400 + "吾輩は苦沙弥先生の膝に乗った。")]
        out = ai.cooccurrence_context(docs, self._entries())
        assert "吾輩は苦沙弥先生の膝に乗った" in out

    def test_does_not_return_the_same_scene_twice(self, cast):
        docs = [("a.md", "ジョバンニとカムパネルラ。" * 200)]
        out = ai.cooccurrence_context(docs, self._entries(), window=100, limit=5)
        assert out.count("###") == 5      # 重なる窓は捨てて別の場面を採る

    @pytest.mark.anyio
    async def test_falls_back_when_no_pair_shares_a_scene(self, cast, monkeypatch):
        """窓が 1 つも立たないときに空の本文を渡さない（combine に落ちる）。"""
        from glosspop import store
        seen = {}
        monkeypatch.setattr(ai, "_generate", lambda p, **_: seen.setdefault("prompt", p) and "[]")
        docs = [("a.md", "ジョバンニ" + "。" * 5000 + "カムパネルラ")]
        await ai.draft_relations(store.load_all(), docs, spoiler="full")
        assert "ジョバンニ" in seen["prompt"]
