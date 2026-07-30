from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from glosspop import ai, categories, config
from glosspop.app import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


ENTRY = {
    "term": "冪等",
    "reading": "べきとう",
    "aliases": ["idempotent"],
    "category": "プログラミング",
    "subcategory": "API",
    "summary": "何度実行しても結果が同じであること。",
    "definition": "同じ操作を繰り返しても状態が変わらない性質。\n\n`PUT` は冪等。",
}


def ref_path(ref: str) -> str:
    return "/".join(quote(part) for part in ref.split("/"))


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["entry_count"] == 0


def test_create_then_render_adds_links(client):
    created = client.post("/api/entries", json=ENTRY)
    assert created.status_code == 201, created.text
    ref = created.json()["ref"]
    assert ref == "プログラミング/冪等"

    res = client.post("/api/render", json={"text": "# メモ\n\nこの操作は冪等です。", "kind": "markdown"})
    assert res.status_code == 200
    body = res.json()
    assert f'href="/glossary/{ref_path(ref)}"' in body["html"]
    assert 'data-gloss="冪等"' in body["html"]
    assert body["title"] == "メモ"
    assert [t["ref"] for t in body["terms"]] == [ref]


def test_alias_also_links(client):
    client.post("/api/entries", json=ENTRY)
    res = client.post("/api/render", json={"text": "idempotent な設計", "kind": "text"})
    assert 'class="gloss-link"' in res.json()["html"]


def test_duplicate_in_same_category_returns_409(client):
    assert client.post("/api/entries", json=ENTRY).status_code == 201
    dup = client.post("/api/entries", json=ENTRY)
    assert dup.status_code == 409
    assert "既に登録" in dup.json()["detail"]


def test_same_term_other_category_is_allowed(client):
    client.post("/api/entries", json=ENTRY)
    other = client.post("/api/entries", json={**ENTRY, "category": "数学", "subcategory": ""})
    assert other.status_code == 201
    assert other.json()["ref"] == "数学/冪等"


def test_detail_does_not_link_same_term_in_another_category(client):
    # プログラミングの「ソース」本文に出てくる「ソース」が料理へ飛ばないこと
    client.post("/api/entries", json={
        "term": "ソース", "category": "料理", "summary": "調味料", "definition": "調味料。",
    })
    ref = client.post("/api/entries", json={
        "term": "ソース", "category": "プログラミング",
        "definition": "人が書くプログラムそのもの。ソースコードとも言う。",
    }).json()["ref"]
    detail = client.get(f"/api/entries/{ref_path(ref)}").json()
    assert 'class="gloss-link"' not in detail["definition_html"]


def test_entry_detail_does_not_self_link(client):
    ref = client.post("/api/entries", json=ENTRY).json()["ref"]
    detail = client.get(f"/api/entries/{ref_path(ref)}").json()
    assert detail["path_label"] == "プログラミング / API"
    assert detail["url"] == f"/glossary/{ref_path(ref)}"
    assert 'class="gloss-link"' not in detail["definition_html"]
    # 本文中のコードスパンは崩れない
    assert "<code>PUT</code>" in detail["definition_html"]


def test_lookup_returns_every_category(client):
    a = client.post("/api/entries", json=ENTRY).json()["ref"]
    b = client.post("/api/entries", json={**ENTRY, "category": "数学"}).json()["ref"]
    body = client.get("/api/lookup", params={"term": "idempotent"}).json()
    assert body["found"] is True
    assert body["count"] == 2
    assert {e["ref"] for e in body["entries"]} == {a, b}


def test_lookup_miss_is_not_an_error(client):
    # 登録前の重複チェックに使うので、未登録は 200 + found:false で返す
    res = client.get("/api/lookup", params={"term": "無い語"})
    assert res.status_code == 200
    assert res.json() == {"term": "無い語", "found": False, "count": 0, "entries": []}


def test_update_and_delete(client):
    ref = client.post("/api/entries", json=ENTRY).json()["ref"]
    updated = client.put(f"/api/entries/{ref_path(ref)}", json={**ENTRY, "summary": "書き換え"})
    assert updated.status_code == 200
    assert updated.json()["summary"] == "書き換え"
    assert updated.json()["ref"] == ref
    assert client.delete(f"/api/entries/{ref_path(ref)}").status_code == 204
    assert client.get(f"/api/entries/{ref_path(ref)}").status_code == 404


