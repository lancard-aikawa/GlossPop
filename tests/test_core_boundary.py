"""`glosspop/core/` が「どのマシンか」を知らないことを見張る。

この層だけが GlossPopApp と共有される（→ `docs/design-notes.md`）。共有するものが
2 か所に分かれると、片方で直してもう片方で直し忘れて「同じ辞書なのに違う語がリンクに
なる」という、**画面を見ても分からない壊れ方**をする。

**人が気を付ける形にすると必ず越える。**「ついでに開いているフォルダを見たい」は
毎回出てくるので、機械に見張らせる。越えたくなったら、**呼ぶ側から引数で渡すこと**
（`timeline.py` が `Linker` と `Document` を引数で受けているのがその形）。
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

from glosspop import core

#: core から import してはいけないもの。どれも「実行しているマシンの事情」を持つ。
FORBIDDEN = {
    "config",  # 保存先・開いているフォルダ・設定ファイル
    "store",  # 辞書の置き場所
    "categories",  # カテゴリマスターの置き場所
    "ai",  # プロンプトの組み立て（config と store を掴む）
    "llm",  # 提供元・モデル・API キー
    "fetcher",  # 外への通信
    "sites",  # URL 辞書の置き場所
    "archive",  # 書庫の置き場所
    "merge",  # store を掴む
    "app",
    "cli",
    "picker",
    "appwindow",
    "watchdog",
    "installer",
    "updates",
}

CORE_DIR = Path(core.__file__).parent


def _core_modules() -> list[str]:
    return sorted(m.name for m in pkgutil.iter_modules([str(CORE_DIR)]))


def _imported_siblings(path: Path) -> set[str]:
    """そのファイルが `glosspop` パッケージ内から取り込んでいる名前を集める。

    見るのは相対 import だけ。core の中に絶対 import (`from glosspop import …`) を
    書く経路も塞ぎたいので、そちらは別のテストで弾く。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        if node.module:
            # from .models import X / from ..config import Y
            found.add(node.module.split(".")[0])
        else:
            # from . import a, b
            found.update(alias.name for alias in node.names)
    return found


def test_core_has_the_modules_we_expect():
    """うっかり増減したら気付けるように、顔ぶれを固定しておく。"""
    assert _core_modules() == [
        "archivefmt",
        "doctor",
        "documents",
        "entryfile",
        "htmlclean",
        "imagefmt",
        "linker",
        "models",
        "relations",
        "render",
        "timeline",
        "whenfmt",
    ]


@pytest.mark.parametrize("name", _core_modules())
def test_core_does_not_reach_outside(name: str):
    """core から `config` や `store` を掴まない。"""
    used = _imported_siblings(CORE_DIR / f"{name}.py")
    crossed = used & FORBIDDEN
    assert not crossed, (
        f"glosspop/core/{name}.py が {sorted(crossed)} を import している。"
        " core は『どのマシンか』を知らない層なので、必要な値は呼ぶ側から引数で渡すこと。"
    )


@pytest.mark.parametrize("name", _core_modules())
def test_core_does_not_use_absolute_package_imports(name: str):
    """`from glosspop import …` で迂回しない（相対 import だけを見張っているので）。"""
    tree = ast.parse((CORE_DIR / f"{name}.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("glosspop"):
            pytest.fail(f"glosspop/core/{name}.py が絶対 import を使っている: {node.module}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("glosspop"), (
                    f"glosspop/core/{name}.py が絶対 import を使っている: {alias.name}"
                )


def test_core_imports_without_touching_config():
    """core だけを import しても、設定ファイルや保存先の解決が走らない。

    `config` は import 時に保存先を解決するので、core がそれを引きずり込んでいると
    **サーバ側で使う側に「開いているフォルダ」の概念が漏れる**。
    """
    for name in _core_modules():
        module = importlib.import_module(f"glosspop.core.{name}")
        assert not hasattr(module, "config")
