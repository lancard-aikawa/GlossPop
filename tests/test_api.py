from __future__ import annotations

from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from glosspop import config
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


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["entry_count"] == 0


def test_create_then_render_adds_links(client):
    created = client.post("/api/entries", json=ENTRY)
    assert created.status_code == 201, created.text
    slug = created.json()["slug"]

    res = client.post("/api/render", json={"text": "# メモ\n\nこの操作は冪等です。", "kind": "markdown"})
    assert res.status_code == 200
    body = res.json()
    # href は percent-encode される。参照用の slug は data-gloss に生のまま入る
    assert f'href="/glossary/{quote(slug)}"' in body["html"]
    assert f'data-gloss="{slug}"' in body["html"]
    assert body["title"] == "メモ"
    assert [t["slug"] for t in body["terms"]] == [slug]


def test_alias_also_links(client):
    client.post("/api/entries", json=ENTRY)
    res = client.post("/api/render", json={"text": "idempotent な設計", "kind": "text"})
    assert 'class="gloss-link"' in res.json()["html"]


def test_duplicate_returns_409(client):
    assert client.post("/api/entries", json=ENTRY).status_code == 201
    dup = client.post("/api/entries", json=ENTRY)
    assert dup.status_code == 409
    assert "既に登録" in dup.json()["detail"]


def test_entry_detail_does_not_self_link(client):
    slug = client.post("/api/entries", json=ENTRY).json()["slug"]
    detail = client.get(f"/api/entries/{slug}").json()
    assert detail["path_label"] == "プログラミング / API"
    assert 'class="gloss-link"' not in detail["definition_html"]
    # 本文中のコードスパンは崩れない
    assert "<code>PUT</code>" in detail["definition_html"]


def test_lookup_by_alias(client):
    slug = client.post("/api/entries", json=ENTRY).json()["slug"]
    hit = client.get("/api/lookup", params={"term": "idempotent"})
    assert hit.status_code == 200
    body = hit.json()
    assert body["found"] is True
    assert body["entry"]["slug"] == slug
    assert body["entry"]["term"] == "冪等"


def test_lookup_miss_is_not_an_error(client):
    # 登録前の重複チェックに使うので、未登録は 200 + found:false で返す
    res = client.get("/api/lookup", params={"term": "無い語"})
    assert res.status_code == 200
    assert res.json() == {"found": False, "entry": None}


def test_update_and_delete(client):
    slug = client.post("/api/entries", json=ENTRY).json()["slug"]
    updated = client.put(f"/api/entries/{slug}", json={**ENTRY, "summary": "書き換え"})
    assert updated.status_code == 200
    assert updated.json()["summary"] == "書き換え"
    assert client.delete(f"/api/entries/{slug}").status_code == 204
    assert client.get(f"/api/entries/{slug}").status_code == 404


def test_categories(client):
    client.post("/api/entries", json=ENTRY)
    tree = client.get("/api/categories").json()
    assert tree == [
        {"category": "プログラミング", "count": 1, "subcategories": [{"name": "API", "count": 1}]}
    ]


def test_content_listing_and_read(client):
    (config.CONTENT_DIR / "sub").mkdir()
    (config.CONTENT_DIR / "sub" / "note.md").write_text("# ノート\n", encoding="utf-8")
    (config.CONTENT_DIR / "ignore.png").write_bytes(b"x")

    listing = client.get("/api/content").json()
    assert [f["path"] for f in listing] == ["sub/note.md"]
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


def test_pages_are_served(client):
    for path in ("/", "/glossary", "/glossary/anything"):
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