def test_move_entry_between_categories(client):
    ref = client.post("/api/entries", json=ENTRY).json()["ref"]
    moved = client.post(f"/api/move/{ref_path(ref)}", json={"category": "数学"})
    assert moved.status_code == 200
    assert moved.json()["ref"] == "数学/冪等"
    assert client.get(f"/api/entries/{ref_path(ref)}").status_code == 404
    assert (config.GLOSSARY_DIR / "数学" / "冪等.md").exists()


def test_update_with_new_category_also_moves(client):
    ref = client.post("/api/entries", json=ENTRY).json()["ref"]
    res = client.put(f"/api/entries/{ref_path(ref)}", json={**ENTRY, "category": "数学"})
    assert res.json()["ref"] == "数学/冪等"


class TestCategories:
    def test_master_lists_empty_categories(self, client):
        assert client.post("/api/categories", json={"name": "法律"}).status_code == 201
        tree = {n["category"]: n for n in client.get("/api/categories").json()}
        assert tree["法律"]["count"] == 0

    def test_invalid_name_is_422(self, client):
        res = client.post("/api/categories", json={"name": "a/b"})
        assert res.status_code == 422
        assert "使えない文字" in res.json()["detail"]

    def test_reserved_name_is_422(self, client):
        assert client.post("/api/categories", json={"name": "CON"}).status_code == 422

    def test_rename_moves_entries(self, client):
        client.post("/api/entries", json=ENTRY)
        res = client.put("/api/categories/プログラミング", json={"name": "開発"})
        assert res.status_code == 200
        assert client.get(f"/api/entries/{ref_path('開発/冪等')}").status_code == 200
        assert (config.GLOSSARY_DIR / "開発" / "冪等.md").exists()

    def test_delete_requires_empty(self, client):
        client.post("/api/entries", json=ENTRY)
        assert client.delete("/api/categories/プログラミング").status_code == 400
        client.post("/api/categories", json={"name": "空"})
        assert client.delete("/api/categories/空").status_code == 204

    def test_created_via_entry_is_registered(self, client):
        client.post("/api/entries", json=ENTRY)
        assert "プログラミング" in categories.names()


def test_content_listing_and_read(client):
    (config.CONTENT_DIR / "sub").mkdir()
    (config.CONTENT_DIR / "sub" / "note.md").write_text("# ノート\n", encoding="utf-8")
    (config.CONTENT_DIR / "ignore.png").write_bytes(b"x")

    listing = client.get("/api/content").json()
    assert [f["path"] for f in listing["files"]] == ["sub/note.md"]
    assert listing["root"] == str(config.CONTENT_DIR)
    assert listing["is_default"] is True
    assert client.get("/api/content/sub/note.md").json()["text"] == "# ノート\n"


def test_content_path_traversal_blocked(client):
    res = client.get("/api/content/../../secret.md")
    assert res.status_code in (400, 404)


def test_plain_text_render_keeps_paragraphs(client):
    res = client.post("/api/render", json={"text": "1 行目\n2 行目\n\n次の段落", "kind": "text"})
    html = res.json()["html"]
    assert html.count("<p>") == 2
    assert "<br>" in html


def test_markdown_is_not_raw_html(client):
    res = client.post("/api/render", json={"text": "<script>alert(1)</script>", "kind": "markdown"})
    assert "<script>" not in res.json()["html"]


def test_html_kind_is_sanitized_on_render(client):
    # 取得済み HTML はクライアント経由で戻ってくるので、表示前にもう一度掃除する
    res = client.post("/api/render", json={
        "text": '<p onclick="evil()">本文</p><script>alert(1)</script>',
        "kind": "html",
    })
    html = res.json()["html"]
    assert "<script>" not in html
    assert "onclick" not in html
    assert "本文" in html


def test_pages_are_served(client):
    for path in ("/", "/glossary", "/glossary/cat/slug"):
        res = client.get(path)
        assert res.status_code == 200
        assert "GlossPop" in res.text


def test_static_and_pages_are_not_browser_cached(client):
    # 古い JS がキャッシュから出てくると原因不明の壊れ方をするので、必ず検証させる
    assert client.get("/").headers["cache-control"] == "no-cache"
    res = client.get("/static/popup.js")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-cache"
    assert res.headers.get("etag")


