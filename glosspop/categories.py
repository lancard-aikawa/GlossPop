"""カテゴリのマスター (``categories.yaml``)。

辞書は ``<辞書ルート>/<category>/<slug>.md`` に置くので、カテゴリ = ディレクトリ。
ただしディレクトリだけだと「まだ 1 語も入っていないカテゴリ」「並び順」「説明」
「サブカテゴリの先出し」を持てないため、マスターを別に持つ。

AI が下書きの時点で新カテゴリを登録でき、そのまま保存されずに終わっても
（空振りでも）マスターには残る。

**マスターは辞書ごとにある。** グローバルは ``data/categories.yaml``、
開いているフォルダのローカルは ``<フォルダ>/.glosspop/categories.yaml``。
ローカルのマスターはフォルダに閉じているので、**小説の「登場人物」が全体の
マスターに残る**という以前の懸念は起きない（フォルダごとコピーすれば付いていく）。
URL の辞書を作っていないときはローカルのマスターが無いので、``None`` を返しうる
経路がある —— 呼び出し側はそれを前提にすること。

**キャッシュの鍵にはファイルのパス自体を入れる。** 入れないと、フォルダを
切り替えても前のフォルダのマスターが出てくる（``store._signature()`` で踏んだ穴）。
"""

from __future__ import annotations

import threading
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from . import config
from .models import GLOBAL_SCOPE, LOCAL_SCOPE, SCOPES, CategoryNameError, normalize_category

_lock = threading.RLock()
#: scope -> (signature, categories)
_cache: dict[str, tuple[object, list["Category"]]] = {}


class Category(BaseModel):
    name: str
    subcategories: list[str] = Field(default_factory=list)
    description: str = ""


class NoMasterError(CategoryNameError):
    """その辞書のマスターが無い（URL の辞書を作っていない、など）。"""


def _check_scope(scope: str) -> str:
    if scope not in SCOPES:
        raise CategoryNameError(f"不明な保存先です: {scope}")
    return scope


def file_for(scope: str = GLOBAL_SCOPE) -> Path | None:
    """マスターのファイル。ローカル辞書が無いときは ``None``。"""
    _check_scope(scope)
    if scope == LOCAL_SCOPE:
        return config.local_categories_file()
    return config.CATEGORIES_FILE


def _require_file(scope: str) -> Path:
    path = file_for(scope)
    if path is None:
        raise NoMasterError("このフォルダの辞書がありません（フォルダを開いてください）")
    return path


def _glossary_dir(scope: str) -> Path | None:
    """マスターに取り込む対象のディレクトリ（``store`` を import しないため直接引く）。"""
    if scope == LOCAL_SCOPE:
        return config.local_glossary_dir()
    return config.GLOSSARY_DIR


def _signature(scope: str) -> object:
    path = file_for(scope)
    if path is None:
        return None
    try:
        st = path.stat()
        return (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(path), None)          # 場所が変われば別物として扱う


def _read(scope: str) -> list[Category]:
    path = file_for(scope)
    if path is None or not path.exists():
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


def _write(categories: list[Category], scope: str = GLOBAL_SCOPE) -> None:
    path = _require_file(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
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
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)
    invalidate()


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = {}


def load(scope: str = GLOBAL_SCOPE) -> list[Category]:
    """マスターを読む。ディレクトリだけ在るカテゴリは自動で取り込む。

    ローカル辞書が無いスコープでは空リスト（マスターを作らない）。
    """
    _check_scope(scope)
    with _lock:
        if file_for(scope) is None:
            return []
        sig = _signature(scope)
        hit = _cache.get(scope)
        if hit is not None and hit[0] == sig:
            return hit[1]
        categories = _read(scope)
        known = {c.name for c in categories}
        added = False
        # 手で mkdir されたカテゴリを拾う
        root = _glossary_dir(scope)
        if root is not None and root.exists():
            for child in sorted(root.iterdir()):
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
            _write(categories, scope)
            sig = _signature(scope)
        _cache[scope] = (sig, categories)
        return categories


