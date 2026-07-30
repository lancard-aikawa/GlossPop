"""OS のフォルダ選択ダイアログを出す。

ブラウザの ``<input type=file>`` は選ばれたフォルダの絶対パスを渡してくれないので、
サーバ側（＝ユーザーと同じ PC の同じセッション）でダイアログを開く。

**必ず別プロセスで開く。** tkinter はメインスレッドでしか安全に動かせず、
FastAPI のワーカースレッドから呼ぶと固まったりプロセスごと落ちたりする。
凍結した exe には python インタプリタが無いので、その場合は自分自身を
隠しコマンド ``__pick-folder`` で再実行する。
"""

from __future__ import annotations

import os
import subprocess
import sys

from . import config

#: 子プロセスは選ばれたパスを **UTF-8 のバイト列** で返す。
#:
#: テキストとして書くと、凍結した exe ではコンソールのコードページ (日本語 Windows
#: なら CP932) で符号化される。親が UTF-8 として読むので日本語を含むパスが壊れる
#: （開発時は stdout が UTF-8 なので気付けない）。
_CHILD_CODE = (
    "import sys;"
    "from glosspop.picker import run_dialog;"
    "sys.stdout.buffer.write(run_dialog(sys.argv[1] if len(sys.argv) > 1 else '').encode('utf-8'))"
)

#: テスト用: これが設定されていればダイアログを開かず、その値を選んだことにする
STUB_ENV = "GLOSSPOP_PICKER_RESULT"

#: ダイアログを開いたまま放置されても、いつかは諦める
_TIMEOUT = 600


class PickerError(RuntimeError):
    pass


def run_dialog(initial: str = "") -> str:
    """**子プロセス側で**動く本体。選ばれたパス、キャンセルなら空文字を返す。"""
    stub = os.environ.get(STUB_ENV)
    if stub is not None:
        return stub  # テスト用: ダイアログを開かずに決め打ちの答えを返す

    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    # 他のウィンドウの裏に隠れると「固まった」ように見えるので必ず前面に出す
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(
            title="GlossPop で開くフォルダを選ぶ",
            initialdir=initial or None,
            mustexist=True,
        ) or ""
    finally:
        root.destroy()


def _child_command(initial: str) -> list[str]:
    if config.FROZEN:
        return [sys.executable, "__pick-folder", initial]
    return [sys.executable, "-c", _CHILD_CODE, initial]


def pick_folder(initial: str = "") -> str:
    """ダイアログを開き、選ばれたパスを返す。キャンセルなら空文字。"""
    kwargs = {}
    if sys.platform == "win32":
        # 子プロセスのコンソールが一瞬ちらつくのを防ぐ
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        # バイト列で受け取って自分で UTF-8 として読む (text=True だと
        # ロケール依存で復号され、日本語を含むパスが壊れる)
        proc = subprocess.run(
            _child_command(initial),
            capture_output=True,
            timeout=_TIMEOUT,
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        raise PickerError("フォルダ選択ダイアログが閉じられませんでした") from exc
    except OSError as exc:
        raise PickerError(f"フォルダ選択ダイアログを開けません: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()[-400:]
        raise PickerError(f"フォルダ選択ダイアログを開けません: {detail or proc.returncode}")
    return (proc.stdout or b"").decode("utf-8", errors="replace").strip()
