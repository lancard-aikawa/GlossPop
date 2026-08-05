"""FastAPI アプリ本体。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from anyio import to_thread
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (
    __version__,
    ai,
    archive,
    categories,
    config,
    doctor,
    documents,
    fetcher,
    installer,
    llm,
    merge,
    picker,
    relations,
    render,
    sites,
    store,
    timeline,
    updates,
)
from .linker import Linker, entry_url
from .models import (
    GLOBAL_SCOPE,
    LOCAL_SCOPE,
    SCOPES,
    CategoryNameError,
    Entry,
    EntryDraft,
    Relation,
    normalize_category,
)

CONTENT_SUFFIXES = {
    ".md", ".markdown", ".mdown", ".txt",
    ".html", ".htm",
    ".epub", ".pdf",
}

#: 一覧で降りないディレクトリ。任意のフォルダを開けるので、リポジトリや
#: 仮想環境を掴んだときに数万ファイルを走査しないためのもの
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".idea", ".vscode",
})

#: 一覧の上限。超えたぶんは切って ``truncated`` で知らせる
MAX_CONTENT_FILES = 2000

#: 保存先を AI に選ばせるときの指定値
AUTO_SCOPE = "auto"


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
    #: position | first | full （空なら設定の既定）
    spoiler: str = ""
    #: 初出ファイル（content ルートからの相対パス）。spoiler=first のとき文脈を作るのに使う
    file: str = ""
    #: どちらの辞書に入れるか。"auto" なら AI に選ばせる。
    #: カテゴリマスターを触るかの判断にも使う
    scope: str = AUTO_SCOPE
    #: 抽出時の種別 (ai.EXTRACT_KINDS のキー)。保存先の下敷きにする
    kind: str = ""
    #: いま書かれている説明。**渡すと「書き直し」になる** ——
    #: 事実は変えずに、文体や書きぶりだけを整え直させる（文体を変えたあと、
    #: 登録済みの語を書き直したいときの経路）
    current: str = ""


class FetchRequest(BaseModel):
    url: str


class ExtractRequest(BaseModel):
    text: str = ""
    source: str = ""
    limit: int = 12
    #: 何を抜き出すか (ai.EXTRACT_KINDS のキー)。空なら ai.DEFAULT_KINDS
    kinds: list[str] = []


class RelationsDraftRequest(BaseModel):
    """登録済みの用語どうしの関係を AI に下書きさせる。"""

    #: 関係を探す範囲。空なら辞書全体
    category: str = ""
    scope: str = ""             # global | local
    #: この語が**一方の端になる関係だけ**を探す（用語ページからの下書き）。
    #: 相手は上の範囲から選ばれる —— 関係は 2 語が揃って初めて書けるので、
    #: 「1 語だけ」を範囲にはできない
    ref: str = ""
    limit: int = 20
    max_files: int = 40
    #: 読ませる本文。渡されればフォルダを読まずにこれを使う。
    #: URL を読んでいるときはフォルダに本文が無いので、この経路が唯一の手段になる
    text: str = ""
    source: str = ""
    #: first なら各用語の初出の場面だけを渡す（position は関係を作れないので不可）
    spoiler: str = ""


class RelationItem(BaseModel):
    """1 本の関係。``from_ref`` の側に書き足す。"""

    from_ref: str
    to: str
    label: str = ""
    back: str = ""
    rank: str = ""
    reveal: str = ""


class RelationsApplyRequest(BaseModel):
    """下書きから選んだ関係をまとめて書き込む。

    1 本ずつ PUT させないのは、同じエントリに複数の関係が付くとき、
    クライアント側の読み書きが競って先に書いたぶんを消すため。
    """

    relations: list[RelationItem] = []


class AISettingsRequest(BaseModel):
    """AI の選択。**省略した項目は触らない**（None と空文字を区別する）。

    ``gemini_api_key`` に空文字を渡すと登録済みの鍵を消す。読み出す口は無い。
    """

    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    gemini_api_key: str | None = None


class AIStyleRequest(BaseModel):
    """文体（口調）の指定。**空文字を渡すと「指定なし」に戻す。**

    AI の選択 (``AISettingsRequest``) と別の口にしてあるのは、**保存先を選ぶ操作**
    だから。``local`` はフォルダに ``.glosspop/style.md`` を作りうるので、
    モデルを選び直したついでに書かれると「開いただけのフォルダを汚さない」が崩れる。
    """

    scope: str = GLOBAL_SCOPE
    style: str = ""


class AliasItem(BaseModel):
    ref: str
    alias: str


class AliasApplyRequest(BaseModel):
    """抽出が見つけた「別の呼び方」をまとめて既存エントリに足す。

    関係と同じ理由でエントリ単位にまとめる。同じ人物に別名が 2 つ付くとき、
    1 件ずつ PUT すると後の書き込みが前のものを消す。
    """

    aliases: list[AliasItem] = []


class CategoryRequest(BaseModel):
    name: str
    subcategories: list[str] = []
    description: str = ""


class CategoryUpdateRequest(BaseModel):
    """カテゴリの更新。**省略した項目は触らない。**

    ``subcategories`` を ``[]`` 既定にすると、名前だけ変えるつもりの
    ``{"name": ...}`` でサブカテゴリが全部消える。空リストは「全部消す」という
    明示的な指定なので、「指定なし」と区別できる ``None`` を既定にする。
    """

    name: str = ""
    subcategories: list[str] | None = None
    description: str = ""


class MergeRequest(BaseModel):
    """統合の実行。**衝突した項目は決まった値だけを受け取る。**

    ``fields`` に無い項目は残す側の値になる（サーバが勝手に消える側へ寄せない）。
    ``relations`` は行き先ごとに採ると決めた並びで、``None`` なら既定の畳み方
    （残す側優先 + 消える側にしか無いものを引き継ぐ）。
    """

    keep: str
    drop: str
    fields: dict = {}
    relations: list[dict] | None = None


class CategoryOrderRequest(BaseModel):
    """並び順の差し替え。**そのスコープの全カテゴリを順に並べて送る。**

    差分（「これを 1 つ上へ」）ではなく全体を送るのは、関係の書き込みと同じ
    理由で、部分更新を重ねると後の書き込みが前のものを消すため。
    """

    names: list[str] = []
    scope: str = GLOBAL_SCOPE


class MoveRequest(BaseModel):
    """カテゴリ / 保存先の移動。省略した項目はそのまま。"""

    category: str = ""
    #: global | local。辞書間の移し替えはここでだけ行う（更新では動かさない）
    scope: str = ""


class SettingsRequest(BaseModel):
    """データの保存先を変える。空文字なら既定（アプリの隣）に戻す。"""

    data_root: str = ""
    #: いまの保存先の中身を新しい場所へ複製するか（元は消さない）
    copy_existing: bool = True


class ImportRequest(BaseModel):
    """別のフォルダからデータを引き継ぐ。元は消さない。"""

    path: str


class UpdateCheckRequest(BaseModel):
    """更新の確認をするか。外へ通信する唯一の経路なので明示的に切れるようにする。"""

    enabled: bool = True


class ContentRootRequest(BaseModel):
    path: str = ""


class PickFolderRequest(BaseModel):
    initial: str = ""


class UrlContextRequest(BaseModel):
    url: str = ""


class UrlDictionaryRequest(BaseModel):
    prefix: str


# --------------------------------------------------------------------------- #
# ヘルパ
# --------------------------------------------------------------------------- #

#: 組み立て済みの Linker。**辞書が変わっていなければ使い回す。**
#: 鍵はエントリ集合そのもの（`load_all()` の返すリストの同一性）—— `store` は
#: 中身が変わったときだけ新しいリストを作るので、これで十分かつ確実
_linker_cache: tuple[object, Linker] | None = None


def _linker() -> Linker:
    """全エントリぶんの自動リンカ。

    組み立ては件数に比例する（実測: 3000 語で 20 ms）。1 リクエストに 1 回とはいえ、
    辞書が変わっていないのに毎回作り直す理由が無いので使い回す。

    **辞書の変更を取りこぼさないこと**が条件なので、鍵には `load_all()` が返す
    リストそのものを使う。`store` は署名（各ファイルの mtime とサイズ）が変わった
    ときだけ新しいリストを作るので、**外のエディタで書き換えられた場合も別物になる**。
    """
    global _linker_cache
    entries = store.load_all()
    if _linker_cache is not None and _linker_cache[0] is entries:
        return _linker_cache[1]
    linker = Linker(entries)
    _linker_cache = (entries, linker)
    return linker


#: 画像の拡張子 → Content-Type。**推測に任せない**（間違えるとブラウザが出さない）
PERSONA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _persona_url(scope: str) -> str:
    """その辞書のペルソナ画像の URL。無ければ空文字。

    **差し替えたときに古い顔が出ないよう、更新時刻を付ける**（`/static` に
    `no-cache` を付けているのと同じ話。こちらは URL 自体を変える）。
    """
    path = store.persona_file(scope)
    if path is None:
        return ""
    try:
        stamp = int(path.stat().st_mtime)
    except OSError:
        return ""
    return f"/api/persona?scope={scope}&v={stamp}"


def _ai_state() -> dict:
    """画面に返す AI まわり一式（``/api/ai/*`` が返すもの）。

    **顔の URL はここで足す。** 形（更新時刻つきのクエリ）を決めているのは
    `_persona_url()` なので、`ai.describe_style()` の側に写しを作らない。
    """
    data = {**llm.describe(), **ai.describe_style()}
    for item in data.get("personas", []):
        item["url"] = _persona_url(item["scope"])
    return data


def _term_card(entry: Entry, *, personas: dict[str, str] | None = None) -> dict:
    # **一覧では 1 回だけ調べて配る。** エントリごとに調べると、3000 語の一覧で
    # 拡張子の数だけ stat が飛ぶ（辞書は 2 つしかないので 1 回で足りる）
    persona = (personas or {}).get(entry.scope)
    if persona is None:
        persona = _persona_url(entry.scope)
    return {
        "ref": entry.ref,
        "slug": entry.slug,
        "term": entry.term,
        "reading": entry.reading,
        "summary": entry.summary,
        "category": entry.category,
        "subcategory": entry.subcategory,
        "scope": entry.scope,
        "path_label": entry.path_label,
        "url": entry_url(entry),
        "persona_url": persona,
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
    # **画像はエントリの居場所につく**（文体は「いま読んでいるフォルダ」だが、
    # こちらは「すでに書かれたものの出どころ」なので基準が違う）。揃えると、
    # 小説のフォルダを開いている間だけ全体辞書の用語にもその顔が付く
    data["persona_url"] = _persona_url(entry.scope)
    # 実際の保存先。グローバルとローカルでルートが違うので、組み立てを UI に任せない
    data["path"] = str(store.path_for_ref(entry.ref))
    data["definition_html"] = definition_html
    data["summary_html"] = render.md_to_html(entry.summary) if entry.summary else ""
    data["examples_html"] = [render.md_to_html(x) for x in entry.examples]
    # 関係は片側にしか書かないので、書かれていない側にも見えるよう両方向を返す
    entries = store.load_all()
    data["relations_resolved"] = relations.resolved_relations(entry, entries)
    data["backlinks"] = relations.backlinks(entry, entries)
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


@app.get("/graph", include_in_schema=False)
def page_graph() -> FileResponse:
    return _page("graph.html")


@app.get("/doctor", include_in_schema=False)
def page_doctor() -> FileResponse:
    return _page("doctor.html")


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
        "reading_url": config.reading_url() or "",
        "local_glossary_dir": str(config.local_glossary_dir() or ""),
        # マスターは辞書ごとにある。フォルダの辞書が使えるかは UI 側でも要る
        # （カテゴリをどちらに作るかを選ばせるため）
        "local_categories_file": str(config.local_categories_file() or ""),
        "local_available": store.local_available(),
        "spoiler_default": config.SPOILER_DEFAULT,
        "local_entry_count": sum(1 for e in store.load_all() if e.is_local),
        "entry_count": len(store.load_all()),
        "category_count": len(categories.load()),
    }


# --------------------------------------------------------------------------- #
# API: 設定（データの保存先）
#
# 既定ではデータがアプリの隣にあるので、更新のたびに手でコピーすることになる。
# アプリの外へ移しておけば、更新は**フォルダを入れ替えるだけ**で済む。
# --------------------------------------------------------------------------- #

def _outside_data_root() -> list[str]:
    """``DATA_ROOT`` の外に出ている保存先。

    個々のパスは環境変数で別々に動かせるので、外に出ていると**複製に乗らない**。
    黙っていると「移したのに辞書が付いてこない」になるので、名前で返して UI に出す。
    """
    root = Path(config.DATA_ROOT).resolve()
    out = []
    for label, path in (
        ("全体の辞書", config.GLOSSARY_DIR),
        ("カテゴリマスター", config.CATEGORIES_FILE),
        ("URL ごとの辞書", config.SITES_DIR),
        ("読む文書", config.CONTENT_DIR),
        ("専用ウィンドウの設定・お気に入り", config.WINDOW_PROFILE_DIR),
    ):
        try:
            resolved = Path(path).resolve()
        except OSError:
            continue
        if root != resolved and root not in resolved.parents:
            out.append(label)
    return out


def _settings_payload() -> dict:
    env_locked = bool(os.environ.get("GLOSSPOP_DATA_ROOT"))
    saved = config.load_settings().get("data_root") or ""
    if env_locked:
        source = "env"
    elif saved:
        source = "settings"
    else:
        source = "default"
    return {
        "settings_file": str(config.SETTINGS_FILE),
        "data_root": str(config.DATA_ROOT),
        "saved_data_root": str(saved),
        "app_dir": str(config.APP_DIR),
        "default_data_root": str(config.APP_DIR),
        # 環境変数が勝つので、その場合は設定を書いても効かない。UI に出す
        "source": source,
        "env_locked": env_locked,
        "portable": Path(config.DATA_ROOT) == Path(config.APP_DIR),
        # 環境変数で個別に外へ出されているもの。複製に乗らないので UI に出す
        "outside": _outside_data_root(),
        # 隣に置き去りのデータ。更新後に「辞書が消えた」ように見える状態の救済
        "import_candidates": config.find_data_candidates(),
        "paths": {
            "glossary": str(config.GLOSSARY_DIR),
            "categories": str(config.CATEGORIES_FILE),
            "sites": str(config.SITES_DIR),
            "content": str(config.CONTENT_DIR),
            "window_profile": str(config.WINDOW_PROFILE_DIR),
            # 取り込みの前に自動で取る控え。**場所を知らせないと戻れない**
            "backups": str(archive.backup_dir()),
        },
    }


@app.get("/api/settings")
def get_settings() -> dict:
    return _settings_payload()


@app.post("/api/import")
def import_data(req: ImportRequest) -> dict:
    """別のフォルダのデータを、いまの保存先へ引き継ぐ。

    新しい版を隣に展開して既定のまま起動すると、辞書は旧フォルダに残ったままで
    **消えたように見える**。その救済がここ。元は消さない。

    **効くのは次の起動から** —— `store` が読み込み済みのものを見ているので、
    ここで差し替えると一覧とキャッシュが食い違う。
    """
    try:
        source = Path(req.path).expanduser().resolve()
    except OSError as exc:
        raise HTTPException(400, f"使えないパスです: {req.path}") from exc
    if not source.is_dir():
        raise HTTPException(404, f"フォルダがありません: {source}")
    try:
        report = config.copy_data_root(source, Path(config.DATA_ROOT))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    store.invalidate()
    return {
        "from": str(source),
        "to": str(config.DATA_ROOT),
        "copy": report,
        # 読み込み済みのものと食い違うので、開き直してもらう
        "restart_required": True,
    }


@app.get("/api/export")
def export_glossary(category: list[str] = Query(default=[])) -> Response:
    """全体の辞書とカテゴリマスターを zip で返す（バックアップ / 持ち出し）。

    中身は Markdown のまま。解凍すればエディタで読めることを保つため、独自形式に
    しない。フォルダの辞書と URL ごとの辞書は含まない（それぞれ別の運び方がある）。

    ``category`` を渡すと**そのカテゴリだけ**を書き出す（1 カテゴリだけ人に渡す
    用途）。**取り込む側は変えていない** —— 併合は入っているものを足して上書き
    するだけなので、中身が一部でもそのまま通る。
    """
    picked = [name for name in category if name.strip()]
    return Response(
        content=archive.export_bytes(picked),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{archive.export_name(picked)}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/export/plan")
def export_glossary_plan(category: list[str] = Query(default=[])) -> dict:
    """書き出す前の下見。**何語入るか**と、**行き先が外に出る関係が何本か**。

    一部だけ渡すと、渡した先で相手の居ない関係ができる。押す前に数で見せる。
    """
    return archive.export_plan([name for name in category if name.strip()])


@app.get("/api/backups")
def list_backups() -> dict:
    """取り込み前に自動で取った控えの一覧（新しい順）。

    併合の衝突は「取り込む側が勝つ」なので、**上書きされた語は控えにしか残らない**。
    zip を手で開かせるのでは約束が半分しか果たせないので、画面から中を見られる
    ようにしてある。**古いものを自動で消す口は作らない**（合計の大きさだけ出す）。
    """
    return archive.list_backups()


@app.get("/api/backups/{name}")
def read_backup(name: str) -> dict:
    """控え 1 つの中身。**いま手元にあるか**も返す（戻すと上書きになるかが分かる）。"""
    try:
        return archive.backup_contents(name)
    except archive.ArchiveError as exc:
        raise HTTPException(404, str(exc)) from exc


class RestoreRequest(BaseModel):
    ref: str


@app.post("/api/backups/{name}/restore")
def restore_from_backup(name: str, req: RestoreRequest) -> dict:
    """控えから**1 件だけ**書き戻す。控えの中身をそのまま書く（保存し直さない）。"""
    try:
        return archive.restore_entry(name, req.ref)
    except archive.ArchiveError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/api/backups/{name}", status_code=204)
def delete_backup(name: str) -> None:
    """控えを 1 つ捨てる。溜まったぶんの片付けは人が決める。"""
    try:
        archive.delete_backup(name)
    except archive.ArchiveError as exc:
        raise HTTPException(404, str(exc)) from exc


async def _archive_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > archive.MAX_ARCHIVE_BYTES:
        raise HTTPException(413, "zip が大きすぎます")
    data = await request.body()
    if not data:
        raise HTTPException(400, "zip が空です")
    return data


@app.post("/api/import-glossary/plan")
async def import_glossary_plan(request: Request, mode: str = "replace") -> dict:
    """取り込む前の下見。**何が増えて・上書きされて・消えるか**を数える。

    データを変える前に必ず通す。置き換えで消える語をここで見せておかないと、
    「入れ替わる」という一言だけで押させることになる。
    """
    try:
        return archive.plan(await _archive_body(request), mode)
    except archive.ArchiveError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/import-glossary")
async def import_glossary(request: Request, mode: str = "replace") -> dict:
    """書き出した zip を取り込む。``replace`` は置き換え、``merge`` は併合。

    **どちらも先に控えを取る**（`archive.import_bytes`）。併合で衝突したものは
    **取り込む側が勝つ**ので、上書きされた語は控えにしか残らない。

    保存先は変わらないので再起動は要らない。読み直しはサーバ側で済ませてある。
    """
    try:
        return archive.import_bytes(await _archive_body(request), mode)
    except archive.ArchiveError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/update")
async def check_update(force: bool = False) -> dict:
    """新しい版が出ているかを返す。**失敗しても 200 で、error に理由を入れる。**

    ここでしか外へ通信しない。lifespan で叩かないのは、起動のたびに勝手に
    出ていくのを避けるため（テストの TestClient も lifespan を走らせる）。
    """
    return await updates.check(force=force)


@app.post("/api/update/download")
async def download_update() -> dict:
    """新しい版を落として**アプリの隣に**展開する。起動も置き換えもしない。

    自分自身を差し替えないのは、動いている exe を置き換えられないことと、
    署名なしバイナリの自己書き換えがウイルス対策ソフトに嫌われること、
    `Program Files` では昇格が要ることの 3 つを避けるため。旧フォルダは
    そのまま残るので、問題があれば戻れる。
    """
    if not updates.enabled():
        raise HTTPException(409, "更新の確認が切ってあります。⚙ から有効にしてください。")
    try:
        return await to_thread.run_sync(installer.install_latest, abandon_on_cancel=True)
    except installer.InstallError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.put("/api/update")
def set_update_check(req: UpdateCheckRequest) -> dict:
    """更新の確認をするかを切り替える。"""
    settings = config.load_settings()
    settings["update_check"] = req.enabled
    config.save_settings(settings)
    updates.invalidate()
    return {"enabled": updates.enabled()}


@app.put("/api/settings")
def put_settings(req: SettingsRequest) -> dict:
    """データの保存先を書き換える。**効くのは次の起動から。**

    走っているプロセスの ``config`` は import 時に解決済みで、途中で差し替えると
    ``store`` のキャッシュや開いているフォルダの状態と食い違う。ここでは設定
    ファイルを書くだけにして、UI に再起動を促す。
    """
    if os.environ.get("GLOSSPOP_DATA_ROOT"):
        raise HTTPException(
            409,
            "環境変数 GLOSSPOP_DATA_ROOT が設定されているので、設定より優先されます。"
            "変えるにはその環境変数を外してください。",
        )

    settings = config.load_settings()
    raw = req.data_root.strip()
    copy_report = None

    if not raw:
        settings.pop("data_root", None)          # 既定（アプリの隣）に戻す
        target = config.APP_DIR
    else:
        try:
            target = Path(raw).expanduser().resolve()
        except OSError as exc:
            raise HTTPException(400, f"使えないパスです: {raw}") from exc
        if target.exists() and not target.is_dir():
            raise HTTPException(400, f"フォルダではありません: {target}")
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(400, f"フォルダを作れません: {exc}") from exc
        settings["data_root"] = str(target)

    if req.copy_existing and target != config.DATA_ROOT:
        try:
            copy_report = config.copy_data_root(config.DATA_ROOT, target)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    config.save_settings(settings)
    return {
        **_settings_payload(),
        # いまのプロセスは古い場所を見たまま。ここを黙ると「移したのに反映されない」になる
        "restart_required": True,
        "pending_data_root": str(target),
        "copied_from": str(config.DATA_ROOT) if copy_report else "",
        "copy": copy_report,
    }


# --------------------------------------------------------------------------- #
# API: カテゴリマスター
# --------------------------------------------------------------------------- #

@app.get("/api/categories")
def list_categories() -> list[dict]:
    return store.category_tree()


@app.post("/api/categories", status_code=201)
def create_category(req: CategoryRequest, scope: str = GLOBAL_SCOPE) -> dict:
    """カテゴリを登録する（用語 0 件でも作れる）。

    ``scope`` はどちらの辞書のマスターに載せるか。**渡さない経路を作らないこと**
    —— 既定に落ちると、フォルダのカテゴリのつもりが全体のマスターに残る。
    """
    category = categories.ensure(req.name, description=req.description, scope=scope)
    if req.subcategories:
        category = categories.set_subcategories(category.name, req.subcategories, scope)
    return {**category.model_dump(), "scope": scope}


@app.put("/api/categories/{name}")
def update_category(name: str, req: CategoryUpdateRequest, scope: str = GLOBAL_SCOPE) -> dict:
    """カテゴリを改名する（サブカテゴリの並びもここで差し替える）。

    マスターは辞書ごとにあるので、**ローカルでも同じことができる**。
    ``scope`` の辞書の中だけを触る。
    """
    current = categories.get(name, scope)
    if current is None:
        raise HTTPException(404, f"カテゴリ「{name}」がありません")
    if req.name and req.name.strip() != current.name:
        store.rename_category(current.name, req.name, scope)
        current = categories.get(req.name, scope)
    assert current is not None
    if req.subcategories is not None:
        current = categories.set_subcategories(current.name, req.subcategories, scope)
    if req.description:
        current = categories.set_description(current.name, req.description, scope)
    return {**current.model_dump(), "scope": scope}


@app.put("/api/category-order")
def reorder_categories(req: CategoryOrderRequest) -> list[dict]:
    """カテゴリの並び順を差し替える。

    パスを ``/api/categories/order`` にしないのは、``order`` という名前の
    カテゴリを作れてしまうと ``/api/categories/{name}`` と食い合うため。
    """
    categories.reorder(req.names, req.scope)
    return store.category_tree()


@app.delete("/api/categories/{name}", status_code=204)
def delete_category(name: str, scope: str = GLOBAL_SCOPE) -> None:
    store.delete_category(name, scope)


# --------------------------------------------------------------------------- #
# API: 相関図
# --------------------------------------------------------------------------- #

@app.get("/api/graph")
def graph(
    category: str | None = None,
    scope: str | None = None,
    spoilers: bool = False,
    doc: str | None = None,
) -> dict:
    """エントリ間の関係をノードと辺で返す。配置はクライアントの仕事。

    ``doc`` を渡すと、**その文書に出てくる語だけ**の図になる。出てくるかどうかは
    `Linker` に決めさせる —— 素の部分一致に戻すと `API` が `rapid` に当たり、
    **リンクにならない語を「出てくる」と言う**ことになる（`?ref=` の出現探しと
    同じ規則）。読むのは 1 文書だけで、フォルダ全体には広げない。

    ``spoilers=False`` （既定）では ``reveal`` が書かれた関係を出さない。
    伏せた本数は ``hidden`` で返すので、UI は「黙って欠けている」状態にはならない。

    ``doc`` があるときは、**その文書のどこで読めるようになるか**も足す
    （`timeline.annotate()`。時系列の見せ方が使う）。**辞書全体の図には足さない**
    —— 読むものが決まっていない以上、時系列は定義できない（`?doc=` と同じ約束）。
    """
    if scope is not None and scope not in (GLOBAL_SCOPE, LOCAL_SCOPE):
        raise HTTPException(400, f"不明な保存先です: {scope}")

    entries = store.load_all()
    only: set[str] | None = None
    document: documents.Document | None = None
    if doc:
        path = _safe_content_path(doc)
        try:
            document = documents.read_cached(path)
        except documents.DocumentError as exc:
            raise HTTPException(400, f"読めません: {doc}（{exc}）") from exc
        only = {e.ref for e in _linker().entries_in(document.plain)}

    result = relations.build_graph(
        entries, scope=scope, category=category, spoilers=spoilers, only=only
    )
    if document is not None:
        timeline.annotate(result, document, _linker())
    # 何に絞ったのかは画面に出す（絞っていないときは「辞書全体」と言わせる）
    result["doc"] = doc or ""
    return result


def _relation_scope(category: str, scope: str) -> list[Entry]:
    """関係を探す対象のエントリ。範囲指定が空なら辞書全体。"""
    if scope and scope not in (GLOBAL_SCOPE, LOCAL_SCOPE):
        raise HTTPException(400, f"不明な保存先です: {scope}")
    return [
        e for e in store.load_all()
        if (not category or e.category == category) and (not scope or e.scope == scope)
    ]


@app.post("/api/ai/relations")
async def ai_relations(req: RelationsDraftRequest) -> dict:
    """登録済みの用語どうしの関係を下書きする。用語は作らない。

    関係のデータ構造だけあっても 1 本ずつ手で書くことになり、図が空のまま
    終わる。ここが埋める側。**保存はしない** —— 選んで書き込むのは
    ``/api/relations`` の仕事。
    """
    if not ai.available():
        raise HTTPException(503, "claude CLI が見つかりません。関係は手で書けます。")

    target = _relation_scope(req.category, req.scope)
    focus = None
    if req.ref:
        focus = store.get(req.ref)
        if focus is None:
            raise HTTPException(404, f"見つかりません: {req.ref}")
        # **相手が要る。** 範囲の絞り込みで当の語が落ちていたら足し直す
        # （落ちたまま探すと、その語を端にした関係は 1 本も作れない）
        if all(e.ref != focus.ref for e in target):
            target = [focus, *target]
    if len(target) < 2:
        raise HTTPException(400, "関係を探すには、その範囲に 2 語以上の登録が要ります")

    if req.text.strip():
        # 表示中の文書をそのまま読む。URL を読んでいるときはフォルダに本文が
        # 無い（sites/ にあるのは辞書だけ）ので、この経路でしか下書きできない
        docs = [(req.source or "表示中の文書", req.text)]
        unread: list[str] = []
    else:
        # **本文を渡さない経路は用語ページの「この語の関係を下書き」だけ。**
        # あちらには読んでいる文書が無いので、ここでフォルダを読む。
        # 候補語の抽出と違って**待ち時間には効かない** —— 読むのは
        # `read_cached` なので実測 17.6 ms（温まれば 3.2 ms）で、AI に渡すのは
        # そこから選んだ窓だけ。所要時間を決めるのは本数のほう
        docs, unread, base = _read_content_docs(req.max_files)
        if not docs:
            raise HTTPException(400, f"読める文書がありません: {base}")

    spoiler = req.spoiler if req.spoiler in config.SPOILER_LEVELS else config.SPOILER_DEFAULT
    if spoiler == "position":
        # 位置だけでは関係は作れない。全文ではなく初出の場面に倒す
        spoiler = "first"

    try:
        result = await ai.draft_relations(
            store.load_all(),
            docs,
            scope=target,
            limit=max(1, min(req.limit, 40)),
            spoiler=spoiler,
            focus=focus,
        )
    except ai.AIError as exc:
        raise HTTPException(502, str(exc)) from exc

    result["spoiler"] = spoiler
    result["files_skipped"] = unread
    result["terms"] = [e.term for e in target]
    return result


@app.post("/api/relations")
def apply_relations(req: RelationsApplyRequest) -> dict:
    """選ばれた関係をまとめて書き込む。

    **エントリ単位でまとめて保存する。** 1 本ずつ書くと、同じエントリに複数の
    関係が付いたときに後の書き込みが前のものを消す。
    """
    by_source: dict[str, list[RelationItem]] = {}
    for item in req.relations:
        by_source.setdefault(item.from_ref, []).append(item)

    applied = 0
    results: list[dict] = []
    for ref, items in by_source.items():
        entry = store.get(ref)
        if entry is None:
            results.append({"ref": ref, "ok": False, "detail": f"見つかりません: {ref}"})
            continue
        draft = EntryDraft.model_validate(entry.model_dump())
        # 既存の関係を残したまま足す。同じ行き先は Relation の検証が 1 本に潰す
        draft.relations = [
            *entry.relations,
            *(Relation.model_validate(i.model_dump(exclude={"from_ref"})) for i in items),
        ]
        try:
            saved = store.save(draft, ref=ref)
        except store.StoreError as exc:
            results.append({"ref": ref, "ok": False, "detail": str(exc)})
            continue
        added = len(saved.relations) - len(entry.relations)
        applied += added
        results.append({
            "ref": saved.ref,
            "ok": True,
            "term": saved.term,
            "added": added,
            "url": entry_url(saved),
        })
    return {"applied": applied, "results": results}


@app.post("/api/aliases")
def apply_aliases(req: AliasApplyRequest) -> dict:
    """「同じものの別の呼び方」をまとめて既存エントリの別名に足す。

    **新しいエントリを作らないための口。** 同じ人物が呼び方ごとに別エントリへ
    割れると、本文のリンク先も相関図のノードも二重になる。
    """
    by_ref: dict[str, list[str]] = {}
    for item in req.aliases:
        alias = item.alias.strip()
        if alias:
            by_ref.setdefault(item.ref, []).append(alias)

    applied = 0
    results: list[dict] = []
    for ref, names in by_ref.items():
        entry = store.get(ref)
        if entry is None:
            results.append({"ref": ref, "ok": False, "detail": f"見つかりません: {ref}"})
            continue
        draft = EntryDraft.model_validate(entry.model_dump())
        draft.aliases = [*entry.aliases, *names]
        try:
            saved = store.save(draft, ref=ref)
        except store.StoreError as exc:
            results.append({"ref": ref, "ok": False, "detail": str(exc)})
            continue
        added = len(saved.aliases) - len(entry.aliases)
        applied += added
        results.append({
            "ref": saved.ref,
            "ok": True,
            "term": saved.term,
            "added": added,
            "path_label": saved.path_label,
            "url": entry_url(saved),
        })
    return {"applied": applied, "results": results}


# --------------------------------------------------------------------------- #
# API: 点検
# --------------------------------------------------------------------------- #

@app.get("/api/doctor")
def run_doctor() -> dict:
    """辞書全体を点検する。壊れているものだけを返す。

    参照を名前で書ける（ID を持たない）ぶん、書き間違いや相手の削除で静かに
    切れる。``/api/graph`` はカテゴリ単位でしか壊れを返さないので、横断して
    集める受け皿がここ。
    """
    return doctor.check(store.load_all())


# --------------------------------------------------------------------------- #
# API: 辞書
# --------------------------------------------------------------------------- #

@app.get("/api/entries")
def list_entries(
    q: str = "",
    category: str | None = None,
    subcategory: str | None = None,
    scope: str | None = None,
    tag: str | None = None,
) -> list[dict]:
    needle = q.strip().casefold()
    personas = {s: _persona_url(s) for s in SCOPES}
    out = []
    for e in store.load_all():
        if scope is not None and e.scope != scope:
            continue
        if category is not None and e.category != category:
            continue
        if subcategory is not None and e.subcategory != subcategory:
            continue
        # タグは完全一致。``q`` はタグも本文も舐めるので、
        # 「タグ名がたまたま本文に出る別の語」まで引っかかる
        if tag is not None and tag not in e.tags:
            continue
        if needle:
            haystack = " ".join([e.term, e.reading, e.summary, e.definition, *e.aliases, *e.tags]).casefold()
            if needle not in haystack:
                continue
        card = _term_card(e, personas=personas)
        card["aliases"] = e.aliases
        card["tags"] = e.tags
        card["updated_at"] = e.updated_at
        out.append(card)
    return out


@app.get("/api/tags")
def list_tags() -> list[dict]:
    """使われているタグと件数。多い順、同数なら名前順。

    **タグにマスターは無い。** カテゴリと違って置き場所を決めないので、
    実際に使われているものを数え上げるしかない（``categories.yaml`` に相当する
    ものを作ると、用語 0 件のタグが残って掃除の口が要る）。
    """
    counts: dict[str, int] = {}
    for e in store.load_all():
        for name in e.tags:
            counts[name] = counts.get(name, 0) + 1
    return [
        {"name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


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
        entry = store.move(ref, req.category or None, scope=req.scope or None)
    except store.StoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _entry_payload(entry)


# --------------------------------------------------------------------------- #
# API: 統合（割れてしまった同じものを 1 つにまとめる）
#
# **下見と実行を分ける。** これはデータを消す経路なので、何がどうなるかを
# 全部見せてから実行する。畳めない項目（本文・要約）は人が決めた値だけを受け取り、
# サーバは黙って片方に寄せない。
# --------------------------------------------------------------------------- #

@app.get("/api/merge")
def merge_plan(keep: str, drop: str) -> dict:
    try:
        return merge.plan(keep, drop)
    except merge.MergeError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/merge")
def merge_apply(req: MergeRequest) -> dict:
    try:
        entry = merge.apply(
            req.keep, req.drop, fields=req.fields, relations=req.relations
        )
    except merge.MergeError as exc:
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
    local_root = config.local_root()
    return {
        "root": str(base),
        "is_default": config.is_default_content_dir(),
        "files": files,
        "truncated": truncated,
        # ローカル辞書は祖先にあることがある (1 巻 2 巻で共有するとき)。
        # 黙って別の場所を使わないよう、実際の置き場所を返す
        "local_dir": str(config.local_glossary_dir() or ""),
        "local_is_ancestor": local_root is not None and local_root != base,
        "reading_url": config.reading_url() or "",
    }


@app.post("/api/pick-folder")
async def pick_folder(req: PickFolderRequest) -> dict:
    """OS のフォルダ選択ダイアログを開く（サーバ＝手元の PC で開く）。

    選ばれても root は変えない。切り替えは ``/api/content-root`` の役目。
    """
    initial = req.initial or str(config.content_dir())
    try:
        path = await to_thread.run_sync(picker.pick_folder, initial, abandon_on_cancel=True)
    except picker.PickerError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"path": path, "cancelled": not path}


@app.post("/api/url-context")
def set_url_context(req: UrlContextRequest) -> dict:
    """いま読んでいる URL を伝える（空なら開いているフォルダに戻る）。

    フォルダと URL は排他。小説フォルダを開いたまま Web ページを読んで、
    登場人物名が無関係なページでリンクになる、という事故を防ぐ。
    """
    config.set_reading_url(req.url)
    return sites.describe(config.reading_url())


@app.post("/api/url-dictionary", status_code=201)
def create_url_dictionary(req: UrlDictionaryRequest) -> dict:
    """``ドメイン/パス`` に辞書を作る。以後その配下を読むときに効く。"""
    try:
        sites.create(req.prefix)
    except sites.SiteError as exc:
        raise HTTPException(400, str(exc)) from exc
    store.invalidate()
    return sites.describe(config.reading_url())


@app.get("/api/url-dictionaries")
def list_url_dictionaries() -> list[dict]:
    """作ってある URL 辞書の一覧（prefix と語数）。"""
    base = config.SITES_DIR
    out = []
    if base.exists():
        for marker in sorted(base.rglob(config.LOCAL_DIR_NAME)):
            if not marker.is_dir():
                continue
            glossary = marker / "glossary"
            out.append({
                "prefix": sites.prefix_of(marker.parent),
                "dir": str(glossary),
                "count": len(list(glossary.glob("*/*.md"))) if glossary.exists() else 0,
            })
    return out


