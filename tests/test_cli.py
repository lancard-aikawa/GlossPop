"""CLI (`glosspop add` ほか)。スキルが叩く経路なので、入出力の文字コードも見る。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from glosspop import categories, config, store


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


def _run(argv: list[str]) -> int:
    """パーサを通して実行する（``--folder`` の反映も含めて見る）。"""
    from glosspop.cli import main

    return main(argv)


def test_folder_option_targets_that_folders_local_dictionary(tmp_path, capsys):
    """``--folder`` を渡すと、そのフォルダの ``.glosspop`` が保存先になる。

    CLI には「開いているフォルダ」が無いので、指定が無いと既定の content フォルダの
    辞書に書いてしまう。ビューアと同じ経路（``config.set_content_dir()`` →
    ``store.glossary_dir(LOCAL_SCOPE)``）を通すこと。
    """
    novel = tmp_path / "銀河鉄道の夜"
    novel.mkdir()

    assert _run(["add", "--term", "ザネリ", "--category", "登場人物",
                 "--scope", "local", "--folder", str(novel)]) == 0

    out = json.loads(capsys.readouterr().out)
    assert out["ref"] == ".local/登場人物/ザネリ"
    assert (novel / ".glosspop" / "glossary" / "登場人物" / "ザネリ.md").exists()
    # カテゴリマスターはグローバルのもの。フォルダ固有のカテゴリで汚さない
    assert "登場人物" not in [c.name for c in categories.load()]


def test_missing_folder_is_rejected_before_touching_the_dictionary(tmp_path, capsys):
    assert _run(["list", "--folder", str(tmp_path / "ない")]) == 2
    assert "フォルダがありません" in capsys.readouterr().err


def test_local_dictionary_location_is_announced(tmp_path, capsys):
    """どこに書いたかを黙らない。

    ローカル辞書は祖先方向に探すので、``--folder`` に渡した場所とは限らない。
    ビューアが画面に出しているのと同じ理由で CLI も出す。
    """
    work = tmp_path / "作品" / "1巻"
    work.mkdir(parents=True)
    (tmp_path / "作品" / ".glosspop").mkdir()      # 親に辞書がある

    assert _run(["add", "--term", "ザネリ", "--category", "登場人物",
                 "--scope", "local", "--folder", str(work)]) == 0
    err = capsys.readouterr().err
    assert str(tmp_path / "作品" / ".glosspop" / "glossary") in err


def test_move_between_scopes(tmp_path, capsys, add_entry):
    """``move --to-scope`` で全体 ↔ フォルダの辞書を移せる。"""
    folder = tmp_path / "小説"
    folder.mkdir()
    add_entry("ザネリ", category="登場人物")
    capsys.readouterr()

    assert _run(["move", "ザネリ", "--to-scope", "local", "--folder", str(folder)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["from"] == "登場人物/ザネリ"
    assert out["ref"] == ".local/登場人物/ザネリ"
    assert (folder / ".glosspop" / "glossary" / "登場人物" / "ザネリ.md").exists()
    assert not (tmp_path / "glossary" / "登場人物" / "ザネリ.md").exists()


def test_category_rename_targets_the_chosen_dictionary(tmp_path, capsys):
    """`--folder` を付けるとローカルのカテゴリも一覧に出る。

    改名と削除がグローバル決め打ちだと、**同名のグローバル側を触る**ことになる。
    """
    from glosspop import categories

    folder = tmp_path / "小説"
    folder.mkdir()
    categories.ensure("登場人物")                       # 全体には空で作っておく
    assert _run(["add", "--term", "ザネリ", "--category", "登場人物",
                 "--scope", "local", "--folder", str(folder)]) == 0
    capsys.readouterr()

    assert _run(["categories", "--rename", "登場人物", "人物",
                 "--scope", "local", "--folder", str(folder)]) == 0
    assert [e.ref for e in store.load_all()] == [".local/人物/ザネリ"]
    assert [c.name for c in categories.load()] == ["登場人物"]   # 全体は無傷


def test_move_needs_a_destination(capsys, add_entry):
    add_entry("ザネリ", category="登場人物")
    capsys.readouterr()
    assert _run(["move", "ザネリ"]) == 2
    assert "--to" in capsys.readouterr().err


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
