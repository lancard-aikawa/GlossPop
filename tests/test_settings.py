"""データの保存先の設定。

既定ではデータがアプリの隣にあるので、更新のたびに手でコピーすることになる。
アプリの外へ移せるようにしたのがここ。**壊れた設定で起動できなくならないこと**と、
**移したつもりで移っていない状態を作らないこと**が要点。
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from glosspop import config


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """設定ファイルをテスト用の場所に向ける。

    本物 (%APPDATA%) を触らないよう、環境変数で逃がしてから config を読み直す。
    """
    path = tmp_path / "settings" / "settings.json"
    monkeypatch.setenv("GLOSSPOP_SETTINGS_FILE", str(path))
    monkeypatch.delenv("GLOSSPOP_DATA_ROOT", raising=False)
    importlib.reload(config)
    yield path
    monkeypatch.delenv("GLOSSPOP_SETTINGS_FILE", raising=False)
    importlib.reload(config)


def reload_config():
    importlib.reload(config)
    return config


class TestSettingsFile:
    def test_lives_outside_the_app_folder(self, settings_file):
        """アプリのフォルダを丸ごと入れ替えても残ること（それが目的）。"""
        assert config.APP_DIR not in config.SETTINGS_FILE.parents

    def test_missing_file_is_not_an_error(self, settings_file):
        assert not settings_file.exists()
        assert config.load_settings() == {}

    def test_broken_json_falls_back_to_defaults(self, settings_file):
        """壊れた設定で起動できなくならない。"""
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text("{ これは JSON ではない", encoding="utf-8")
        assert config.load_settings() == {}
        assert reload_config().DATA_ROOT == config.APP_DIR

    def test_a_non_mapping_is_ignored(self, settings_file):
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text("[1, 2, 3]", encoding="utf-8")
        assert config.load_settings() == {}

    def test_save_then_load_round_trips(self, settings_file):
        config.save_settings({"data_root": "C:/somewhere"})
        assert config.load_settings()["data_root"] == "C:/somewhere"
        assert json.loads(settings_file.read_text(encoding="utf-8"))


class TestDataRootPrecedence:
    def test_defaults_to_the_app_folder(self, settings_file):
        assert reload_config().DATA_ROOT == config.APP_DIR

    def test_the_settings_file_moves_it(self, settings_file, tmp_path):
        target = tmp_path / "elsewhere"
        target.mkdir()
        config.save_settings({"data_root": str(target)})
        mod = reload_config()
        assert mod.DATA_ROOT == target.resolve()
        # data/ も content/ も付いてくる（更新時のコピーが不要になるのが目的）
        assert mod.GLOSSARY_DIR == target.resolve() / "data" / "glossary"
        assert mod.CONTENT_DIR == target.resolve() / "content"
        assert mod.WINDOW_PROFILE_DIR == target.resolve() / "data" / "window"

    def test_the_env_var_wins_over_the_settings_file(self, settings_file, tmp_path, monkeypatch):
        """テストや一時的な切り替えが設定ファイルに引きずられないこと。"""
        saved = tmp_path / "from-settings"
        forced = tmp_path / "from-env"
        saved.mkdir()
        forced.mkdir()
        config.save_settings({"data_root": str(saved)})
        monkeypatch.setenv("GLOSSPOP_DATA_ROOT", str(forced))
        assert reload_config().DATA_ROOT == forced.resolve()

    def test_an_unusable_path_falls_back(self, settings_file):
        config.save_settings({"data_root": ""})
        assert reload_config().DATA_ROOT == config.APP_DIR


class TestCopyDataRoot:
    def _seed(self, root):
        (root / "data" / "glossary" / "テスト").mkdir(parents=True)
        (root / "data" / "glossary" / "テスト" / "冪等.md").write_text("本文", encoding="utf-8")
        (root / "data" / "categories.yaml").write_text("- name: テスト\n", encoding="utf-8")
        # お気に入りと設定はここ。取りこぼすと静かに消える
        (root / "data" / "window").mkdir(parents=True)
        (root / "data" / "window" / "Local State").write_text("{}", encoding="utf-8")
        (root / "content").mkdir(parents=True)
        (root / "content" / "ようこそ.md").write_text("# ようこそ", encoding="utf-8")

    def test_copies_data_and_content(self, tmp_path):
        src, dst = tmp_path / "old", tmp_path / "new"
        src.mkdir()
        self._seed(src)
        report = config.copy_data_root(src, dst)
        assert (dst / "data" / "glossary" / "テスト" / "冪等.md").read_text(encoding="utf-8") == "本文"
        assert (dst / "data" / "categories.yaml").exists()
        assert (dst / "content" / "ようこそ.md").exists()
        # 専用ウィンドウのプロファイルも持っていく（お気に入り・ネタバレ設定がここ）
        assert (dst / "data" / "window" / "Local State").exists()
        assert report["skipped"] == []
        assert len(report["copied"]) == 4

    def test_leaves_the_original_alone(self, tmp_path):
        """移した先で問題が出たときに戻れるようにする。"""
        src, dst = tmp_path / "old", tmp_path / "new"
        src.mkdir()
        self._seed(src)
        config.copy_data_root(src, dst)
        assert (src / "data" / "glossary" / "テスト" / "冪等.md").exists()

    def test_refuses_to_copy_into_itself(self, tmp_path):
        src = tmp_path / "old"
        src.mkdir()
        self._seed(src)
        with pytest.raises(ValueError):
            config.copy_data_root(src, src / "inside")

    def test_refuses_the_same_folder(self, tmp_path):
        src = tmp_path / "old"
        src.mkdir()
        with pytest.raises(ValueError):
            config.copy_data_root(src, src)

    def test_an_empty_source_is_not_an_error(self, tmp_path):
        src, dst = tmp_path / "old", tmp_path / "new"
        src.mkdir()
        assert config.copy_data_root(src, dst) == {
            "copied": [], "skipped": [], "cache_skipped": 0
        }


class TestCacheIsNotCarriedOver:
    """ブラウザのキャッシュは数百ファイルあり、消えても作り直される。

    ただし**お気に入りと設定 (localStorage) は運ぶ**。名前に Cache を含まないので
    残る、という線引きが効いているかを見る。
    """

    def _seed_profile(self, root):
        prof = root / "data" / "window"
        (prof / "Default" / "Local Storage" / "leveldb").mkdir(parents=True)
        (prof / "Default" / "Local Storage" / "leveldb" / "000003.log").write_text(
            "お気に入り", encoding="utf-8"
        )
        for name in ("Cache", "Code Cache", "GPUCache", "ShaderCache"):
            d = prof / "Default" / name
            d.mkdir(parents=True)
            (d / "data_0").write_bytes(b"x" * 10)
        (prof / "Crashpad").mkdir(parents=True)
        (prof / "Crashpad" / "settings.dat").write_bytes(b"x")

    def test_keeps_local_storage_and_drops_caches(self, tmp_path):
        src, dst = tmp_path / "old", tmp_path / "new"
        src.mkdir()
        self._seed_profile(src)
        report = config.copy_data_root(src, dst)

        kept = dst / "data" / "window" / "Default" / "Local Storage" / "leveldb" / "000003.log"
        assert kept.read_text(encoding="utf-8") == "お気に入り"
        assert not (dst / "data" / "window" / "Default" / "Cache").exists()
        assert not (dst / "data" / "window" / "Crashpad").exists()
        # 黙って落とさず件数は返す
        assert report["cache_skipped"] == 5
        assert report["skipped"] == []


class TestFindDataCandidates:
    """更新後に「辞書が消えた」ように見える状態の検出。

    新しい版を隣に展開して既定のまま起動すると、辞書は旧フォルダに残ったまま。
    これが更新でいちばん怖い事故なので、見つけて案内する。
    """

    def _install(self, root: Path, entries: int) -> Path:
        d = root / "data" / "glossary" / "テスト"
        d.mkdir(parents=True)
        for i in range(entries):
            (d / f"語{i}.md").write_text("本文", encoding="utf-8")
        return root

    def test_finds_a_sibling_with_data(self, tmp_path, monkeypatch):
        new = tmp_path / "GlossPop-0.5.0"
        new.mkdir()
        self._install(tmp_path / "GlossPop-0.4.0", 3)
        monkeypatch.setattr(config, "APP_DIR", new)
        monkeypatch.setattr(config, "DATA_ROOT", new)
        found = config.find_data_candidates()
        assert [c["name"] for c in found] == ["GlossPop-0.4.0"]
        assert found[0]["entry_count"] == 3

    def test_stays_quiet_when_we_already_have_data(self, tmp_path, monkeypatch):
        new = self._install(tmp_path / "GlossPop-0.5.0", 1)
        self._install(tmp_path / "GlossPop-0.4.0", 3)
        monkeypatch.setattr(config, "APP_DIR", new)
        monkeypatch.setattr(config, "DATA_ROOT", new)
        assert config.find_data_candidates() == []

    def test_does_not_offer_the_folder_we_are_using(self, tmp_path, monkeypatch):
        new = tmp_path / "GlossPop-0.5.0"
        new.mkdir()
        monkeypatch.setattr(config, "APP_DIR", new)
        monkeypatch.setattr(config, "DATA_ROOT", new)
        assert config.find_data_candidates() == []

    def test_orders_by_how_much_is_in_there(self, tmp_path, monkeypatch):
        new = tmp_path / "GlossPop-0.5.0"
        new.mkdir()
        self._install(tmp_path / "GlossPop-0.3.0", 2)
        self._install(tmp_path / "GlossPop-0.4.0", 7)
        monkeypatch.setattr(config, "APP_DIR", new)
        monkeypatch.setattr(config, "DATA_ROOT", new)
        assert [c["entry_count"] for c in config.find_data_candidates()] == [7, 2]

    def test_ignores_folders_without_a_dictionary(self, tmp_path, monkeypatch):
        new = tmp_path / "GlossPop-0.5.0"
        new.mkdir()
        (tmp_path / "無関係").mkdir()
        monkeypatch.setattr(config, "APP_DIR", new)
        monkeypatch.setattr(config, "DATA_ROOT", new)
        assert config.find_data_candidates() == []
