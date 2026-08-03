"""更新の確認。

**このアプリが外へ通信する唯一の常時経路**なので、テストの主眼は 2 つ:

- 呼ばれていないのに通信しないこと（とくに lifespan と、切ってあるとき）
- 失敗しても本体に影響しないこと
"""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from glosspop import __version__, config, updates
from glosspop.app import app


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    updates.invalidate()
    monkeypatch.delenv("GLOSSPOP_UPDATE_CHECK", raising=False)
    yield
    updates.invalidate()


@pytest.fixture
def no_network(monkeypatch):
    """外へ出たら即座に落ちるようにする。"""

    def boom(*args, **kwargs):
        raise AssertionError("テスト中に外へ通信しようとしました")

    monkeypatch.setattr(httpx.AsyncClient, "get", boom)


def fake_release(tag: str):
    """GitHub の応答を差し替える。"""

    async def _get(self, url, **kwargs):
        return httpx.Response(
            200, json={"tag_name": tag}, request=httpx.Request("GET", url)
        )

    return _get


class TestVersionCompare:
    @pytest.mark.parametrize(
        ("latest", "current", "expected"),
        [
            ("v0.5.0", "0.4.0", True),
            ("0.5.0", "0.4.0", True),
            ("v0.4.1", "0.4.0", True),
            ("v0.4.0", "0.4.0", False),
            ("v0.3.9", "0.4.0", False),
            ("v0.4", "0.4.0", False),       # 桁数が違っても揃えて比べる
            ("v0.4.0.1", "0.4.0", True),
            ("v1.0.0", "0.9.9", True),
            ("v0.10.0", "0.9.0", True),     # 文字列比較だと逆になる組み合わせ
        ],
    )
    def test_compares_numerically(self, latest, current, expected):
        assert updates.is_newer(latest, current) is expected

    @pytest.mark.parametrize("latest", ["", "nightly", "リリース", "vX"])
    def test_unreadable_versions_are_not_newer(self, latest):
        """読めない文字列で「更新があります」と出すほうが害が大きい。"""
        assert updates.is_newer(latest, "0.4.0") is False


class TestEnabled:
    def test_on_by_default(self, isolated_dirs):
        assert updates.enabled() is True

    def test_the_settings_file_can_turn_it_off(self, isolated_dirs):
        config.save_settings({"update_check": False})
        assert updates.enabled() is False

    @pytest.mark.parametrize("raw", ["0", "false", "off", "no", ""])
    def test_the_env_var_can_turn_it_off(self, isolated_dirs, monkeypatch, raw):
        monkeypatch.setenv("GLOSSPOP_UPDATE_CHECK", raw)
        assert updates.enabled() is False

    def test_the_env_var_wins_over_the_settings_file(self, isolated_dirs, monkeypatch):
        config.save_settings({"update_check": False})
        monkeypatch.setenv("GLOSSPOP_UPDATE_CHECK", "1")
        assert updates.enabled() is True


class TestCheck:
    @pytest.mark.anyio
    async def test_disabled_does_not_touch_the_network(self, isolated_dirs, no_network):
        config.save_settings({"update_check": False})
        result = await updates.check()
        assert result["enabled"] is False and result["newer"] is False

    @pytest.mark.anyio
    async def test_reports_a_newer_release(self, isolated_dirs, monkeypatch):
        monkeypatch.setattr(httpx.AsyncClient, "get", fake_release("v99.0.0"))
        result = await updates.check()
        assert result["newer"] is True
        assert result["latest"] == "v99.0.0" and result["current"] == __version__

    @pytest.mark.anyio
    async def test_the_same_version_is_not_newer(self, isolated_dirs, monkeypatch):
        monkeypatch.setattr(httpx.AsyncClient, "get", fake_release(f"v{__version__}"))
        assert (await updates.check())["newer"] is False

    @pytest.mark.anyio
    async def test_a_recent_check_is_not_repeated(self, isolated_dirs, no_network):
        """再起動を繰り返しても GitHub を叩かない（時刻は設定ファイルに残す）。"""
        config.save_settings(
            {"update_last_checked": int(time.time()), "update_latest": "v99.0.0"}
        )
        result = await updates.check()          # no_network なので通信すれば落ちる
        assert result["latest"] == "v99.0.0" and result["newer"] is True

    @pytest.mark.anyio
    async def test_an_old_check_is_repeated(self, isolated_dirs, monkeypatch):
        config.save_settings(
            {"update_last_checked": int(time.time()) - 10 * 24 * 3600, "update_latest": "v0.0.1"}
        )
        monkeypatch.setattr(httpx.AsyncClient, "get", fake_release("v99.0.0"))
        assert (await updates.check())["latest"] == "v99.0.0"

    @pytest.mark.anyio
    async def test_a_failure_is_reported_but_not_raised(self, isolated_dirs, monkeypatch):
        async def fail(self, url, **kwargs):
            raise httpx.ConnectError("ネットにつながりません")

        monkeypatch.setattr(httpx.AsyncClient, "get", fail)
        result = await updates.check()
        assert result["newer"] is False and result["error"]

    @pytest.mark.anyio
    async def test_a_failure_is_not_remembered(self, isolated_dirs, monkeypatch):
        """失敗を覚えると、次に開いても再試行しなくなる。"""
        async def fail(self, url, **kwargs):
            raise httpx.ConnectError("だめ")

        monkeypatch.setattr(httpx.AsyncClient, "get", fail)
        await updates.check()
        assert not config.load_settings().get("update_last_checked")

    @pytest.mark.anyio
    async def test_the_result_is_cached_in_the_process(self, isolated_dirs, monkeypatch):
        calls = []

        async def counted(self, url, **kwargs):
            calls.append(url)
            return httpx.Response(200, json={"tag_name": "v99.0.0"},
                                  request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.AsyncClient, "get", counted)
        await updates.check()
        await updates.check()
        assert len(calls) == 1


class TestApi:
    def test_startup_does_not_check(self, isolated_dirs, no_network):
        """lifespan で叩かない。叩くと起動のたびに勝手に外へ出ていく。"""
        with TestClient(app) as client:
            assert client.get("/api/health").status_code == 200

    def test_the_endpoint_reports_the_state(self, isolated_dirs, monkeypatch):
        monkeypatch.setattr(httpx.AsyncClient, "get", fake_release("v99.0.0"))
        with TestClient(app) as client:
            body = client.get("/api/update").json()
        assert body["newer"] is True and body["url"].endswith("/releases/latest")

    def test_the_endpoint_can_be_turned_off(self, isolated_dirs, no_network):
        with TestClient(app) as client:
            assert client.put("/api/update", json={"enabled": False}).json()["enabled"] is False
            assert client.get("/api/update").json()["enabled"] is False
        assert config.load_settings()["update_check"] is False