@app.post("/api/content-root")
def set_content_root(req: ContentRootRequest) -> dict:
    """開くフォルダを切り替える (空文字で既定に戻す)。

    ローカル専用ツールなので任意のパスを受ける。ブラウザの他タブから叩かれても
    JSON の POST は preflight が要るので素通りはしない。
    """
    config.set_reading_url(None)   # フォルダに戻る
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


#: 横断検索で開くファイル数の上限。一覧 (MAX_CONTENT_FILES) より小さいのは、
#: 一覧が stat するだけなのに対し、こちらは**中身を全部読む**ため。
#: epub と pdf は 1 冊で数百ページある
MAX_SEARCH_FILES = 300

#: 返すヒットの総数と、1 ファイルあたりの数。1 つの長い本で埋めない
MAX_SEARCH_HITS = 200
MAX_HITS_PER_FILE = 5

#: 抜粋の前後の文字数
SNIPPET_LEAD = 30
SNIPPET_TRAIL = 60

#: 例文として拾う 1 文の上限。句点の無い段落で壁にならないように
MAX_SENTENCE_CHARS = 200

#: 文の切れ目。改行も 1 文の終わりとして扱う（詩や台詞で句点が無い）
_SENTENCE_END = "。．.!?！？\n"


def _snippet(text: str, start: int, end: int) -> str:
    """一致の周りを 1 行に均して切り出す。"""
    lead = max(0, start - SNIPPET_LEAD)
    tail = min(len(text), end + SNIPPET_TRAIL)
    piece = " ".join(text[lead:tail].split())
    return ("…" if lead > 0 else "") + piece + ("…" if tail < len(text) else "")


