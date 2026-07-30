from __future__ import annotations

import pytest

from glosspop import categories, config, store


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """各テストを使い捨ての辞書 / content ディレクトリで走らせる。"""
    glossary = tmp_path / "glossary"
    content = tmp_path / "content"
    glossary.mkdir()
    content.mkdir()
    monkeypatch.setattr(config, "GLOSSARY_DIR", glossary)
    monkeypatch.setattr(config, "CONTENT_DIR", content)
    monkeypatch.setattr(config, "CATEGORIES_FILE", tmp_path / "categories.yaml")
    monkeypatch.setattr(config, "SITES_DIR", tmp_path / "sites")
    monkeypatch.setattr(store, "_ready", False)
    # 「フォルダを開く」「URL を読む」の状態はプロセス内に残るので持ち越さない
    config.set_content_dir(None)
    config.set_reading_url(None)
    store.invalidate()
    categories.invalidate()
    yield tmp_path
    config.set_content_dir(None)
    config.set_reading_url(None)
    store.invalidate()
    categories.invalidate()


@pytest.fixture
def add_entry():
    from glosspop.models import EntryDraft

    def _add(term: str, **kwargs):
        kwargs.setdefault("category", "テスト")
        return store.save(EntryDraft(term=term, **kwargs))

    return _add
