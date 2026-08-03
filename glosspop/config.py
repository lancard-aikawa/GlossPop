"""パス・実行設定。すべて環境変数で上書きできる。"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


#: PyInstaller で固めた exe から動いているか
FROZEN = bool(getattr(sys, "frozen", False))


# --------------------------------------------------------------------------- #
# ユーザー設定ファイル
#
# **アプリのフォルダの外に置く。** 中身は「データをどこに置くか」で、更新のたびに
# アプリのフォルダを丸ごと入れ替えても残る必要があるため。ここを data/ の下に
# 置くと、設定を読むために設定が要る、という循環になる。
# --------------------------------------------------------------------------- #

def _settings_file() -> Path:
    """設定ファイルの場所。OS のユーザー領域に置く。"""
    raw = os.environ.get("GLOSSPOP_SETTINGS_FILE")
    if raw:
        return Path(raw).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "GlossPop" / "settings.json"


SETTINGS_FILE = _settings_file()


def load_settings() -> dict:
    """設定ファイルを読む。壊れていても起動は止めない（既定に落ちる）。"""
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(values: dict) -> Path:
    """設定ファイルを書く。返すのは書いた場所。"""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    os.replace(tmp, SETTINGS_FILE)
    return SETTINGS_FILE

#: パッケージの場所。凍結時は一時展開先 (_internal) を指す
PACKAGE_DIR = Path(__file__).resolve().parent

#: アプリ本体の場所。**更新のたびに丸ごと入れ替わる側。**
#: 凍結時に PACKAGE_DIR を基準にすると、保存した辞書が一時展開先に書かれて
#: 終了時に消える。exe の隣を基準にする。
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else PACKAGE_DIR.parent


def _data_root() -> Path:
    """書き込むデータ (辞書・content) の基準ディレクトリ。

    優先順は **環境変数 > 設定ファイル > アプリの隣**。環境変数を最優先にするのは、
    テストと一時的な切り替えが設定ファイルに引きずられないようにするため。

    既定がアプリの隣なのは、フォルダごと持ち運べる（USB でも動く）性質を保つため。
    アプリの外へ移すと、**更新はフォルダを入れ替えるだけで済む**（データを手で
    コピーしなくてよくなる）。設定メニューから切り替えられる。
    """
    raw = os.environ.get("GLOSSPOP_DATA_ROOT") or load_settings().get("data_root")
    if raw:
        try:
            return Path(str(raw)).expanduser().resolve()
        except OSError:
            pass          # 壊れた設定で起動できなくならないよう既定に落ちる
    return APP_DIR


DATA_ROOT = _data_root()

#: 後方互換 (開発時は従来どおりリポジトリルート)
PROJECT_ROOT = DATA_ROOT

#: 辞書 Markdown の置き場所 (data/glossary/<カテゴリ>/<slug>.md)
GLOSSARY_DIR = _env_path("GLOSSPOP_GLOSSARY_DIR", DATA_ROOT / "data" / "glossary")

#: カテゴリマスター
CATEGORIES_FILE = _env_path("GLOSSPOP_CATEGORIES_FILE", GLOSSARY_DIR.parent / "categories.yaml")

#: ビューアがブラウズできる .md / .txt の置き場所
CONTENT_DIR = _env_path("GLOSSPOP_CONTENT_DIR", DATA_ROOT / "content")

#: 静的ファイル
STATIC_DIR = PACKAGE_DIR / "static"

#: 専用ウィンドウ（ブラウザのアプリモード）が使うプロファイル。
#: 普段のブラウザと混ぜないためと、窓の寿命を親から追えるようにするため
#: （`appwindow` の docstring 参照）。localStorage もここに貯まる
WINDOW_PROFILE_DIR = _env_path("GLOSSPOP_WINDOW_PROFILE_DIR", DATA_ROOT / "data" / "window")

#: claude CLI の場所 (見つからなければ AI 下書き機能だけが無効になる)
CLAUDE_BIN = os.environ.get("GLOSSPOP_CLAUDE_BIN") or shutil.which("claude")

#: claude -p に渡す追加引数
CLAUDE_EXTRA_ARGS = shlex.split(os.environ.get("GLOSSPOP_CLAUDE_ARGS", "--model sonnet"))

#: claude CLI のタイムアウト秒
CLAUDE_TIMEOUT = int(os.environ.get("GLOSSPOP_CLAUDE_TIMEOUT", "180"))

#: AI にどこまで読ませるか（小説の人物辞書などでのネタバレ対策）
#:
#: position … AI を呼ばない。初出位置だけ記録して本文は自分で書く
#: first    … 初出の前後だけを渡す。それ以降の展開は知らないものとして書かせる
#: full     … 全部渡す（ネタバレ可）
SPOILER_LEVELS = ("position", "first", "full")
SPOILER_DEFAULT = os.environ.get("GLOSSPOP_SPOILER_DEFAULT", "full")
if SPOILER_DEFAULT not in SPOILER_LEVELS:
    SPOILER_DEFAULT = "full"

#: URL 読み込みの上限とタイムアウト
FETCH_TIMEOUT = float(os.environ.get("GLOSSPOP_FETCH_TIMEOUT", "20"))
FETCH_MAX_BYTES = int(os.environ.get("GLOSSPOP_FETCH_MAX_BYTES", str(8 * 1024 * 1024)))
FETCH_MAX_REDIRECTS = int(os.environ.get("GLOSSPOP_FETCH_MAX_REDIRECTS", "5"))
FETCH_USER_AGENT = os.environ.get(
    "GLOSSPOP_FETCH_USER_AGENT", "GlossPop/0.1 (+local reader)"
)


#: 実行中に切り替えられる content ルート (ビューアの「フォルダを開く」)。
#: 既定 (CONTENT_DIR) は環境変数と起動場所で決まり、こちらはプロセス内の一時的な上書き。
_content_override: Path | None = None


def content_dir() -> Path:
    """いま開いている content フォルダ。

    ``CONTENT_DIR`` を直接見ずに必ずこれを通すこと。UI から切り替えたあとも
    一覧・読み出し・パス検査が同じ基準を使う必要がある。
    """
    return _content_override or CONTENT_DIR


def set_content_dir(path: Path | None) -> Path:
    """content フォルダを切り替える。``None`` で既定に戻す。"""
    global _content_override
    _content_override = path
    return content_dir()


def is_default_content_dir() -> bool:
    return _content_override is None


#: 開いているフォルダに置くローカル辞書のディレクトリ名。
#: フォルダごとコピーすれば辞書も一緒についていく
LOCAL_DIR_NAME = ".glosspop"

#: ローカル辞書を祖先方向に探す段数。
#: 1 巻 2 巻をフォルダで分けていても、作品フォルダに 1 つ置けば共有できる。
#: 無制限に遡るとドライブ全体の辞書を掴みかねないので段数で止める
LOCAL_SEARCH_DEPTH = int(os.environ.get("GLOSSPOP_LOCAL_SEARCH_DEPTH", "6"))


#: URL 用のローカル辞書の置き場所。``sites/<ドメイン>/<パス>/.glosspop/``
SITES_DIR = _env_path("GLOSSPOP_SITES_DIR", GLOSSARY_DIR.parent / "sites")

#: いま読んでいる URL（フォルダを読んでいるときは None）。
#: フォルダと URL は排他。小説フォルダを開いたまま Web を読んで、
#: 登場人物名が無関係なページでリンクになる、という事故を防ぐ
_reading_url: str | None = None


def set_reading_url(url: str | None) -> None:
    """URL を読み始める / 読み終える。空なら（開いている）フォルダに戻る。"""
    global _reading_url
    _reading_url = url or None


def reading_url() -> str | None:
    return _reading_url


def _folder_local_root() -> Path:
    """開いているフォルダから祖先方向へ ``.glosspop`` を探す。

    **いちばん近いもの**を使い、見つからなければ開いているフォルダ自身
    （そこに作られる）。巻ごとにフォルダを分けていても、作品フォルダに 1 つ置けば
    共有できる。逆に巻ごとに分けたいなら、その巻に置けば近い方が勝つ。
    """
    base = content_dir()
    current = base
    for _ in range(max(0, LOCAL_SEARCH_DEPTH) + 1):
        if (current / LOCAL_DIR_NAME).is_dir():
            return current
        parent = current.parent
        if parent == current:      # ドライブのルートまで来た
            break
        current = parent
    return base


def local_root() -> Path | None:
    """いま使うローカル辞書のルート。無ければ ``None``。

    URL を読んでいるときは ``sites/`` の下を最長一致で探す。**URL 側は
    勝手に作らない** ので、辞書を作っていないサイトでは ``None`` になる
    （訪れたサイトの数だけ空ディレクトリが増えないように）。
    """
    if _reading_url:
        from .sites import site_root  # 循環 import を避けて遅延読み込み

        return site_root(_reading_url)
    return _folder_local_root()


def local_glossary_dir() -> Path | None:
    """いま使うローカル辞書。切り替えれば当然変わる。無ければ ``None``。"""
    root = local_root()
    return None if root is None else root / LOCAL_DIR_NAME / "glossary"


def ensure_dirs() -> None:
    GLOSSARY_DIR.mkdir(parents=True, exist_ok=True)
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    CATEGORIES_FILE.parent.mkdir(parents=True, exist_ok=True)


#: 保存先を移すときに持っていくもの。data/ に辞書・カテゴリ・URL 辞書・
#: 専用ウィンドウのプロファイル（お気に入りや設定の localStorage）が全部入る
DATA_SUBTREES = ("data", "content")

#: 中身を運ばないディレクトリ。ブラウザプロファイルのキャッシュは数百ファイルあり、
#: 消えても作り直されるだけ。**お気に入りや設定は Local Storage にあるので残す** ——
#: 名前に Cache を含むものだけを外すのはそのため（Local Storage は名前に含まない）
_CACHE_DIR_MARKERS = ("cache", "crashpad")


def _is_cache(rel: Path) -> bool:
    return any(
        any(marker in part.casefold() for marker in _CACHE_DIR_MARKERS)
        for part in rel.parts[:-1] + (rel.name,)
    )


#: 隣を探すときに見るディレクトリ数の上限。アプリを雑多なフォルダの下に置かれても
#: 走査で固まらないように
MAX_SIBLING_SCAN = 60


def count_entries(root: Path) -> int:
    """``<root>/data/glossary`` にある .md の数。壊れていても数えるだけ。"""
    base = root / "data" / "glossary"
    if not base.is_dir():
        return 0
    try:
        return sum(1 for _ in base.glob("*/*.md"))
    except OSError:
        return 0


def find_data_candidates(limit: int = 5) -> list[dict]:
    """**隣のフォルダに置き去りになっているデータ**を探す。

    新しい版を隣に展開して既定のまま起動すると、辞書は旧フォルダに残ったままで
    **消えたように見える**。これが更新でいちばん怖い事故なので、見つけて案内する。

    探すのはアプリと同じ階層だけ（深く潜らない）。いま使っている場所と、
    語が 1 つも無いフォルダは候補にしない。
    """
    if count_entries(DATA_ROOT) > 0:
        return []          # いま中身があるなら黙る
    here = {Path(DATA_ROOT).resolve(), Path(APP_DIR).resolve()}
    out: list[dict] = []
    try:
        siblings = sorted(APP_DIR.parent.iterdir())[:MAX_SIBLING_SCAN]
    except OSError:
        return []
    for path in siblings:
        if len(out) >= limit:
            break
        try:
            if not path.is_dir() or path.resolve() in here:
                continue
        except OSError:
            continue
        count = count_entries(path)
        if count:
            out.append({"path": str(path), "name": path.name, "entry_count": count})
    # 語数の多いものを先に
    out.sort(key=lambda c: -c["entry_count"])
    return out


def copy_data_root(src: Path, dst: Path) -> dict:
    """データ一式を新しい場所へ**複製する**。元は消さない。

    消さないのは、移した先で問題が出たときに戻れるようにするため。旧フォルダを
    片付けるのはユーザーの判断に任せ、場所を返して知らせる。

    専用ウィンドウが動いていると `data/window` のファイルは掴まれていて読めない。
    そこで止めずに、**読めなかったものを理由つきで返す**（黙って欠けたまま
    「移せました」と言わない）。キャッシュだけは数が多く、消えても作り直されるので
    件数だけ返す。
    """
    src = src.resolve()
    dst = dst.resolve()
    if src == dst:
        raise ValueError("移動元と移動先が同じです")
    if src in dst.parents:
        raise ValueError("移動先が移動元の中にあります（無限にコピーされます）")

    copied: list[str] = []
    skipped: list[dict] = []
    cache_skipped = 0
    for name in DATA_SUBTREES:
        base = src / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(src)
            if _is_cache(rel):
                if path.is_file():
                    cache_skipped += 1
                continue
            target = dst / rel
            try:
                if path.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                copied.append(rel.as_posix())
            except OSError as exc:
                skipped.append({"path": rel.as_posix(), "reason": str(exc)})
    return {"copied": copied, "skipped": skipped, "cache_skipped": cache_skipped}
