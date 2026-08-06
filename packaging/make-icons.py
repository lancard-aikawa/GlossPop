"""``packaging/icons/*.svg`` から exe 用の ``.ico`` を作る。

    uv run python packaging/make-icons.py

**出来上がった ``.ico`` は git に入れる。** ビルドのたびに作らないのは、
ブラウザ（playwright）が要るため —— CI もビルドも、それ抜きで通るようにしておく。
図案を直したときだけ手で走らせて、結果を一緒にコミットすること。

**ラスタライズは Chrome にやらせる。** Pillow も cairosvg も依存に入っていないので、
既に開発依存にある playwright で描く。新しい依存を増やさずに済ませるための選択で、
凝ったことをしているわけではない（SVG を開いて、その大きさで撮るだけ）。

``.ico`` は「ヘッダ + 各サイズの目録 + 中身」という素直な入れ物で、中身は PNG を
そのまま入れられる（Vista 以降）。だから自前で組める。
"""

from __future__ import annotations

import pathlib
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
ICONS = HERE / "icons"

#: 入れる大きさ。**16 を落とさないこと** —— エクスプローラの詳細表示と
#: タスクバーの小さい表示がこれを使う。大きいものだけだと縮小が汚くなる
SIZES = (16, 24, 32, 48, 64, 128, 256)

#: どの svg から、どの ico を作るか
TARGETS = {
    "app.svg": "glosspop-app.ico",     # glosspopw.exe（ダブルクリック用）
    "cli.svg": "glosspop-cli.ico",     # glosspop.exe（コマンドライン用）
}


def render(page, svg: str, size: int) -> bytes:
    """SVG を ``size`` × ``size`` の PNG にする（背景は透明のまま）。"""
    page.set_viewport_size({"width": size, "height": size})
    page.set_content(
        "<style>html,body{margin:0;padding:0;background:transparent}"
        f"svg{{display:block;width:{size}px;height:{size}px}}</style>{svg}"
    )
    # **透明を残す。** 付けないと白地で塗られ、角丸の外側が白い四角になる
    return page.screenshot(omit_background=True)


def build_ico(pngs: list[tuple[int, bytes]]) -> bytes:
    """PNG の並びを ICO 1 つにまとめる。

    目録の 1 件は 16 バイト。**256 は幅・高さを 0 で書く**（1 バイトに入らない）。
    """
    header = struct.pack("<HHH", 0, 1, len(pngs))     # reserved, type=icon, count
    offset = len(header) + 16 * len(pngs)
    entries, blobs = [], []
    for size, data in pngs:
        entries.append(struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,                # width
            size if size < 256 else 0,                # height
            0,                                        # 色数（PNG なので 0）
            0,                                        # reserved
            1,                                        # color planes
            32,                                       # bits per pixel
            len(data),
            offset,
        ))
        blobs.append(data)
        offset += len(data)
    return header + b"".join(entries) + b"".join(blobs)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright が要ります: uv sync --all-groups", file=sys.stderr)
        return 1

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception as exc:                       # noqa: BLE001
            print(f"Chrome を起動できません: {exc}", file=sys.stderr)
            return 1
        page = browser.new_context().new_page()
        for src, out in TARGETS.items():
            svg = (ICONS / src).read_text(encoding="utf-8")
            pngs = [(size, render(page, svg, size)) for size in SIZES]
            path = ICONS / out
            path.write_bytes(build_ico(pngs))
            print(f"{src} -> {path.name}  ({path.stat().st_size:,} バイト / {len(SIZES)} サイズ)")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
