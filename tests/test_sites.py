"""URL ごとのローカル辞書（sites/<ドメイン>/<パス>/.glosspop/）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from glosspop import config, sites, store
from glosspop.app import app
from glosspop.models import EntryDraft


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


class TestPathMapping:
    def test_url_becomes_directory_segments(self):
        assert sites.split_target("https://docs.python.org/3/library/os.html") == [
            "docs.python.org", "3", "library", "os.html",
        ]

    def test_query_and_fragment_are_dropped(self):
        assert sites.split_target("https://example.com/a?b=1#c") == ["example.com", "a"]

    def test_bare_domain_and_path_work(self):
        assert sites.split_target("docs.python.org/3/library") == [
            "docs.python.org", "3", "library",
        ]

    def test_host_is_lowercased(self):
        assert sites.split_target("HTTPS://Example.COM/A")[0] == "example.com"
        assert sites.split_target("HTTPS://Example.COM/A")[1] == "A"  # パスは変えない

    def test_percent_encoding_is_decoded(self):
        assert sites.split_target("https://ja.wikipedia.org/wiki/%E9%8A%80%E6%B2%B3") == [
            "ja.wikipedia.org", "wiki", "銀河",
        ]

    @pytest.mark.parametrize("bad", ["", "https://", "not a url", "/just/a/path"])
    def test_unusable_targets_are_rejected(self, bad):
        with pytest.raises(sites.SiteError):
            sites.path_for(bad)

    def test_traversal_cannot_escape_the_sites_dir(self):
        path = sites.path_for("example.com/../../../etc")
        assert config.SITES_DIR.resolve() in path.parents

    def test_suggested_prefix_drops_the_page(self):
        assert sites.suggested_prefix("https://docs.python.org/3/library/os.html") == (
            "docs.python.org/3/library"
        )
        assert sites.suggested_prefix("https://docs.python.org/3/") == "docs.python.org/3"


class TestResolution:
    def test_longest_match_wins(self):
        sites.create("docs.python.org")
        sites.create("docs.python.org/3/library")

        root = sites.site_root("https://docs.python.org/3/library/os.html")
        assert sites.prefix_of(root) == "docs.python.org/3/library"

        # 範囲外はドメイン側の辞書に落ちる
        other = sites.site_root("https://docs.python.org/3/tutorial/intro.html")
        assert sites.prefix_of(other) == "docs.python.org"

    def test_no_dictionary_means_none(self):
        assert sites.site_root("https://example.com/a") is None

    def test_other_domains_are_not_affected(self):
        sites.create("docs.python.org")
        assert sites.site_root("https://example.com/") is None


class TestReadingContext:
    def test_url_context_switches_the_local_dictionary(self):
        # フォルダ側の辞書
        store.save(EntryDraft(term="カムパネルラ", category="登場人物", scope="local"))
        assert [e.term for e in store.load_all()] == ["カムパネルラ"]

        # URL を読み始めると、フォルダ側の辞書は効かない
        config.set_reading_url("https://docs.python.org/3/library/os.html")
        assert store.load_all() == []
        assert store.local_available() is False

        sites.create("docs.python.org/3/library")
        assert store.local_available() is True
        store.save(EntryDraft(term="デコレータ", category="Python", scope="local"))
        assert [e.term for e in store.load_all()] == ["デコレータ"]

        # フォルダに戻れば元どおり
        config.set_reading_url(None)
        assert [e.term for e in store.load_all()] == ["カムパネルラ"]

    def test_saving_without_a_dictionary_is_refused(self):
        config.set_reading_url("https://example.com/a")
        with pytest.raises(store.StoreError):
            store.save(EntryDraft(term="用語", category="テスト", scope="local"))

    def test_the_url_dictionary_is_shared_below_the_prefix(self):
        sites.create("docs.python.org/3")
        config.set_reading_url("https://docs.python.org/3/library/os.html")
        store.save(EntryDraft(term="デコレータ", category="Python", scope="local"))

        config.set_reading_url("https://docs.python.org/3/tutorial/intro.html")
        assert [e.term for e in store.load_all()] == ["デコレータ"]


class TestApi:
    def test_context_and_creation_round_trip(self, client):
        info = client.post(
            "/api/url-context", json={"url": "https://docs.python.org/3/library/os.html"}
        ).json()
        assert info["prefix"] == ""                       # まだ無い
        assert info["suggested_prefix"] == "docs.python.org/3/library"

        created = client.post("/api/url-dictionary", json={"prefix": info["suggested_prefix"]})
        assert created.status_code == 201
        assert created.json()["prefix"] == "docs.python.org/3/library"

        listed = client.get("/api/url-dictionaries").json()
        assert [d["prefix"] for d in listed] == ["docs.python.org/3/library"]

    def test_entries_are_scoped_to_the_url(self, client):
        client.post("/api/url-context", json={"url": "https://docs.python.org/3/library/os.html"})
        client.post("/api/url-dictionary", json={"prefix": "docs.python.org/3/library"})
        created = client.post(
            "/api/entries",
            json={"term": "デコレータ", "category": "Python", "scope": "local", "definition": "x"},
        )
        assert created.status_code == 201
        assert created.json()["scope"] == "local"

        # 別のサイトを読むと見えない
        client.post("/api/url-context", json={"url": "https://example.com/"})
        assert client.get("/api/entries").json() == []

    def test_opening_a_folder_leaves_the_url_context(self, client, tmp_path):
        client.post("/api/url-context", json={"url": "https://example.com/"})
        assert client.get("/api/health").json()["reading_url"] == "https://example.com/"

        folder = tmp_path / "資料"
        folder.mkdir()
        client.post("/api/content-root", json={"path": str(folder)})
        assert client.get("/api/health").json()["reading_url"] == ""

    def test_broken_prefix_is_rejected(self, client):
        assert client.post("/api/url-dictionary", json={"prefix": "  "}).status_code == 400
