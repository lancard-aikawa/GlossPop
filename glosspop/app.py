"""FastAPI アプリ本体。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from anyio import to_thread
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, ai, categories, config, fetcher, render, store
from .linker import Linker, entry_url
from .models import CategoryNameError, Entry, EntryDraft

CONTENT_SUFFIXES = {".md", ".markdown", ".mdown", ".txt", ".html", ".htm"}

#: 一覧で降りないディレクトリ。任意のフォルダを開けるので、リポジトリや
#: 仮想環境を掴んだときに数万ファイルを走査しないためのもの
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".idea", ".vscode",
})

#: 一覧の上限。超えたぶんは切って ``truncated`` で知らせる
MAX_CONTENT_FILES = 2000


@asynccontextmanager
async def lifespan(_app: FastAPI):
    for line in store.ensure_ready():
        print(f"[glosspop] 旧レイアウトを移行しました: {line}")
    yield


app = FastAPI(title="GlossPop", version=__version__, lifespan=lifespan)


class RevalidatingStatic(StaticFiles):
    """ETag での検証は残しつつ、ブラウザに黙ってキャッシュさせない。

    ES モジュールを触りながら使うツールなので、リロードで古い JS が
    出てくるほうが害が大きい。
    """

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", RevalidatingStatic(directory=str(config.STATIC_DIR)), name="static")


# --------------------------------------------------------------------------- #
# リクエストモデル
# --------------------------------------------------------------------------- #

class RenderRequest(BaseModel):
    text: str = ""
    kind: str = "auto"          # markdown | text | html | auto
    filename: str | None = None
    base_url: str = ""
    title: str = ""
    first_only: bool = False


class DraftRequest(BaseModel):
    term: str
    context: str = ""
    source: str = ""


class FetchRequest(BaseModel):
    url: str


class CategoryRequest(BaseModel):
    name: str
    subcategories: list[str] = []
    description: str = ""


class CategoryRenameRequest(BaseModel):
    name: str


class MoveRequest(BaseModel):
    category: str


class ContentRootRequest(BaseModel):
    path: str = ""


# --------------------------------------------------------------------------- #
# ヘルパ
# --------------------------------------------------------------------------- #

def _linker() -> Linker:
    return Linker(store.load_all())


def _term_card(entry: Entry) -> dict:
    return {
        "ref": entry.ref,
        "slug": entry.slug,
        "term": entry.term,
        "reading": entry.reading,
        "summary": entry.summary,
        "category": entry.category,
        "subcategory": entry.subcategory,
        "path_label": entry.path_label,
        "url": entry_url(entry),
    }


def _self_refs(entry: Entry) -> list[str]:
    """本文中で自己参照リンクにしたくないエントリ。

    自分自身だけでなく「同じ表記の別カテゴリのエントリ」も外す。
    そうしないと、プログラミングの「ソース」の本文に出てくる「ソース」が
    料理の「ソース」に飛んでしまう。
    """
    own = {s.casefold() for s in entry.surfaces}
    return [
        e.ref for e in store.load_all()
        if any(s.casefold() in own for s in e.surfaces)
    ]


def _entry_payload(entry: Entry, *, linker: Linker | None = None) -> dict:
    linker = linker or _linker()
    definition_html, _ = linker.annotate(
        render.definition_to_html(entry.definition), skip_refs=_self_refs(entry)
    )
    data = entry.model_dump()
    data["ref"] = entry.ref
    data["url"] = entry_url(entry)
    data["path_label"] = entry.path_label
    data["definition_html"] = definition_html
    data["summary_html"] = render.md_to_html(entry.summary) if entry.summary else ""
    data["examples_html"] = [render.md_to_html(x) for x in entry.examples]
    return data


def _safe_content_path(rel: str) -> Path:
    base = config.content_dir().resolve()
    target = (base / rel).resolve()
    if base not in target.parents:
        raise HTTPException(400, "content ディレクトリ外は開けません")
    if not target.is_file():
        raise HTTPException(404, f"ファイルがありません: {rel}")
    if target.suffix.lower() not in CONTENT_SUFFIXES:
        raise HTTPException(400, f"対応していない拡張子です: {target.suffix}")
    return target


# --------------------------------------------------------------------------- #
# ページ
# --------------------------------------------------------------------------- #

def _page(name: str) -> FileResponse:
    return FileResponse(
        config.STATIC_DIR / name,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/", include_in_schema=False)
def page_viewer() -> FileResponse:
    return _page("index.html")


@app.get("/glossary", include_in_schema=False)
def page_glossary() -> FileResponse:
    return _page("glossary.html")


@app.get("/glossary/{ref:path}", include_in_schema=False)
def page_entry(ref: str) -> FileResponse:
    return _page("entry.html")


# --------------------------------------------------------------------------- #
# API: メタ
# --------------------------------------------------------------------------- #

@app.get("/api/health")
def health() -> dict:
    return {
        "version": __version__,
        "ai_available": ai.available(),
        "claude_bin": config.CLAUDE_BIN,
        "glossary_dir": str(config.GLOSSARY_DIR),
        "categories_file": str(config.CATEGORIES_FILE),
        "content_dir": str(config.content_dir()),
        "entry_count": len(store.load_all()),
        "category_count": len(categories.load()),
    }


# --------------------------------------------------------------------------- #
# API: カテゴリマスター
# --------------------------------------------------------------------------- #

@app.get("/api/categories")
def list_categories() -> list[dict]:
    return store.category_tree()


@app.post("/api/categories", status_code=201)
def create_category(req: CategoryRequest) -> dict:
    category = categories.ensure(req.name, description=req.description)
    if req.subcategories:
        category = categories.set_subcategories(category.name, req.subcategories)
    return category.model_dump()


@app.put("/api/categories/{name}")
def update_category(name: str, req: CategoryRequest) -> dict:
    current = categories.get(name)
    if current is None:
        raise HTTPException(404, f"カテゴリ「{name}」がありません")
    if req.name and req.name.strip() != current.name:
        store.rename_category(current.name, req.name)
        current = categories.get(req.name)
    assert current is not None
    if req.subcategories or current.subcategories:
        current = categories.set_subcategories(current.name, req.subcategories)
    return current.model_dump()


@app.delete("/api/categories/{name}", status_code=204)
def delete_category(name: str) -> None:
    store.delete_category(name)


# --------------------------------------------------------------------------- #
# API: 辞書
# --------------------------------------------------------------------------- #

@app.get("/api/entries")
def list_entries(
    q: str = "",
    category: str | None = None,
    subcategory: str | None = None,
) -> list[dict]:
    needle = q.strip().casefold()
    out = []
    for e in store.load_all():
        if category is not None and e.category != category:
            continue
        if subcategory is not None and e.subcategory != subcategory:
            continue
        if needle:
            haystack = " ".join([e.term, e.reading, e.summary, e.definition, *e.aliases, *e.tags]).casefold()
            if needle not in haystack:
                continue
        card = _term_card(e)
        card["aliases"] = e.aliases
        card["tags"] = e.tags
        card["updated_at"] = e.updated_at
        out.append(card)
    return out


@app.get("/api/lookup")
def lookup(term: str = Query(..., min_length=1)) -> dict:
    """用語名 / 別名の完全一致で引く。同名がカテゴリ違いであれば全部返す。

    未登録は「異常」ではなく普通の答えなので 404 にしない
    (登録前の重複チェックに使うため、コンソールにエラーを出したくない)。
    """
    matches = store.find_by_surface(term)
    linker = _linker()
    return {
        "term": term,
        "found": bool(matches),
        "count": len(matches),
        "entries": [_entry_payload(e, linker=linker) for e in matches],
    }


@app.post("/api/entries", status_code=201)
def create_entry(draft: EntryDraft) -> dict:
    try:
        entry = store.save(draft)
    except store.StoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _entry_payload(entry)


@app.put("/api/entries/{ref:path}")
def update_entry(ref: str, draft: EntryDraft) -> dict:
    try:
        entry = store.save(draft, ref=ref)
    except store.StoreError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _entry_payload(entry)


@app.post("/api/move/{ref:path}")
def move_entry(ref: str, req: MoveRequest) -> dict:
    try:
        entry = store.move(ref, req.category)
    except store.StoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _entry_payload(entry)


@app.delete("/api/entries/{ref:path}", status_code=204)
def delete_entry(ref: str) -> None:
    if not store.delete(ref):
        raise HTTPException(404, f"用語が見つかりません: {ref}")


@app.get("/api/entries/{ref:path}")
def get_entry(ref: str) -> dict:
    entry = store.get(ref)
    if entry is None:
        raise HTTPException(404, f"用語が見つかりません: {ref}")
    return _entry_payload(entry)


# --------------------------------------------------------------------------- #
# API: レンダリング
# --------------------------------------------------------------------------- #

@app.post("/api/render")
def render_text(req: RenderRequest) -> dict:
    kind = render.resolve_kind(req.kind, req.filename)
    html = render.render_source(
        req.text, kind=kind, filename=req.filename, base_url=req.base_url
    )
    linked, entries = _linker().annotate(html, first_only=req.first_only)
    if req.title:
        title = req.title
    elif kind == "html":
        # ローカルの .html は <title> を題に使う (URL 経由は fetcher が付けてくる)
        title = render.html_title(req.text) or (req.filename or "")
    else:
        title = render.guess_title(req.text, fallback=req.filename or "")
    return {
        "html": linked,
        "title": title,
        "terms": [_term_card(e) for e in entries],
    }


# --------------------------------------------------------------------------- #
# API: content ディレクトリ / URL
# --------------------------------------------------------------------------- #

def _iter_content_files(base: Path):
    """開いているフォルダを走査する。隠しディレクトリと SKIP_DIRS には降りない。"""
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix.lower() in CONTENT_SUFFIXES:
                yield path


@app.get("/api/content")
def list_content() -> dict:
    base = config.content_dir()
    files: list[dict] = []
    truncated = False
    if base.exists():
        for path in _iter_content_files(base):
            if len(files) >= MAX_CONTENT_FILES:
                truncated = True
                break
            try:
                size = path.stat().st_size
            except OSError:
                continue
            files.append(
                {"path": path.relative_to(base).as_posix(), "name": path.name, "size": size}
            )
    return {
        "root": str(base),
        "is_default": config.is_default_content_dir(),
        "files": files,
        "truncated": truncated,
    }


@app.post("/api/content-root")
def set_content_root(req: ContentRootRequest) -> dict:
    """開くフォルダを切り替える (空文字で既定に戻す)。

    ローカル専用ツールなので任意のパスを受ける。ブラウザの他タブから叩かれても
    JSON の POST は preflight が要るので素通りはしない。
    """
    raw = (req.path or "").strip().strip('"')
    if not raw:
        config.set_content_dir(None)
        return list_content()
    target = Path(raw).expanduser()
    try:
        target = target.resolve(strict=True)
    except OSError as exc:
        raise HTTPException(404, f"開けません: {raw}") from exc
    if not target.is_dir():
        raise HTTPException(400, f"フォルダではありません: {target}")
    config.set_content_dir(target)
    return list_content()


@app.get("/api/content/{rel:path}")
def read_content(rel: str) -> dict:
    path = _safe_content_path(rel)
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"path": rel, "name": path.name, "text": text}


@app.post("/api/fetch")
async def fetch_url(req: FetchRequest) -> dict:
    try:
        return await to_thread.run_sync(fetcher.fetch, req.url, abandon_on_cancel=True)
    except fetcher.FetchError as exc:
        raise HTTPException(502, str(exc)) from exc


# --------------------------------------------------------------------------- #
# API: AI 下書き
# --------------------------------------------------------------------------- #

@app.post("/api/ai/draft")
async def ai_draft(req: DraftRequest) -> dict:
    if not ai.available():
        raise HTTPException(
            503, "claude CLI が見つかりません。手動入力で登録してください。"
        )
    try:
        draft = await ai.draft_entry(req.term, req.context, source=req.source)
    except ai.AIError as exc:
        raise HTTPException(502, str(exc)) from exc

    # 下書き段階でカテゴリをマスターに登録しておく (保存されず空振りでも残す)
    registered = None
    warning = ""
    if draft.category:
        try:
            registered = categories.ensure(draft.category, subcategory=draft.subcategory).name
        except CategoryNameError as exc:
            # カテゴリ名が使えなくても下書き自体は返す。ユーザーが選び直せばよい
            warning = f"AI が提案したカテゴリ「{draft.category}」は使えません: {exc}"
            draft.category = ""

    existing = store.find_by_surface(draft.term) or store.find_by_surface(req.term)
    return {
        "draft": draft.model_dump(),
        "registered_category": registered,
        "warning": warning,
        "existing": [_term_card(e) for e in existing],
    }


# --------------------------------------------------------------------------- #
# エラーハンドラ
# --------------------------------------------------------------------------- #

@app.exception_handler(store.StoreError)
def _store_error_handler(_request, exc: store.StoreError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(CategoryNameError)
def _category_error_handler(_request, exc: CategoryNameError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=422)
