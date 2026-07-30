"""辞書ストア: 1 用語 = 1 Markdown ファイル (YAML frontmatter + 本文)。

本文がそのまま ``definition`` になるので、エディタで直接開いても
Claude が読んでも自然に扱える形になっている。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import yaml

from . import config, render
from .models import Entry, EntryDraft, now_iso, slugify

#: frontmatter に書き出すキーの順序
_FM_KEYS = (
    "term",
    "reading",
    "aliases",
    "category",
    "subcategory",
    "summary",
    "examples",
    "related",
    "tags",
    "source",
    "created_at",
    "updated_at",
)

# save() が中で load_all() を呼ぶので再入可能ロックにする
_lock = threading.RLock()
_cache: tuple[object, list[Entry]] | None = None


class StoreError(Exception):
    pass


# --------------------------------------------------------------------------- #
# シリアライズ
# --------------------------------------------------------------------------- #

def parse_markdown(text: str) -> tuple[dict, str]:
    """frontmatter と本文に分割する。frontmatter が無ければ全体を本文とみなす。"""
    if not text.startswith("---"):
        return {}, text.strip()
    lines = text.splitlines()
    if lines[0].strip() != "---":
        return {}, text.strip()
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            raw = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1:]).strip()
            try:
                meta = yaml.safe_load(raw) or {}
            except yaml.YAMLError as exc:
                raise StoreError(f"frontmatter の YAML が壊れています: {exc}") from exc
            if not isinstance(meta, dict):
                raise StoreError("frontmatter がマッピングではありません")
            return meta, body
    # 閉じ `---` が無い → 全体を本文扱い
    return {}, text.strip()


def dump_markdown(entry: Entry) -> str:
    meta = {}
    for key in _FM_KEYS:
        value = getattr(entry, key)
        # 空文字 / 空リストは書かない (ファイルをノイズで埋めない)
        if value in ("", [], None):
            continue
        meta[key] = value
    front = yaml.safe_dump(
        meta,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    ).rstrip()
    body = entry.definition.strip()
    return f"---\n{front}\n---\n\n{body}\n"


def _entry_from_file(path: Path) -> Entry:
    meta, body = parse_markdown(path.read_text(encoding="utf-8"))
    meta = dict(meta)
    meta["definition"] = body
    meta["slug"] = path.stem
    meta.setdefault("term", path.stem)
    return Entry.model_validate(meta)


# --------------------------------------------------------------------------- #
# 読み出し (mtime ベースの簡易キャッシュ付き)
# --------------------------------------------------------------------------- #

def _signature() -> object:
    if not config.GLOSSARY_DIR.exists():
        return ()
    sig = []
    with os.scandir(config.GLOSSARY_DIR) as it:
        for e in it:
            if e.is_file() and e.name.endswith(".md"):
                st = e.stat()
                sig.append((e.name, st.st_mtime_ns, st.st_size))
    return tuple(sorted(sig))


def load_all(*, force: bool = False) -> list[Entry]:
    """全エントリを term 昇順で返す。壊れたファイルは黙って飛ばさず例外にする。"""
    global _cache
    with _lock:
        sig = _signature()
        if not force and _cache is not None and _cache[0] == sig:
            return _cache[1]
        entries: list[Entry] = []
        if config.GLOSSARY_DIR.exists():
            for path in sorted(config.GLOSSARY_DIR.glob("*.md")):
                entries.append(_entry_from_file(path))
        entries.sort(key=lambda e: (e.category, e.subcategory, e.reading or e.term))
        _cache = (sig, entries)
        return entries


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def path_for(slug: str) -> Path:
    """slug からファイルパスを作る。ディレクトリ外への脱出を防ぐ。"""
    if not slug or "/" in slug or "\\" in slug or slug.startswith("."):
        raise StoreError(f"不正な slug: {slug!r}")
    path = (config.GLOSSARY_DIR / f"{slug}.md").resolve()
    if path.parent != config.GLOSSARY_DIR.resolve():
        raise StoreError(f"不正な slug: {slug!r}")
    return path


def get(slug: str) -> Entry | None:
    for e in load_all():
        if e.slug == slug:
            return e
    return None


def find_by_surface(surface: str) -> Entry | None:
    """用語名 or 別名の完全一致 (大文字小文字無視) で引く。"""
    needle = surface.strip().casefold()
    for e in load_all():
        for s in e.surfaces:
            if s.casefold() == needle:
                return e
    return None


# --------------------------------------------------------------------------- #
# 書き込み
# --------------------------------------------------------------------------- #

def _allocate_slug(term: str) -> str:
    base = slugify(term)
    candidate = base
    n = 2
    while path_for(candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def save(draft: EntryDraft, *, slug: str | None = None) -> Entry:
    """新規作成 (slug=None) または更新。

    新規で同じ用語がすでにある場合は ``StoreError`` にする — 上書きは
    slug を明示した更新でのみ起こる。
    """
    if not draft.term:
        raise StoreError("term は必須です")

    with _lock:
        config.ensure_dirs()
        if slug is None:
            existing = find_by_surface(draft.term)
            if existing is not None:
                raise StoreError(
                    f"「{draft.term}」は既に登録されています (slug: {existing.slug})"
                )
            slug = _allocate_slug(draft.term)
            created = now_iso()
        else:
            path = path_for(slug)
            if not path.exists():
                raise StoreError(f"見つかりません: {slug}")
            created = _entry_from_file(path).created_at

        # 保存時に本文を 1 文 1 行へ整える。ファイル自体が読みやすくなり、
        # git の差分も文単位になる
        data = draft.model_dump()
        data["definition"] = render.soften_paragraphs(data["definition"])
        entry = Entry(**data, slug=slug, created_at=created, updated_at=now_iso())
        _write_atomic(path_for(slug), dump_markdown(entry))
        global _cache
        _cache = None
        return entry


def delete(slug: str) -> bool:
    with _lock:
        path = path_for(slug)
        if not path.exists():
            return False
        path.unlink()
        global _cache
        _cache = None
        return True


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# 一覧・集計
# --------------------------------------------------------------------------- #

def category_tree() -> list[dict]:
    """[{category, count, subcategories: [{name, count}]}] をカテゴリ名順で返す。"""
    tree: dict[str, dict[str, int]] = {}
    for e in load_all():
        subs = tree.setdefault(e.category, {})
        subs[e.subcategory] = subs.get(e.subcategory, 0) + 1
    out = []
    for cat in sorted(tree):
        subs = tree[cat]
        out.append(
            {
                "category": cat,
                "count": sum(subs.values()),
                "subcategories": [
                    {"name": name, "count": subs[name]}
                    for name in sorted(subs, key=lambda s: (s == "", s))
                ],
            }
        )
    return out
