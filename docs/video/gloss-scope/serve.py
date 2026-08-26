"""収録用に、まっさらなデータルートで GlossPop を起動する。

動画は同じ plan.json から何度でも同じ絵が録れることが前提なので、
実際の辞書ではなく使い捨てのデータルートを使う。実辞書を汚さずに済み、
撮り直すたびに「0 語登録」の状態から始められる。

    python docs/video/gloss-scope/serve.py
"""

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOC = "ようこそ.md"

# **ドライブ直下に置く。** GlossPop は画面の上端に「既定のフォルダ」としてデータルートの
# フルパスを出すので、%TEMP% の下だと **公開する動画にユーザー名が最初から最後まで
# 映る** (実際に映った)。同じ理由で GhostMoviePlay の撮影用ダミーもドライブ直下にある。
ROOT = Path(os.environ.get("SystemDrive", "C:") + "/gmp-glosspop")


def main() -> int:
    shutil.rmtree(ROOT, ignore_errors=True)
    (ROOT / "content").mkdir(parents=True)
    shutil.copy(REPO / "content" / DOC, ROOT / "content" / DOC)

    env = dict(os.environ, GLOSSPOP_DATA_ROOT=str(ROOT))
    print(f"データルート: {ROOT}", flush=True)
    try:
        return subprocess.call(["uv", "run", "glosspop", "serve"], cwd=REPO, env=env)
    finally:
        # 使い捨てなので畳むときに消す。掴まれていても収録は終わっているので黙って諦める
        shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