def _sentence(text: str, start: int, end: int) -> str:
    """一致を含む 1 文。**そのまま使用例に貼れる形**にする。

    抜粋 (`_snippet`) は文字数で切るので前後が欠ける。例文として辞書に残すなら
    文の切れ目まで採らないと、「…」の付いた半端な文が溜まる。
    """
    lo = start
    while lo > 0 and text[lo - 1] not in _SENTENCE_END:
        lo -= 1
    hi = end
    while hi < len(text) and text[hi - 1] not in _SENTENCE_END:
        hi += 1
    return " ".join(text[lo:hi].split())[:MAX_SENTENCE_CHARS]


def _plain_finder(needle: str):
    """ただの部分一致。フォルダの横断検索はこちら。"""
    def find(text: str):
        folded = (text or "").casefold()
        start = folded.find(needle)
        while start >= 0:
            yield start, start + len(needle)
            start = folded.find(needle, start + len(needle))
    return find


def _entry_finder(entry: Entry):
    """**自動リンクと同じ規則**で当てる。用語の出現を探すのはこちら。

    素の部分一致にすると、`API` が `rapid` に当たるなど**リンクにならない語を
    「出てくる」と言う**ことになる。規則は `Linker` 1 か所から出す。
    """
    linker = Linker([entry])

    def find(text: str):
        for m in linker.finditer(text or ""):
            yield m.start(), m.end()
    return find


