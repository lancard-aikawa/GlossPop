"""CLI (`glosspop add` ほか)。スキルが叩く経路なので、入出力の文字コードも見る。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from glosspop import config, store


def _add(**data) -> int:
    """`cmd_add` を JSON 入力で呼ぶ（stdin を経由しない）。"""
    from glosspop.cli import cmd_add

    args = argparse.Namespace(json=json.dumps(data, ensure_ascii=False), update=False)
    return cmd_add(args)


def test_local_scope_does_not_collide_with_a_global_entry(capsys, add_entry):
    """同名でもスコープが違えば別エントリ。

    衝突判定をグローバル固定で引いていたため、``scope: local`` を渡しても
    「全体の辞書に同名がある」と誤検出して登録できなかった。
    """
    add_entry("冪等", category="プログラミング")
    capsys.readouterr()

    assert _add(term="冪等", category="プログラミング", scope="local") == 0
    refs = {e.ref for e in store.load_all()}
    assert refs == {"プログラミング/冪等", ".local/プログラミング/冪等"}


def test_same_category_and_scope_still_collides(capsys, add_entry):
    add_entry("冪等", category="プログラミング")
    capsys.readouterr()

    assert _add(term="冪等", category="プログラミング") == 1
    assert json.loads(capsys.readouterr().out)["status"] == "exists"


def test_json_from_stdin_is_read_as_utf8(tmp_path):
    """``echo '{...}' | glosspop add --json -`` で日本語が壊れないこと。

    stdin をロケール (日本語 Windows なら CP932) で復号すると、UTF-8 で流し込んだ
    見出し語がサロゲートに化けたまま保存される。スキルが使う経路なので
    サブプロセスで実際に流して確かめる。
    """
    env = {
        **os.environ,
        "GLOSSPOP_GLOSSARY_DIR": str(tmp_path / "glossary"),
        "GLOSSPOP_CATEGORIES_FILE": str(tmp_path / "categories.yaml"),
        "GLOSSPOP_CONTENT_DIR": str(tmp_path / "content"),
        "PYTHONIOENCODING": "",
    }
    payload = json.dumps(
        {"term": "冪等", "category": "プログラミング", "summary": "何回やっても同じ"},
        ensure_ascii=False,
    ).encode("utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "glosspop", "add", "--json", "-"],
        input=payload,
        capture_output=True,
        env=env,
        cwd=str(config.PACKAGE_DIR.parent),
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")

    out = json.loads(proc.stdout.decode("utf-8"))
    assert out["term"] == "冪等"
    assert out["ref"] == "プログラミング/冪等"
    assert (tmp_path / "glossary" / "プログラミング" / "冪等.md").exists()
