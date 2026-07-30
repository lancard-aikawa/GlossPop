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
    monkeypatch.setattr(store, "_ready", False)
    # 「フォルダを開く」の上書きはプロセス内に残るのでテスト間で持ち越さない
    config.set_content_dir(None)
    store.invalidate()
    categories.invalidate()
    yield tmp_path
    config.set_content_dir(None)
    store.invalidate()
    categories.invalidate()


@pytest.fixture
def add_entry():
    from glosspop.models import EntryDraft

    def _add(term: str, **kwargs):
        kwargs.setdefault("category", "テスト")
        return store.save(EntryDraft(term=term, **kwargs))

    return _add
