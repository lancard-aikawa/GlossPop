"""収録用に、まっさらなデータルートで GlossPop を起動する。

動画は同じ plan.json から何度でも同じ絵が録れることが前提なので、
実際の辞書ではなく使い捨てのデータルートを使う。実辞書を汚さずに済み、
撮り直すたびに「0 語登録」の状態から始められる。

    python docs/video/gloss-scope/serve.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOC = "ようこそ.md"


def main() -> int:
    root = Path(tempfile.gettempdir()) / "glosspop-video"
    shutil.rmtree(root, ignore_errors=True)
    (root / "content").mkdir(parents=True)
    shutil.copy(REPO / "content" / DOC, root / "content" / DOC)

    env = dict(os.environ, GLOSSPOP_DATA_ROOT=str(root))
    print(f"データルート: {root}", flush=True)
    return subprocess.call(["uv", "run", "glosspop", "serve"], cwd=REPO, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
