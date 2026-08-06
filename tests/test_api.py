from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from glosspop import ai, categories, config, picker, store
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


class TestStayingAlive:
    """専用ウィンドウで開いたときだけ、ページの生存確認を数える。

    `serve`（ブラウザは自分で開く）で数えてしまうと、**タブを閉じただけで
    サーバが落ちる**。数えているかどうかはページ側も見て、送るのをやめる。
    """

    def test_it_is_off_unless_the_window_opened_it(self, client):
        from glosspop import watchdog

        assert client.get("/api/health").json()["window_mode"] is False
        assert client.post("/api/alive").json() == {"armed": False}
        assert watchdog.pings() == 0            # 数えてもいない

    def test_pings_are_counted_once_armed(self, client):
        from glosspop import watchdog

        watchdog.arm()
        assert client.get("/api/health").json()["window_mode"] is True
        assert client.post("/api/alive").json() == {"armed": True}
        assert watchdog.pings() == 1


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


def test_tag_filter_is_exact_unlike_free_text_search(client):
    """タグの絞り込みは完全一致。``q`` と違って本文のかすりでは引っかからない。

    用語ページの `#タグ` を `?q=` に流していたため、**タグ名がたまたま本文に出る
    別の語**まで混ざっていた。それを直すのがこの絞り込み。
    """
    client.post("/api/entries", json={**ENTRY, "tags": ["設計原則"]})
    client.post("/api/entries", json={
        "term": "副作用", "category": "プログラミング",
        "summary": "設計原則の話でよく出てくる。", "definition": "本文。", "tags": [],
    })

    tagged = client.get("/api/entries", params={"tag": "設計原則"}).json()
    assert [e["term"] for e in tagged] == ["冪等"]
    # 全文検索だと本文がかすった「副作用」まで出る（＝タグ絞り込みが要る理由）
    searched = client.get("/api/entries", params={"q": "設計原則"}).json()
    assert {e["term"] for e in searched} == {"冪等", "副作用"}


def test_tags_are_counted_without_a_master(client):
    client.post("/api/entries", json={**ENTRY, "tags": ["設計原則", "API"]})
    client.post("/api/entries", json={
        "term": "冪等", "category": "数学", "definition": "本文。", "tags": ["設計原則"],
    })
    assert client.get("/api/tags").json() == [
        {"name": "設計原則", "count": 2},
        {"name": "API", "count": 1},
    ]


def test_unknown_tag_returns_nothing(client):
    client.post("/api/entries", json={**ENTRY, "tags": ["設計原則"]})
    assert client.get("/api/entries", params={"tag": "無いタグ"}).json() == []


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

    def test_rename_alone_keeps_subcategories(self, client):
        """名前だけ変えるリクエストでサブカテゴリを巻き添えにしない。

        ``subcategories`` の既定を ``[]`` にしていたため、``{"name": ...}`` だけの
        更新で全部消えていた（ビューアは毎回現在値を送るので表面化せず、CLI や
        スキルから叩いたときだけ黙って消える）。
        """
        client.post("/api/categories", json={"name": "音楽", "subcategories": ["和声", "楽器"]})
        res = client.put("/api/categories/音楽", json={"name": "音楽理論"})
        assert res.status_code == 200
        assert res.json()["subcategories"] == ["和声", "楽器"]
        assert categories.get("音楽理論").subcategories == ["和声", "楽器"]

    def test_empty_subcategories_still_clears_them(self, client):
        """``[]`` を明示したときは従来どおり全部消す（「指定なし」とは別物）。"""
        client.post("/api/categories", json={"name": "音楽", "subcategories": ["和声"]})
        res = client.put("/api/categories/音楽", json={"name": "音楽", "subcategories": []})
        assert res.json()["subcategories"] == []

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


def test_export_downloads_a_zip(client):
    client.post("/api/entries", json=ENTRY)
    res = client.get("/api/export")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert "glosspop-glossary-" in res.headers["content-disposition"]
    assert res.content[:2] == b"PK"


def test_export_can_take_one_category(client):
    """一部だけ渡す。**決めるのは書き出す側だけ**（取り込む側は変えていない）。"""
    import io
    import zipfile

    client.post("/api/entries", json=ENTRY)
    client.post("/api/entries", json={"term": "ソース", "category": "料理"})

    res = client.get("/api/export", params={"category": "料理"})
    assert res.status_code == 200
    assert "-part-" in res.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        names = set(zf.namelist())
    assert "glossary/料理/ソース.md" in names
    assert not any(name.startswith("glossary/プログラミング/") for name in names)


def test_the_export_plan_says_what_would_lose_its_target(client):
    """一部だけ渡すと、渡した先で相手の居ない関係ができる。押す前に数で出す。"""
    client.post("/api/entries", json={"term": "ソース", "category": "料理"})
    client.post("/api/entries", json={
        **ENTRY, "relations": [{"to": "料理/ソース", "label": "例"}],
    })

    whole = client.get("/api/export/plan").json()
    assert whole["entries"] == 2 and whole["dangling_count"] == 0

    part = client.get("/api/export/plan", params={"category": "プログラミング"}).json()
    assert part["entries"] == 1 and part["dangling_count"] == 1
    assert part["partial"] is True


def test_import_replaces_the_glossary_and_keeps_a_backup(client):
    from glosspop import archive

    client.post("/api/entries", json=ENTRY)
    exported = client.get("/api/export").content
    client.post("/api/entries", json={**ENTRY, "term": "結果整合性"})
    assert len(client.get("/api/entries").json()) == 2

    res = client.post("/api/import-glossary", content=exported,
                      headers={"Content-Type": "application/zip"})
    assert res.status_code == 200, res.text
    assert [e["term"] for e in client.get("/api/entries").json()] == ["冪等"]
    # **再起動は要らない**（保存先は変わらないので、読み直しはサーバ側で済む）
    assert Path(res.json()["backup"]).exists()
    assert archive.BACKUP_DIR_NAME in res.json()["backup"]


def test_import_can_merge_instead_of_replacing(client):
    """併合では**手元にしか無い語が消えない**。置き換えとの違いはここだけ。"""
    client.post("/api/entries", json=ENTRY)
    exported = client.get("/api/export").content
    client.post("/api/entries", json={**ENTRY, "term": "結果整合性"})

    res = client.post("/api/import-glossary?mode=merge", content=exported,
                      headers={"Content-Type": "application/zip"})
    assert res.status_code == 200, res.text
    assert {e["term"] for e in client.get("/api/entries").json()} == {"冪等", "結果整合性"}
    assert Path(res.json()["backup"]).exists()


def test_backups_can_be_read_and_restored_one_at_a_time(client):
    """**上書きされた語は控えにしか残らない。** 画面から中を見て 1 件だけ戻せること。"""
    client.post("/api/entries", json={**ENTRY, "definition": "元の本文。"})
    exported = client.get("/api/export").content
    # 取り込みの前に控えが取られる（このときの中身が「元の本文」）
    client.post("/api/entries", json={"term": "ソース", "category": "料理"})
    client.post("/api/import-glossary?mode=replace", content=exported,
                headers={"Content-Type": "application/zip"})

    listed = client.get("/api/backups").json()
    assert len(listed["items"]) == 1 and listed["total_bytes"] > 0
    name = listed["items"][0]["name"]

    inside = client.get(f"/api/backups/{name}").json()
    refs = {e["ref"]: e for e in inside["entries"]}
    assert "料理/ソース" in refs and refs["料理/ソース"]["here"] is False

    res = client.post(f"/api/backups/{name}/restore", json={"ref": "料理/ソース"})
    assert res.status_code == 200 and res.json()["overwritten"] is False
    assert {e["term"] for e in client.get("/api/entries").json()} == {"冪等", "ソース"}


def test_backups_refuse_a_name_that_points_outside(client):
    assert client.get("/api/backups/..%2Fsecret.zip").status_code in (404, 400)
    assert client.get("/api/backups/backup-9999.zip").status_code == 404


