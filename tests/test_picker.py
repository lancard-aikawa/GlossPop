"""フォルダ選択ダイアログ。子プロセスとのやりとり（特に文字コード）を見る。"""

from __future__ import annotations

import pytest

from glosspop import picker

JAPANESE = r"C:\書籍\銀河鉄道の夜\1巻"


def test_japanese_path_survives_the_child_process(monkeypatch):
    """実際に子プロセスを起動して日本語パスを往復させる。"""
    monkeypatch.setenv(picker.STUB_ENV, JAPANESE)
    assert picker.pick_folder("") == JAPANESE


def test_japanese_path_survives_a_cp932_child(monkeypatch):
    """凍結した exe は stdout を CP932 で書く。そこで壊れていた。

    子のテキスト出力を CP932 に固定して、その状況を再現する。子が
    ``sys.stdout.buffer`` へ UTF-8 のバイト列を書いていれば影響を受けない。
    """
    monkeypatch.setenv(picker.STUB_ENV, JAPANESE)
    monkeypatch.setenv("PYTHONIOENCODING", "cp932")
    assert picker.pick_folder("") == JAPANESE


def test_cancel_returns_empty(monkeypatch):
    # 何も出力せずに正常終了 = キャンセル。
    # (空文字の環境変数は Windows では「未設定」になるので stub では表せない)
    monkeypatch.setattr(picker, "_child_command", lambda initial: ["cmd", "/c", "exit 0"])
    assert picker.pick_folder("") == ""


def test_child_failure_becomes_picker_error(monkeypatch):
    monkeypatch.setattr(picker, "_child_command", lambda initial: ["cmd", "/c", "exit 3"])
    with pytest.raises(picker.PickerError):
        picker.pick_folder("")


def test_missing_program_becomes_picker_error(monkeypatch):
    monkeypatch.setattr(picker, "_child_command", lambda initial: ["glosspop-does-not-exist"])
    with pytest.raises(picker.PickerError):
        picker.pick_folder("")
