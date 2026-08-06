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
    # tkinter はフォルダ選択ダイアログ (picker.py) が使うので落とさない
    excludes=["pytest", "IPython"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

# **実行ファイルは 2 本。** Windows の実行ファイルは console / GUI のどちらかの
# サブシステムで作るしかなく、1 本でどちらも満たすことはできない。`python.exe` /
# `pythonw.exe`、`java.exe` / `javaw.exe` と同じ形にしてある。
#
#   glosspop.exe   … console=True。CLI (`glosspop.exe list` など) 用
#   glosspopw.exe  … console=False。ダブルクリックで開く用（窓が付いてこない）
#
# **大文字小文字だけで分けないこと**（`GlossPop.exe` と `glosspop.exe`）。Windows の
# ファイル名は大文字小文字を区別しないので**同じ名前**になり、zip の展開や更新の
# 入れ替えで片方が黙って消える。だから `w` を足す —— 上の 2 例がそうしているのと同じ。
#
# 以前は 1 本 (console=True) にして、窓が開いた時点で `FreeConsole` で離脱していた。
# **子プロセスの窓が露出する**という副作用があり（親にコンソールが無いので claude が
# 自分の窓を作った。しかもそれを閉じると下書きが失敗する）、変則をやめてこの形にした。
# アイコンは 2 つとも別（→ `packaging/icons/`）。**同じ絵にしないこと** ——
# exe が 2 つ並ぶので、同じ見た目だとどちらを押すのか分からない。
# `.ico` は `make-icons.py` が svg から作って git に入れてある（ビルドのたびには
# 作らない。ブラウザが要るので、CI とビルドはそれ抜きで通るようにしておく）。
_ICONS = str(Path(SPECPATH) / "icons")  # noqa: F821


def _exe(name: str, console: bool, icon: str):
    return EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=console,
        icon=str(Path(_ICONS) / icon),
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )


exe_cli = _exe("glosspop", True, "glosspop-cli.ico")
exe_app = _exe("glosspopw", False, "glosspop-app.ico")

coll = COLLECT(  # noqa: F821
    exe_cli,
    exe_app,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GlossPop",
)
