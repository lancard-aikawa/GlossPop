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


def hide_own_console() -> bool:
    """自分で立てたコンソールから離れる。離れたら真。

    exe は ``console=True`` で作ってある（``glosspop.exe list`` のような CLI を
    そのまま使えるようにするため）ので、**エクスプローラからダブルクリックすると
    必ずコンソール窓が付いてくる**。アプリの窓と 2 つ並ぶうえ、そちらを閉じると
    **サーバだけ死んでアプリの窓が残る**（＝以後どこを押しても接続エラー）。

    **共有しているコンソールは絶対に隠さないこと。** ``glosspop.exe list`` を
    PowerShell から叩いたときのコンソールは**呼んだ人のもの**で、隠すと
    その端末ごと消える。見分けは ``GetConsoleProcessList`` の数 —— 自分ひとりなら
    ダブルクリックで立った自分専用のもの、2 つ以上なら呼び出し元と共有している。

    離れるのは**アプリの窓が開けたあと**だけにすること（→ ``cli._open_window_later``）。
    先に離れると、窓が開けなかったときに何も残らない。止める手段は生存確認
    （→ ``watchdog.py``）が引き受けるので、離れても終われなくならない。

    **``ShowWindow`` で窓を隠す形にしないこと。** Windows 11 の既定の
    ホスト（Windows Terminal / ConPTY）では ``GetConsoleWindow()`` が返すのは
    **見えないダミーの窓**で、隠しても端末の窓はそのまま残る（実際に踏んだ。
    conhost の環境でだけ効くので、手元の設定によっては通ってしまう）。
    ``FreeConsole`` でこちらが客をやめれば、端末は客がいなくなったので閉じる。

    **離れる前に標準入出力を捨て先へ繋ぎ直す。** 先に ``FreeConsole`` すると
    ハンドルが無効になり、以後 uvicorn がログを書いた瞬間に落ちる。付け替えるのは
    **fd のほう**（1 / 2 / 0）—— ログの出力先は既に ``sys.stderr`` を掴んでいるので、
    Python 側のオブジェクトを差し替えても間に合わない。
    """
    if not console_is_ours():
        return False
    try:
        import ctypes

        # 先に捨て先へ。開いた fd は閉じない（プロセスが終わるまで要る）
        devnull = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            try:
                os.dup2(devnull, fd)
            except OSError:
                pass
        return bool(ctypes.windll.kernel32.FreeConsole())
    except Exception:                          # noqa: BLE001
        return False                           # 離れられなくても動作には支障がない


def console_is_ours() -> bool:
    """いま繋がっているコンソールが**自分専用**か（＝隠してよいか）。

    判定と実行を分けてあるのは、**テストで踏むと実行した人のコンソールが消える**
    から。ここは副作用が無いので確かめられる。
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        if not kernel32.GetConsoleWindow():
            return False                      # コンソールが無い（窓なしで起動された）
        buf = (ctypes.c_uint * 2)()
        # 返るのは「このコンソールに繋がっているプロセスの数」。1 = 自分だけ
        return kernel32.GetConsoleProcessList(buf, 2) == 1
    except Exception:                          # noqa: BLE001
        return False


# テストから差し替えられるように名前を通しておく
def _now() -> float:
    import time

    return time.monotonic()


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
