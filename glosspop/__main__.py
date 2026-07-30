"""``python -m glosspop`` = ``glosspop serve``。"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    argv = sys.argv[1:] or ["serve"]
    raise SystemExit(main(argv))