def names(scope: str = GLOBAL_SCOPE) -> list[str]:
    return [c.name for c in load(scope)]


def get(name: str, scope: str = GLOBAL_SCOPE) -> Category | None:
    normalized = normalize_category(name)
    for c in load(scope):
        if c.name == normalized:
            return c
    return None


def exists(name: str, scope: str = GLOBAL_SCOPE) -> bool:
    try:
        return get(name, scope) is not None
    except CategoryNameError:
        return False


def ensure(
    name: str,
    *,
    subcategory: str = "",
    description: str = "",
    scope: str = GLOBAL_SCOPE,
) -> Category:
    """カテゴリを（無ければ）マスターに登録する。サブカテゴリも足す。"""
    name = normalize_category(name)
    subcategory = (subcategory or "").strip()
    with _lock:
        categories = list(load(scope))
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
                _write(categories, scope)
            return c
        created = Category(
            name=name,
            subcategories=[subcategory] if subcategory else [],
            description=description,
        )
        categories.append(created)
        _write(categories, scope)
        return created


def rename(
    old: str, new: str, scope: str = GLOBAL_SCOPE, *, allow_missing: bool = False
) -> Category:
    """マスター上の名前を差し替える。ディレクトリの移動は store 側で行う。

    ``allow_missing`` は「マスターに載っていなくても新しい名前で作る」。
    ディレクトリを手で作った直後などに使う。
    """
    old = normalize_category(old)
    new = normalize_category(new)
    with _lock:
        categories = list(load(scope))
        if old == new:
            return get(old, scope) or ensure(new, scope=scope)
        if any(c.name == new for c in categories):
            raise CategoryNameError(f"カテゴリ「{new}」は既にあります")
        for c in categories:
            if c.name == old:
                c.name = new
                _write(categories, scope)
                return c
        if allow_missing:
            return ensure(new, scope=scope)
        raise CategoryNameError(f"カテゴリ「{old}」がありません")


def remove(name: str, scope: str = GLOBAL_SCOPE) -> bool:
    name = normalize_category(name)
    with _lock:
        current = load(scope)
        categories = [c for c in current if c.name != name]
        if len(categories) == len(current):
            return False
        _write(categories, scope)
        return True


def set_subcategories(
    name: str, subcategories: list[str], scope: str = GLOBAL_SCOPE
) -> Category:
    name = normalize_category(name)
    subs = list(dict.fromkeys(s.strip() for s in subcategories if s and s.strip()))
    with _lock:
        categories = list(load(scope))
        for c in categories:
            if c.name == name:
                c.subcategories = subs
                _write(categories, scope)
                return c
        raise CategoryNameError(f"カテゴリ「{name}」がありません")


def set_description(name: str, description: str, scope: str = GLOBAL_SCOPE) -> Category:
    name = normalize_category(name)
    with _lock:
        categories = list(load(scope))
        for c in categories:
            if c.name == name:
                c.description = (description or "").strip()
                _write(categories, scope)
                return c
        raise CategoryNameError(f"カテゴリ「{name}」がありません")


def reorder(order: list[str], scope: str = GLOBAL_SCOPE) -> list[Category]:
    """並べ替える。**渡されなかったものは後ろにそのままの順で残す。**

    一覧を作ってから並べ替えを送るまでの間に別の経路でカテゴリが増えることが
    あるので、「送られた順 + 残り」で組み立てる。落とすと**カテゴリが黙って
    消える**（ディレクトリは残るので次の ``load()`` で復活するが、順序と説明は
    失われる）。
    """
    with _lock:
        categories = list(load(scope))
        by_name = {c.name: c for c in categories}
        out: list[Category] = []
        for raw in order:
            try:
                name = normalize_category(str(raw))
            except CategoryNameError:
                continue
            c = by_name.pop(name, None)
            if c is not None:
                out.append(c)
        out.extend(c for c in categories if c.name in by_name)
        _write(out, scope)
        return out
