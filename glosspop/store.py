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
from pydantic import BaseModel

from . import categories, config, render
from .models import (
    GLOBAL_SCOPE,
    LOCAL_SCOPE,
    SCOPES,
    CategoryNameError,
    Entry,
    EntryDraft,
    make_ref,
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
    # 旧 "related" は書き出さない。読み込み時に relations へ畳まれるので、
    # 次に保存した時点で自動的に移行する (migrate_layout と同じ考え方)
    "relations",
    "tags",
    "source",
    "first_file",
    "first_locator",
    "former_refs",
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


def _plain(value: object) -> object:
    """yaml.safe_dump に渡せる素の値にする。

    ``relations`` は pydantic モデルのリストなので dict に落とし、そのうえで
    空の項目を落とす（``label: ''`` が全行に並ぶとファイルが読めなくなる）。
    """
    if isinstance(value, BaseModel):
        return {k: v for k, v in value.model_dump().items() if v not in ("", [], None)}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def dump_markdown(entry: Entry) -> str:
    meta = {}
    for key in _FM_KEYS:
        value = getattr(entry, key)
        # 空文字 / 空リストは書かない (ファイルをノイズで埋めない)
        if value in ("", [], None):
            continue
        meta[key] = _plain(value)
    front = yaml.safe_dump(
        meta,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    ).rstrip()
    body = entry.definition.strip()
    return f"---\n{front}\n---\n\n{body}\n"


def _entry_from_file(path: Path, scope: str = GLOBAL_SCOPE) -> Entry:
    meta, body = parse_markdown(path.read_text(encoding="utf-8"))
    meta = dict(meta)
    meta["definition"] = body
    meta["slug"] = path.stem
    meta["category"] = path.parent.name  # ディレクトリ名が正
    meta["scope"] = scope                # 置き場所が正 (frontmatter には書かない)
    meta.setdefault("term", path.stem)
    return Entry.model_validate(meta)


# --------------------------------------------------------------------------- #
# 辞書の置き場所 (グローバル / 開いているフォルダのローカル)
# --------------------------------------------------------------------------- #

def glossary_dir(scope: str = GLOBAL_SCOPE) -> Path | None:
    """スコープに対応する辞書ルート。

    ``config`` を直接見ずに必ずここを通すこと。ローカルは「いま読んでいるもの」
    （フォルダ or URL）に追従するので、参照のたびに解決し直す必要がある。
    URL を読んでいて辞書が無ければ ``None``。
    """
    if scope == LOCAL_SCOPE:
        return config.local_glossary_dir()
    return config.GLOSSARY_DIR


def persona_file(scope: str = GLOBAL_SCOPE) -> Path | None:
    """スコープに対応するペルソナ画像。無ければ ``None``。

    ``glossary_dir()`` と同じで、**``config`` を直接見ずにここを通すこと**。
    ローカルは「いま読んでいるもの」に追従するので、参照のたびに解決し直す。
    """
    if scope == LOCAL_SCOPE:
        return config.local_persona_file()
    return config.global_persona_file()


def local_available() -> bool:
    """ローカル辞書を使えるか。

    フォルダを読んでいるならそのフォルダが在ること、URL を読んでいるなら
    その URL に効く辞書が作られていること。
    """
    directory = config.local_glossary_dir()
    if directory is None:
        return False
    if config.reading_url():
        return True                      # 見つかった時点で存在している
    try:
        return config.content_dir().is_dir()
    except OSError:
        return False


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
    """辞書の「いまの姿」を表す値。変わっていればキャッシュを捨てる。

    **`glob` + `stat()` ではなく `os.scandir` を使う。** ディレクトリを読んだ時点で
    OS は各項目の情報を返しているので、`scandir` の `stat()` はそれを使い回して
    **追加のシステムコールを出さない**。`Path.glob` は項目ごとに `stat()` を
    呼び直すぶん、件数に比例して効く（実測: 3000 語で 33 ms → 6 ms）。

    ここは**全リクエストで通る**（`load_all()` がキャッシュに当たるかを決める）ので、
    件数が増えたときにいちばん素直に効いてくる場所。
    """
    sig = []
    for scope in SCOPES:
        base = glossary_dir(scope)
        # ローカルは対象を切り替えると別物になるので、ルート自体も鍵に含める
        sig.append((scope, str(base)))
        if base is None:
            continue
        try:
            categories_dirs = list(os.scandir(base))
        except OSError:
            continue                       # まだ作られていない
        for category in categories_dirs:
            if not category.is_dir():
                continue
            try:
                children = list(os.scandir(category.path))
            except OSError:
                continue
            for item in children:
                if not item.name.endswith(".md"):
                    continue
                try:
                    st = item.stat()
                except OSError:
                    continue
                sig.append((scope, category.name, item.name, st.st_mtime_ns, st.st_size))
    return tuple(sorted(sig, key=repr))


def load_all(*, force: bool = False) -> list[Entry]:
    """全エントリを返す。壊れたファイルは黙って飛ばさず例外にする。

    ローカル辞書（開いているフォルダ）のエントリを先に並べる。同じ表記が
    両方にあるとき、吹き出しでローカルの意味を上に出すため。
    """
    global _cache
    with _lock:
        sig = _signature()
        if not force and _cache is not None and _cache[0] == sig:
            return _cache[1]
        entries: list[Entry] = []
        for scope in SCOPES:
            base = glossary_dir(scope)
            if base is None or not base.exists():
                continue
            for path in sorted(base.glob("*/*.md")):
                entries.append(_entry_from_file(path, scope))
        # 並び順はそれぞれの辞書のマスターが持つ。グローバルの順をローカルにも
        # 当てると、フォルダ側で決めた並び (主要人物 → 脇役) が効かない
        order = {
            (scope, name): i
            for scope in SCOPES
            for i, name in enumerate(categories.names(scope))
        }
        entries.sort(
            key=lambda e: (
                0 if e.is_local else 1,
                order.get((e.scope, e.category), 10**6),
                e.category,
                e.subcategory,
                e.reading or e.term,
            )
        )
        _cache = (sig, entries)
        return entries


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def path_for(category: str, slug: str, scope: str = GLOBAL_SCOPE) -> Path:
    """カテゴリと slug からファイルパスを作る。ディレクトリ外への脱出を防ぐ。"""
    category = normalize_category(category)
    if not slug or "/" in slug or "\\" in slug or slug.startswith("."):
        raise StoreError(f"不正な slug: {slug!r}")
    root = glossary_dir(scope)
    if root is None:
        raise StoreError("ローカル辞書がありません（この URL の辞書を作ってください）")
    base = root.resolve()
    path = (base / category / f"{slug}.md").resolve()
    if path.parent.parent != base:
        raise StoreError(f"不正な参照です: {category}/{slug}")
    return path


def path_for_ref(ref: str) -> Path:
    scope, category, slug = split_ref(ref)
    return path_for(category, slug, scope)


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


def find_in_category(category: str, surface: str, scope: str = GLOBAL_SCOPE) -> Entry | None:
    """同じ辞書・同じカテゴリの同名エントリ。衝突判定に使う。

    スコープが違えば別エントリなので、ここでは一致させない
    （フォルダ固有の意味を、全体辞書の同名語と衝突させない）。
    """
    for e in find_by_surface(surface):
        if e.category == category and e.scope == scope:
            return e
    return None


# --------------------------------------------------------------------------- #
# 書き込み
# --------------------------------------------------------------------------- #

def _allocate_slug(category: str, term: str, scope: str = GLOBAL_SCOPE) -> str:
    base = slugify(term)
    candidate = base
    n = 2
    while path_for(category, candidate, scope).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def save(draft: EntryDraft, *, ref: str | None = None) -> Entry:
    """新規作成 (ref=None) または更新。

    更新時にカテゴリを変えるとファイルごと移動する（＝カテゴリ移動）。
    新規で同じカテゴリに同じ用語がある場合は ``StoreError``。
    別カテゴリ、あるいは別スコープに同名があるのは正常なので通す。

    保存先の辞書は、新規なら ``draft.scope``、更新なら ``ref`` が決める
    （更新でスコープは変えない。移し替えは別操作）。
    """
    if not draft.term:
        raise StoreError("term は必須です")

    with _lock:
        config.ensure_dirs()
        category = normalize_category(draft.category or "未分類")

        old_path: Path | None = None
        # 過去に名乗っていた ref。参照側を書き換えずに済ませるための転送情報で、
        # **下書きの値ではなくファイルの値を正とする**（部分的な PUT で消えないように）
        former: list[str] = []
        if ref is None:
            scope = draft.scope
            created = now_iso()
        else:
            scope, old_category, old_slug = split_ref(ref)
            old_path = path_for_ref(ref)
            if not old_path.exists():
                raise StoreError(f"見つかりません: {ref}")
            existing = _entry_from_file(old_path, scope)
            created = existing.created_at
            former = list(existing.former_refs)

        if scope == LOCAL_SCOPE and not local_available():
            raise StoreError("ローカル辞書に保存できません（フォルダが開かれていません）")
        # **マスターは辞書ごと。** ローカルのカテゴリを全体のマスターに載せない
        # という約束は変わらず、載る先がそのフォルダの .glosspop になった
        categories.ensure(category, subcategory=draft.subcategory, scope=scope)

        clash = find_in_category(category, draft.term, scope)
        if clash is not None and (ref is None or clash.ref != ref):
            raise StoreError(
                f"「{draft.term}」はカテゴリ「{category}」に既に登録されています"
                f"（別のカテゴリなら同じ名前で登録できます）"
            )

        if ref is not None and old_category == category:
            slug = old_slug           # 同カテゴリ内の更新はファイル名を変えない
        else:
            slug = _allocate_slug(category, draft.term, scope)

        new_ref = make_ref(scope, category, slug)
        if ref is not None and ref != new_ref:
            former.append(ref)     # カテゴリ移動: 旧 ref を転送先として残す

        data = draft.model_dump()
        data["category"] = category
        data["scope"] = scope
        data["former_refs"] = [r for r in former if r != new_ref]
        # 保存時に本文を 1 文 1 行へ整える。ファイル自体が読みやすくなり、
        # git の差分も文単位になる
        data["definition"] = render.soften_paragraphs(data["definition"])
        entry = Entry(**data, slug=slug, created_at=created, updated_at=now_iso())

        target = path_for(category, slug, scope)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(target, dump_markdown(entry))
        if old_path is not None and old_path != target:
            old_path.unlink(missing_ok=True)  # カテゴリ移動: 旧ファイルを消す

        global _cache
        _cache = None
        return entry


def move(ref: str, category: str | None = None, *, scope: str | None = None) -> Entry:
    """エントリを別カテゴリ / 別の辞書へ移す。片方だけでも両方でも指定できる。

    **``save()`` と違ってスコープをまたげるのがここ。** ``save()`` は更新時に
    ``ref`` の位置を正とする（下書きに載った古い ``scope`` で勝手にファイルが
    動かないように）ので、辞書間の移し替えはこの明示的な操作でだけ行う。

    移動先に書いてから移動元を消す。作成日時は引き継ぎ、更新日時だけ進める。
    """
    with _lock:
        entry = get(ref)
        if entry is None:
            raise StoreError(f"見つかりません: {ref}")

        target_category = normalize_category(category) if category else entry.category
        target_scope = scope or entry.scope
        if target_scope not in SCOPES:
            raise StoreError(f"不明な保存先です: {target_scope}")
        if (target_category, target_scope) == (entry.category, entry.scope):
            return entry

        if target_scope == LOCAL_SCOPE and not local_available():
            raise StoreError(
                "このフォルダの辞書に移せません"
                "（フォルダを開くか、この URL の辞書を作ってください）"
            )
        clash = find_in_category(target_category, entry.term, target_scope)
        if clash is not None and clash.ref != ref:
            raise StoreError(
                f"「{entry.term}」は移動先の「{target_category}」に既に登録されています"
            )
        # マスターは辞書ごとにあるので、移した先のマスターに登録する
        # （移動元のマスターからは消さない —— 0 件のカテゴリは残す側の判断）
        categories.ensure(target_category, subcategory=entry.subcategory, scope=target_scope)

        old_path = path_for_ref(ref)
        slug = _allocate_slug(target_category, entry.term, target_scope)
        new_ref = make_ref(target_scope, target_category, slug)
        # **参照側は書き換えない。** 旧 ref を転送先として残せば、他エントリの
        # relations はそのまま解決し続ける (wiki のリダイレクトと同じ考え方)。
        # 全エントリを書き換えて回るより、壊れる余地がはるかに小さい
        moved = entry.model_copy(
            update={
                "category": target_category,
                "scope": target_scope,
                "slug": slug,
                "former_refs": [r for r in [*entry.former_refs, entry.ref] if r != new_ref],
                "updated_at": now_iso(),
            }
        )
        target = path_for(target_category, slug, target_scope)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(target, dump_markdown(moved))
        if old_path != target:
            old_path.unlink(missing_ok=True)
        invalidate()
        return moved


def write(entry: Entry, *, replacing: str = "") -> Entry:
    """組み立て済みの ``Entry`` をそのまま書く。``replacing`` の ref は消す。

    ``save()`` は下書きから category / slug / former_refs を組み直すので、
    **どう畳むかを呼び出し側が決め切っている**とき（統合）には使えない。
    その結果をそのまま書くための口。

    **書いてから消す。** 途中で落ちたときに両方残るほうが、片方だけ消えるより
    回復しやすい（``move()`` と同じ判断）。
    """
    with _lock:
        path = path_for(entry.category, entry.slug, entry.scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, dump_markdown(entry))
        if replacing and replacing != entry.ref:
            delete(replacing)
        invalidate()
        return entry


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


def _category_root(scope: str) -> Path:
    """カテゴリ操作の対象になる辞書ルート。無ければ例外。"""
    if scope not in SCOPES:
        raise StoreError(f"不明な保存先です: {scope}")
    root = glossary_dir(scope)
    if root is None:
        raise StoreError("このフォルダの辞書がありません（フォルダを開いてください）")
    return root


def rename_category(old: str, new: str, scope: str = GLOBAL_SCOPE) -> int:
    """カテゴリを改名する。ディレクトリごと動かし、その辞書のマスターも直す。

    **マスターを先に直してからディレクトリを動かす。** 逆にすると、
    ``categories.load()`` が動かした先のディレクトリを新カテゴリとして自動で
    取り込んでしまい、続く改名が「既にあります」で落ちる（キャッシュが温まって
    いるときだけ通る、という危うい形になっていた）。
    """
    with _lock:
        old_norm = normalize_category(old)
        new_norm = normalize_category(new)
        if old_norm == new_norm:
            return 0
        root = _category_root(scope)
        src = root / old_norm
        dst = root / new_norm
        if dst.exists():
            raise StoreError(f"カテゴリ「{new_norm}」のディレクトリが既にあります")
        if not src.exists() and not categories.exists(old_norm, scope):
            raise StoreError(f"カテゴリ「{old_norm}」がありません")
        # ディレクトリだけ在って（手で mkdir された）マスターに無い場合も通す
        categories.rename(old_norm, new_norm, scope, allow_missing=True)
        moved = 0
        if src.exists():
            os.replace(src, dst)
            moved = len(list(dst.glob("*.md")))
        invalidate()
        return moved


def delete_category(name: str, scope: str = GLOBAL_SCOPE) -> None:
    """空のカテゴリだけ消す。

    **空かどうかは同じ辞書の中だけで数える。** グローバルのエントリだけを数えて
    いたため、用語の入ったローカルのカテゴリが「空」と判定され、**同名の
    グローバル側が代わりに消えた**。スコープを跨いで消さないこと。
    """
    with _lock:
        name = normalize_category(name)
        root = _category_root(scope)
        if any(e.category == name and e.scope == scope for e in load_all()):
            raise StoreError(f"カテゴリ「{name}」にはまだ用語があります")
        directory = root / name
        existed = directory.exists()
        if existed:
            try:
                directory.rmdir()
            except OSError as exc:
                raise StoreError(f"ディレクトリを削除できません: {exc}") from exc
        # マスターにだけ在る（ディレクトリを作っていない）カテゴリも消せる。
        # **消すのはそのスコープのマスターだけ** —— 跨ぐと同名の別辞書が消える
        if not categories.remove(name, scope) and not existed:
            raise StoreError(f"カテゴリ「{name}」がありません")
        invalidate()


# --------------------------------------------------------------------------- #
# 一覧・集計
# --------------------------------------------------------------------------- #

def _subcategory_nodes(subs: dict[str, int], extra: list[str] | None = None) -> list[dict]:
    names = list(dict.fromkeys([*(extra or []), *subs.keys()]))
    return [
        {"name": name, "count": subs.get(name, 0)}
        for name in sorted(names, key=lambda s: (s == "", s))
    ]


def category_tree() -> list[dict]:
    """[{category, scope, count, subcategories: [...]}] を返す。

    **どちらの辞書もマスターの順で並べる。** 1 語も無いカテゴリも ``count: 0``
    で含める（空振り登録と、先に枠だけ作った並びを見えるようにする）。
    グローバルが先、開いているフォルダのローカルが後ろ。

    ローカル辞書が無いとき（URL の辞書を作っていないなど）マスターも無いので、
    ``categories.load(LOCAL_SCOPE)`` は空を返す。**その場合でも実在するカテゴリは
    落とさない** よう、マスターに載っていないものを最後に足す。
    """
    used: dict[tuple[str, str], dict[str, int]] = {}
    for e in load_all():
        key = (e.scope, e.category)
        subs = used.setdefault(key, {})
        subs[e.subcategory] = subs.get(e.subcategory, 0) + 1

    out = []
    for scope in SCOPES:
        listed: set[str] = set()
        for cat in categories.load(scope):
            listed.add(cat.name)
            subs = used.get((scope, cat.name), {})
            out.append(
                {
                    "category": cat.name,
                    "scope": scope,
                    "description": cat.description,
                    "count": sum(subs.values()),
                    "subcategories": _subcategory_nodes(subs, cat.subcategories),
                }
            )
        for (used_scope, name), subs in sorted(used.items()):
            if used_scope != scope or name in listed:
                continue
            out.append(
                {
                    "category": name,
                    "scope": scope,
                    "description": "",
                    "count": sum(subs.values()),
                    "subcategories": _subcategory_nodes(subs),
                }
            )
    return out
