"""専用ウィンドウ（ブラウザのアプリモード）での起動。"""

from __future__ import annotations

import contextlib
import socket

import pytest

from glosspop import appwindow, config
from glosspop.cli import build_parser


class TestFindBrowser:
    def test_prefers_edge_over_chrome(self, tmp_path, monkeypatch):
        edge = tmp_path / "pf" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        chrome = tmp_path / "pf" / "Google" / "Chrome" / "Application" / "chrome.exe"
        for exe in (edge, chrome):
            exe.parent.mkdir(parents=True)
            exe.write_text("", encoding="utf-8")
        monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        assert appwindow.find_browser() == edge

    def test_finds_a_user_installed_browser(self, tmp_path, monkeypatch):
        chrome = tmp_path / "local" / "Google" / "Chrome" / "Application" / "chrome.exe"
        chrome.parent.mkdir(parents=True)
        chrome.write_text("", encoding="utf-8")
        monkeypatch.delenv("ProgramFiles", raising=False)
        monkeypatch.delenv("ProgramFiles(x86)", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
        assert appwindow.find_browser() == chrome

    def test_returns_none_when_nothing_is_installed(self, monkeypatch):
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            monkeypatch.delenv(env, raising=False)
        monkeypatch.setattr(appwindow.shutil, "which", lambda _name: None)
        assert appwindow.find_browser() is None


class TestWaitUntilReady:
    def test_returns_true_once_the_port_accepts(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            assert appwindow.wait_until_ready("127.0.0.1", port, timeout=2) is True
        finally:
            srv.close()

    def test_gives_up_when_nothing_listens(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.close()          # 誰も listen していないポート
        assert appwindow.wait_until_ready("127.0.0.1", port, timeout=0.4) is False

    def test_waits_for_a_late_server(self, monkeypatch):
        """繋がるまで諦めずに試し直すこと。

        **本物のソケットとタイマーで書かない。** 以前はスレッドで遅れて listen
        させていたが、繋ぐ側と listen する側の競走になっていて時々落ちた
        （手元で 20 回に 1 回、CI ではリリースを止めた）。試行の回数だけを見る。
        """
        attempts = []

        def fake_connect(target, timeout=None):
            attempts.append(target)
            if len(attempts) < 3:
                raise OSError("まだ listen していない")
            return contextlib.nullcontext()

        monkeypatch.setattr(appwindow.socket, "create_connection", fake_connect)
        monkeypatch.setattr(appwindow, "_sleep", lambda _s: None)
        assert appwindow.wait_until_ready("127.0.0.1", 8765, timeout=3) is True
        assert len(attempts) == 3

    def test_any_address_is_probed_on_loopback(self, monkeypatch):
        """``--host 0.0.0.0`` に接続しに行かない（Windows では繋がらない）。"""
        seen = []

        def fake_connect(target, timeout=None):
            seen.append(target)
            raise OSError

        monkeypatch.setattr(appwindow.socket, "create_connection", fake_connect)
        appwindow.wait_until_ready("0.0.0.0", 8765, timeout=0.2)
        assert seen and all(host == "127.0.0.1" for host, _ in seen)


class TestOpenWindow:
    def test_falls_back_to_the_default_browser(self, monkeypatch):
        opened = []
        monkeypatch.setattr(appwindow, "find_browser", lambda: None)
        monkeypatch.setattr(appwindow.webbrowser, "open", lambda url: opened.append(url))
        assert appwindow.open_window("http://127.0.0.1:8765/") is None
        assert opened == ["http://127.0.0.1:8765/"]

    def test_launches_in_app_mode_with_its_own_profile(self, tmp_path, monkeypatch):
        cmds = []
        monkeypatch.setattr(appwindow, "find_browser", lambda: tmp_path / "msedge.exe")
        monkeypatch.setattr(config, "WINDOW_PROFILE_DIR", tmp_path / "profile")
        monkeypatch.setattr(appwindow.subprocess, "Popen", lambda cmd, **kw: cmds.append(cmd))

        appwindow.open_window("http://127.0.0.1:8765/")
        cmd = cmds[0]
        # --app が無いと普通のタブになる。プロファイルを分けないと普段の
        # ブラウザのセッションと混ざる
        assert "--app=http://127.0.0.1:8765/" in cmd
        assert f"--user-data-dir={tmp_path / 'profile'}" in cmd
        assert (tmp_path / "profile").is_dir()

    def test_falls_back_when_the_browser_cannot_be_started(self, tmp_path, monkeypatch):
        opened = []
        monkeypatch.setattr(appwindow, "find_browser", lambda: tmp_path / "msedge.exe")
        monkeypatch.setattr(config, "WINDOW_PROFILE_DIR", tmp_path / "profile")
        monkeypatch.setattr(appwindow.webbrowser, "open", lambda url: opened.append(url))

        def boom(_cmd, **_kw):
            raise OSError("起動できない")

        monkeypatch.setattr(appwindow.subprocess, "Popen", boom)
        assert appwindow.open_window("http://x/") is None
        assert opened == ["http://x/"]


class TestCliWiring:
    @pytest.mark.parametrize(
        ("argv", "want_open"),
        [(["serve"], False), (["app"], True)],
    )
    def test_only_app_opens_a_window(self, argv, want_open):
        args = build_parser().parse_args(argv)
        assert args.open is want_open

    def test_app_takes_the_same_options_as_serve(self):
        args = build_parser().parse_args(["app", "--port", "9000", "--host", "0.0.0.0"])
        assert (args.host, args.port, args.open) == ("0.0.0.0", 9000, True)

    def test_frozen_exe_opens_a_window_by_default(self):
        """引数なしの exe は `app`。ダブルクリックで URL を開かせない。"""
        entry = (config.PACKAGE_DIR.parent / "packaging" / "entry.py").read_text(encoding="utf-8")
        assert 'sys.argv[1:] or ["app"]' in entry
