"""専用ウィンドウでビューアを開く（ブラウザのアプリモード）。

``msedge.exe --app=<URL>`` で開くと、タブもアドレスバーもブックマークバーも無い
ウィンドウになる。**依存を増やさずにアプリらしい見た目にするため**にこれを使う
（pywebview 等を足すと、凍結 exe の spec と WebView2 ランタイムの面倒を抱える）。

``--user-data-dir`` で専用プロファイルを渡しているのは 2 つの理由から:

* ユーザーが普段使っているブラウザのセッションと混ざらない
* **起動した ``msedge.exe`` がそのプロファイルのブラウザ本体になるので、
  ウィンドウを閉じたらプロセスが終わる。** これを親から待てるので「窓を閉じたら
  サーバも止まる」が作れる。既定プロファイルで開くと、既に Edge が動いている
  場合 ``msedge.exe`` は即座に終了してしまい、窓の寿命を追えない

副作用として localStorage も専用プロファイル側になる。フォルダ履歴・お気に入り・
ネタバレ設定はそこに貯まる（普通のブラウザのタブで開いたときとは別勘定）。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path

from . import config

#: ``--app`` を解釈するブラウザ。Edge は Windows 11 に必ず入っている
_BROWSERS = (
    r"Microsoft\Edge\Application\msedge.exe",
    r"Google\Chrome\Application\chrome.exe",
    r"BraveSoftware\Brave-Browser\Application\brave.exe",
)

#: 上を探す基準ディレクトリ（64bit / 32bit / ユーザーインストール）
_PROGRAM_DIRS = ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")

#: PATH から探すときの名前
_COMMANDS = ("msedge", "chrome", "chromium", "brave")

#: 初期ウィンドウサイズ
WINDOW_SIZE = os.environ.get("GLOSSPOP_WINDOW_SIZE", "1280,900")

#: サーバが listen するまで待つ秒数
READY_TIMEOUT = float(os.environ.get("GLOSSPOP_WINDOW_TIMEOUT", "30"))


def find_browser() -> Path | None:
    """アプリモードで開けるブラウザ。見つからなければ ``None``。"""
    for relative in _BROWSERS:                  # Edge を優先する（ディレクトリより先に見る）
        for env in _PROGRAM_DIRS:
            base = os.environ.get(env)
            if not base:
                continue
            path = Path(base) / relative
            if path.is_file():
                return path
    for name in _COMMANDS:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def available() -> bool:
    return find_browser() is not None


def wait_until_ready(host: str, port: int, *, timeout: float = READY_TIMEOUT) -> bool:
    """サーバが接続を受けるまで待つ。決め打ちの sleep にしない。"""
    target = ("127.0.0.1" if host in ("0.0.0.0", "::", "") else host, port)
    deadline = _now() + timeout
    while _now() < deadline:
        try:
            with socket.create_connection(target, timeout=0.5):
                return True
        except OSError:
            _sleep(0.1)
    return False


def open_window(url: str) -> subprocess.Popen | None:
    """専用ウィンドウで開く。

    開けたらそのプロセスを返す（``wait()`` すると窓が閉じるまで待てる）。
    ブラウザが見つからない・起動できないときは既定のブラウザに投げて ``None``。
    """
    exe = find_browser()
    if exe is None:
        webbrowser.open(url)
        return None

    profile = config.WINDOW_PROFILE_DIR
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError:
        webbrowser.open(url)
        return None

    cmd = [
        str(exe),
        f"--app={url}",
        f"--user-data-dir={profile}",
        f"--window-size={WINDOW_SIZE}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    kwargs: dict = {}
    if sys.platform == "win32":
        # 親のコンソールを引き継がせない（ログに混ざる）
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.Popen(cmd, **kwargs)
    except OSError:
        webbrowser.open(url)
        return None


# テストから差し替えられるように名前を通しておく
def _now() -> float:
    import time

    return time.monotonic()


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
