"""FastAPI アプリ本体。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, ai, config, render, store
from .linker import Linker
from .models import Entry, EntryDraft

CONTENT_SUFFIXES = {".md", ".markdown", ".mdown", ".txt"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.ensure_dirs()
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
    kind: str = "auto"          # markdown | text | auto
    filename: str | None = None
    first_only: bool = False


class DraftRequest(BaseModel):
    term: str
    context: str = ""
    source: str = ""


# --------------------------------------------------------------------------- #
# ヘルパ
# --------------------------------------------------------------------------- #

def _linker() -> Linker:
    return Linker(store.load_all())


def _term_card(entry: Entry) -> dict:
    return {
        "slug": entry.slug,
        "term": entry.term,
        "reading": entry.reading,
        "summary": entry.summary,
        "category": entry.category,
        "subcategory": entry.subcategory,
        "path_label": entry.path_label,
    }


def _entry_payload(entry: Entry, *, linker: Linker | None = None) -> dict:
    linker = linker or _linker()
    definition_html, _ = linker.annotate(
        render.definition_to_html(entry.definition), skip_slugs=[entry.slug]
    )
    data = entry.model_dump()
    data["path_label"] = entry.path_label
    data["definition_html"] = definition_html
    data["summary_html"] = render.md_to_html(entry.summary) if entry.summary else ""
    data["examples_html"] = [render.md_to_html(x) for x in entry.examples]
    return data


def _safe_content_path(rel: str) -> Path:
    base = config.CONTENT_DIR.resolve()
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


@app.get("/glossary/{slug}", include_in_schema=False)
def page_entry(slug: str) -> FileResponse:
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
        "content_dir": str(config.CONTENT_DIR),
        "entry_count": len(store.load_all()),
    }


@app.get("/api/categories")
def categories() -> list[dict]:
    return store.category_tree()


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


@app.get("/api/entries/{slug}")
def get_entry(slug: str) -> dict:
    entry = store.get(slug)
    if entry is None:
        raise HTTPException(404, f"用語が見つかりません: {slug}")
    return _entry_payload(entry)


@app.get("/api/lookup")
def lookup(term: str = Query(..., min_length=1)) -> dict:
    """用語名 / 別名の完全一致で引く。

    未登録は「異常」ではなく普通の答えなので 404 にしない
    (登録前の重複チェックに使うため、コンソールにエラーを出したくない)。
    """
    entry = store.find_by_surface(term)
    return {
        "found": entry is not None,
        "entry": _entry_payload(entry) if entry is not None else None,
    }


@app.post("/api/entries", status_code=201)
def create_entry(draft: EntryDraft) -> dict:
    try:
        entry = store.save(draft)
    except store.StoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _entry_payload(entry)


@app.put("/api/entries/{slug}")
def update_entry(slug: str, draft: EntryDraft) -> dict:
    try:
        entry = store.save(draft, slug=slug)
    except store.StoreError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _entry_payload(entry)


@app.delete("/api/entries/{slug}", status_code=204)
def delete_entry(slug: str) -> None:
    if not store.delete(slug):
        raise HTTPException(404, f"用語が見つかりません: {slug}")


# --------------------------------------------------------------------------- #
# API: レンダリング
# --------------------------------------------------------------------------- #

@app.post("/api/render")
def render_text(req: RenderRequest) -> dict:
    html = render.render_source(req.text, kind=req.kind, filename=req.filename)
    linked, entries = _linker().annotate(html, first_only=req.first_only)
    return {
        "html": linked,
        "title": render.guess_title(req.text, fallback=req.filename or ""),
        "terms": [_term_card(e) for e in entries],
    }


# --------------------------------------------------------------------------- #
# API: content ディレクトリ
# --------------------------------------------------------------------------- #

@app.get("/api/content")
def list_content() -> list[dict]:
    base = config.CONTENT_DIR
    if not base.exists():
        return []
    files = []
    for path in sorted(base.rglob("*")):
        if path.is_file() and path.suffix.lower() in CONTENT_SUFFIXES:
            rel = path.relative_to(base).as_posix()
            files.append({"path": rel, "name": path.name, "size": path.stat().st_size})
    return files


@app.get("/api/content/{rel:path}")
def read_content(rel: str) -> dict:
    path = _safe_content_path(rel)
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"path": rel, "name": path.name, "text": text}


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
    existing = store.find_by_surface(draft.term) or store.find_by_surface(req.term)
    return {
        "draft": draft.model_dump(),
        "existing_slug": existing.slug if existing else None,
    }


@app.exception_handler(store.StoreError)
def _store_error_handler(_request, exc: store.StoreError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)
