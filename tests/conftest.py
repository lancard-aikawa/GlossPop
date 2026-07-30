from __future__ import annotations

import pytest

from glosspop import config, store


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """各テストを使い捨ての辞書 / content ディレクトリで走らせる。"""
    glossary = tmp_path / "glossary"
    content = tmp_path / "content"
    glossary.mkdir()
    content.mkdir()
    monkeypatch.setattr(config, "GLOSSARY_DIR", glossary)
    monkeypatch.setattr(config, "CONTENT_DIR", content)
    store.invalidate()
    yield tmp_path
    store.invalidate()


@pytest.fixture
def add_entry():
    from glosspop.models import EntryDraft

    def _add(term: str, **kwargs):
        return store.save(EntryDraft(term=term, **kwargs))

    return _add
