"""PyInstaller に渡すエントリスクリプト。

``glosspop/__main__.py`` は相対 import (``from .cli import main``) を使うので、
PyInstaller にトップレベルスクリプトとして渡すと import に失敗する。
ここでは絶対 import で叩く。**引数なしで起動したら ``app`` 扱い**にする
（exe をダブルクリックする人に「ブラウザで URL を開く」をさせない）。
CLI として使うぶんには従来どおり ``glosspop.exe list`` のように叩ける。

**このスクリプトは 2 本の exe で共有される**（``glosspop.exe`` = コンソール、
``glosspopw.exe`` = 窓なし）。窓なしのほうは標準入出力が無いことがあるので、
下の ``_ensure_streams()`` を先に通す。
"""

from __future__ import annotations

import multiprocessing
import os
import sys

from glosspop.cli import main


def _ensure_streams() -> None:
    """``sys.stdout`` / ``sys.stderr`` が無いときに捨て先を繋ぐ。

    **窓なしの exe をダブルクリックすると、この 2 つが ``None`` になる**
    （リダイレクトされていれば普通に使えるので、パイプ越しに試すと再現しない ——
    実際にそれで見落とした）。``print()`` は ``None`` を黙って読み飛ばすが、
    **uvicorn のログはそうではない**ので、そのままだとサーバが立ち上がらない。

    捨てるだけなので中身は要らない。**閉じない**（プロセスが終わるまで使う）。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    sink = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = sink
    if sys.stderr is None:
        sys.stderr = sink


if __name__ == "__main__":
    _ensure_streams()
    # 凍結した exe では子プロセスの spawn が exe の再実行になる。
    # これを呼ばないと、何かが multiprocessing を使った瞬間にサーバが
    # 無限に立ち上がる
    multiprocessing.freeze_support()
    raise SystemExit(main(sys.argv[1:] or ["app"]))
