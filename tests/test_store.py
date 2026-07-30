from __future__ import annotations

import pytest

from glosspop import categories, config, store
from glosspop.models import CategoryNameError, EntryDraft, normalize_category, slugify


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
    loaded = store.get(saved.ref)
    assert loaded is not None
    for field in ("term", "reading", "aliases", "category", "subcategory",
                  "summary", "definition", "examples", "related", "tags", "source"):
        assert getattr(loaded, field) == getattr(saved, field), field
    assert loaded.created_at == saved.created_at


def test_file_lives_under_category_directory(add_entry):
    entry = add_entry("用語", category="カテゴリ", definition="# 見出し\n\n本文")
    path = config.GLOSSARY_DIR / "カテゴリ" / "用語.md"
    assert path.exists()
    assert store.path_for_ref(entry.ref) == path
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "term: 用語" in text
    # カテゴリはディレクトリ名が正なので frontmatter には書かない
    assert "category:" not in text
    assert text.rstrip().endswith("本文")


def test_same_term_in_different_categories(add_entry):
    a = add_entry("ソース", category="プログラミング", summary="コード")
    b = add_entry("ソース", category="料理", summary="調味料")
    assert a.ref == "プログラミング/ソース"
    assert b.ref == "料理/ソース"
    hits = store.find_by_surface("ソース")
    assert {e.ref for e in hits} == {a.ref, b.ref}


def test_duplicate_within_same_category_rejected(add_entry):
    add_entry("重複", category="A", aliases=["dup"])
    with pytest.raises(store.StoreError):
        store.save(EntryDraft(term="重複", category="A"))
    with pytest.raises(store.StoreError):
        store.save(EntryDraft(term="DUP", category="A"))  # 別名・大文字小文字も衝突扱い
    # 別カテゴリなら通る
    assert store.save(EntryDraft(term="重複", category="B")).ref == "B/重複"


def test_update_keeps_created_at_and_ref(add_entry):
    entry = add_entry("更新前", category="A", summary="v1")
    updated = store.save(EntryDraft(term="更新後", category="A", summary="v2"), ref=entry.ref)
    assert updated.ref == entry.ref          # 同カテゴリ更新はファイル名を変えない
    assert updated.created_at == entry.created_at
    assert updated.summary == "v2"
    assert store.get(entry.ref).term == "更新後"


def test_changing_category_moves_the_file(add_entry):
    entry = add_entry("移動する語", category="旧カテゴリ", summary="そのまま")
    moved = store.move(entry.ref, "新カテゴリ")
    assert moved.ref == "新カテゴリ/移動する語"
    assert moved.summary == "そのまま"
    assert moved.created_at == entry.created_at
    assert not (config.GLOSSARY_DIR / "旧カテゴリ" / "移動する語.md").exists()
    assert (config.GLOSSARY_DIR / "新カテゴリ" / "移動する語.md").exists()
    assert store.get(entry.ref) is None


def test_move_into_occupied_category_is_rejected(add_entry):
    add_entry("かぶる", category="A")
    b = add_entry("かぶる", category="B")
    with pytest.raises(store.StoreError):
        store.move(b.ref, "A")


def test_delete(add_entry):
    entry = add_entry("消す", category="A")
    assert store.delete(entry.ref) is True
    assert store.delete(entry.ref) is False
    assert store.get(entry.ref) is None


def test_find_by_surface_matches_alias(add_entry):
    entry = add_entry("キャッシュ", category="A", aliases=["cache"])
    assert [e.ref for e in store.find_by_surface("cache")] == [entry.ref]
    assert [e.ref for e in store.find_by_surface("  CACHE ")] == [entry.ref]
    assert store.find_by_surface("存在しない") == []


def test_slug_collision_within_category_gets_suffix():
    a = store.save(EntryDraft(term="A B", category="X"))
    b = store.save(EntryDraft(term="a/b", category="X"))  # slugify すると同じ "a-b"
    assert a.slug == "a-b"
    assert b.slug == "a-b-2"


