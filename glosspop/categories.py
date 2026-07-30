"""カテゴリのマスター (``data/categories.yaml``)。

辞書は ``data/glossary/<category>/<slug>.md`` に置くので、カテゴリ = ディレクトリ。
ただしディレクトリだけだと「まだ 1 語も入っていないカテゴリ」を git で持てないため、
マスターを別に持って順序・サブカテゴリ・説明を管理する。

AI が下書きの時点で新カテゴリを登録でき、そのまま保存されずに終わっても
（空振りでも）マスターには残る。
"""

from __future__ import annotations

import threading

import yaml
from pydantic import BaseModel, Field

from . import config
from .models import CategoryNameError, normalize_category

_lock = threading.RLock()
_cache: tuple[object, list["Category"]] | None = None


class Category(BaseModel):
    name: str
    subcategories: list[str] = Field(default_factory=list)
    description: str = ""


def _signature() -> object:
    path = config.CATEGORIES_FILE
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _read() -> list[Category]:
    path = config.CATEGORIES_FILE
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise CategoryNameError(f"{path.name} の YAML が壊れています: {exc}") from exc
    items = raw.get("categories") if isinstance(raw, dict) else raw
    out: list[Category] = []
    seen: set[str] = set()
    for item in items or []:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict) or not item.get("name"):
            continue
        try:
            name = normalize_category(str(item["name"]))
        except CategoryNameError:
            continue  # 手で壊れた名前が入っていても全体を落とさない
        if name in seen:
            continue
        seen.add(name)
        subs = [str(s).strip() for s in (item.get("subcategories") or []) if str(s).strip()]
        out.append(
            Category(
                name=name,
                subcategories=list(dict.fromkeys(subs)),
                description=str(item.get("description") or "").strip(),
            )
        )
    return out


def _write(categories: list[Category]) -> None:
    config.ensure_dirs()
    payload = {
        "categories": [
            {
                k: v
                for k, v in (
                    ("name", c.name),
                    ("subcategories", c.subcategories),
                    ("description", c.description),
                )
                if v not in ("", [], None) or k == "name"
            }
            for c in categories
        ]
    }
    text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False)
    tmp = config.CATEGORIES_FILE.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(config.CATEGORIES_FILE)
    invalidate()


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def load() -> list[Category]:
    """マスターを読む。ディレクトリだけ在るカテゴリは自動で取り込む。"""
    global _cache
    with _lock:
        sig = _signature()
        if _cache is not None and _cache[0] == sig:
            return _cache[1]
        categories = _read()
        known = {c.name for c in categories}
        added = False
        # 手で mkdir されたカテゴリを拾う
        if config.GLOSSARY_DIR.exists():
            for child in sorted(config.GLOSSARY_DIR.iterdir()):
                if not child.is_dir():
                    continue
                try:
                    name = normalize_category(child.name)
                except CategoryNameError:
                    continue
                if name not in known:
                    categories.append(Category(name=name))
                    known.add(name)
                    added = True
        if added:
            _write(categories)
            sig = _signature()
        _cache = (sig, categories)
        return categories


def names() -> list[str]:
    return [c.name for c in load()]


def get(name: str) -> Category | None:
    normalized = normalize_category(name)
    for c in load():
        if c.name == normalized:
            return c
    return None


def exists(name: str) -> bool:
    try:
        return get(name) is not None
    except CategoryNameError:
        return False


def ensure(name: str, *, subcategory: str = "", description: str = "") -> Category:
    """カテゴリを（無ければ）マスターに登録する。サブカテゴリも足す。"""
    name = normalize_category(name)
    subcategory = (subcategory or "").strip()
    with _lock:
        categories = list(load())
        for c in categories:
            if c.name != name:
                continue
            changed = False
            if subcategory and subcategory not in c.subcategories:
                c.subcategories.append(subcategory)
                changed = True
            if description and not c.description:
                c.description = description
                changed = True
            if changed:
                _write(categories)
            return c
        created = Category(
            name=name,
            subcategories=[subcategory] if subcategory else [],
            description=description,
        )
        categories.append(created)
        _write(categories)
        return created


def rename(old: str, new: str) -> Category:
    """マスター上の名前を差し替える。ディレクトリの移動は store 側で行う。"""
    old = normalize_category(old)
    new = normalize_category(new)
    with _lock:
        categories = list(load())
        if old == new:
            return get(old) or ensure(new)
        if any(c.name == new for c in categories):
            raise CategoryNameError(f"カテゴリ「{new}」は既にあります")
        for c in categories:
            if c.name == old:
                c.name = new
                _write(categories)
                return c
        raise CategoryNameError(f"カテゴリ「{old}」がありません")


def remove(name: str) -> bool:
    name = normalize_category(name)
    with _lock:
        categories = [c for c in load() if c.name != name]
        if len(categories) == len(load()):
            return False
        _write(categories)
        return True


def set_subcategories(name: str, subcategories: list[str]) -> Category:
    name = normalize_category(name)
    subs = list(dict.fromkeys(s.strip() for s in subcategories if s and s.strip()))
    with _lock:
        categories = list(load())
        for c in categories:
            if c.name == name:
                c.subcategories = subs
                _write(categories)
                return c
        raise CategoryNameError(f"カテゴリ「{name}」がありません")
