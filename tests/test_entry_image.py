"""用語ごとの画像（語り手の顔とは別物）。

**顔は「誰が書いているか」で辞書に 1 枚、こちらは「その語そのもの」で語ごと。**
だからエントリと同じ 2 段（`images/<カテゴリ>/<slug>.<拡張子>`）に置き、
**エントリが動けば一緒に動く**。ここで見張るのはその追従と、外へ出る名前を
通さないこと。
"""

from __future__ import annotations

import io
import zipfile

import pytest

from glosspop import archive, config, merge, store

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40
GIF = b"GIF89a" + b"0" * 40


@pytest.fixture
def put_image():
    """その語の画像を置く。**置き場所は `store` 任せ**（テストが組み立てない）。"""
    def _put(ref: str, data: bytes = PNG, suffix: str = ".png"):
        path = store.image_path(ref, suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    return _put


class TestWhereItGoes:
    def test_it_sits_beside_the_dictionary_in_two_levels(self, add_entry, put_image):
        entry = add_entry("赤シャツ", category="人物")
        put_image(entry.ref)
        found = store.image_file(entry.ref)
        assert found.relative_to(store.images_dir()).as_posix() == "人物/赤シャツ.png"

    def test_the_same_term_in_two_categories_gets_two_images(self, add_entry, put_image):
        """**名前だけを鍵にしない。** カテゴリ違いの同名はこの辞書の狙いどおりの機能。"""
        a = add_entry("ソース", category="料理")
        b = add_entry("ソース", category="プログラミング")
        put_image(a.ref, PNG)
        put_image(b.ref, GIF)
        assert store.image_file(a.ref).read_bytes() == PNG
        assert store.image_file(b.ref).read_bytes() == GIF

    @pytest.mark.parametrize("ref", ["../persona", "人物/../../x", "人物", "", "人物/"])
    def test_a_ref_that_escapes_is_refused(self, ref):
        assert store.image_file(ref) is None
        assert store.image_path(ref, ".png") is None

    def test_svg_is_not_a_place_to_write(self):
        """**SVG は通さない**（配る口が出し方で守っていない側）。"""
        assert store.image_path("人物/赤シャツ", ".svg") is None

    def test_the_index_is_built_in_one_walk(self, add_entry, put_image):
        a = add_entry("赤シャツ", category="人物")
        add_entry("山嵐", category="人物")
        put_image(a.ref)
        assert {ref: p.name for ref, p in store.list_images().items()} \
            == {a.ref: "赤シャツ.png"}


class TestItFollowsTheEntry:
    def test_moving_the_category_moves_the_image(self, add_entry, put_image):
        """**動かさないと元のカテゴリに取り残される**（画面からは消えて見える）。"""
        entry = add_entry("赤シャツ", category="人物")
        put_image(entry.ref)
        moved = store.move(entry.ref, "主要人物")

        assert store.image_file(moved.ref) is not None
        assert store.image_file(entry.ref) is None

    def test_deleting_the_entry_deletes_the_image(self, add_entry, put_image):
        """残すと、同じ名前で登録し直したときに**前の語の画像が出る**。"""
        entry = add_entry("赤シャツ", category="人物")
        put_image(entry.ref)
        store.delete(entry.ref)

        assert store.image_file(entry.ref) is None
        again = add_entry("赤シャツ", category="人物")
        assert store.image_file(again.ref) is None

    def test_merging_takes_over_when_the_keeper_has_none(self, add_entry, put_image):
        """消える側にしか無い画像を落とすと、**まとめた結果として絵が消える**。"""
        keep = add_entry("赤シャツ", category="人物")
        drop = add_entry("教頭", category="人物")
        put_image(drop.ref)

        merged = merge.apply(keep.ref, drop.ref)

        assert store.image_file(merged.ref) is not None
        assert store.image_file(drop.ref) is None

    def test_merging_keeps_the_keepers_image(self, add_entry, put_image):
        keep = add_entry("赤シャツ", category="人物")
        drop = add_entry("教頭", category="人物")
        put_image(keep.ref, PNG)
        put_image(drop.ref, GIF)

        merged = merge.apply(keep.ref, drop.ref)

        assert store.image_file(merged.ref).read_bytes() == PNG
        assert store.image_file(drop.ref) is None


class TestItTravelsInTheZip:
    """地図と同じ扱い —— **入れないと渡した先で画像だけ消える**。
    ただし**取り込みでは消さない**（zip に無い ＝ 消してよい、ではない）。
    """

    def test_export_carries_them(self, add_entry, put_image):
        entry = add_entry("赤シャツ", category="人物")
        put_image(entry.ref)
        with zipfile.ZipFile(io.BytesIO(archive.export_bytes())) as zf:
            assert "images/人物/赤シャツ.png" in zf.namelist()

    def test_only_the_chosen_category(self, add_entry, put_image):
        a = add_entry("赤シャツ", category="人物")
        b = add_entry("冪等", category="プログラミング")
        put_image(a.ref)
        put_image(b.ref)
        with zipfile.ZipFile(io.BytesIO(archive.export_bytes(["人物"]))) as zf:
            names = [n for n in zf.namelist() if n.startswith("images/")]
        assert names == ["images/人物/赤シャツ.png"]

    def test_import_puts_them_back(self, add_entry, put_image):
        entry = add_entry("赤シャツ", category="人物")
        put_image(entry.ref)
        data = archive.export_bytes()
        store.delete_image(entry.ref)

        report = archive.import_bytes(data, "replace")

        assert store.image_file(entry.ref) is not None
        assert report["images_added_count"] == 1

    def test_replace_does_not_delete_images_the_zip_lacks(self, add_entry, put_image):
        entry = add_entry("赤シャツ", category="人物")
        without = archive.export_bytes()          # まだ画像が無いうちに書き出す
        put_image(entry.ref)

        archive.import_bytes(without, "replace")

        assert store.image_file(entry.ref) is not None

    def test_a_refused_suffix_never_lands(self, add_entry):
        """**配る口は中身を検査せずに返す。** 名乗りだけで置き場所に入れない。"""
        add_entry("赤シャツ", category="人物")
        data = archive.export_bytes()
        with zipfile.ZipFile(io.BytesIO(data), "a") as zf:
            zf.writestr("images/人物/わな.html", "<script>alert(1)</script>")
            zf.writestr("images/人物/わな.svg", "<svg onload='alert(1)'/>")

        archive.import_bytes(data, "merge")

        directory = store.images_dir()
        assert not list(directory.rglob("わな.*")) if directory.is_dir() else True

    def test_a_deeper_layout_is_not_taken_as_an_image(self, add_entry):
        """**辞書と同じ 2 段**と決まっている（それ以外は別のものとみなす）。"""
        add_entry("赤シャツ", category="人物")
        data = archive.export_bytes()
        with zipfile.ZipFile(io.BytesIO(data), "a") as zf:
            zf.writestr("images/人物/奥/深い.png", PNG.decode("latin-1"))

        archive.import_bytes(data, "merge")

        assert not (config.GLOSSARY_DIR.parent / "images" / "人物" / "奥").exists()
