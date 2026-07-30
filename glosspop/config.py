"""パス・実行設定。すべて環境変数で上書きできる。"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


#: プロジェクトルート (このファイルの 2 つ上)
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

#: 辞書 Markdown の置き場所 (1 用語 = 1 ファイル)
GLOSSARY_DIR = _env_path("GLOSSPOP_GLOSSARY_DIR", PROJECT_ROOT / "data" / "glossary")

#: ビューアがブラウズできる .md / .txt の置き場所
CONTENT_DIR = _env_path("GLOSSPOP_CONTENT_DIR", PROJECT_ROOT / "content")

#: 静的ファイル
STATIC_DIR = PACKAGE_DIR / "static"

#: claude CLI の場所 (見つからなければ AI 下書き機能だけが無効になる)
CLAUDE_BIN = os.environ.get("GLOSSPOP_CLAUDE_BIN") or shutil.which("claude")

#: claude -p に渡す追加引数
CLAUDE_EXTRA_ARGS = shlex.split(os.environ.get("GLOSSPOP_CLAUDE_ARGS", "--model sonnet"))

#: claude CLI のタイムアウト秒
CLAUDE_TIMEOUT = int(os.environ.get("GLOSSPOP_CLAUDE_TIMEOUT", "180"))


def ensure_dirs() -> None:
    GLOSSARY_DIR.mkdir(parents=True, exist_ok=True)
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
