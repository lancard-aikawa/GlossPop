"""ローカル辞書（開いているフォルダの .glosspop/）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from glosspop import config, store
from glosspop.app import app
from glosspop.models import CategoryNameError, EntryDraft, make_ref, split_ref


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def add(term: str, *, scope: str = "global", category: str = "テスト", **kwargs):
    return store.save(EntryDraft(term=term, category=category, scope=scope, **kwargs))


class TestRef:
    def test_global_ref_is_unchanged(self):
        assert make_ref("global", "プログラミング", "冪等") == "プログラミング/冪等"
        assert split_ref("プログラミング/冪等") == ("global", "プログラミング", "冪等")

    def test_local_ref_is_prefixed(self):
        assert make_ref("local", "登場人物", "太郎") == ".local/登場人物/太郎"
        assert split_ref(".local/登場人物/太郎") == ("local", "登場人物", "太郎")

    def test_a_category_cannot_collide_with_the_prefix(self):
        # ".local" というカテゴリは作れないので ref は曖昧にならない
        from glosspop.models import normalize_category

        with pytest.raises(CategoryNameError):
            normalize_category(".local")

    def test_broken_ref_is_rejected(self):
        with pytest.raises(CategoryNameError):
            split_ref(".local/太郎")


class TestStorage:
    def test_local_entry_lands_in_the_open_folder(self):
        entry = add("太郎", scope="local", category="登場人物")
        path = config.CONTENT_DIR / ".glosspop" / "glossary" / "登場人物" / entry.slug
        assert path.with_suffix(".md").exists()
        assert entry.ref == f".local/登場人物/{entry.slug}"
        assert entry.is_local is True

    def test_same_term_can_exist_in_both_dictionaries(self):
        g = add("ソース", category="料理")
        loc = add("ソース", scope="local", category="料理")
        assert g.ref != loc.ref
        hits = store.find_by_surface("ソース")
        assert len(hits) == 2
        # ローカルを先に出す (吹き出しで上に来る)
        assert hits[0].is_local is True

    def test_duplicate_within_the_same_scope_is_rejected(self):
        add("太郎", scope="local", category="登場人物")
        with pytest.raises(store.StoreError):
            add("太郎", scope="local", category="登場人物")

    def test_switching_folder_switches_the_local_dictionary(self, tmp_path):
        add("太郎", scope="local", category="登場人物")
        assert [e.term for e in store.load_all()] == ["太郎"]

        other = tmp_path / "別の作品"
        other.mkdir()
        config.set_content_dir(other)
        # 別フォルダなので見えない (キャッシュも持ち越さない)
        assert store.load_all() == []

        add("花子", scope="local", category="登場人物")
        assert [e.term for e in store.load_all()] == ["花子"]

    def test_local_category_does_not_touch_the_global_master(self):
        from glosspop import categories

        add("太郎", scope="local", category="登場人物")
        assert "登場人物" not in categories.names()
        tree = {(n["scope"], n["category"]) for n in store.category_tree()}
        assert ("local", "登場人物") in tree

    def test_local_entry_is_marked_in_the_label(self):
        entry = add("太郎", scope="local", category="登場人物", subcategory="主要")
        assert entry.path_label == "📁 登場人物 / 主要"
        assert add("冪等").path_label == "テスト"


class TestApi:
    ENTRY = {"term": "太郎", "category": "登場人物", "summary": "主人公", "definition": "主人公。"}

    def test_create_read_and_delete_local_entry(self, client):
        created = client.post("/api/entries", json={**self.ENTRY, "scope": "local"})
        assert created.status_code == 201
        ref = created.json()["ref"]
        assert ref.startswith(".local/")
        assert created.json()["scope"] == "local"
        assert created.json()["url"] == "/glossary/.local/%E7%99%BB%E5%A0%B4%E4%BA%BA%E7%89%A9/%E5%A4%AA%E9%83%8E"

        assert client.get(f"/api/entries/{ref}").json()["term"] == "太郎"
        assert client.delete(f"/api/entries/{ref}").status_code == 204
        assert client.get(f"/api/entries/{ref}").status_code == 404

    def test_local_entry_links_in_rendered_text(self, client):
        client.post("/api/entries", json={**self.ENTRY, "scope": "local"})
        res = client.post("/api/render", json={"text": "太郎は走った。", "kind": "text"}).json()
        assert 'class="gloss-link"' in res["html"]
        assert res["terms"][0]["scope"] == "local"

    def test_listing_can_filter_by_scope(self, client):
        client.post("/api/entries", json={**self.ENTRY, "scope": "local"})
        client.post("/api/entries", json={"term": "冪等", "category": "プログラミング"})
        assert [e["term"] for e in client.get("/api/entries", params={"scope": "local"}).json()] == ["太郎"]
        assert [e["term"] for e in client.get("/api/entries", params={"scope": "global"}).json()] == ["冪等"]

    def test_health_reports_the_local_dictionary(self, client):
        client.post("/api/entries", json={**self.ENTRY, "scope": "local"})
        health = client.get("/api/health").json()
        assert health["local_entry_count"] == 1
        assert health["local_glossary_dir"].endswith(".glosspop\\glossary") or health[
            "local_glossary_dir"
        ].endswith(".glosspop/glossary")

    def test_update_keeps_the_scope(self, client):
        ref = client.post("/api/entries", json={**self.ENTRY, "scope": "local"}).json()["ref"]
        # 本文だけ直す。scope を global で送っても勝手に移らない
        updated = client.put(f"/api/entries/{ref}", json={**self.ENTRY, "scope": "global", "summary": "書き換え"})
        assert updated.status_code == 200
        assert updated.json()["scope"] == "local"
        assert updated.json()["summary"] == "書き換え"

    def test_move_to_the_local_dictionary(self, client):
        ref = client.post("/api/entries", json={**self.ENTRY, "category": "人名"}).json()["ref"]
        moved = client.post(f"/api/move/{ref}", json={"scope": "local"})
        assert moved.status_code == 200
        assert moved.json()["ref"] == ".local/人名/太郎"
        # 移動先に在って、移動元は消えている
        assert (config.CONTENT_DIR / ".glosspop" / "glossary" / "人名" / "太郎.md").exists()
        assert not (config.GLOSSARY_DIR / "人名" / "太郎.md").exists()
        assert client.get(f"/api/entries/{ref}").status_code == 404

    def test_move_back_to_the_global_dictionary(self, client):
        ref = client.post("/api/entries", json={**self.ENTRY, "scope": "local"}).json()["ref"]
        moved = client.post(f"/api/move/{ref}", json={"scope": "global"}).json()
        assert moved["ref"] == "登場人物/太郎"
        assert moved["scope"] == "global"
        assert not (config.CONTENT_DIR / ".glosspop" / "glossary" / "登場人物" / "太郎.md").exists()

    def test_move_keeps_the_content_and_the_creation_time(self, client):
        created = client.post("/api/entries", json={**self.ENTRY, "category": "人名"}).json()
        moved = client.post(f"/api/move/{created['ref']}", json={"scope": "local"}).json()
        assert moved["definition"] == created["definition"]
        assert moved["summary"] == created["summary"]
        assert moved["created_at"] == created["created_at"]

    def test_move_can_change_the_category_and_the_scope_at_once(self, client):
        ref = client.post("/api/entries", json={**self.ENTRY, "category": "人名"}).json()["ref"]
        moved = client.post(f"/api/move/{ref}", json={"category": "登場人物", "scope": "local"}).json()
        assert moved["ref"] == ".local/登場人物/太郎"

    def test_move_to_local_does_not_touch_the_category_master(self, client):
        from glosspop import categories

        ref = client.post("/api/entries", json={**self.ENTRY, "category": "人名"}).json()["ref"]
        client.post(f"/api/move/{ref}", json={"category": "登場人物", "scope": "local"})
        # 「登場人物」はフォルダ固有のカテゴリ。マスターに残すと他の文書で邪魔になる
        assert "登場人物" not in categories.names()

    def test_move_back_registers_the_category_in_the_master(self, client):
        from glosspop import categories

        ref = client.post("/api/entries", json={**self.ENTRY, "scope": "local"}).json()["ref"]
        client.post(f"/api/move/{ref}", json={"scope": "global"})
        assert "登場人物" in categories.names()

    def test_move_is_rejected_when_the_destination_already_has_the_term(self, client):
        client.post("/api/entries", json={**self.ENTRY, "scope": "local"})
        ref = client.post("/api/entries", json=self.ENTRY).json()["ref"]
        res = client.post(f"/api/move/{ref}", json={"scope": "local"})
        assert res.status_code == 409
        assert "既に登録されています" in res.json()["detail"]
        # 失敗したら元のまま残っていること
        assert client.get(f"/api/entries/{ref}").status_code == 200

    def test_move_to_local_is_rejected_without_a_local_dictionary(self, client):
        """URL を読んでいて、その URL の辞書がまだ無いとき。

        ローカル辞書が無い状態で移すと行き先が決まらない。URL 側は辞書を勝手に
        作らないので、ここは必ず起きうる。
        """
        ref = client.post("/api/entries", json=self.ENTRY).json()["ref"]
        client.post("/api/url-context", json={"url": "https://docs.python.org/3/"})
        res = client.post(f"/api/move/{ref}", json={"scope": "local"})
        assert res.status_code == 409
        assert "辞書に移せません" in res.json()["detail"]
        assert client.get(f"/api/entries/{ref}").status_code == 200

    def test_move_lands_in_the_url_dictionary_when_reading_a_url(self, client):
        ref = client.post("/api/entries", json=self.ENTRY).json()["ref"]
        client.post("/api/url-context", json={"url": "https://docs.python.org/3/library/os.html"})
        client.post("/api/url-dictionary", json={"prefix": "docs.python.org/3/library"})
        moved = client.post(f"/api/move/{ref}", json={"scope": "local"}).json()
        assert moved["ref"].startswith(".local/")
        assert "sites" in moved["path"] and "docs.python.org" in moved["path"]

    def test_category_only_move_still_works(self, client):
        """scope を送らない従来のリクエストが壊れていないこと。"""
        ref = client.post("/api/entries", json={**self.ENTRY, "scope": "local"}).json()["ref"]
        moved = client.post(f"/api/move/{ref}", json={"category": "脇役"}).json()
        assert moved["ref"] == ".local/脇役/太郎"   # ローカルのまま

    def test_local_and_global_same_term_are_both_returned(self, client):
        client.post("/api/entries", json={**self.ENTRY, "scope": "local"})
        client.post("/api/entries", json={**self.ENTRY, "category": "人名"})
        body = client.get("/api/lookup", params={"term": "太郎"}).json()
        assert body["count"] == 2
        assert body["entries"][0]["scope"] == "local"  # ローカルが先


class TestDraftDoesNotPolluteTheMaster:
    """ローカル辞書に入れるつもりの下書きで、グローバルのカテゴリマスターを汚さない。"""

    def _fake_ai(self, monkeypatch):
        from glosspop import ai, config as cfg

        monkeypatch.setattr(ai, "_generate", lambda prompt: '{"term": "九条ミナ", "category": "登場人物"}')
        monkeypatch.setattr(cfg, "CLAUDE_BIN", "claude")

    def test_local_draft_leaves_the_master_alone(self, client, monkeypatch):
        from glosspop import categories

        self._fake_ai(monkeypatch)
        res = client.post(
            "/api/ai/draft",
            json={"term": "九条ミナ", "spoiler": "full", "scope": "local"},
        ).json()
        assert res["draft"]["category"] == "登場人物"
        assert res["registered_category"] is None
        assert "登場人物" not in categories.names()

    def test_global_draft_still_registers_it(self, client, monkeypatch):
        from glosspop import categories

        self._fake_ai(monkeypatch)
        res = client.post("/api/ai/draft", json={"term": "九条ミナ", "spoiler": "full"}).json()
        assert res["registered_category"] == "登場人物"
        assert "登場人物" in categories.names()


class TestCategoryManagement:
    """カテゴリの改名・削除は**同じ辞書の中だけ**で完結すること。

    グローバル決め打ちだったため、ローカルのカテゴリを消そうとして
    **同名のグローバル側が消えた**。スコープを跨いで触らないのが要点。
    """

    def test_deleting_a_local_category_does_not_touch_the_global_one(self, tmp_path):
        from glosspop import categories

        folder = tmp_path / "小説"
        folder.mkdir()
        config.set_content_dir(folder)
        categories.ensure("登場人物")                    # グローバルに空で作っておく
        add("ザネリ", scope="local", category="登場人物")

        # ローカルには用語が入っているので消せない（グローバルは巻き添えにしない）
        with pytest.raises(store.StoreError, match="まだ用語があります"):
            store.delete_category("登場人物", "local")
        assert "登場人物" in categories.names()
        assert [e.ref for e in store.load_all()] == [".local/登場人物/ザネリ"]

    def test_an_empty_local_category_can_be_deleted(self, tmp_path):
        folder = tmp_path / "小説"
        folder.mkdir()
        config.set_content_dir(folder)
        entry = add("ザネリ", scope="local", category="登場人物")
        store.delete(entry.ref)

        store.delete_category("登場人物", "local")
        assert not (folder / ".glosspop" / "glossary" / "登場人物").exists()

    def test_renaming_a_local_category_moves_the_directory_only(self, tmp_path):
        from glosspop import categories

        folder = tmp_path / "小説"
        folder.mkdir()
        config.set_content_dir(folder)
        add("ザネリ", scope="local", category="登場人物")

        assert store.rename_category("登場人物", "人物", "local") == 1
        assert [e.ref for e in store.load_all()] == [".local/人物/ザネリ"]
        # マスターはグローバルのもの。ローカルの改名では触らない
        assert categories.names() == []

    def test_renaming_a_missing_local_category_is_an_error(self, tmp_path):
        folder = tmp_path / "小説"
        folder.mkdir()
        config.set_content_dir(folder)
        with pytest.raises(store.StoreError, match="がありません"):
            store.rename_category("無い", "別名", "local")

    def test_global_management_is_unchanged(self):
        from glosspop import categories

        add("冪等", category="プログラミング")
        assert store.rename_category("プログラミング", "設計") == 1
        assert categories.names() == ["設計"]
        assert [e.ref for e in store.load_all()] == ["設計/冪等"]

    def test_the_api_takes_a_scope(self, client, tmp_path):
        folder = tmp_path / "小説"
        folder.mkdir()
        config.set_content_dir(folder)
        add("ザネリ", scope="local", category="登場人物")

        res = client.put("/api/categories/登場人物", params={"scope": "local"},
                         json={"name": "人物"})
        assert res.status_code == 200, res.text
        assert res.json()["scope"] == "local"
        assert [e.ref for e in store.load_all()] == [".local/人物/ザネリ"]

    def test_the_api_refuses_to_delete_a_local_category_that_has_entries(self, client, tmp_path):
        folder = tmp_path / "小説"
        folder.mkdir()
        config.set_content_dir(folder)
        add("ザネリ", scope="local", category="登場人物")

        res = client.delete("/api/categories/登場人物", params={"scope": "local"})
        assert res.status_code == 400, res.text
        assert "まだ用語があります" in res.json()["detail"]
        assert [e.ref for e in store.load_all()] == [".local/登場人物/ザネリ"]


class TestSharedAcrossVolumes:
    """1 巻 2 巻でローカル辞書を共有する（いちばん近い .glosspop を使う）。"""

    def _series(self, tmp_path):
        series = tmp_path / "銀河鉄道の夜"
        for volume in ("1巻", "2巻"):
            (series / volume).mkdir(parents=True)
        return series

    def test_volume_folder_uses_the_series_dictionary(self, tmp_path):
        series = self._series(tmp_path)
        (series / ".glosspop").mkdir()          # 作品フォルダに 1 つ置く

        config.set_content_dir(series / "1巻")
        add("カムパネルラ", scope="local", category="登場人物")
        assert (series / ".glosspop" / "glossary" / "登場人物" / "カムパネルラ.md").exists()

        # 2 巻を開いても同じ辞書が見える
        config.set_content_dir(series / "2巻")
        assert [e.term for e in store.load_all()] == ["カムパネルラ"]

    def test_a_nearer_dictionary_wins(self, tmp_path):
        series = self._series(tmp_path)
        (series / ".glosspop").mkdir()
        (series / "2巻" / ".glosspop").mkdir()   # 巻ごとに分けたい場合

        config.set_content_dir(series / "1巻")
        add("共通の語", scope="local", category="登場人物")
        config.set_content_dir(series / "2巻")
        add("2巻だけの語", scope="local", category="登場人物")

        assert [e.term for e in store.load_all()] == ["2巻だけの語"]
        config.set_content_dir(series / "1巻")
        assert [e.term for e in store.load_all()] == ["共通の語"]

    def test_without_any_dictionary_it_is_created_in_the_open_folder(self, tmp_path):
        series = self._series(tmp_path)
        config.set_content_dir(series / "1巻")
        add("太郎", scope="local", category="登場人物")
        assert (series / "1巻" / ".glosspop").is_dir()
        assert not (series / ".glosspop").exists()

    def test_the_search_stops_after_the_configured_depth(self, tmp_path, monkeypatch):
        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        (tmp_path / ".glosspop").mkdir()

        monkeypatch.setattr(config, "LOCAL_SEARCH_DEPTH", 1)
        config.set_content_dir(deep)
        # 1 段しか遡らないので tmp_path の辞書は掴まない
        assert config.local_root() == deep

        monkeypatch.setattr(config, "LOCAL_SEARCH_DEPTH", 6)
        assert config.local_root() == tmp_path

    def test_listing_says_where_the_dictionary_is(self, client, tmp_path):
        series = self._series(tmp_path)
        (series / ".glosspop").mkdir()
        client.post("/api/content-root", json={"path": str(series / "1巻")})

        listing = client.get("/api/content").json()
        assert listing["local_is_ancestor"] is True
        assert listing["local_dir"] == str(series / ".glosspop" / "glossary")


class TestAutoScope:
    """保存先を AI に選ばせる（scope="auto"）。"""

    def _ai(self, monkeypatch, response: str, seen: dict | None = None):
        from glosspop import ai, config as cfg

        def fake_run(prompt: str) -> str:
            if seen is not None:
                seen["prompt"] = prompt
            return response

        monkeypatch.setattr(ai, "_generate", fake_run)
        monkeypatch.setattr(cfg, "CLAUDE_BIN", "claude")

    def test_ai_choice_is_used(self, client, monkeypatch):
        seen = {}
        self._ai(monkeypatch, '{"term": "カムパネルラ", "category": "登場人物", "scope": "local"}', seen)
        res = client.post("/api/ai/draft", json={"term": "カムパネルラ", "spoiler": "full"}).json()
        assert res["draft"]["scope"] == "local"
        # 保存先を選ばせるときだけプロンプトに説明が入る
        assert "保存先 (scope)" in seen["prompt"]

    def test_explicit_scope_overrides_the_ai(self, client, monkeypatch):
        seen = {}
        self._ai(monkeypatch, '{"term": "冪等", "category": "プログラミング", "scope": "local"}', seen)
        res = client.post(
            "/api/ai/draft", json={"term": "冪等", "spoiler": "full", "scope": "global"}
        ).json()
        assert res["draft"]["scope"] == "global"
        assert "保存先 (scope)" not in seen["prompt"]   # 選ばせないなら聞かない

    def test_local_choice_registers_no_category(self, client, monkeypatch):
        from glosspop import categories

        self._ai(monkeypatch, '{"term": "ザネリ", "category": "登場人物", "scope": "local"}')
        res = client.post("/api/ai/draft", json={"term": "ザネリ", "spoiler": "full"}).json()
        assert res["registered_category"] is None
        assert "登場人物" not in categories.names()

    def test_global_choice_registers_the_category(self, client, monkeypatch):
        from glosspop import categories

        self._ai(monkeypatch, '{"term": "冪等", "category": "プログラミング", "scope": "global"}')
        res = client.post("/api/ai/draft", json={"term": "冪等", "spoiler": "full"}).json()
        assert res["registered_category"] == "プログラミング"
        assert "プログラミング" in categories.names()

    def test_missing_answer_falls_back_to_global(self, client, monkeypatch):
        self._ai(monkeypatch, '{"term": "冪等", "category": "プログラミング"}')
        res = client.post("/api/ai/draft", json={"term": "冪等", "spoiler": "full"}).json()
        assert res["draft"]["scope"] == "global"

    def test_position_only_does_not_ask_the_ai(self, client, monkeypatch):
        from glosspop import config as cfg

        monkeypatch.setattr(cfg, "CLAUDE_BIN", "")
        res = client.post("/api/ai/draft", json={"term": "ザネリ", "spoiler": "position"}).json()
        assert res["draft"]["scope"] == "global"   # 判断材料が無いので全体に置く