def _search_document(doc: documents.Document, find) -> tuple[list[dict], int]:
    """1 文書の中を探す。``(抜粋のリスト, 総ヒット数)``。

    位置は章 / ページの名前があればそれを、無ければ行番号を使う
    （``Document.locate()`` と同じ規則で、こちらは**出現ごと**に出す）。
    """
    hits: list[dict] = []
    total = 0
    for label, text in doc.segments:
        for start, end in find(text):
            total += 1
            if len(hits) < MAX_HITS_PER_FILE:
                hits.append({
                    "locator": label or f"L.{text[:start].count(chr(10)) + 1}",
                    "snippet": _snippet(text, start, end),
                    # 使用例にそのまま貼れる形（文の切れ目まで）
                    "sentence": _sentence(text, start, end),
                })
    return hits, total


@app.get("/api/content-search")
def search_content(q: str = "", ref: str = "") -> dict:
    """開いているフォルダの**本文**を横断して探す。

    一覧はファイル名しか見ていないので、「あの言い回しがどこに出てきたか」を
    探す手段がなかった。索引は持たずにその場で読む —— 索引を持つと、外で
    書き換えられたファイルとずれる（辞書が mtime で読み直しているのと同じ問題を、
    こちらは本文の量で抱えることになる）。

    ``ref`` を渡すと、その用語の**表記すべて**（別名を含む）を自動リンクと同じ
    規則で探す。用語ページの「この語が出てくる文書」がこれを使う。

    **打ち切ったことは必ず返す。** 黙って切ると「無かった」と区別が付かない。
    """
    if ref:
        entry = store.get(ref)
        if entry is None:
            raise HTTPException(404, f"見つかりません: {ref}")
        find = _entry_finder(entry)
        label = entry.term
    else:
        needle = q.strip().casefold()
        if not needle:
            raise HTTPException(400, "探す語を入れてください")
        find = _plain_finder(needle)
        label = q.strip()
    base = config.content_dir()
    results: list[dict] = []
    skipped: list[dict] = []
    scanned = 0
    hit_count = 0
    files_truncated = hits_truncated = False

    if base.exists():
        for path in _iter_content_files(base):
            if scanned >= MAX_SEARCH_FILES:
                files_truncated = True
                break
            if hit_count >= MAX_SEARCH_HITS:
                hits_truncated = True
                break
            scanned += 1
            rel = path.relative_to(base).as_posix()
            try:
                doc = documents.read_cached(path)
            except (documents.DocumentError, OSError) as exc:
                # 読めないファイルがあること自体を隠さない（「無かった」ではない）
                skipped.append({"path": rel, "reason": str(exc)})
                continue
            hits, total = _search_document(doc, find)
            if not total:
                continue
            hit_count += total
            results.append({
                "path": rel,
                "name": path.name,
                "title": doc.title,
                "count": total,
                "hits": hits,
            })

    # 多く出てくる文書ほど上。同数ならパス順（同じ検索で並びが揺れない）
    results.sort(key=lambda r: (-r["count"], r["path"]))
    return {
        "query": label,
        "root": str(base),
        "files_scanned": scanned,
        "files_truncated": files_truncated,
        "hits_truncated": hits_truncated,
        "total_hits": hit_count,
        "results": results,
        "skipped": skipped,
    }