def test_a_backup_can_be_thrown_away(client):
    """溜まったぶんの片付けは人が決める（**自動では消さない**）。"""
    client.post("/api/entries", json=ENTRY)
    exported = client.get("/api/export").content
    client.post("/api/import-glossary", content=exported,
                headers={"Content-Type": "application/zip"})

    name = client.get("/api/backups").json()["items"][0]["name"]
    assert client.delete(f"/api/backups/{name}").status_code == 204
    assert client.get("/api/backups").json()["items"] == []


def test_the_import_plan_changes_nothing(client):
    """**押す前に見せる。** 下見は数えるだけで、控えも取らない。"""
    client.post("/api/entries", json=ENTRY)
    exported = client.get("/api/export").content
    client.post("/api/entries", json={**ENTRY, "term": "結果整合性"})

    res = client.post("/api/import-glossary/plan?mode=replace", content=exported,
                      headers={"Content-Type": "application/zip"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["removed_count"] == 1 and body["unchanged"] == 1
    assert len(client.get("/api/entries").json()) == 2      # 何も変わっていない


def test_an_unknown_import_mode_is_rejected(client):
    client.post("/api/entries", json=ENTRY)
    exported = client.get("/api/export").content
    res = client.post("/api/import-glossary?mode=そのほか", content=exported,
                      headers={"Content-Type": "application/zip"})
    assert res.status_code == 400
    assert len(client.get("/api/entries").json()) == 1


def test_import_of_a_foreign_zip_is_rejected(client):
    client.post("/api/entries", json=ENTRY)
    res = client.post("/api/import-glossary", content=b"not a zip",
                      headers={"Content-Type": "application/zip"})
    assert res.status_code == 400
    assert "zip として読めません" in res.json()["detail"]
    assert len(client.get("/api/entries").json()) == 1


def test_import_of_an_empty_body_is_rejected(client):
    res = client.post("/api/import-glossary", content=b"")
    assert res.status_code == 400


def test_content_search_finds_text_across_files(client):
    """一覧はファイル名しか見ていないので、本文を横断して探す経路が要る。"""
    base = config.content_dir()
    (base / "一巻.txt").write_text("ジョバンニは活版所にいた。\n\nカムパネルラは黙っていた。\n", encoding="utf-8")
    (base / "二巻.txt").write_text("その夜、ジョバンニは丘へ行った。\nジョバンニは走った。\n", encoding="utf-8")
    (base / "無関係.md").write_text("# 別の話\n\n何も出てこない。\n", encoding="utf-8")

    res = client.get("/api/content-search", params={"q": "ジョバンニ"}).json()
    assert res["total_hits"] == 3
    # 多く出てくる文書ほど上
    assert [r["path"] for r in res["results"]] == ["二巻.txt", "一巻.txt"]
    assert res["results"][0]["count"] == 2
    assert "ジョバンニ" in res["results"][0]["hits"][0]["snippet"]
    # 位置は行番号で出る（epub は章名、pdf はページ番号になる）
    assert [h["locator"] for h in res["results"][0]["hits"]] == ["L.1", "L.2"]
    assert res["files_scanned"] == 3
    assert not res["files_truncated"] and not res["hits_truncated"]


def test_searching_by_entry_uses_the_auto_link_rules(client):
    """用語で探すときは**自動リンクと同じ規則**で当てる。

    素の部分一致にすると `API` が `rapid` に当たり、**リンクにならない語を
    「出てくる」と言う**ことになる。別名も一緒に探す。
    """
    ref = client.post("/api/entries", json={
        "term": "API", "aliases": ["ＡＰＩ"], "category": "プログラミング", "definition": "本文。",
    }).json()["ref"]
    (config.content_dir() / "a.md").write_text(
        "API を叩く。rapid な開発。ＡＰＩ とも書く。\n", encoding="utf-8"
    )

    res = client.get("/api/content-search", params={"ref": ref}).json()
    assert res["query"] == "API"
    # API と ＡＰＩ の 2 件。3 件なら rapid まで拾っている（＝素の部分一致）
    assert res["total_hits"] == 2
    assert len(res["results"][0]["hits"]) == 2


def test_searching_by_entry_returns_a_whole_sentence_for_examples(client):
    """使用例に貼るので、抜粋ではなく文の切れ目まで採る。"""
    ref = client.post("/api/entries", json={
        "term": "冪等", "category": "プログラミング", "definition": "本文。",
    }).json()["ref"]
    (config.content_dir() / "a.md").write_text(
        "前の文です。PUT は冪等なのでリトライしても安全。次の文です。\n", encoding="utf-8"
    )

    hit = client.get("/api/content-search", params={"ref": ref}).json()["results"][0]["hits"][0]
    assert hit["sentence"] == "PUT は冪等なのでリトライしても安全。"


def test_searching_by_an_unknown_entry_is_404(client):
    assert client.get("/api/content-search", params={"ref": "無い/語"}).status_code == 404


def test_content_search_needs_something_to_look_for(client):
    assert client.get("/api/content-search").status_code == 400


def test_content_search_is_case_insensitive_and_reports_nothing_found(client):
    (config.content_dir() / "a.md").write_text("The API is idempotent.\n", encoding="utf-8")
    assert client.get("/api/content-search", params={"q": "api"}).json()["total_hits"] == 1
    empty = client.get("/api/content-search", params={"q": "存在しない語"}).json()
    assert empty["results"] == [] and empty["total_hits"] == 0


def test_content_search_says_when_it_stopped_early(client, monkeypatch):
    """打ち切りは黙らない。黙ると「無かった」と区別が付かない。"""
    from glosspop import app as app_module

    base = config.content_dir()
    for i in range(4):
        (base / f"{i}.txt").write_text("ジョバンニ\n", encoding="utf-8")
    monkeypatch.setattr(app_module, "MAX_SEARCH_FILES", 2)

    res = client.get("/api/content-search", params={"q": "ジョバンニ"}).json()
    assert res["files_scanned"] == 2
    assert res["files_truncated"] is True


def test_content_search_reports_unreadable_files(client):
    """読めなかったファイルは「見つからなかった」ではない。"""
    (config.content_dir() / "壊れた.epub").write_bytes(b"not a zip")
    (config.content_dir() / "よい.txt").write_text("ジョバンニ\n", encoding="utf-8")

    res = client.get("/api/content-search", params={"q": "ジョバンニ"}).json()
    assert res["total_hits"] == 1
    assert [s["path"] for s in res["skipped"]] == ["壊れた.epub"]


def test_content_read_returns_sections_for_an_epub(client):
    """目次の材料。**名前のある区切りを持つ文書だけ**が返す。"""
    from tests.test_documents import make_epub

    make_epub(config.content_dir() / "本.epub", [("一、午后の授業", "本文"), ("二、活版所", "本文")])
    res = client.get("/api/content/本.epub").json()
    assert res["sections"] == ["一、午后の授業", "二、活版所"]
    # 章の名前は本文にも入っている（目次はこれを段落に対応づける）
    for label in res["sections"]:
        assert label in res["text"]


def test_content_read_has_no_sections_for_plain_text(client):
    (config.content_dir() / "a.txt").write_text("ただの文章。\n", encoding="utf-8")
    assert client.get("/api/content/a.txt").json()["sections"] == []


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

        def fake_run(prompt: str, **_) -> str:
            assert "冪等" in prompt  # 登録済みの語は除外指示として渡っている
            return '[{"term": "結果整合性"}, {"term": "冪等"}, {"term": "無い語"}]'

        monkeypatch.setattr(ai, "_generate", fake_run)
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


class TestPickFolder:
    """OS のダイアログはサーバ側で開く。ここでは呼び出し方だけ見る。"""

    def test_returns_the_chosen_path(self, client, monkeypatch, tmp_path):
        seen = {}

        def fake_pick(initial: str) -> str:
            seen["initial"] = initial
            return str(tmp_path)

        monkeypatch.setattr(picker, "pick_folder", fake_pick)
        res = client.post("/api/pick-folder", json={}).json()
        assert res == {"path": str(tmp_path), "cancelled": False}
        # 初期位置は今開いているフォルダ
        assert seen["initial"] == str(config.CONTENT_DIR)

    def test_cancel_is_not_an_error(self, client, monkeypatch):
        monkeypatch.setattr(picker, "pick_folder", lambda initial: "")
        res = client.post("/api/pick-folder", json={})
        assert res.status_code == 200
        assert res.json()["cancelled"] is True

    def test_dialog_failure_is_503(self, client, monkeypatch):
        def boom(initial: str) -> str:
            raise picker.PickerError("開けません")

        monkeypatch.setattr(picker, "pick_folder", boom)
        assert client.post("/api/pick-folder", json={}).status_code == 503

    def test_picking_does_not_switch_by_itself(self, client, monkeypatch, tmp_path):
        # 切り替えは /api/content-root の役目 (選んだだけでは変えない)
        monkeypatch.setattr(picker, "pick_folder", lambda initial: str(tmp_path))
        client.post("/api/pick-folder", json={})
        assert client.get("/api/content").json()["is_default"] is True


def test_there_is_no_folder_wide_extract_endpoint(client, monkeypatch):
    """**候補語の抽出にフォルダ横断の口は無い。**

    かつて ``/api/ai/extract-folder`` があったが、何ファイルまとめても AI に
    渡せる本文の枠 (``ai.EXTRACT_TEXT_CHARS``) は 1 文書のときと同じで、
    ファイル数ぶん薄まるだけだった（→ docs/design-notes.md）。戻すなら先に
    「渡せる枠を増やせるか」から考えること。
    """
    monkeypatch.setattr(ai, "available", lambda: True)
    assert client.post("/api/ai/extract-folder", json={}).status_code == 404
    assert not hasattr(ai, "extract_terms_from_documents")


class TestSpoilerLevels:
    """AI にどこまで読ませるか（小説の人物辞書向け）。"""

    NOVEL = "第一章\n駅前に太郎が立っていた。\n\n第十章\n太郎の正体は間諜だった。\n"

    def _write_novel(self):
        (config.CONTENT_DIR / "第一章.md").write_text(self.NOVEL, encoding="utf-8")

    def test_position_only_does_not_call_claude(self, client, monkeypatch):
        self._write_novel()

        def boom(prompt: str) -> str:
            raise AssertionError("AI を呼んではいけない")

        monkeypatch.setattr(ai, "_generate", boom)
        monkeypatch.setattr(config, "CLAUDE_BIN", "")   # claude が無くても通ること

        res = client.post(
            "/api/ai/draft",
            json={"term": "太郎", "file": "第一章.md", "spoiler": "position"},
        )
        assert res.status_code == 200
        draft = res.json()["draft"]
        assert draft["term"] == "太郎"
        assert draft["first_file"] == "第一章.md"
        assert draft["first_locator"] == "L.2"
        assert draft["definition"] == ""     # 本文は自分で書く

    def test_first_only_hides_later_chapters(self, client, monkeypatch):
        self._write_novel()
        seen = {}

        def fake_run(prompt: str, **_) -> str:
            seen["prompt"] = prompt
            return '{"term": "太郎", "summary": "駅前にいた人物。", "category": "登場人物"}'

        monkeypatch.setattr(ai, "_generate", fake_run)
        monkeypatch.setattr(config, "CLAUDE_BIN", "claude")

        res = client.post(
            "/api/ai/draft",
            json={"term": "太郎", "file": "第一章.md", "spoiler": "first"},
        )
        assert res.status_code == 200
        assert "駅前に太郎" in seen["prompt"]
        assert "間諜" not in seen["prompt"]           # 後の展開は渡さない
        assert "ネタバレの禁止" in seen["prompt"]
        assert res.json()["draft"]["first_locator"] == "L.2"

    def test_full_passes_the_given_context(self, client, monkeypatch):
        self._write_novel()
        seen = {}

        def fake_run(prompt: str, **_) -> str:
            seen["prompt"] = prompt
            return '{"term": "太郎", "category": "登場人物"}'

        monkeypatch.setattr(ai, "_generate", fake_run)
        monkeypatch.setattr(config, "CLAUDE_BIN", "claude")

        client.post(
            "/api/ai/draft",
            json={
                "term": "太郎",
                "file": "第一章.md",
                "context": "太郎の正体は間諜だった。",
                "spoiler": "full",
            },
        )
        assert "間諜" in seen["prompt"]
        assert "ネタバレの禁止" not in seen["prompt"]

    def test_the_current_text_makes_it_a_rewrite(self, client, monkeypatch):
        """文体を変えたあと、登録済みの語を書き直せること。"""
        seen = {}

        def fake_run(prompt: str, **_) -> str:
            seen["prompt"] = prompt
            return '{"term": "冪等", "summary": "書き直した要約。", "category": "プログラミング"}'

        monkeypatch.setattr(ai, "_generate", fake_run)
        monkeypatch.setattr(config, "CLAUDE_BIN", "claude")

        res = client.post("/api/ai/draft", json={
            "term": "冪等",
            "current": "何度実行しても同じ結果になること。",
            "spoiler": "full",
        })
        assert res.status_code == 200
        assert "いまの説明" in seen["prompt"]
        assert "何度実行しても同じ結果になること。" in seen["prompt"]
        # **事実を作り直させない**（渡した説明が唯一の情報源になりうる）
        assert "書かれている事実は変えないでください" in seen["prompt"]
        assert res.json()["draft"]["summary"] == "書き直した要約。"

    def test_unknown_level_falls_back_to_the_configured_default(self, client, monkeypatch):
        monkeypatch.setattr(config, "SPOILER_DEFAULT", "position")
        monkeypatch.setattr(config, "CLAUDE_BIN", "")
        res = client.post("/api/ai/draft", json={"term": "太郎", "spoiler": "でたらめ"})
        assert res.status_code == 200      # position 扱いなので AI を呼ばない

    def test_health_reports_the_default(self, client):
        assert client.get("/api/health").json()["spoiler_default"] in config.SPOILER_LEVELS

    def test_first_seen_survives_saving(self, client):
        created = client.post(
            "/api/entries",
            json={
                "term": "太郎",
                "category": "登場人物",
                "scope": "local",
                "definition": "主人公。",
                "first_file": "第一章.md",
                "first_locator": "L.2",
            },
        ).json()
        assert created["first_file"] == "第一章.md"
        assert created["first_locator"] == "L.2"
        # frontmatter にも残る (ファイルを直接見ても分かる)
        saved = (config.CONTENT_DIR / ".glosspop" / "glossary" / "登場人物" / "太郎.md").read_text(
            encoding="utf-8"
        )
        assert "first_file: 第一章.md" in saved


# --------------------------------------------------------------------------- #
# 関係と相関図
# --------------------------------------------------------------------------- #

def _person(client, term: str, **extra) -> str:
    body = {"term": term, "category": "登場人物", **extra}
    return client.post("/api/entries", json=body).json()["ref"]


def test_graph_returns_nodes_and_edges(client):
    a = _person(client, "ジョバンニ")
    b = _person(client, "カムパネルラ")
    client.put(
        f"/api/entries/{ref_path(a)}",
        json={
            "term": "ジョバンニ",
            "category": "登場人物",
            "relations": [{"to": b, "label": "親友", "back": "親友", "rank": "対等"}],
        },
    )
    g = client.get("/api/graph", params={"category": "登場人物"}).json()
    assert {n["term"] for n in g["nodes"]} == {"ジョバンニ", "カムパネルラ"}
    assert g["edges"][0]["mutual"] is True
    assert g["broken"] == []


def test_graph_hides_revealed_relations_by_default(client):
    a = _person(client, "ジョバンニ")
    b = _person(client, "カムパネルラ")
    client.put(
        f"/api/entries/{ref_path(a)}",
        json={
            "term": "ジョバンニ",
            "category": "登場人物",
            "relations": [{"to": b, "label": "実は兄弟", "reveal": "第6章"}],
        },
    )
    hidden = client.get("/api/graph", params={"category": "登場人物"}).json()
    assert hidden["edges"] == [] and hidden["hidden"] == 1   # 黙って消さない
    shown = client.get(
        "/api/graph", params={"category": "登場人物", "spoilers": True}
    ).json()
    assert [e["label"] for e in shown["edges"]] == ["実は兄弟"]


def test_graph_rejects_an_unknown_scope(client):
    assert client.get("/api/graph", params={"scope": "どこか"}).status_code == 400


def test_graph_can_be_narrowed_to_one_document(client):
    """`?doc=` は、その文書に出てくる語だけの図にする。

    出てくるかどうかは `Linker` の規則で決める（素の部分一致に戻すと
    リンクにならない語まで「出てくる」ことになる）。相手が出てこない関係は
    落として数だけ返す —— 足すと、その文書に無い語が図に混ざる。
    """
    a = _person(client, "ジョバンニ")
    b = _person(client, "カムパネルラ")
    _person(client, "ザネリ")
    client.put(
        f"/api/entries/{ref_path(a)}",
        json={
            "term": "ジョバンニ",
            "category": "登場人物",
            "relations": [{"to": b, "label": "親友"}, {"to": "ザネリ", "label": "同級生"}],
        },
    )
    (config.content_dir() / "章1.md").write_text(
        "ジョバンニはカムパネルラと歩いた。\n", encoding="utf-8"
    )

    g = client.get("/api/graph", params={"doc": "章1.md"}).json()
    assert {n["term"] for n in g["nodes"]} == {"ジョバンニ", "カムパネルラ"}
    assert [e["label"] for e in g["edges"]] == ["親友"]
    assert g["outside"] == 1            # ザネリ行きは黙って消さない
    assert g["doc"] == "章1.md"

    # 絞らなければ全部出る（既定は変わっていない）
    whole = client.get("/api/graph").json()
    assert len(whole["nodes"]) == 3 and whole["outside"] == 0 and whole["doc"] == ""


def test_graph_dates_relations_when_a_document_is_given(client):
    """`?doc=` のときは「その文書のどこで読めるようになるか」も返す（時系列）。

    位置は**両端が出そろうところ**。保存はしないので、本文を直せば次に読んだ
    ときの図が変わるだけ（→ `timeline.py`）。
    """
    a = _person(client, "ジョバンニ")
    b = _person(client, "カムパネルラ")
    client.put(
        f"/api/entries/{ref_path(a)}",
        json={
            "term": "ジョバンニ",
            "category": "登場人物",
            "relations": [{"to": b, "label": "親友"}],
        },
    )
    (config.content_dir() / "章1.md").write_text(
        "# 一\n\nジョバンニは走った。\n\n# 二\n\nカムパネルラが現れた。\n", encoding="utf-8"
    )

    g = client.get("/api/graph", params={"doc": "章1.md"}).json()
    edge = g["edges"][0]
    assert edge["at_label"] == "L.7"                  # 遅いほうの語が出てくる行
    assert edge["at"] > 0 and g["undated"] == 0
    assert {n["term"]: n["at_label"] for n in g["nodes"]} == {
        "ジョバンニ": "L.3", "カムパネルラ": "L.7",
    }


def test_graph_of_the_whole_glossary_has_no_timeline(client):
    """**辞書全体には時系列を足さない。** 読むものが決まっていないと定義できない。"""
    _person(client, "ジョバンニ")
    g = client.get("/api/graph").json()
    assert "undated" not in g
    assert all("at" not in n for n in g["nodes"])


def test_graph_refuses_a_document_outside_content(client):
    assert client.get("/api/graph", params={"doc": "../secret.md"}).status_code == 400
    assert client.get("/api/graph", params={"doc": "ない.md"}).status_code == 404


def test_entry_detail_carries_relations_both_ways(client):
    a = _person(client, "ジョバンニ")
    b = _person(client, "カムパネルラ")
    client.put(
        f"/api/entries/{ref_path(a)}",
        json={
            "term": "ジョバンニ",
            "category": "登場人物",
            "relations": [{"to": "カムパネルラ", "label": "親友", "back": "親友"}],
        },
    )
    forward = client.get(f"/api/entries/{ref_path(a)}").json()
    assert forward["relations_resolved"][0]["term"] == "カムパネルラ"
    assert forward["relations_resolved"][0]["missing"] is False
    # 書いていない側にも見える (両側に書かせない)
    back = client.get(f"/api/entries/{ref_path(b)}").json()
    assert [x["term"] for x in back["backlinks"]] == ["ジョバンニ"]


def test_relations_survive_a_category_move(client):
    a = _person(client, "ジョバンニ")
    b = _person(client, "カムパネルラ")
    client.put(
        f"/api/entries/{ref_path(a)}",
        json={
            "term": "ジョバンニ",
            "category": "登場人物",
            "relations": [{"to": b, "label": "親友"}],
        },
    )
    client.post(f"/api/move/{ref_path(b)}", json={"category": "主要人物"})
    detail = client.get(f"/api/entries/{ref_path(a)}").json()
    # 参照側は書き換えていない。旧 ref が転送として効いている
    assert detail["relations_resolved"][0]["missing"] is False
    assert detail["relations_resolved"][0]["ref"].startswith("主要人物/")


def test_extract_kinds_are_listed_for_the_ui(client):
    body = client.get("/api/ai/kinds").json()
    keys = [k["key"] for k in body["kinds"]]
    assert "person" in keys and "term" in keys
    assert body["default"] == list(ai.DEFAULT_KINDS)


def test_extract_kind_hints_have_no_prompt_markup(client):
    """種別の説明は UI にそのまま出る。プロンプト用の ** を混ぜない。"""
    for kind in client.get("/api/ai/kinds").json()["kinds"]:
        assert "**" not in kind["hint"]


# --------------------------------------------------------------------------- #
# 関係の一括書き込みと点検
# --------------------------------------------------------------------------- #

def test_apply_relations_merges_into_the_source_entry(client):
    a = _person(client, "ジョバンニ")
    b = _person(client, "カムパネルラ")
    c = _person(client, "ザネリ")
    res = client.post("/api/relations", json={
        "relations": [
            {"from_ref": a, "to": b, "label": "親友", "back": "親友"},
            {"from_ref": a, "to": c, "label": "同級生"},
        ]
    })
    assert res.status_code == 200
    assert res.json()["applied"] == 2
    detail = client.get(f"/api/entries/{ref_path(a)}").json()
    assert [r["term"] for r in detail["relations_resolved"]] == ["カムパネルラ", "ザネリ"]


def test_apply_relations_keeps_what_was_already_there(client):
    a = _person(client, "ジョバンニ")
    b = _person(client, "カムパネルラ")
    c = _person(client, "ザネリ")
    client.put(f"/api/entries/{ref_path(a)}", json={
        "term": "ジョバンニ", "category": "登場人物",
        "relations": [{"to": b, "label": "親友"}],
    })
    client.post("/api/relations", json={
        "relations": [{"from_ref": a, "to": c, "label": "同級生"}]
    })
    detail = client.get(f"/api/entries/{ref_path(a)}").json()
    assert [r["term"] for r in detail["relations_resolved"]] == ["カムパネルラ", "ザネリ"]


def test_apply_relations_reports_a_missing_source(client):
    body = client.post("/api/relations", json={
        "relations": [{"from_ref": "登場人物/いない", "to": "だれか", "label": "x"}]
    }).json()
    assert body["applied"] == 0
    assert body["results"][0]["ok"] is False


class TestAISettings:
    """使う AI・モデル・思考の深さの設定。**鍵は返さない。**"""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for name in ("GLOSSPOP_AI_PROVIDER", "GLOSSPOP_AI_MODEL", "GLOSSPOP_AI_EFFORT",
                     "GLOSSPOP_AI_STYLE", "GLOSSPOP_GEMINI_KEY", "GEMINI_API_KEY"):
            monkeypatch.delenv(name, raising=False)

    def test_reports_the_current_choice(self, client):
        body = client.get("/api/ai/settings").json()
        assert body["provider"] == "claude"
        assert [p["id"] for p in body["providers"]] == ["claude", "gemini"]
        assert "" in [e["id"] for e in body["efforts"]]

    def test_saves_and_takes_effect_without_a_restart(self, client):
        res = client.put("/api/ai/settings", json={"provider": "claude", "model": "opus", "effort": "high"})
        assert res.status_code == 200
        assert res.json()["model"] == "opus" and res.json()["effort"] == "high"
        # 次の呼び出しからそのまま効く（読み直しても同じ）
        assert client.get("/api/ai/settings").json()["model"] == "opus"

    def test_rejects_an_unknown_provider_or_effort(self, client):
        assert client.put("/api/ai/settings", json={"provider": "でたらめ"}).status_code == 400
        assert client.put("/api/ai/settings", json={"effort": "ものすごく"}).status_code == 400

    def test_the_key_is_stored_but_never_returned(self, client):
        body = client.put("/api/ai/settings", json={
            "provider": "gemini", "gemini_api_key": "秘密の鍵",
        }).json()
        assert body["gemini_key_set"] is True
        assert "秘密の鍵" not in client.get("/api/ai/settings").text
        # 空文字は「消す」
        cleared = client.put("/api/ai/settings", json={"gemini_api_key": ""}).json()
        assert cleared["gemini_key_set"] is False

    def test_omitted_fields_are_left_alone(self, client):
        client.put("/api/ai/settings", json={"provider": "gemini", "gemini_api_key": "k"})
        client.put("/api/ai/settings", json={"effort": "low"})     # 鍵は触らない
        body = client.get("/api/ai/settings").json()
        assert body["gemini_key_set"] is True and body["effort"] == "low"

    def test_the_style_round_trips_and_can_be_cleared(self, client):
        from glosspop import ai
        body = client.put("/api/ai/style", json={"style": "講談調で"}).json()
        assert body["style"] == "講談調で" and body["style_source"] == "settings"
        assert body["style_presets"]                   # 例は画面ではなくここが持つ
        assert ai.style() == "講談調で"                 # 次の下書きからそのまま効く
        cleared = client.put("/api/ai/style", json={"style": "  "}).json()
        assert cleared["style"] == "" and cleared["style_source"] == "default"

    def test_the_style_presets_describe_the_writing_not_a_person(self, client):
        """例は**こちらが出す献立**なので、実在の個人を名指ししない。

        仕事をしているのは「俯瞰した視点から淡々と」の側で、名前ではない
        （名前だけだと似顔絵のような戯画になりやすい）。**自由記述の欄は残して
        あるので**、誰かの名前で頼みたい人はそう書ける —— それは書く人の判断。
        → docs/design-notes.md
        """
        presets = client.get("/api/ai/settings").json()["style_presets"]
        assert presets
        blob = " ".join(f"{p['label']} {p['value']}" for p in presets)
        for name in ("司馬遼太郎", "広川太一郎"):
            assert name not in blob
        # 「〜風」で人を名指しする形に戻っていないこと（作品・媒体の名前は可）
        assert "の吹き替え" not in blob
        # 1 つの作品にしか合わない口調は既定に置かない（voices.md のレシピ側）
        assert "怪盗" not in blob

    def test_rejects_a_style_longer_than_the_limit(self, client):
        from glosspop import ai
        res = client.put("/api/ai/style", json={"style": "あ" * (ai.STYLE_MAX_CHARS + 1)})
        assert res.status_code == 400

    def test_the_style_is_not_written_by_the_other_ai_settings(self, client):
        """**モデルを選び直したついでにフォルダを汚さない**（別の口にした理由）。"""
        res = client.put("/api/ai/settings", json={"style": "講談調で", "effort": "low"})
        assert res.status_code == 200
        assert res.json()["style"] == "" and res.json()["effort"] == "low"

    def test_the_folder_wins_over_the_global_setting(self, client, tmp_path):
        from glosspop import ai, config
        config.set_content_dir(tmp_path)
        try:
            client.put("/api/ai/style", json={"scope": "global", "style": "全体の口調"})
            body = client.put("/api/ai/style", json={"scope": "local", "style": "この作品の口調"}).json()
            assert body["style"] == "この作品の口調" and body["style_source"] == "folder"
            # 全体の指定は消えていない（押しのけられているだけ）
            assert body["style_global"] == "全体の口調"
            assert (tmp_path / ".glosspop" / "style.md").is_file()
            assert ai.style() == "この作品の口調"
            # 空文字はファイルごと消す（空のファイルは「何か指定されている」に見える）
            back = client.put("/api/ai/style", json={"scope": "local", "style": ""}).json()
            assert back["style"] == "全体の口調" and back["style_source"] == "settings"
            assert not (tmp_path / ".glosspop" / "style.md").exists()
        finally:
            config.set_content_dir(None)

    def test_a_folder_style_is_not_created_until_it_is_saved(self, client, tmp_path):
        """開いただけのフォルダを汚さない（カテゴリマスターと同じ約束）。"""
        from glosspop import config
        config.set_content_dir(tmp_path)
        try:
            client.get("/api/ai/settings")
            assert not (tmp_path / ".glosspop").exists()
        finally:
            config.set_content_dir(None)

    def test_the_persona_places_are_reported_even_when_empty(self, client):
        body = client.get("/api/ai/settings").json()
        assert [p["scope"] for p in body["personas"]] == ["global", "local"]
        assert all(p["found"] is False for p in body["personas"])
        assert body["persona_name"].startswith("persona")
        # **置き場所は顔が無くても返す**（「どこに置かれるか」を画面に出せるように）
        assert body["personas"][0]["dir"]

    def test_each_provider_keeps_its_own_model(self, client):
        client.put("/api/ai/settings", json={"provider": "claude", "model": "opus"})
        client.put("/api/ai/settings", json={"provider": "gemini", "model": "gemini-3.5-flash"})
        assert client.get("/api/ai/settings").json()["model"] == "gemini-3.5-flash"
        back = client.put("/api/ai/settings", json={"provider": "claude"}).json()
        assert back["model"] == "opus"          # 選び直させない

    def test_claude_models_come_from_the_app(self, client):
        body = client.get("/api/ai/models", params={"provider": "claude"}).json()
        assert [m["id"] for m in body["models"]] == ["", "haiku", "sonnet", "opus"]

    def test_gemini_models_need_a_key(self, client):
        res = client.get("/api/ai/models", params={"provider": "gemini"})
        assert res.status_code == 502
        assert "API キー" in res.json()["detail"]


def test_apply_aliases_adds_another_name_to_an_existing_entry(client):
    a = _person(client, "主人")
    body = client.post("/api/aliases", json={
        "aliases": [{"ref": a, "alias": "苦沙弥先生"}]
    }).json()
    assert body["applied"] == 1
    detail = client.get(f"/api/entries/{ref_path(a)}").json()
    assert detail["aliases"] == ["苦沙弥先生"]


def test_apply_aliases_keeps_the_existing_ones(client):
    a = _person(client, "主人")
    client.post("/api/aliases", json={"aliases": [{"ref": a, "alias": "苦沙弥先生"}]})
    # 同じエントリに 2 件付くとき、後の書き込みが前のものを消さないこと
    body = client.post("/api/aliases", json={
        "aliases": [{"ref": a, "alias": "珍野"}, {"ref": a, "alias": "先生"}]
    }).json()
    assert body["applied"] == 2
    detail = client.get(f"/api/entries/{ref_path(a)}").json()
    assert detail["aliases"] == ["苦沙弥先生", "珍野", "先生"]


def test_apply_aliases_reports_a_missing_entry(client):
    body = client.post("/api/aliases", json={
        "aliases": [{"ref": "登場人物/いない", "alias": "別名"}]
    }).json()
    assert body["applied"] == 0 and body["results"][0]["ok"] is False


def test_doctor_is_quiet_on_a_healthy_dictionary(client):
    client.post("/api/entries", json=ENTRY)
    body = client.get("/api/doctor").json()
    assert body["issues"] == [] and body["checked"] == 1


def test_doctor_reports_a_broken_relation(client):
    a = _person(client, "ジョバンニ")
    client.put(f"/api/entries/{ref_path(a)}", json={
        "term": "ジョバンニ", "category": "登場人物",
        "summary": "主人公。", "definition": "本文。",
        "relations": [{"to": "いない人", "label": "兄"}],
    })
    body = client.get("/api/doctor").json()
    assert [i["kind"] for i in body["issues"]] == ["broken_relation"]
    assert body["errors"] == 1


def test_relations_draft_needs_two_entries(client, monkeypatch):
    # claude CLI の有無は環境で変わる。手元では 400 まで届くが、無い環境では
    # 503 で止まって別のものを見てしまう（CI で実際に落ちた）
    monkeypatch.setattr(ai, "available", lambda: True)
    _person(client, "ジョバンニ")
    res = client.post("/api/ai/relations", json={"category": "登場人物"})
    assert res.status_code == 400
    assert "2 語以上" in res.json()["detail"]


def test_relations_draft_reports_a_missing_claude_cli(client, monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: False)
    res = client.post("/api/ai/relations", json={"category": "登場人物"})
    assert res.status_code == 503


def test_relations_draft_can_read_the_displayed_document(client, monkeypatch):
    """URL を読んでいるときはフォルダに本文が無い。表示中の本文を渡せること。"""
    import json as _json

    from glosspop import ai

    _person(client, "ジョバンニ")
    _person(client, "カムパネルラ")
    seen = {}

    def fake(prompt, **_):        # 関係の下書きは timeout を渡して呼ぶ
        seen["prompt"] = prompt
        return _json.dumps([
            {"from": "ジョバンニ", "to": "カムパネルラ", "label": "親友", "back": "親友"}
        ])

    monkeypatch.setattr(ai, "_generate", fake)
    monkeypatch.setattr(ai, "available", lambda: True)
    body = client.post("/api/ai/relations", json={
        "category": "登場人物",
        "spoiler": "full",
        "text": "ジョバンニとカムパネルラは親友だった。",
        "source": "https://example.com/gingatetsudo",
    }).json()
    assert [r["to_term"] for r in body["relations"]] == ["カムパネルラ"]
    assert "ジョバンニとカムパネルラは親友だった。" in seen["prompt"]


def test_relations_draft_reads_the_folder_when_no_text_is_given(client, monkeypatch):
    """**本文を渡さない経路が残っている。** 用語ページの「✨ この語の関係を下書き」は
    読んでいる文書を持たないので、サーバがフォルダを読んで補う。

    候補語の抽出からはフォルダを読む道を畳んだが、**こちらは畳んでいない** ——
    渡すのは読んだ本文そのものではなく選んだ窓なので、ファイルを読む代金
    (実測 17.6 ms) は待ち時間に効かない。ここを一緒に消すと、用語ページの
    ボタンが「読める文書がありません」しか返さなくなる（実際に消しかけた）。
    """
    import json as _json

    from glosspop import ai

    (config.CONTENT_DIR / "銀河鉄道の夜.md").write_text(
        "ジョバンニとカムパネルラは親友だった。", encoding="utf-8"
    )
    ref = _person(client, "ジョバンニ")
    _person(client, "カムパネルラ")
    seen = {}

    def fake(prompt, **_):
        seen["prompt"] = prompt
        return _json.dumps([
            {"from": "ジョバンニ", "to": "カムパネルラ", "label": "親友", "back": "親友"}
        ])

    monkeypatch.setattr(ai, "_generate", fake)
    monkeypatch.setattr(ai, "available", lambda: True)
    body = client.post("/api/ai/relations", json={"ref": ref, "spoiler": "full"}).json()

    assert [r["to_term"] for r in body["relations"]] == ["カムパネルラ"]
    assert "ジョバンニとカムパネルラは親友だった。" in seen["prompt"]


def test_relations_draft_can_focus_on_one_entry(client, monkeypatch):
    """用語ページからの下書き。**その語が端に居る関係だけ**を返す。

    範囲を「1 語だけ」にはできない（関係は 2 語が揃って初めて書ける）ので、
    相手は今までどおり範囲から選ばせて、**頼む側と落とす側の両方**で絞る。
    """
    import json as _json

    from glosspop import ai

    ref = _person(client, "ジョバンニ")
    _person(client, "カムパネルラ")
    _person(client, "ザネリ")
    seen = {}

    def fake(prompt, **_):
        seen["prompt"] = prompt
        return _json.dumps([
            {"from": "ジョバンニ", "to": "カムパネルラ", "label": "親友", "back": "親友"},
            # 頼んでいない組。プロンプトで禁じていても返ってくることがある
            {"from": "カムパネルラ", "to": "ザネリ", "label": "級友"},
        ])

    monkeypatch.setattr(ai, "_generate", fake)
    monkeypatch.setattr(ai, "available", lambda: True)
    body = client.post("/api/ai/relations", json={
        "ref": ref,
        "spoiler": "full",
        "text": "ジョバンニとカムパネルラは親友だった。カムパネルラはザネリと同級だ。",
    }).json()

    assert [(r["from_term"], r["to_term"]) for r in body["relations"]] == [
        ("ジョバンニ", "カムパネルラ")
    ]
    assert any("ジョバンニ" in d["reason"] for d in body["dropped"]), body["dropped"]
    # **落とすだけでなく頼む。** 頼まないと上限の大半を他人どうしの関係が食う
    assert "かならず「ジョバンニ」の関係にすること" in seen["prompt"]


def test_relations_draft_reports_an_unknown_focus(client, monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: True)
    _person(client, "ジョバンニ")
    _person(client, "カムパネルラ")
    res = client.post("/api/ai/relations", json={"ref": "登場人物/居ない人"})
    assert res.status_code == 404


# --------------------------------------------------------------------------- #
# 設定（データの保存先）
# --------------------------------------------------------------------------- #

@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    """本物の配置と同じ形にしてから設定 API を叩く。

    autouse の ``isolated_dirs`` は各パスを直接 tmp_path に向けるが、それだと
    ``DATA_ROOT/data/glossary`` という実際の入れ子になっておらず、複製の対象に
    入らない（＝テストが実物と違う形を見てしまう）。
    """
    path = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", path)
    monkeypatch.setattr(config, "GLOSSARY_DIR", tmp_path / "data" / "glossary")
    monkeypatch.setattr(config, "CATEGORIES_FILE", tmp_path / "data" / "categories.yaml")
    monkeypatch.setattr(config, "SITES_DIR", tmp_path / "data" / "sites")
    monkeypatch.setattr(config, "CONTENT_DIR", tmp_path / "content")
    monkeypatch.setattr(config, "WINDOW_PROFILE_DIR", tmp_path / "data" / "window")
    monkeypatch.delenv("GLOSSPOP_DATA_ROOT", raising=False)
    config.ensure_dirs()
    store.invalidate()
    return path


def test_settings_reports_where_everything_lives(client, settings_env):
    body = client.get("/api/settings").json()
    assert body["source"] == "default"
    assert body["settings_file"] == str(settings_env)
    # 更新のとき何を持っていくかが分かること
    assert set(body["paths"]) == {
        "glossary", "categories", "sites", "content", "window_profile",
        # 取り込みの前に自動で取る控え。場所を知らせないと戻れない
        "backups",
    }


def test_settings_move_writes_the_file_and_asks_for_a_restart(
    client, settings_env, tmp_path_factory
):
    target = tmp_path_factory.mktemp("外の場所")
    res = client.put("/api/settings", json={"data_root": str(target), "copy_existing": False})
    assert res.status_code == 200
    body = res.json()
    # いまのプロセスは古い場所を見たまま。黙ると「移したのに反映されない」になる
    assert body["restart_required"] is True
    assert body["pending_data_root"] == str(target.resolve())
    assert json.loads(settings_env.read_text(encoding="utf-8"))["data_root"] == str(target.resolve())


def test_settings_move_copies_the_existing_data(client, settings_env, tmp_path_factory):
    client.post("/api/entries", json=ENTRY)
    # いまの保存先の**外**を選ぶ（中を選ぶのは別のテストで弾いている）
    target = tmp_path_factory.mktemp("移動先")
    body = client.put(
        "/api/settings", json={"data_root": str(target), "copy_existing": True}
    ).json()
    assert body["copy"]["skipped"] == []
    assert any("glossary" in p for p in body["copy"]["copied"])
    # 元は消さない（戻れるようにする）
    assert client.get("/api/entries").json()


def test_settings_reset_removes_the_override(client, settings_env, tmp_path):
    client.put("/api/settings", json={"data_root": str(tmp_path / "x"), "copy_existing": False})
    body = client.put("/api/settings", json={"data_root": "", "copy_existing": False}).json()
    assert body["saved_data_root"] == ""
    assert "data_root" not in json.loads(settings_env.read_text(encoding="utf-8"))


def test_settings_rejects_a_file_as_the_target(client, settings_env, tmp_path):
    target = tmp_path / "ファイル.txt"
    target.write_text("x", encoding="utf-8")
    res = client.put("/api/settings", json={"data_root": str(target)})
    assert res.status_code == 400


def test_settings_refuses_when_the_env_var_wins(client, settings_env, tmp_path, monkeypatch):
    """環境変数が勝つのに設定を書けると、効かない設定が残って混乱する。"""
    monkeypatch.setenv("GLOSSPOP_DATA_ROOT", str(tmp_path / "forced"))
    assert client.get("/api/settings").json()["env_locked"] is True
    res = client.put("/api/settings", json={"data_root": str(tmp_path / "other")})
    assert res.status_code == 409


def test_settings_refuses_a_target_inside_the_current_root(client, settings_env, tmp_path):
    """いまの保存先の中を選ぶと、複製が入れ子になって無限に増える。"""
    res = client.put(
        "/api/settings",
        json={"data_root": str(tmp_path / "中に作る"), "copy_existing": True},
    )
    assert res.status_code == 400
    assert "中にあります" in res.json()["detail"]


def test_settings_warns_about_paths_outside_the_root(client, settings_env, tmp_path_factory,
                                                     monkeypatch):
    """環境変数で外に出ているものは複製に乗らない。黙らずに名前で知らせる。"""
    monkeypatch.setattr(config, "GLOSSARY_DIR", tmp_path_factory.mktemp("外の辞書"))
    assert "全体の辞書" in client.get("/api/settings").json()["outside"]


def test_import_brings_data_from_another_folder(client, settings_env, tmp_path_factory):
    """更新後に「辞書が消えた」状態の救済。元は消さない。"""
    old = tmp_path_factory.mktemp("旧バージョン")
    d = old / "data" / "glossary" / "プログラミング"
    d.mkdir(parents=True)
    (d / "冪等.md").write_text("---\nterm: 冪等\n---\n\n本文\n", encoding="utf-8")

    body = client.post("/api/import", json={"path": str(old)}).json()
    assert body["restart_required"] is True
    assert (config.GLOSSARY_DIR / "プログラミング" / "冪等.md").exists()
    assert (d / "冪等.md").exists()          # 元は残す
    # 引き継いだものがそのまま読める
    assert [e["term"] for e in client.get("/api/entries").json()] == ["冪等"]


def test_import_reports_a_missing_folder(client, settings_env, tmp_path):
    res = client.post("/api/import", json={"path": str(tmp_path / "無い")})
    assert res.status_code == 404


def test_settings_lists_a_sibling_to_import_from(client, settings_env, tmp_path, monkeypatch):
    # 隣を走査するので、並びを閉じ込めた場所に置く
    # (tmp_path.parent は他のテストと共有していて、拾ってしまう)
    installs = tmp_path / "installs"
    new = installs / "GlossPop-0.5.0"
    new.mkdir(parents=True)
    d = installs / "GlossPop-0.4.0" / "data" / "glossary" / "テスト"
    d.mkdir(parents=True)
    (d / "語.md").write_text("本文", encoding="utf-8")

    monkeypatch.setattr(config, "APP_DIR", new)
    body = client.get("/api/settings").json()
    assert [c["name"] for c in body["import_candidates"]] == ["GlossPop-0.4.0"]


def test_download_is_refused_when_the_check_is_off(client, settings_env):
    client.put("/api/update", json={"enabled": False})
    res = client.post("/api/update/download")
    assert res.status_code == 409


# --------------------------------------------------------------------------- #
# 自動リンカの使い回し
#
# 組み立ては件数に比例するので使い回すが、**辞書の変更を取りこぼしたら意味がない**。
# 外のエディタで書き換えてよい、が売りなので、そこが崩れないことを見張る。
# --------------------------------------------------------------------------- #

def test_the_linker_is_reused_while_the_glossary_is_unchanged(client):
    from glosspop import app as app_module

    client.post("/api/entries", json=ENTRY)
    first = app_module._linker()
    assert app_module._linker() is first


def test_the_linker_is_rebuilt_when_an_entry_is_added(client):
    from glosspop import app as app_module

    client.post("/api/entries", json=ENTRY)
    first = app_module._linker()
    client.post("/api/entries", json={**ENTRY, "term": "結果整合性"})
    assert app_module._linker() is not first


def test_the_linker_notices_an_edit_made_outside(client):
    """**外のエディタで足したファイル**も拾うこと。

    鍵にしているのは `load_all()` が返すリストで、`store` は署名
    （各ファイルの mtime とサイズ）が変わったときだけ新しいリストを作る。
    ここが時間や件数だけの判定に変わると、外の編集が黙って無視される。
    """
    from glosspop import app as app_module

    client.post("/api/entries", json=ENTRY)
    before, _ = app_module._linker().annotate("<p>冪等と結果整合性</p>")
    assert before.count("gloss-link") == 1

    path = config.GLOSSARY_DIR / "プログラミング" / "結果整合性.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nterm: 結果整合性\n---\n\n本文。\n", encoding="utf-8")

    after, _ = app_module._linker().annotate("<p>冪等と結果整合性</p>")
    assert after.count("gloss-link") == 2


def test_content_search_sees_a_file_edited_outside(client):
    """**解釈済みの文書を使い回しても、外の編集を取りこぼさないこと。**

    索引ではなく「変わっていないものを読み直さない」だけなので、ここが崩れると
    「その語は無かった」と区別が付かなくなる（打ち切りを必ず返しているのと同じ理由）。
    """
    doc = config.content_dir() / "銀河.md"
    doc.write_text("ジョバンニは活版所にいた。\n", encoding="utf-8")
    first = client.get("/api/content-search", params={"q": "カムパネルラ"}).json()
    assert first["results"] == []

    doc.write_text("ジョバンニは活版所にいた。カムパネルラも来た。\n", encoding="utf-8")
    second = client.get("/api/content-search", params={"q": "カムパネルラ"}).json()
    assert [r["path"] for r in second["results"]] == ["銀河.md"]


class TestPersona:
    """ペルソナ（語り手）の顔。**辞書に 1 枚**で、エントリの居場所につく。"""

    PNG = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6300010000050001"
        "0d0a2db40000000049454e44ae426082"
    )

    def _put(self, directory, name="persona.png"):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(self.PNG)

    def test_missing_is_a_404_not_an_error(self, client):
        assert client.get("/api/persona").status_code == 404

    def test_rejects_an_unknown_scope(self, client):
        assert client.get("/api/persona?scope=でたらめ").status_code == 400

    def test_the_global_face_sits_next_to_the_category_master(self, client):
        self._put(config.CATEGORIES_FILE.parent)
        res = client.get("/api/persona?scope=global")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("image/png")
        assert res.content == self.PNG

    def test_svg_is_not_served(self, client):
        """**中身を検査せずに配る口**なので、スクリプトを持てる形式は入れない。"""
        self._put(config.CATEGORIES_FILE.parent, "persona.svg")
        assert client.get("/api/persona?scope=global").status_code == 404

    def test_the_entry_carries_the_url_of_its_own_dictionary(self, client, add_entry):
        add_entry("冪等", category="プログラミング")
        ref = store.load_all()[0].ref
        assert client.get(f"/api/entries/{ref}").json()["persona_url"] == ""
        self._put(config.CATEGORIES_FILE.parent)
        url = client.get(f"/api/entries/{ref}").json()["persona_url"]
        assert url.startswith("/api/persona?scope=global")
        # **差し替えたら URL が変わること**（古い顔が残らない）
        assert "&v=" in url

    def test_the_lookup_carries_it_too(self, client, add_entry):
        add_entry("冪等", category="プログラミング")
        self._put(config.CATEGORIES_FILE.parent)
        entry = client.get("/api/lookup?term=冪等").json()["entries"][0]
        assert entry["persona_url"].startswith("/api/persona?scope=global")

    # ----------------------------------------------------------------- 差し替え

    GIF = b"GIF89a" + b"\x01\x00\x01\x00\x00\xff\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x00;"

    def _post(self, client, data, scope="global", content_type="image/png"):
        return client.post(
            f"/api/persona?scope={scope}",
            content=data,
            headers={"Content-Type": content_type},
        )

    def test_a_face_can_be_replaced_from_the_screen(self, client):
        res = self._post(client, self.PNG)
        assert res.status_code == 200
        # 応答は文体と同じ形（クライアントはこれで描き直すだけでよい）
        assert res.json()["personas"][0]["found"] is True
        assert client.get("/api/persona?scope=global").content == self.PNG

    def test_the_suffix_comes_from_the_content_not_the_declared_type(self, client):
        """**送られてきた名乗りは使わない。** 中身が GIF なら ``persona.gif``。"""
        self._post(client, self.GIF, content_type="image/png")
        assert (config.CATEGORIES_FILE.parent / "persona.gif").is_file()
        assert not (config.CATEGORIES_FILE.parent / "persona.png").exists()
        assert client.get("/api/persona?scope=global").headers["content-type"].startswith(
            "image/gif"
        )

    def test_something_that_is_not_an_image_is_refused(self, client):
        """`.png` という名前の HTML を置かせない（配る口は中身を検査しない）。"""
        res = self._post(client, b"<html><script>alert(1)</script></html>")
        assert res.status_code == 400
        assert client.get("/api/persona?scope=global").status_code == 404

    def test_svg_is_refused_too(self, client):
        res = self._post(client, b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
        assert res.status_code == 400

    def test_an_oversized_image_is_refused(self, client):
        from glosspop import ai

        res = self._post(client, self.PNG + b"\0" * ai.PERSONA_MAX_BYTES)
        assert res.status_code in (400, 413)
        assert client.get("/api/persona?scope=global").status_code == 404

    def test_replacing_clears_the_other_suffix(self, client):
        """**探索順で決まる顔を残さない**（「差し替えたのに変わらない」の正体）。"""
        self._put(config.CATEGORIES_FILE.parent, "persona.jpg")
        self._post(client, self.PNG)
        assert not (config.CATEGORIES_FILE.parent / "persona.jpg").exists()
        assert client.get("/api/persona?scope=global").content == self.PNG

    def test_a_face_can_be_deleted(self, client):
        self._post(client, self.PNG)
        res = client.delete("/api/persona?scope=global")
        assert res.status_code == 200
        assert res.json()["personas"][0]["found"] is False
        assert client.get("/api/persona?scope=global").status_code == 404
        # **ディレクトリは残す**（辞書とカテゴリマスターが入っている）
        assert config.CATEGORIES_FILE.parent.is_dir()

    def test_deleting_nothing_is_not_an_error(self, client):
        assert client.delete("/api/persona?scope=global").status_code == 200

    def test_an_unknown_scope_is_refused_on_write(self, client):
        assert self._post(client, self.PNG, scope="でたらめ").status_code == 400
        assert client.delete("/api/persona?scope=でたらめ").status_code == 400


class TestTheMapImage:
    """相関図の「地図」で敷く絵を配る口 (`/api/map`)。

    **顔と違って SVG を通す。** 地図は線画で拡大が本題なので、ラスタだと背景だけ
    ボケる（「にじむと SVG の意味が無い」と決めてある側と食い違う）。**通せる根拠は
    形式ではなく出し方**なので、そのヘッダをここで見張る —— 落とすと、直接開かれた
    ときにスクリプトがこちらのオリジンで動く。

    **名前は決め打ちにできない**（地図は辞書に数枚ある）ので、組み立てた結果が
    置き場所の中にあることも見る。
    """

    SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'

    def _put(self, name="ほんの図"):
        directory = store.maps_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.svg").write_bytes(self.SVG)

    def test_a_missing_map_is_404(self, client):
        assert client.get("/api/map", params={"name": "ない図"}).status_code == 404

    def test_an_unknown_scope_is_refused(self, client):
        assert client.get(
            "/api/map", params={"name": "ほんの図", "scope": "でたらめ"}
        ).status_code == 400

    def test_a_map_is_served_with_the_headers_that_make_svg_safe(self, client):
        self._put()
        res = client.get("/api/map", params={"name": "ほんの図"})
        assert res.status_code == 200
        assert res.content == self.SVG
        assert res.headers["content-type"].startswith("image/svg+xml")
        # **直接開かれたときの穴を塞ぐのはこの 1 行**（埋め込みだけでは足りない）
        assert res.headers["content-security-policy"] == "sandbox"
        assert res.headers["x-content-type-options"] == "nosniff"

    @pytest.mark.parametrize(
        "name", ["../categories", r"..\..\pyproject", "ほんの図/../../x", ""]
    )
    def test_a_name_that_escapes_the_folder_is_refused(self, client, name):
        self._put()
        assert client.get("/api/map", params={"name": name}).status_code == 404

    def test_the_graph_lists_the_maps_its_nodes_point_at(self, client):
        """候補は**出ている語から**作る（置いてある絵を並べる口は持たない）。"""
        self._put()
        client.post("/api/entries", json={
            "term": "港", "category": "場所", "definition": "船着き場。",
            "map": "ほんの図", "pin": [0.25, 0.5],
        })
        client.post("/api/entries", json={
            "term": "丘", "category": "場所", "definition": "小高いところ。",
        })
        body = client.get("/api/graph").json()
        assert [m["name"] for m in body["maps"]] == ["ほんの図"]
        assert body["maps"][0]["count"] == 1
        assert "v=" in body["maps"][0]["url"]     # 差し替えても古い絵が出ないように
        spots = {n["term"]: n["shape"] for n in body["nodes"]}
        assert spots == {"港": {"kind": "point", "points": [[0.25, 0.5]]}, "丘": None}

    def test_a_map_with_no_picture_is_not_offered(self, client):
        """座標は書いてあるが絵が無い、は候補に出さない（押しても 404 になる）。"""
        client.post("/api/entries", json={
            "term": "港", "category": "場所", "definition": "船着き場。",
            "map": "無い図", "pin": [0.25, 0.5],
        })
        assert client.get("/api/graph").json()["maps"] == []

    def test_a_line_and_an_area_are_folded_into_one_shape(self, client):
        """**書き方は 3 つ、内部は 1 つ。** 描く側に場合分けを持ち込まない。"""
        self._put()
        client.post("/api/entries", json={
            "term": "街道", "category": "場所", "definition": "道。",
            "map": "ほんの図", "path": [[0.1, 0.2], [0.4, 0.5], [0.8, 0.3]],
        })
        client.post("/api/entries", json={
            "term": "国", "category": "場所", "definition": "領域。",
            "map": "ほんの図", "area": [[0, 0], [1, 0], [0.5, 1]],
        })
        shapes = {n["term"]: n["shape"]["kind"] for n in client.get("/api/graph").json()["nodes"]}
        assert shapes == {"街道": "path", "国": "area"}

    def test_too_few_points_is_emptied(self, client):
        """線は 2 点、領域は 3 点から。足りなければ**丸ごと空**（半端を描かない）。"""
        client.post("/api/entries", json={
            "term": "街道", "category": "場所", "definition": "道。",
            "map": "ほんの図", "path": [[0.1, 0.2]],
        })
        assert client.get("/api/graph").json()["nodes"][0]["shape"] is None

    def test_writing_two_shapes_is_reported_by_the_doctor(self, client):
        """**黙って片方を選ばない。** 描くために細かいほうを採るが、点検が挙げる。"""
        client.post("/api/entries", json={
            "term": "港", "category": "場所", "definition": "船着き場。",
            "map": "ほんの図", "pin": [0.25, 0.5], "area": [[0, 0], [1, 0], [0.5, 1]],
        })
        assert client.get("/api/graph").json()["nodes"][0]["shape"]["kind"] == "area"
        kinds = [i["kind"] for i in client.get("/api/doctor").json()["issues"]]
        assert "two_map_shapes" in kinds

    def test_a_broken_coordinate_is_emptied_not_guessed(self, client):
        """読めない座標は**空にする**。0 に寄せると絵の左上に点が湧く。"""
        client.post("/api/entries", json={
            "term": "港", "category": "場所", "definition": "船着き場。",
            "map": "ほんの図", "pin": [0.25],
        })
        assert client.get("/api/graph").json()["nodes"][0]["shape"] is None
