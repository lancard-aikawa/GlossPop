"""PyInstaller に渡すエントリスクリプト。

``glosspop/__main__.py`` は相対 import (``from .cli import main``) を使うので、
PyInstaller にトップレベルスクリプトとして渡すと import に失敗する。
ここでは絶対 import で叩く。引数なしで起動したら ``serve`` 扱いにするのは
``python -m glosspop`` と同じ。
"""

from __future__ import annotations

import multiprocessing
import sys

from glosspop.cli import main

if __name__ == "__main__":
    # 凍結した exe では子プロセスの spawn が exe の再実行になる。
    # これを呼ばないと、何かが multiprocessing を使った瞬間にサーバが
    # 無限に立ち上がる
    multiprocessing.freeze_support()
    raise SystemExit(main(sys.argv[1:] or ["serve"]))