@app.get("/api/content/{rel:path}")
def read_content(rel: str) -> dict:
    path = _safe_content_path(rel)
    try:
        doc = documents.read_cached(path)
    except documents.DocumentError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "path": rel,
        "name": path.name,
        "text": doc.text,
        # epub は HTML、pdf はテキストになる。拡張子から推測させない
        "kind": doc.kind,
        "title": doc.title,
        # 読めなかった章。黙って欠けた本文を出さないための報告
        "skipped": doc.skipped,
        # 目次。**名前のある区切りを持つ文書だけ**（epub は章、pdf はページ）。
        # .md / .txt は区切りが 1 つしかないので空になる
        "sections": [label for label, _ in doc.segments if label],
    }


@app.post("/api/fetch")
async def fetch_url(req: FetchRequest) -> dict:
    try:
        return await to_thread.run_sync(fetcher.fetch, req.url, abandon_on_cancel=True)
    except fetcher.FetchError as exc:
        raise HTTPException(502, str(exc)) from exc


# --------------------------------------------------------------------------- #
# API: AI 下書き
# --------------------------------------------------------------------------- #

@app.get("/api/persona")
def persona(scope: str = GLOBAL_SCOPE) -> FileResponse:
    """ペルソナ（語り手）の顔を返す。無ければ 404。

    **パスは受け取らない。** 名前も拡張子も決め打ちで、`scope` から場所を引くだけ
    （外から来た文字列で組み立てないのは、控えの取り出しと同じ規則）。
    **SVG は配らない** —— スクリプトを持てるうえ、ここは中身を検査せずにそのまま
    返す口なので、`htmlclean` の許可制と同じ線を引いておく。
    """
    if scope not in SCOPES:
        raise HTTPException(400, f"不明な保存先です: {scope}")
    path = store.persona_file(scope)
    if path is None:
        raise HTTPException(404, "ペルソナの画像がありません")
    return FileResponse(
        path,
        media_type=PERSONA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        # URL に更新時刻を入れてあるので、こちらは長く持たせてよい
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.post("/api/persona")
async def put_persona(request: Request, scope: str = GLOBAL_SCOPE) -> dict:
    """語り手の顔を差し替える。**中身は生のバイト列で受け取る。**

    ``multipart`` にしないのは、送られてくるファイル名を**そもそも受け取らない**
    ため（保存先は `scope` から引き、拡張子は中身から見分ける）。取り込みの zip
    (`/api/import-glossary`) と同じ形。
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > ai.PERSONA_MAX_BYTES:
        raise HTTPException(413, "画像が大きすぎます")
    data = await request.body()
    try:
        ai.save_persona(scope, data)
    except ai.AIError as exc:
        raise HTTPException(400, str(exc)) from exc
    # 文体と同じで、書いたあとの状態一式を返す（クライアントは描き直すだけでよい）
    return _ai_state()


@app.delete("/api/persona")
def remove_persona(scope: str = GLOBAL_SCOPE) -> dict:
    """語り手の顔を消す。**辞書のディレクトリは残す**（中に用語が入っている）。"""
    try:
        ai.delete_persona(scope)
    except ai.AIError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _ai_state()


@app.get("/api/ai/settings")
def ai_settings() -> dict:
    """いまどの AI・モデル・思考の深さで動くか。**キーそのものは返さない。**

    文体 (`style`) だけは ``ai`` の側が持つ。「何を頼むか」なので ``llm`` の
    仕事ではなく、同じ画面に出るというだけの理由でここが繋いでいる。
    """
    return _ai_state()


@app.put("/api/ai/settings")
def put_ai_settings(req: AISettingsRequest) -> dict:
    """AI の選択を保存する。**保存先の設定と違って、次の呼び出しから効く。**

    ``store`` のキャッシュや開いているフォルダのような、途中で変わると食い違う
    状態を持たないため、再起動を待たせる理由が無い。
    """
    if req.provider and req.provider not in llm.PROVIDERS:
        raise HTTPException(400, f"知らない AI です: {req.provider}")
    if req.effort is not None and req.effort not in llm.EFFORTS:
        raise HTTPException(400, f"知らない思考の深さです: {req.effort}")

    settings = config.load_settings()
    if req.provider is not None:
        settings["ai_provider"] = req.provider
    if req.effort is not None:
        settings["ai_effort"] = req.effort
    if req.model is not None:
        # モデルは AI ごとに覚える（切り替えて戻したときに選び直させない）
        target = req.provider or llm.resolve()["provider"]
        settings[llm.MODEL_SETTINGS[target]] = req.model.strip()
    if req.gemini_api_key is not None:
        key = req.gemini_api_key.strip()
        if key:
            settings["gemini_api_key"] = key
        else:
            settings.pop("gemini_api_key", None)     # 空文字は「消す」
    config.save_settings(settings)
    return _ai_state()


@app.put("/api/ai/style")
def put_ai_style(req: AIStyleRequest) -> dict:
    """文体（口調）を保存する。``scope`` が ``local`` ならフォルダの側に書く。

    **書いた場所は返す値に出る**（`style_folder_path`）—— 祖先のフォルダに
    書かれることがあるので、黙って書かない。
    """
    try:
        ai.save_style(req.scope, req.style)
    except ai.AIError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _ai_state()


@app.get("/api/ai/models")
def ai_models(provider: str = "") -> dict:
    """選べるモデルを返す。**Gemini は API から引く**（焼き込むと古くなる）。"""
    provider = provider or llm.resolve()["provider"]
    if provider not in llm.PROVIDERS:
        raise HTTPException(400, f"知らない AI です: {provider}")
    if provider != "gemini":
        return {"provider": provider, "models": llm.CLAUDE_MODELS}
    try:
        models = llm.list_gemini_models()
    except llm.LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"provider": provider, "models": models}


@app.get("/api/ai/kinds")
def ai_kinds() -> dict:
    """抽出で選べる種別。**何を抜き出すかを先に決める**ための一覧。

    種別を指定しないと AI は語義説明のできる語ばかり挙げ、登場人物が丸ごと
    落ちる。UI はここを引いてチェックボックスを出す。
    """
    return {
        "kinds": [
            {
                "key": key,
                "label": spec["label"],
                # プロンプト用の ** を落とした素の文を返す (UI にそのまま出る)
                "hint": ai.plain_hint(key),
                "scope": spec["scope"],
            }
            for key, spec in ai.EXTRACT_KINDS.items()
        ],
        "default": list(ai.DEFAULT_KINDS),
    }


@app.post("/api/ai/extract")
async def ai_extract(req: ExtractRequest) -> dict:
    """表示中の文書から候補語を挙げる（1 回の呼び出しで済ませる）。

    ここでは登録も下書き生成もしない。選ばれた語について
    ``/api/ai/draft`` を語数ぶん呼ぶのはクライアント側の仕事。
    """
    if not ai.available():
        raise HTTPException(503, "claude CLI が見つかりません。手動入力で登録してください。")
    try:
        return await ai.extract_terms(
            req.text,
            source=req.source,
            limit=max(1, min(req.limit, 30)),
            kinds=req.kinds,
        )
    except ai.AIError as exc:
        raise HTTPException(502, str(exc)) from exc


# **候補語の抽出にフォルダ横断の口は無い**（``/api/ai/extract`` の 1 つだけ）。
# かつて ``/api/ai/extract-folder`` が全ファイルを読んでいたが、何ファイル
# まとめても AI に渡せる本文の枠 (``ai.EXTRACT_TEXT_CHARS``) は 1 文書のときと
# 同じなので、**ファイル数ぶん薄まるだけで待ち時間（実測 100〜260 秒）が積み上がる**。
# フォルダを丸ごと辞書化したいときは、ファイルごとに ✨ 用語を抽出 を回すこと
# （そのほうが 1 ファイルあたりの取り分は大きい）。
# → docs/design-notes.md「フォルダ全体を AI に読ませる道は、抽出からは畳む」
#
# 下の ``_read_content_docs()`` は**関係の下書きだけ**が使う。あちらは読んだ本文を
# そのまま渡すのではなく窓を選んで渡すので、ファイルを読む代金（実測 17.6 ms）は
# 待ち時間に効かない —— 同じ「フォルダを読む」でも話が違う。


def _read_content_docs(max_files: int) -> tuple[list[tuple[str, str]], list[str], Path]:
    """開いているフォルダの文書を読む。返すのは (読めたもの, 読まなかったもの, ルート)。

    **呼ぶのは ``/api/ai/relations`` の 1 か所だけ** —— 用語ページの
    「✨ この語の関係を下書き」には読んでいる文書が無いので、ここで補う。

    読まなかったファイルを返すのは、黙って切らないため（呼び出し側が UI に出す）。
    """
    limit = max(1, min(max_files, 200))
    docs: list[tuple[str, str]] = []
    unread: list[str] = []
    base = config.content_dir()
    for path in _iter_content_files(base):
        rel = path.relative_to(base).as_posix()
        if len(docs) >= limit:
            unread.append(rel)
            continue
        try:
            docs.append((rel, documents.read_cached(path).plain))
        except (OSError, documents.DocumentError):
            unread.append(rel)
    return docs, unread, base


def _first_seen_in_file(rel: str, term: str) -> tuple[str, str]:
    """content 内のファイルから初出位置と、その場面の抜粋を取る。"""
    try:
        path = _safe_content_path(rel)
        doc = documents.read_cached(path)
    except (HTTPException, documents.DocumentError):
        return "", ""
    return doc.locate(term), ai.context_up_to_first(doc.plain, term)


@app.post("/api/ai/draft")
async def ai_draft(req: DraftRequest) -> dict:
    spoiler = req.spoiler if req.spoiler in config.SPOILER_LEVELS else config.SPOILER_DEFAULT

    locator = ""
    context = req.context
    if req.file:
        locator, first_context = _first_seen_in_file(req.file, req.term)
        if spoiler == "first":
            # それ以降の展開は渡さない。初出の場面だけに差し替える
            context = first_context or req.context

    auto_scope = req.scope == AUTO_SCOPE

    if spoiler == "position":
        # AI を呼ばない。初出位置だけ埋めて、本文はユーザーが書く
        # 保存先は抽出時の種別だけが手がかり (人物・独自語ならこのフォルダの辞書)。
        # 種別も無ければ全体の辞書に置く
        hinted = ai.EXTRACT_KINDS.get(req.kind, {}).get("scope", "")
        if auto_scope:
            scope = hinted if hinted in (GLOBAL_SCOPE, LOCAL_SCOPE) else GLOBAL_SCOPE
            if scope == LOCAL_SCOPE and not store.local_available():
                scope = GLOBAL_SCOPE
        else:
            scope = req.scope
        draft = EntryDraft(
            term=req.term,
            source=req.source,
            first_file=req.file,
            first_locator=locator,
            scope=scope,
        )
        return {
            "draft": draft.model_dump(),
            "registered_category": None,
            "warning": "",
            "existing": [_term_card(e) for e in store.find_by_surface(req.term)],
        }

    if not ai.available():
        raise HTTPException(
            503, "claude CLI が見つかりません。手動入力で登録してください。"
        )
    try:
        draft = await ai.draft_entry(
            req.term,
            context,
            source=req.source,
            spoiler=spoiler,
            # 自動のときだけ保存先も選ばせる。フォルダ名が判断材料になる
            scope_folder=config.content_dir().name if auto_scope else None,
            kind=req.kind,
            current=req.current,
        )
    except ai.AIError as exc:
        raise HTTPException(502, str(exc)) from exc
    draft.first_file = req.file
    draft.first_locator = locator
    if not auto_scope:
        draft.scope = req.scope          # 指定されていれば AI の答えより優先する
    if draft.scope == LOCAL_SCOPE and not store.local_available():
        draft.scope = GLOBAL_SCOPE       # フォルダが無ければローカルには置けない

    # 下書き段階でカテゴリをマスターに登録しておく (保存されず空振りでも残す)。
    # **ローカルは空振りぶんを登録しない。** マスターは辞書ごとに持てるように
    # なったが、書き込む先が「利用者のフォルダ」なので、保存もしなかった提案で
    # そこにファイルを作らない。保存すれば store.save() がそのとき登録する
    registered = None
    warning = ""
    if draft.category and draft.scope != LOCAL_SCOPE:
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
