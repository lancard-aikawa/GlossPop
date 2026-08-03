"""新しい版を落として隣に展開する部分。

**外から実行ファイルを取ってくる経路**なので、テストの主眼は「変なものを
展開しないこと」。通信は conftest が塞いでいるので、zip を自分で作って渡す。
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from glosspop import config, installer


def make_zip(path: Path, files: dict[str, str], *, root: str = "GlossPop/") -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in files.items():
            zf.writestr(f"{root}{name}", body)
    return path


class TestPickAsset:
    def test_prefers_the_windows_zip(self):
        asset = installer.pick_asset([
            {"name": "notes.txt"},
            {"name": "GlossPop-0.5.0-win-x64.zip"},
            {"name": "source.zip"},
        ])
        assert asset["name"] == "GlossPop-0.5.0-win-x64.zip"

    def test_falls_back_to_any_zip(self):
        assert installer.pick_asset([{"name": "x.zip"}])["name"] == "x.zip"

    def test_no_zip_is_an_error(self):
        with pytest.raises(installer.InstallError):
            installer.pick_asset([{"name": "readme.txt"}])


class TestTargetDir:
    def test_sits_next_to_the_app(self):
        assert installer.target_dir("v0.5.0").parent == config.APP_DIR.parent
        assert installer.target_dir("v0.5.0").name == "GlossPop-0.5.0"

    def test_strips_characters_that_cannot_be_a_folder_name(self):
        """タグはリポジトリ側の任意文字列。そのままフォルダ名にしない。"""
        assert "/" not in installer.target_dir("v0.5/evil").name
        assert ".." not in installer.target_dir("../../etc").name


class TestExtract:
    def test_extracts_and_strips_the_wrapping_folder(self, tmp_path):
        """配布 zip は GlossPop/ を 1 枚かぶっている。剥がさないと exe が 1 段深くなる。"""
        src = make_zip(tmp_path / "r.zip", {"glosspop.exe": "x", "_internal/a.dll": "y"})
        dest = tmp_path / "out"
        assert installer.extract(src, dest) == 2
        assert (dest / "glosspop.exe").exists()
        assert (dest / "_internal" / "a.dll").exists()

    def test_keeps_the_layout_when_there_is_no_single_root(self, tmp_path):
        src = make_zip(tmp_path / "r.zip", {"a.txt": "1"}, root="")
        with zipfile.ZipFile(src, "a") as zf:
            zf.writestr("b/c.txt", "2")
        dest = tmp_path / "out"
        installer.extract(src, dest)
        assert (dest / "a.txt").exists() and (dest / "b" / "c.txt").exists()

    def test_refuses_a_folder_that_already_has_contents(self, tmp_path):
        src = make_zip(tmp_path / "r.zip", {"a.txt": "1"})
        dest = tmp_path / "out"
        dest.mkdir()
        (dest / "keep.txt").write_text("大事", encoding="utf-8")
        with pytest.raises(installer.InstallError):
            installer.extract(src, dest)
        assert (dest / "keep.txt").read_text(encoding="utf-8") == "大事"

    def test_rejects_a_broken_zip(self, tmp_path):
        bad = tmp_path / "r.zip"
        bad.write_bytes("これは zip ではない".encode("utf-8"))
        with pytest.raises(installer.InstallError):
            installer.extract(bad, tmp_path / "out")

    def test_rejects_an_empty_zip(self, tmp_path):
        src = tmp_path / "r.zip"
        with zipfile.ZipFile(src, "w"):
            pass
        with pytest.raises(installer.InstallError):
            installer.extract(src, tmp_path / "out")


class TestZipSlip:
    """展開先の外に書かせない。ライブラリ任せにしない。"""

    @pytest.mark.parametrize(
        "name",
        ["../escape.txt", "a/../../escape.txt", "/absolute.txt", "\\\\absolute.txt"],
    )
    def test_paths_that_leave_the_destination_are_refused(self, tmp_path, name):
        src = tmp_path / "r.zip"
        with zipfile.ZipFile(src, "w") as zf:
            zf.writestr(name, "悪意")
        dest = tmp_path / "out"
        with pytest.raises(installer.InstallError):
            installer.extract(src, dest)
        assert not (tmp_path / "escape.txt").exists()

    def test_symlinks_are_refused(self, tmp_path):
        src = tmp_path / "r.zip"
        with zipfile.ZipFile(src, "w") as zf:
            info = zipfile.ZipInfo("GlossPop/link")
            info.external_attr = (0xA1FF << 16)      # symlink
            zf.writestr(info, "C:/Windows")
        with pytest.raises(installer.InstallError):
            installer.extract(src, tmp_path / "out")


class TestDigest:
    def test_a_matching_digest_passes(self, tmp_path):
        f = tmp_path / "a.bin"
        f.write_bytes("中身".encode("utf-8"))
        digest = "sha256:" + hashlib.sha256("中身".encode("utf-8")).hexdigest()
        installer._check_digest(f, digest)          # 例外が出なければよい

    def test_a_wrong_digest_is_an_error(self, tmp_path):
        f = tmp_path / "a.bin"
        f.write_bytes("中身".encode("utf-8"))
        with pytest.raises(installer.InstallError):
            installer._check_digest(f, "sha256:" + "0" * 64)

    @pytest.mark.parametrize("declared", ["", "md5:abc", "なにか"])
    def test_an_unknown_format_is_skipped_not_failed(self, tmp_path, declared):
        """検証できないだけで、落とす理由にはしない。"""
        f = tmp_path / "a.bin"
        f.write_bytes(b"x")
        installer._check_digest(f, declared)


class TestInstallLatest:
    def test_refuses_when_already_up_to_date(self, monkeypatch):
        from glosspop import __version__

        monkeypatch.setattr(
            installer, "fetch_release", lambda: {"tag_name": f"v{__version__}", "assets": []}
        )
        with pytest.raises(installer.InstallError, match="最新"):
            installer.install_latest()

    def test_refuses_a_non_https_url(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "APP_DIR", tmp_path / "app")
        monkeypatch.setattr(
            installer, "fetch_release",
            lambda: {
                "tag_name": "v99.0.0",
                "assets": [{"name": "a-win.zip", "browser_download_url": "http://example.com/a.zip"}],
            },
        )
        with pytest.raises(installer.InstallError, match="https"):
            installer.install_latest()

    def test_a_failed_extract_leaves_nothing_behind(self, monkeypatch, tmp_path):
        """半端なフォルダを残すと、それを起動されかねない。"""
        monkeypatch.setattr(config, "APP_DIR", tmp_path / "app" / "GlossPop")
        (tmp_path / "app").mkdir()

        def fake_download(url, into):
            with zipfile.ZipFile(into, "w") as zf:
                zf.writestr("../escape.txt", "悪意")
            return into.stat().st_size

        monkeypatch.setattr(installer, "_download", fake_download)
        monkeypatch.setattr(
            installer, "fetch_release",
            lambda: {
                "tag_name": "v99.0.0",
                "assets": [{
                    "name": "a-win.zip",
                    "browser_download_url": "https://example.com/a.zip",
                }],
            },
        )
        with pytest.raises(installer.InstallError):
            installer.install_latest()
        assert not installer.target_dir("v99.0.0").exists()

    def test_happy_path_extracts_next_to_the_app(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "APP_DIR", tmp_path / "app" / "GlossPop")
        (tmp_path / "app").mkdir()

        def fake_download(url, into):
            make_zip(into, {"glosspop.exe": "new", "_internal/x.dll": "y"})
            return into.stat().st_size

        monkeypatch.setattr(installer, "_download", fake_download)
        monkeypatch.setattr(
            installer, "fetch_release",
            lambda: {
                "tag_name": "v99.0.0",
                "assets": [{
                    "name": "GlossPop-99.0.0-win-x64.zip",
                    "browser_download_url": "https://example.com/a.zip",
                }],
            },
        )
        result = installer.install_latest()
        dest = Path(result["dir"])
        assert dest == tmp_path / "app" / "GlossPop-99.0.0"
        assert (dest / "glosspop.exe").read_text(encoding="utf-8") == "new"
        # 自分自身は触らない
        assert result["files"] == 2 and result["version"] == "v99.0.0"
