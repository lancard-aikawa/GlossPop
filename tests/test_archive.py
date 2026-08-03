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
