from __future__ import annotations

import pytest

from glosspop import store
from glosspop.models import EntryDraft, slugify


def test_roundtrip_preserves_fields(add_entry):
    saved = add_entry(
        "イミュータブル",
        reading="いみゅーたぶる",
        aliases=["immutable"],
        category="プログラミング",
        subcategory="設計",
        summary="生成後に変更できない値。",
        definition="本文の *Markdown*。\n\n- 箇条書き\n",
        examples=["tuple はイミュータブル"],
        related=["ミュータブル"],
        tags=["設計原則"],
        source="docs/x.md",
    )
    store.invalidate()
    loaded = store.get(saved.slug)
    assert loaded is not None
    for field in ("term", "reading", "aliases", "category", "subcategory",
                  "summary", "definition", "examples", "related", "tags", "source"):
        assert getattr(loaded, field) == getattr(saved, field), field
    assert loaded.created_at == saved.created_at


def test_file_is_readable_markdown(add_entry):
    entry = add_entry("用語", category="カテゴリ", definition="# 見出し\n\n本文")
    text = store.path_for(entry.slug).read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "term: 用語" in text
    assert text.rstrip().endswith("本文")


def test_duplicate_term_rejected(add_entry):
    add_entry("重複", aliases=["dup"])
    with pytest.raises(store.StoreError):
        store.save(EntryDraft(term="重複"))
    with pytest.raises(store.StoreError):
        store.save(EntryDraft(term="DUP"))  # 別名・大文字小文字も衝突扱い


def test_update_keeps_created_at_and_slug(add_entry):
    entry = add_entry("更新前", summary="v1")
    updated = store.save(EntryDraft(term="更新後", summary="v2"), slug=entry.slug)
    assert updated.slug == entry.slug
    assert updated.created_at == entry.created_at
    assert updated.summary == "v2"
    assert store.get(entry.slug).term == "更新後"


def test_delete(add_entry):
    entry = add_entry("消す")
    assert store.delete(entry.slug) is True
    assert store.delete(entry.slug) is False
    assert store.get(entry.slug) is None


def test_find_by_surface_matches_alias(add_entry):
    entry = add_entry("キャッシュ", aliases=["cache"])
    assert store.find_by_surface("cache").slug == entry.slug
    assert store.find_by_surface("  CACHE ").slug == entry.slug
    assert store.find_by_surface("存在しない") is None


def test_slug_collision_gets_suffix():
    a = store.save(EntryDraft(term="A B"))
    b = store.save(EntryDraft(term="a/b"))  # slugify すると同じ "a-b"
    assert a.slug == "a-b"
    assert b.slug == "a-b-2"


def test_category_tree(add_entry):
    add_entry("x1", category="C1", subcategory="S1")
    add_entry("x2", category="C1", subcategory="S1")
    add_entry("x3", category="C1", subcategory="S2")
    add_entry("x4", category="C2")
    tree = store.category_tree()
    assert [n["category"] for n in tree] == ["C1", "C2"]
    assert tree[0]["count"] == 3
    assert tree[0]["subcategories"] == [{"name": "S1", "count": 2}, {"name": "S2", "count": 1}]
    assert tree[1]["subcategories"] == [{"name": "", "count": 1}]


def test_path_traversal_rejected():
    for bad in ("../evil", "sub/evil", ".hidden", "", "a\\b"):
        with pytest.raises(store.StoreError):
            store.path_for(bad)


def test_hand_written_file_is_loaded():
    from glosspop import config

    (config.GLOSSARY_DIR / "手書き.md").write_text(
        "---\nterm: 手書き\ncategory: メモ\naliases:\n  - handwritten\n---\n\n本文です。\n",
        encoding="utf-8",
    )
    store.invalidate()
    entry = store.get("手書き")
    assert entry is not None
    assert entry.aliases == ["handwritten"]
    assert entry.definition == "本文です。"


def test_broken_frontmatter_raises():
    from glosspop import config

    (config.GLOSSARY_DIR / "broken.md").write_text(
        "---\nterm: [unclosed\n---\n\n本文\n", encoding="utf-8"
    )
    store.invalidate()
    with pytest.raises(store.StoreError):
        store.load_all()


@pytest.mark.parametrize(
    "term,expected",
    [
        ("API 設計", "api-設計"),
        ("C#", "c#"),
        ("a/b:c", "a-b-c"),
        ("  spaced  out  ", "spaced-out"),
    ],
)
def test_slugify(term, expected):
    assert slugify(term) == expected


def test_slugify_falls_back_for_unusable_terms():
    assert slugify("///").startswith("term-")
    assert slugify("CON") != "con"
