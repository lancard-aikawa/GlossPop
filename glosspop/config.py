"""パス・実行設定。すべて環境変数で上書きできる。"""

from __future__ import annotations

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

#: パッケージの場所。凍結時は一時展開先 (_internal) を指す
PACKAGE_DIR = Path(__file__).resolve().parent

#: 書き込むデータ (辞書・content) の基準ディレクトリ。
#: 凍結時に PACKAGE_DIR を基準にすると、保存した辞書が一時展開先に書かれて
#: 終了時に消える。exe の隣を基準にする。
DATA_ROOT = Path(sys.executable).resolve().parent if FROZEN else PACKAGE_DIR.parent

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


def local_root() -> Path:
    """ローカル辞書を置く（置いてある）フォルダ。

    開いているフォルダから祖先方向へ ``.glosspop`` を探し、**いちばん近いもの**を
    使う。見つからなければ開いているフォルダ自身（そこに作られる）。

    巻ごとにフォルダを分けていても、作品フォルダに 1 つ置けば共有できる。
    逆に巻ごとに分けたいなら、その巻に ``.glosspop`` を作れば近い方が勝つ。
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


def local_glossary_dir() -> Path:
    """いま使うローカル辞書。フォルダを切り替えれば当然変わる。"""
    return local_root() / LOCAL_DIR_NAME / "glossary"


def ensure_dirs() -> None:
    GLOSSARY_DIR.mkdir(parents=True, exist_ok=True)
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    CATEGORIES_FILE.parent.mkdir(parents=True, exist_ok=True)