def test_local_html_is_rendered_with_its_title(client):
    res = client.post(
        "/api/render",
        json={
            "text": "<html><head><title>ページの題</title></head>"
                    "<body><h1>見出し</h1><p>本文</p><script>alert(1)</script></body></html>",
            "kind": "auto",
            "filename": "page.html",
        },
    )
    data = res.json()
    assert data["title"] == "ページの題"
    assert "<h1>見出し</h1>" in data["html"]
    assert "alert" not in data["html"]


def test_content_listing_includes_html(client):
    (config.CONTENT_DIR / "page.html").write_text("<h1>x</h1>", encoding="utf-8")
    assert "page.html" in [f["path"] for f in client.get("/api/content").json()["files"]]
    assert client.get("/api/content/page.html").json()["text"] == "<h1>x</h1>"


class TestContentRoot:
    """開くフォルダの切り替え。"""

    def test_switch_and_reset(self, client, tmp_path):
        other = tmp_path / "別のフォルダ"
        other.mkdir()
        (other / "外の文書.md").write_text("# 外\n", encoding="utf-8")
        (config.CONTENT_DIR / "中の文書.md").write_text("# 中\n", encoding="utf-8")

        res = client.post("/api/content-root", json={"path": str(other)}).json()
        assert res["root"] == str(other.resolve())
        assert res["is_default"] is False
        assert [f["path"] for f in res["files"]] == ["外の文書.md"]
        # 切り替え後は新しいフォルダのファイルが読める
        assert client.get("/api/content/外の文書.md").json()["text"] == "# 外\n"
        assert client.get("/api/health").json()["content_dir"] == str(other.resolve())

        back = client.post("/api/content-root", json={"path": ""}).json()
        assert back["is_default"] is True
        assert [f["path"] for f in back["files"]] == ["中の文書.md"]

    def test_missing_folder_is_rejected(self, client, tmp_path):
        assert client.post("/api/content-root", json={"path": str(tmp_path / "無い")}).status_code == 404

    def test_file_is_not_a_folder(self, client, tmp_path):
        target = tmp_path / "ファイル.md"
        target.write_text("x", encoding="utf-8")
        assert client.post("/api/content-root", json={"path": str(target)}).status_code == 400

    def test_traversal_still_blocked_after_switch(self, client, tmp_path):
        other = tmp_path / "配下"
        other.mkdir()
        client.post("/api/content-root", json={"path": str(other)})
        assert client.get("/api/content/../../secret.md").status_code in (400, 404)

    def test_heavy_directories_are_skipped(self, client, tmp_path):
        root = tmp_path / "リポジトリ"
        (root / ".git").mkdir(parents=True)
        (root / "node_modules").mkdir()
        (root / ".git" / "内部.md").write_text("x", encoding="utf-8")
        (root / "node_modules" / "依存.md").write_text("x", encoding="utf-8")
        (root / "読む.md").write_text("x", encoding="utf-8")

        res = client.post("/api/content-root", json={"path": str(root)}).json()
        assert [f["path"] for f in res["files"]] == ["読む.md"]


class TestAIExtract:
    """候補抽出のエンドポイント。claude CLI は差し替える。"""

    def test_returns_filtered_candidates(self, client, monkeypatch, add_entry):
        add_entry("冪等", category="プログラミング")
        text = "この API は結果整合性を前提にしている。冪等な操作は安全。"

        def fake_run(prompt: str) -> str:
            assert "冪等" in prompt  # 登録済みの語は除外指示として渡っている
            return '[{"term": "結果整合性"}, {"term": "冪等"}, {"term": "無い語"}]'

        monkeypatch.setattr(ai, "_run_claude", fake_run)
        monkeypatch.setattr(config, "CLAUDE_BIN", "claude")

        res = client.post("/api/ai/extract", json={"text": text}).json()
        assert [c["term"] for c in res["candidates"]] == ["結果整合性"]
        reasons = {d["term"]: d["reason"] for d in res["dropped"]}
        assert "登録済み" in reasons["冪等"]
        assert "見つからない" in reasons["無い語"]

    def test_without_claude_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(config, "CLAUDE_BIN", "")
        assert client.post("/api/ai/extract", json={"text": "x"}).status_code == 503

    def test_empty_text_is_rejected(self, client, monkeypatch):
        monkeypatch.setattr(config, "CLAUDE_BIN", "claude")
        assert client.post("/api/ai/extract", json={"text": "  "}).status_code == 502
