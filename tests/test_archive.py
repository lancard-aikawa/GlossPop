"""辞書の書き出しと取り込み（zip）。

取り込みは**置き換え**なので、このリポジトリで唯一データが消える経路。
「控えを取ってから消す」「途中で失敗しても半端な辞書を残さない」を見張る。
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from glosspop import archive, categories, config, store
from glosspop.models import EntryDraft


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _names(data: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return set(zf.namelist())


def _terms() -> set[str]:
    return {e.term for e in store.load_all()}


# --------------------------------------------------------------------------- #
# 書き出し
# --------------------------------------------------------------------------- #

def test_export_keeps_the_markdown_as_is(add_entry):
    """独自形式にしない。解凍すればエディタでそのまま読める。"""
    add_entry("冪等", category="プログラミング", definition="何度でも同じ。")
    data = archive.export_bytes()

    assert "glossary/プログラミング/冪等.md" in _names(data)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        body = zf.read("glossary/プログラミング/冪等.md").decode("utf-8")
        assert body.startswith("---\n") and "term: 冪等" in body
        manifest = json.loads(zf.read(archive.MANIFEST_NAME))
    assert manifest["app"] == "GlossPop" and manifest["entries"] == 1


def test_export_includes_the_category_master(add_entry):
    add_entry("冪等", category="プログラミング")
    assert archive.CATEGORIES_NAME in _names(archive.export_bytes())


def test_export_of_an_empty_glossary_still_works():
    assert archive.MANIFEST_NAME in _names(archive.export_bytes())


# --------------------------------------------------------------------------- #
# 一部だけ書き出す
#
# **決めるのは書き出す側だけ。** 取り込む側は何も変えていない —— 併合は
# 「入っているものを足して上書きする」だけなので、中身が一部でもそのまま通る。
# --------------------------------------------------------------------------- #

class TestExportingOneCategory:
    def test_takes_only_that_category(self, add_entry):
        add_entry("冪等", category="プログラミング")
        add_entry("ソース", category="料理")
        names = _names(archive.export_bytes(["料理"]))

        assert "glossary/料理/ソース.md" in names
        assert not any(name.startswith("glossary/プログラミング/") for name in names)

    def test_says_so_in_the_manifest(self, add_entry):
        """受け取った側が「これで全部」と思わないように、中身にも残す。"""
        add_entry("冪等", category="プログラミング")
        add_entry("ソース", category="料理")
        with zipfile.ZipFile(io.BytesIO(archive.export_bytes(["料理"]))) as zf:
            manifest = json.loads(zf.read(archive.MANIFEST_NAME))
        assert manifest["partial"] is True
        assert manifest["categories"] == ["料理"] and manifest["entries"] == 1

    def test_trims_the_category_master_too(self, add_entry):
        """渡す相手に関係の無いカテゴリの説明と並びを送りつけない。"""
        add_entry("冪等", category="プログラミング")
        add_entry("ソース", category="料理")
        with zipfile.ZipFile(io.BytesIO(archive.export_bytes(["料理"]))) as zf:
            master = zf.read(archive.CATEGORIES_NAME).decode("utf-8")
        assert "料理" in master and "プログラミング" not in master

    def test_a_full_export_keeps_the_master_file_as_is(self, add_entry):
        """全部書き出すときはファイルそのまま（控えもここを通る）。"""
        add_entry("冪等", category="プログラミング")
        with zipfile.ZipFile(io.BytesIO(archive.export_bytes())) as zf:
            assert zf.read(archive.CATEGORIES_NAME) == config.CATEGORIES_FILE.read_bytes()

    def test_merging_it_back_leaves_the_rest_alone(self, add_entry):
        """**取り込む側は変えていない。** 一部だけの zip でも併合はそのまま通る。"""
        add_entry("冪等", category="プログラミング")
        sauce = add_entry("ソース", category="料理", definition="元の説明。")
        part = archive.export_bytes(["料理"])

        store.save(
            EntryDraft(term="ソース", category="料理", definition="書き替えた。"), ref=sauce.ref
        )
        archive.import_bytes(part, "merge")
        assert _terms() == {"冪等", "ソース"}       # 手元にしか無い語は消えない
        assert "元の説明" in store.get(sauce.ref).definition   # 取り込む側が勝つ

    def test_replacing_with_a_partial_zip_still_replaces(self, add_entry):
        """一部の zip で「置き換え」を選べば、そのぶんだけの辞書になる。

        止めはしない（そう書いてあるとおりに動く）が、控えは必ず取る。
        """
        add_entry("冪等", category="プログラミング")
        add_entry("ソース", category="料理")
        part = archive.export_bytes(["料理"])

        report = archive.import_bytes(part, "replace")
        assert _terms() == {"ソース"}
        assert Path(report["backup"]).exists()


class TestExportPlan:
    def test_counts_what_goes_in(self, add_entry):
        add_entry("冪等", category="プログラミング")
        add_entry("ソース", category="料理")
        assert archive.export_plan(["料理"])["entries"] == 1
        assert archive.export_plan()["entries"] == 2

    def test_counts_relations_that_would_lose_their_target(self, add_entry):
        """**一部だけ渡すと、渡した先で相手の居ない関係ができる。**

        保存はできるが、リンクにも図の辺にもならない。押す前に数で見せる。
        """
        add_entry("ソース", category="料理")
        add_entry("冪等", category="プログラミング", relations=[{"to": "料理/ソース", "label": "例"}])

        whole = archive.export_plan()
        assert whole["dangling_count"] == 0        # 全部渡すなら誰も外に出ない

        part = archive.export_plan(["プログラミング"])
        assert part["dangling_count"] == 1
        assert part["dangling"] == ["冪等 → ソース"]

    def test_ignores_references_that_were_already_broken(self, add_entry):
        """壊れている参照は点検の担当。ここで二重に出さない。"""
        add_entry("冪等", category="プログラミング", relations=[{"to": "居ない語", "label": "例"}])
        assert archive.export_plan(["プログラミング"])["dangling_count"] == 0


# --------------------------------------------------------------------------- #
# 取り込み（置き換え）
# --------------------------------------------------------------------------- #

def test_import_replaces_instead_of_merging(add_entry):
    """zip に無いエントリは消える。混ぜない、が仕様。"""
    add_entry("冪等", category="プログラミング")
    exported = archive.export_bytes()

    store.save(EntryDraft(term="結果整合性", category="プログラミング"))
    add_entry("ソース", category="料理")
    assert _terms() == {"冪等", "結果整合性", "ソース"}

    report = archive.import_bytes(exported)
    assert _terms() == {"冪等"}                 # 書き出した時点の姿に戻る
    assert report["entries"] == 1


def test_import_takes_a_backup_before_deleting(add_entry):
    """**消す前に控えを取る。** 人の手順に任せない。"""
    add_entry("消えるほう", category="料理")
    incoming = _zip({
        "glossary/プログラミング/冪等.md": "---\nterm: 冪等\n---\n\n本文。\n",
        archive.MANIFEST_NAME: "{}",
    })

    report = archive.import_bytes(incoming)
    assert _terms() == {"冪等"}

    backup = config.DATA_ROOT / "data" / archive.BACKUP_DIR_NAME
    saved = list(backup.glob("backup-*.zip"))
    assert [str(p) for p in saved] == [report["backup"]]
    # 控えには消えたほうが入っている（そこから戻せる）
    assert "glossary/料理/消えるほう.md" in _names(saved[0].read_bytes())


def test_the_backup_can_be_imported_back(add_entry):
    """控えから戻せること。戻せない控えは控えではない。"""
    add_entry("冪等", category="プログラミング")
    report = archive.import_bytes(_zip({
        "glossary/料理/ソース.md": "---\nterm: ソース\n---\n\n本文。\n",
        archive.MANIFEST_NAME: "{}",
    }))
    assert _terms() == {"ソース"}

    archive.import_bytes(Path(report["backup"]).read_bytes())
    assert _terms() == {"冪等"}


def test_import_replaces_the_category_master(add_entry):
    add_entry("冪等", category="プログラミング")
    archive.import_bytes(_zip({
        "glossary/料理/ソース.md": "---\nterm: ソース\n---\n\n本文。\n",
        archive.CATEGORIES_NAME: "- name: 料理\n",
        archive.MANIFEST_NAME: "{}",
    }))
    assert [c.name for c in categories.load()] == ["料理"]


def test_import_leaves_no_half_written_glossary(add_entry):
    """入れ替えはディレクトリごと。作業用のフォルダを残さない。"""
    add_entry("冪等", category="プログラミング")
    archive.import_bytes(_zip({
        "glossary/料理/ソース.md": "---\nterm: ソース\n---\n\n本文。\n",
        archive.MANIFEST_NAME: "{}",
    }))
    siblings = {p.name for p in config.GLOSSARY_DIR.parent.iterdir() if p.is_dir()}
    assert not [n for n in siblings if ".incoming" in n or ".replaced" in n]


# --------------------------------------------------------------------------- #
# 通さないもの
# --------------------------------------------------------------------------- #

def test_a_zip_that_is_not_a_glossary_is_refused(add_entry):
    """アプリ本体の zip を取り込ませない（辞書が空で置き換わる）。"""
    add_entry("冪等", category="プログラミング")
    with pytest.raises(archive.ArchiveError, match="書き出した zip ではない"):
        archive.import_bytes(_zip({"GlossPop/glosspop.exe": "MZ", "GlossPop/_internal/x": ""}))
    assert _terms() == {"冪等"}       # 拒んだのだから消えていない


def test_broken_zip_is_refused(add_entry):
    add_entry("冪等", category="プログラミング")
    with pytest.raises(archive.ArchiveError, match="zip として読めません"):
        archive.import_bytes(b"not a zip at all")
    assert _terms() == {"冪等"}


def test_paths_escaping_the_glossary_are_refused(add_entry):
    """外から来た書庫をライブラリ任せにしない（installer と同じ規則）。"""
    add_entry("冪等", category="プログラミング")
    with pytest.raises(archive.ArchiveError):
        archive.import_bytes(_zip({
            "glossary/../../逃げた.md": "x",
            archive.MANIFEST_NAME: "{}",
        }))
    assert _terms() == {"冪等"}


def test_a_deeper_layout_is_not_taken_as_entries():
    """辞書は「カテゴリ / 用語」の 2 段と決まっている。"""
    with pytest.raises(archive.ArchiveError, match="書き出した zip ではない"):
        archive.inspect(_zip({"glossary/a/b/c.md": "x"}))


def test_a_huge_archive_is_refused(monkeypatch):
    monkeypatch.setattr(archive, "MAX_ARCHIVE_BYTES", 10)
    with pytest.raises(archive.ArchiveError, match="大きすぎます"):
        archive.inspect(_zip({archive.MANIFEST_NAME: "{}" * 100}))


# --------------------------------------------------------------------------- #
# 取り込み（併合）
#
# 衝突したら**取り込む側が勝つ**。規則を 1 つに絞る代わりに、控えを必ず取り、
# 上書きした語を全部名前で返す。`updated_at` の新しいほうを採る手を使わないのは、
# 時計がずれた PC が 1 台あると静かに古いほうが勝つため。
# --------------------------------------------------------------------------- #

def _entry(term: str, body: str = "本文。") -> str:
    return f"---\nterm: {term}\n---\n\n{body}\n"


def test_merge_keeps_what_is_only_here(add_entry):
    """**手元にしか無い語が消えないこと。** 置き換えとの違いはここ。"""
    add_entry("冪等", category="プログラミング")
    add_entry("ソース", category="料理")

    report = archive.import_bytes(_zip({
        "glossary/文学/銀河.md": _entry("銀河"),
        archive.MANIFEST_NAME: "{}",
    }), "merge")

    assert _terms() == {"冪等", "ソース", "銀河"}
    assert report["added"] == ["文学/銀河"]
    assert report["removed"] == []


def test_merge_lets_the_incoming_side_win(add_entry):
    add_entry("冪等", category="プログラミング", definition="手元で書いた説明。")

    report = archive.import_bytes(_zip({
        "glossary/プログラミング/冪等.md": _entry("冪等", "持ち込んだ説明。"),
        archive.MANIFEST_NAME: "{}",
    }), "merge")

    assert store.get("プログラミング/冪等").definition == "持ち込んだ説明。"
    # 上書きしたものは黙らずに名前で返す（控えからしか戻せないので）
    assert report["updated"] == ["プログラミング/冪等"]
    assert report["added"] == []


def test_merge_takes_a_backup_too(add_entry):
    """**上書きされた語は控えにしか残らない。** 併合でも必ず取る。"""
    add_entry("冪等", category="プログラミング", definition="手元で書いた説明。")

    report = archive.import_bytes(_zip({
        "glossary/プログラミング/冪等.md": _entry("冪等", "持ち込んだ説明。"),
        archive.MANIFEST_NAME: "{}",
    }), "merge")

    saved = Path(report["backup"]).read_bytes()
    with zipfile.ZipFile(io.BytesIO(saved)) as zf:
        text = zf.read("glossary/プログラミング/冪等.md").decode("utf-8")
    assert "手元で書いた説明。" in text


def test_merge_counts_identical_entries_as_unchanged(add_entry):
    add_entry("冪等", category="プログラミング")
    same = archive.export_bytes()

    report = archive.import_bytes(same, "merge")
    assert report["unchanged"] == 1
    assert report["added"] == [] and report["updated"] == []


def test_merge_keeps_the_local_category_order(add_entry):
    """**手元の並びを保ったまま足す。**

    マスターを丸ごと差し替えると、手元にしか無いカテゴリが順序と説明だけ失う
    （ディレクトリは残るので名前順に復活し、決めた並びが黙って崩れる）。
    """
    add_entry("冪等", category="プログラミング")
    add_entry("ソース", category="料理")
    categories.reorder(["料理", "プログラミング"])
    categories.ensure("料理", description="手元の説明")

    archive.import_bytes(_zip({
        "glossary/文学/銀河.md": _entry("銀河"),
        archive.CATEGORIES_NAME:
            "- name: 文学\n- name: 料理\n  subcategories: [和食]\n",
        archive.MANIFEST_NAME: "{}",
    }), "merge")

    # 手元の順が先、持ち込んだだけのものは後ろ
    assert [c.name for c in categories.load()] == ["料理", "プログラミング", "文学"]
    # サブカテゴリは和集合（値ではなく先出しの候補なので消す理由が無い）
    assert categories.get("料理").subcategories == ["和食"]
    assert categories.get("料理").description == "手元の説明"


def test_replace_still_wipes_the_master(add_entry):
    add_entry("冪等", category="プログラミング")
    archive.import_bytes(_zip({
        "glossary/料理/ソース.md": _entry("ソース"),
        archive.CATEGORIES_NAME: "- name: 料理\n",
        archive.MANIFEST_NAME: "{}",
    }), "replace")
    assert [c.name for c in categories.load()] == ["料理"]


def test_the_plan_shows_what_would_disappear(add_entry):
    """**置き換えで消える語を押す前に見せる。** いちばん怖いのがそれ。"""
    add_entry("冪等", category="プログラミング")
    add_entry("ソース", category="料理")
    incoming = _zip({
        "glossary/プログラミング/冪等.md": _entry("冪等", "別の説明。"),
        "glossary/文学/銀河.md": _entry("銀河"),
        archive.MANIFEST_NAME: "{}",
    })

    replace = archive.plan(incoming, "replace")
    assert replace["removed"] == ["料理/ソース"]
    assert replace["added"] == ["文学/銀河"]
    assert replace["updated"] == ["プログラミング/冪等"]

    merge = archive.plan(incoming, "merge")
    assert merge["removed"] == []           # 併合では消えない
    assert merge["added"] == ["文学/銀河"]


def test_the_plan_changes_nothing(add_entry):
    add_entry("冪等", category="プログラミング")
    archive.plan(_zip({
        "glossary/料理/ソース.md": _entry("ソース"),
        archive.MANIFEST_NAME: "{}",
    }), "replace")
    assert _terms() == {"冪等"}
    assert not list((config.DATA_ROOT / "data" / archive.BACKUP_DIR_NAME).glob("*.zip"))


def test_an_unknown_mode_is_refused():
    with pytest.raises(archive.ArchiveError, match="不明な取り込み方"):
        archive.import_bytes(_zip({archive.MANIFEST_NAME: "{}"}), "そのほか")


def test_a_broken_master_does_not_undo_the_import(add_entry):
    """**エントリはもう入れ替わっている。** ここで例外にすると片手落ちになる。"""
    add_entry("冪等", category="プログラミング")
    archive.import_bytes(_zip({
        "glossary/文学/銀河.md": _entry("銀河"),
        archive.CATEGORIES_NAME: "- name: [壊れた\n",
        archive.MANIFEST_NAME: "{}",
    }), "merge")
    assert _terms() == {"冪等", "銀河"}
