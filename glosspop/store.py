"""辞書ストア: ``data/glossary/<カテゴリ>/<slug>.md`` (YAML frontmatter + 本文)。

ディレクトリ名がカテゴリ、ファイル名が slug の正。frontmatter には書かない。
同じ用語名でもカテゴリが違えば別エントリとして併存できる（「ソース」が
プログラミングにも料理にもある、というケース）。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import yaml

from . import categories, config, render
from .models import (
    CategoryNameError,
    Entry,
    EntryDraft,
    normalize_category,
    now_iso,
    slugify,
    split_ref,
)

#: frontmatter に書き出すキーの順序 (category はディレクトリ名が正なので書かない)
_FM_KEYS = (
    "term",
    "reading",
    "aliases",
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
    meta["category"] = path.parent.name  # ディレクトリ名が正
    meta.setdefault("term", path.stem)
    return Entry.model_validate(meta)


# --------------------------------------------------------------------------- #
# 旧レイアウト (data/glossary/*.md) からの移行
# --------------------------------------------------------------------------- #

_ready = False


def ensure_ready() -> list[str]:
    """プロセス内で一度だけ、ディレクトリ作成と旧レイアウトの移行を行う。

    サーバ起動時と CLI 起動時の両方から呼ぶ。移行したファイルの一覧を返す。
    """
    global _ready
    with _lock:
        if _ready:
            return []
        config.ensure_dirs()
        moved = migrate_layout()
        categories.load()
        _ready = True
        return moved


def migrate_layout() -> list[str]:
    """フラット配置のファイルを ``<category>/`` の下へ移す。移動したファイル名を返す。"""
    moved: list[str] = []
    if not config.GLOSSARY_DIR.exists():
        return moved
    with _lock:
        for path in sorted(config.GLOSSARY_DIR.glob("*.md")):
            meta, _ = parse_markdown(path.read_text(encoding="utf-8"))
            raw = str(meta.get("category") or "").strip()
            try:
                category = normalize_category(raw) if raw else "未分類"
            except CategoryNameError:
                category = "未分類"
            target_dir = config.GLOSSARY_DIR / category
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / path.name
            n = 2
            while target.exists():
                target = target_dir / f"{path.stem}-{n}.md"
                n += 1
            os.replace(path, target)
            categories.ensure(category)
            moved.append(f"{path.name} -> {category}/{target.name}")
        if moved:
            invalidate()
    return moved


# --------------------------------------------------------------------------- #
# 読み出し (mtime ベースの簡易キャッシュ付き)
# --------------------------------------------------------------------------- #

def _signature() -> object:
    if not config.GLOSSARY_DIR.exists():
        return ()
    sig = []
    for path in config.GLOSSARY_DIR.glob("*/*.md"):
        try:
            st = path.stat()
        except OSError:
            continue
        sig.append((path.parent.name, path.name, st.st_mtime_ns, st.st_size))
    return tuple(sorted(sig))


def load_all(*, force: bool = False) -> list[Entry]:
    """全エントリを返す。壊れたファイルは黙って飛ばさず例外にする。"""
    global _cache
    with _lock:
        sig = _signature()
        if not force and _cache is not None and _cache[0] == sig:
            return _cache[1]
        entries: list[Entry] = []
        if config.GLOSSARY_DIR.exists():
            for path in sorted(config.GLOSSARY_DIR.glob("*/*.md")):
                entries.append(_entry_from_file(path))
        order = {name: i for i, name in enumerate(categories.names())}
        entries.sort(
            key=lambda e: (order.get(e.category, 10**6), e.category, e.subcategory, e.reading or e.term)
        )
        _cache = (sig, entries)
        return entries


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def path_for(category: str, slug: str) -> Path:
    """カテゴリと slug からファイルパスを作る。ディレクトリ外への脱出を防ぐ。"""
    category = normalize_category(category)
    if not slug or "/" in slug or "\\" in slug or slug.startswith("."):
        raise StoreError(f"不正な slug: {slug!r}")
    base = config.GLOSSARY_DIR.resolve()
    path = (base / category / f"{slug}.md").resolve()
    if path.parent.parent != base:
        raise StoreError(f"不正な参照です: {category}/{slug}")
    return path


def path_for_ref(ref: str) -> Path:
    return path_for(*split_ref(ref))


def get(ref: str) -> Entry | None:
    for e in load_all():
        if e.ref == ref:
            return e
    return None


def find_by_surface(surface: str) -> list[Entry]:
    """用語名 or 別名の完全一致 (大文字小文字無視) を全部返す。

    同名用語がカテゴリ違いで併存しうるのでリストで返す。
    """
    needle = surface.strip().casefold()
    hits = []
    for e in load_all():
        if any(s.casefold() == needle for s in e.surfaces):
            hits.append(e)
    return hits


def find_in_category(category: str, surface: str) -> Entry | None:
    for e in find_by_surface(surface):
        if e.category == category:
            return e
    return None


# --------------------------------------------------------------------------- #
# 書き込み
# --------------------------------------------------------------------------- #

def _allocate_slug(category: str, term: str) -> str:
    base = slugify(term)
    candidate = base
    n = 2
    while path_for(category, candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def save(draft: EntryDraft, *, ref: str | None = None) -> Entry:
    """新規作成 (ref=None) または更新。

    更新時にカテゴリを変えるとファイルごと移動する（＝カテゴリ移動）。
    新規で同じカテゴリに同じ用語がある場合は ``StoreError``。
    別カテゴリに同名があるのは正常なので通す。
    """
    if not draft.term:
        raise StoreError("term は必須です")

    with _lock:
        config.ensure_dirs()
        category = normalize_category(draft.category or "未分類")
        categories.ensure(category, subcategory=draft.subcategory)

        old_path: Path | None = None
        if ref is None:
            created = now_iso()
        else:
            old_path = path_for_ref(ref)
            if not old_path.exists():
                raise StoreError(f"見つかりません: {ref}")
            created = _entry_from_file(old_path).created_at

        clash = find_in_category(category, draft.term)
        if clash is not None and (ref is None or clash.ref != ref):
            raise StoreError(
                f"「{draft.term}」はカテゴリ「{category}」に既に登録されています"
                f"（別のカテゴリなら同じ名前で登録できます）"
            )

        old_category, old_slug = split_ref(ref) if ref else (None, None)
        if ref is not None and old_category == category:
            slug = old_slug           # 同カテゴリ内の更新はファイル名を変えない
        else:
            slug = _allocate_slug(category, draft.term)

        data = draft.model_dump()
        data["category"] = category
        # 保存時に本文を 1 文 1 行へ整える。ファイル自体が読みやすくなり、
        # git の差分も文単位になる
        data["definition"] = render.soften_paragraphs(data["definition"])
        entry = Entry(**data, slug=slug, created_at=created, updated_at=now_iso())

        target = path_for(category, slug)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(target, dump_markdown(entry))
        if old_path is not None and old_path != target:
            old_path.unlink(missing_ok=True)  # カテゴリ移動: 旧ファイルを消す

        global _cache
        _cache = None
        return entry


def move(ref: str, category: str) -> Entry:
    """エントリを別カテゴリへ移す。"""
    entry = get(ref)
    if entry is None:
        raise StoreError(f"見つかりません: {ref}")
    draft = EntryDraft(**{
        k: v for k, v in entry.model_dump().items()
        if k not in ("slug", "created_at", "updated_at")
    })
    draft.category = normalize_category(category)
    return save(draft, ref=ref)


def delete(ref: str) -> bool:
    with _lock:
        path = path_for_ref(ref)
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


def rename_category(old: str, new: str) -> int:
    """カテゴリを改名する。ディレクトリごと動かし、マスターも更新する。"""
    with _lock:
        old_norm = normalize_category(old)
        new_norm = normalize_category(new)
        if old_norm == new_norm:
            return 0
        src = config.GLOSSARY_DIR / old_norm
        dst = config.GLOSSARY_DIR / new_norm
        moved = 0
        if src.exists():
            if dst.exists():
                raise StoreError(f"カテゴリ「{new_norm}」のディレクトリが既にあります")
            os.replace(src, dst)
            moved = len(list(dst.glob("*.md")))
        categories.rename(old_norm, new_norm)
        invalidate()
        return moved


def delete_category(name: str) -> None:
    """空のカテゴリだけ消す。"""
    with _lock:
        name = normalize_category(name)
        if any(e.category == name for e in load_all()):
            raise StoreError(f"カテゴリ「{name}」にはまだ用語があります")
        directory = config.GLOSSARY_DIR / name
        if directory.exists():
            try:
                directory.rmdir()
            except OSError as exc:
                raise StoreError(f"ディレクトリを削除できません: {exc}") from exc
        if not categories.remove(name):
            raise StoreError(f"カテゴリ「{name}」がありません")
        invalidate()


# --------------------------------------------------------------------------- #
# 一覧・集計
# --------------------------------------------------------------------------- #

def category_tree() -> list[dict]:
    """マスターの順で [{category, count, subcategories: [...]}] を返す。

    1 語も無いカテゴリも ``count: 0`` で含める（空振り登録を見えるようにする）。
    """
    used: dict[str, dict[str, int]] = {}
    for e in load_all():
        used.setdefault(e.category, {})
        subs = used[e.category]
        subs[e.subcategory] = subs.get(e.subcategory, 0) + 1

    out = []
    for cat in categories.load():
        subs = used.get(cat.name, {})
        names = list(dict.fromkeys([*cat.subcategories, *subs.keys()]))
        out.append(
            {
                "category": cat.name,
                "description": cat.description,
                "count": sum(subs.values()),
                "subcategories": [
                    {"name": name, "count": subs.get(name, 0)}
                    for name in sorted(names, key=lambda s: (s == "", s))
                ],
            }
        )
    return out
