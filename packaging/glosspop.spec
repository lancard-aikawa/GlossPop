# -*- mode: python ; coding: utf-8 -*-
"""onedir ビルド定義。``uv run pyinstaller packaging/glosspop.spec`` で使う。

出力は ``dist/GlossPop/`` (exe + ``_internal/``)。辞書と content は exe の隣に
置く前提なので、ここには含めず ``build.ps1`` が別途コピーする。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH は PyInstaller が注入する

hiddenimports = [
    # cmd_serve は app を遅延 import する。取りこぼしを避けて全部入れる
    *collect_submodules("glosspop"),
    # uvicorn はプロトコル実装 / ローダを文字列で動的 import するので静的解析で拾えない
    *collect_submodules("uvicorn"),
    # render.py が使う plugin 群。念のためまとめて
    *collect_submodules("mdit_py_plugins"),
    # anyio のバックエンドも実行時に名前で解決される
    "anyio._backends._asyncio",
]

datas = [
    # app.py が STATIC_DIR からディスク読みするので実ファイルとして同梱する
    (str(ROOT / "glosspop" / "static"), "glosspop/static"),
    *collect_data_files("markdown_it"),
    *collect_data_files("linkify_it"),
]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "IPython"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="glosspop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # ローカルサーバなのでログを見せる
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GlossPop",
)