def test_category_tree_includes_empty_categories(add_entry):
    add_entry("x1", category="C1", subcategory="S1")
    add_entry("x2", category="C1", subcategory="S1")
    add_entry("x3", category="C1", subcategory="S2")
    categories.ensure("空カテゴリ")
    tree = {n["category"]: n for n in store.category_tree()}
    assert tree["C1"]["count"] == 3
    assert tree["C1"]["subcategories"] == [{"name": "S1", "count": 2}, {"name": "S2", "count": 1}]
    assert tree["空カテゴリ"]["count"] == 0


def test_rename_category_moves_directory(add_entry):
    add_entry("語", category="旧名")
    moved = store.rename_category("旧名", "新名")
    assert moved == 1
    assert (config.GLOSSARY_DIR / "新名" / "語.md").exists()
    assert not (config.GLOSSARY_DIR / "旧名").exists()
    assert categories.names() == ["新名"]
    assert store.get("新名/語") is not None


def test_delete_category_requires_empty(add_entry):
    add_entry("語", category="消せない")
    with pytest.raises(store.StoreError):
        store.delete_category("消せない")
    categories.ensure("消せる")
    store.delete_category("消せる")
    assert "消せる" not in categories.names()


def test_path_traversal_rejected():
    for bad in ("sub/evil", ".hidden", "", "a\\b"):
        with pytest.raises(store.StoreError):
            store.path_for("カテゴリ", bad)
    with pytest.raises(CategoryNameError):
        store.path_for("../evil", "slug")


def test_hand_written_file_is_loaded():
    directory = config.GLOSSARY_DIR / "手動"
    directory.mkdir()
    (directory / "手書き.md").write_text(
        "---\nterm: 手書き\naliases:\n  - handwritten\n---\n\n本文です。\n",
        encoding="utf-8",
    )
    store.invalidate()
    categories.invalidate()
    entry = store.get("手動/手書き")
    assert entry is not None
    assert entry.category == "手動"       # ディレクトリ名が正
    assert entry.aliases == ["handwritten"]
    assert entry.definition == "本文です。"
    # mkdir されただけのカテゴリはマスターに取り込まれる
    assert "手動" in categories.names()


def test_broken_frontmatter_raises():
    directory = config.GLOSSARY_DIR / "壊れ"
    directory.mkdir()
    (directory / "broken.md").write_text("---\nterm: [unclosed\n---\n\n本文\n", encoding="utf-8")
    store.invalidate()
    with pytest.raises(store.StoreError):
        store.load_all()


def test_migrates_flat_layout():
    (config.GLOSSARY_DIR / "旧.md").write_text(
        "---\nterm: 旧\ncategory: 昔のカテゴリ\n---\n\n本文\n", encoding="utf-8"
    )
    (config.GLOSSARY_DIR / "無所属.md").write_text("---\nterm: 無所属\n---\n\n本文\n", encoding="utf-8")
    moved = store.migrate_layout()
    assert len(moved) == 2
    assert (config.GLOSSARY_DIR / "昔のカテゴリ" / "旧.md").exists()
    assert (config.GLOSSARY_DIR / "未分類" / "無所属.md").exists()
    assert not list(config.GLOSSARY_DIR.glob("*.md"))
    assert store.get("昔のカテゴリ/旧") is not None
    # 二度目は何もしない
    assert store.migrate_layout() == []


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


class TestCategoryNames:
    @pytest.mark.parametrize("name", ["プログラミング", "Web 開発", "C++", "設計・実装", "日本語OK", "a.b"])
    def test_accepted(self, name):
        assert normalize_category(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "", "   ",
            "a/b", "a\\b", "a:b", "a*b", "a?b", "a<b", "a>b", 'a"b', "a|b",
            "a#b", "a%b",
            ".hidden", "trailing.",
            "CON", "nul", "COM1", "LPT9", "con.txt",
            "x" * 41,
            "改行\nあり", "タブ\tあり",
        ],
    )
    def test_rejected(self, name):
        with pytest.raises(CategoryNameError):
            normalize_category(name)

    def test_trims_and_normalizes_to_nfc(self):
        assert normalize_category("  料理  ") == "料理"
        # NFD の「が」(か + 濁点) は NFC に畳まれる
        assert normalize_category("がぞう") == "がぞう"

    def test_error_message_names_the_bad_character(self):
        with pytest.raises(CategoryNameError, match="/"):
            normalize_category("a/b")
