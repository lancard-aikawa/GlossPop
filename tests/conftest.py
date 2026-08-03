from __future__ import annotations

import httpx
import pytest

from glosspop import categories, config, store


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
    """テストから外へ通信させない。

    更新の確認 (`updates.py`) と URL 取得 (`fetcher.py`) は本物のネットワークを
    使いうる。差し替え忘れると、テストが**手元では通って CI や機内で落ちる**か、
    もっと悪いと知らないうちに外部へ出ていく。

    塞ぐのは実ネットワークの transport だけ。``TestClient`` は ``ASGITransport``
    越しにアプリを叩くので影響しない。
    """

    def boom(*args, **kwargs):
        raise AssertionError(
            "テストが外へ通信しようとしました。httpx の呼び出しを差し替えてください"
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", boom, raising=False)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom, raising=False)


#: AI の選択に効く環境変数。手元にあるだけで結果が変わるので、テストでは外す
_AI_ENV = (
    "GLOSSPOP_AI_PROVIDER", "GLOSSPOP_AI_MODEL", "GLOSSPOP_AI_EFFORT",
    "GLOSSPOP_CLAUDE_ARGS", "GLOSSPOP_GEMINI_KEY", "GEMINI_API_KEY",
)


@pytest.fixture(autouse=True)
def neutral_ai_env(monkeypatch):
    """AI の選択を**手元の環境変数に左右させない。**

    優先順が「環境変数 > 設定ファイル > 既定」なので、開発機に
    ``GEMINI_API_KEY`` があるだけで「鍵は設定済み」になり、設定ファイル側を
    見るテストが**手元でだけ違う結果になる**（実際に踏んだ）。
    `claude` の有無をテストの前提にしないのと同じ話。
    """
    for name in _AI_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """各テストを使い捨ての辞書 / content ディレクトリで走らせる。"""
    glossary = tmp_path / "glossary"
    content = tmp_path / "content"
    glossary.mkdir()
    content.mkdir()
    monkeypatch.setattr(config, "GLOSSARY_DIR", glossary)
    monkeypatch.setattr(config, "CONTENT_DIR", content)
    monkeypatch.setattr(config, "CATEGORIES_FILE", tmp_path / "categories.yaml")
    monkeypatch.setattr(config, "SITES_DIR", tmp_path / "sites")
    # **DATA_ROOT も逃がす。** ここを実物のままにすると、保存先の移動をテストしたとき
    # 開発中のリポジトリの data/ を丸ごと複製しに行く（動作中のブラウザが掴んでいる
    # プロファイルまで読もうとして環境依存で落ちた）
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    # 本物の %APPDATA% に設定を書かない
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(store, "_ready", False)
    # 「フォルダを開く」「URL を読む」の状態はプロセス内に残るので持ち越さない
    config.set_content_dir(None)
    config.set_reading_url(None)
    store.invalidate()
    categories.invalidate()
    yield tmp_path
    config.set_content_dir(None)
    config.set_reading_url(None)
    store.invalidate()
    categories.invalidate()


@pytest.fixture
def add_entry():
    from glosspop.models import EntryDraft

    def _add(term: str, **kwargs):
        kwargs.setdefault("category", "テスト")
        return store.save(EntryDraft(term=term, **kwargs))

    return _add
