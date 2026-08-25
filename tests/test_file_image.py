"""ファイルごとの画像（地図でも用語の画像でもない）。

**フォルダの一覧に出す 1 枚だけ。** 座標も関係も持たないので `core` を通らない。
ここで見張るのは、鍵まわりの 3 つの約束:

- **鍵は相対パス**（対応表を持たない）。だから**ファイル名が変われば切れる**
- **基準は辞書と同じ `local_root()`** —— 1 巻 2 巻で `.glosspop` を共有していたら、
  親を開いても子を開いても**同じ鍵**になる（開いているフォルダ基準にすると、
  親を開いた日と子を開いた日で絵が入れ替わる）
- **置き場所の外へ出る名前は通さない**
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from glosspop import config, store
from glosspop.app import app

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
GIF = b"GIF89a" + b"0" * 32


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _post(client, path, data=PNG):
    return client.post(
        "/api/file-image", params={"path": path}, content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


class TestTheRoundTrip:
    def test_put_get_and_delete(self, client, tmp_path):
        (config.content_dir() / "第一章.md").write_text("本文", encoding="utf-8")
        assert client.get("/api/file-image", params={"path": "第一章.md"}).status_code == 404

        res = _post(client, "第一章.md")
        assert res.status_code == 200
        # **URL に更新時刻を入れる**（入れないと差し替えても古い絵が出る）
        assert "v=" in res.json()["image_url"]

        got = client.get("/api/file-image", params={"path": "第一章.md"})
        assert got.content == PNG
        assert got.headers["content-type"] == "image/png"
        # 置かれたものをそのまま配る口なので、出し方で守る側は緩めない
        assert got.headers["content-security-policy"] == "sandbox"
        assert got.headers["x-content-type-options"] == "nosniff"

        assert client.delete("/api/file-image", params={"path": "第一章.md"}).status_code == 200
        assert client.get("/api/file-image", params={"path": "第一章.md"}).status_code == 404

    def test_the_extension_comes_from_the_content(self, client):
        """**名乗りは使わない。** 送られてきたのが GIF なら GIF として置く。"""
        (config.content_dir() / "第一章.md").write_text("本文", encoding="utf-8")
        _post(client, "第一章.md", GIF)
        got = client.get("/api/file-image", params={"path": "第一章.md"})
        assert got.headers["content-type"] == "image/gif"

    def test_the_list_carries_the_url(self, client):
        """一覧が絵を持ってくる。**引くのは 1 回の走査**（`list_file_images`）。"""
        (config.content_dir() / "第一章.md").write_text("本文", encoding="utf-8")
        (config.content_dir() / "第二章.md").write_text("本文", encoding="utf-8")
        _post(client, "第一章.md")
        files = {f["path"]: f for f in client.get("/api/content").json()["files"]}
        assert "/api/file-image?path=" in files["第一章.md"]["image_url"]
        # 置いていないものは項目ごと出さない（空文字を並べない）
        assert "image_url" not in files["第二章.md"]


class TestTheKey:
    def test_a_renamed_file_loses_its_image(self, client):
        """**鍵は相対パスなので、名前が変われば切れる**（そう決めた）。"""
        base = config.content_dir()
        (base / "第一章.md").write_text("本文", encoding="utf-8")
        _post(client, "第一章.md")
        (base / "第一章.md").rename(base / "序章.md")

        assert client.get("/api/file-image", params={"path": "序章.md"}).status_code == 404
        files = {f["path"]: f for f in client.get("/api/content").json()["files"]}
        assert "image_url" not in files["序章.md"]

    def test_the_key_is_the_same_from_the_parent_and_the_child(self, client, tmp_path):
        """**基準は辞書と同じ `local_root()`。**

        作品フォルダに `.glosspop` を 1 つ置いて 1 巻 2 巻で共有しているとき、
        **親を開いても子を開いても同じ絵が出る** —— 開いているフォルダ基準に
        すると、`1巻/第一章.md` と `第一章.md` で別の鍵になる。
        """
        work = tmp_path / "作品"
        (work / ".glosspop").mkdir(parents=True)
        volume = work / "1巻"
        volume.mkdir()
        (volume / "第一章.md").write_text("本文", encoding="utf-8")

        config.set_content_dir(volume)          # 子を開いて置く
        assert _post(client, "第一章.md").status_code == 200

        config.set_content_dir(work)            # 親を開いても同じ絵
        got = client.get("/api/file-image", params={"path": "1巻/第一章.md"})
        assert got.status_code == 200
        assert got.content == PNG

    def test_it_sits_beside_the_dictionary_mirroring_the_path(self, client, tmp_path):
        """置き場所は**相対パスを写したもの**（対応表を持たない）。"""
        base = config.content_dir()
        (base / "章").mkdir()
        (base / "章" / "一.md").write_text("本文", encoding="utf-8")
        _post(client, "章/一.md")
        found = store.file_image_file("章/一.md")
        assert found.relative_to(store.file_images_dir()).as_posix() == "章/一.md.png"


class TestWhatItRefuses:
    def test_it_will_not_leave_the_folder(self, client, tmp_path):
        """外へ出る名前は通さない（`_safe_content_path` と同じ関門）。"""
        (tmp_path / "外.md").write_text("よそ", encoding="utf-8")
        assert _post(client, "../外.md").status_code == 400

    def test_it_refuses_what_is_not_an_image(self, client):
        """中身が画像でなければ断る。**名乗りではなく中身で見分ける。**"""
        (config.content_dir() / "第一章.md").write_text("本文", encoding="utf-8")
        assert _post(client, "第一章.md", b"<svg xmlns=''></svg>").status_code == 400

    def test_it_refuses_a_file_that_is_not_there(self, client):
        assert _post(client, "無い.md").status_code == 404

    def test_a_url_reader_has_no_files(self, client):
        """**URL を読んでいるときは扱わない**（フォルダの中のファイルが無い）。"""
        config.set_reading_url("https://example.com/a")
        assert store.file_images_dir() is None


class TestItReachesThePublishedPage:
    """**端から端まで。** 一覧で引き当てた絵が、公開したページのカードになるか。

    単体（`test_publish.py`）は `write_site()` を直に叩くので、
    **`store.list_file_images()` → ページ → 隣に置く**の繋ぎは通っていない。
    """

    def test_the_image_becomes_the_card_of_that_document(
        self, client, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("GLOSSPOP_PUBLISH_DIR", str(tmp_path / "pages"))
        monkeypatch.setenv("GLOSSPOP_PUBLISH_BASE_URL", "https://example.github.io/repo/")
        (config.content_dir() / "第一章.md").write_text("冪等な操作。", encoding="utf-8")
        client.post("/api/entries", json={
            "term": "冪等", "category": "プログラミング", "definition": "何度でも同じ。",
        })
        assert _post(client, "第一章.md").status_code == 200

        made = client.post("/api/publish", json={"name": "site", "documents": True})
        assert made.status_code == 200, made.text
        site = tmp_path / "pages" / "site"
        doc = (site / "docs" / "第一章.html").read_text(encoding="utf-8")
        # 絵がページの隣に置かれ、カードのタグがそれを指している
        assert (site / "docs" / "第一章.png").read_bytes() == PNG
        assert "docs/%E7%AC%AC%E4%B8%80%E7%AB%A0.png?v=" in doc
        assert 'twitter:card" content="summary_large_image"' in doc

    def test_a_document_without_an_image_still_gets_a_small_card(
        self, client, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("GLOSSPOP_PUBLISH_DIR", str(tmp_path / "pages"))
        monkeypatch.setenv("GLOSSPOP_PUBLISH_BASE_URL", "https://example.github.io/repo/")
        (config.content_dir() / "第二章.md").write_text("本文。", encoding="utf-8")

        client.post("/api/publish", json={"name": "site", "documents": True})
        doc = (tmp_path / "pages" / "site" / "docs" / "第二章.html").read_text(encoding="utf-8")
        assert "og:image" not in doc
        assert 'twitter:card" content="summary"' in doc
