"""どの AI にどう頼むかの選択。**外へは一切出さない**（conftest が塞いでいる）。"""

from __future__ import annotations

import subprocess

import httpx
import pytest

from glosspop import config, llm


class TestResolve:
    """優先順は 環境変数 > 設定ファイル > 既定（config と同じ規則）。"""

    def test_defaults_to_claude(self):
        current = llm.resolve()
        assert current["provider"] == "claude"
        assert current["model"] == "" and current["effort"] == ""

    def test_settings_file_is_used(self):
        config.save_settings({"ai_provider": "gemini", "ai_effort": "high"})
        current = llm.resolve()
        assert current["provider"] == "gemini"
        assert current["effort"] == "high" and current["effort_source"] == "settings"

    def test_env_wins_over_the_settings_file(self, monkeypatch):
        config.save_settings({"ai_provider": "gemini", "ai_effort": "high"})
        monkeypatch.setenv("GLOSSPOP_AI_PROVIDER", "claude")
        monkeypatch.setenv("GLOSSPOP_AI_EFFORT", "low")
        current = llm.resolve()
        assert current["provider"] == "claude" and current["provider_source"] == "env"
        assert current["effort"] == "low" and current["effort_source"] == "env"

    def test_unknown_values_fall_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("GLOSSPOP_AI_PROVIDER", "でたらめ")
        monkeypatch.setenv("GLOSSPOP_AI_EFFORT", "ものすごく")
        current = llm.resolve()
        assert current["provider"] == "claude" and current["effort"] == ""

    def test_each_provider_remembers_its_own_model(self):
        """claude と gemini を行き来しても、モデル名が相手に付いて回らない。"""
        config.save_settings({
            "ai_provider": "claude",
            "ai_model_claude": "opus",
            "ai_model_gemini": "gemini-3.5-flash",
        })
        assert llm.resolve()["model"] == "opus"
        config.save_settings({**config.load_settings(), "ai_provider": "gemini"})
        assert llm.resolve()["model"] == "gemini-3.5-flash"


class TestAvailability:
    def test_claude_needs_the_cli(self, monkeypatch):
        monkeypatch.setattr(config, "CLAUDE_BIN", "")
        assert llm.available("claude") is False
        assert "claude CLI" in llm.unavailable_reason("claude")

    def test_gemini_needs_a_key(self, monkeypatch):
        assert llm.available("gemini") is False
        monkeypatch.setenv("GEMINI_API_KEY", "k-123")
        assert llm.available("gemini") is True

    def test_describe_never_leaks_the_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "秘密の鍵")
        info = llm.describe()
        assert info["gemini_key_set"] is True
        assert info["gemini_key_source"] == "env"
        assert "秘密の鍵" not in repr(info)


class TestClaudeCommand:
    """モデルと思考の深さがコマンドに乗るか。**二重指定しない**ことも見る。"""

    def _capture(self, monkeypatch) -> list[str]:
        seen: list[str] = []

        def fake_run(cmd, **kwargs):
            seen.extend(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout='{"result": "ok"}', stderr="")

        monkeypatch.setattr(config, "CLAUDE_BIN", "claude")
        monkeypatch.setattr(subprocess, "run", fake_run)
        return seen

    def test_passes_the_chosen_model_and_effort(self, monkeypatch):
        seen = self._capture(monkeypatch)
        config.save_settings({"ai_model_claude": "opus", "ai_effort": "high"})
        assert llm.generate("お願い", timeout=30) == "ok"
        assert seen[seen.index("--model") + 1] == "opus"
        assert seen[seen.index("--effort") + 1] == "high"

    def test_uses_the_long_standing_default_when_nothing_is_chosen(self, monkeypatch):
        # 設定を足したせいで、黙って別のモデルに変わることのないように
        seen = self._capture(monkeypatch)
        llm.generate("お願い", timeout=30)
        assert seen[seen.index("--model") + 1] == llm.DEFAULT_CLAUDE_MODEL
        assert "--effort" not in seen        # 深さは指定しなければ言わない

    def test_does_not_duplicate_what_the_user_set_by_hand(self, monkeypatch):
        seen = self._capture(monkeypatch)
        monkeypatch.setattr(config, "CLAUDE_EXTRA_ARGS", ["--model", "sonnet"])
        config.save_settings({"ai_model_claude": "opus"})
        llm.generate("お願い", timeout=30)
        assert seen.count("--model") == 1      # 手で書いたぶんを勝手に上書きしない
        assert seen[seen.index("--model") + 1] == "sonnet"


class TestGemini:
    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k-123")
        config.save_settings({"ai_provider": "gemini"})

    def _stub(self, monkeypatch, handler):
        """httpx を差し替える。**本物のネットワークには触らせない。**"""
        transport = httpx.MockTransport(handler)
        real = httpx.Client

        def fake_client(*args, **kwargs):
            kwargs["transport"] = transport
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", fake_client)

    def test_returns_the_text_of_the_first_candidate(self, monkeypatch):
        def handler(request):
            body = {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}
            return httpx.Response(200, json=body)

        self._stub(monkeypatch, handler)
        assert llm.generate("お願い", timeout=30) == '{"ok": true}'

    def test_sends_the_key_and_the_chosen_model(self, monkeypatch):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["key"] = request.headers.get("x-goog-api-key")
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

        self._stub(monkeypatch, handler)
        config.save_settings({**config.load_settings(), "ai_model_gemini": "gemini-3.5-flash"})
        llm.generate("お願い", timeout=30)
        assert "models/gemini-3.5-flash:generateContent" in seen["url"]
        assert seen["key"] == "k-123"

    def test_falls_back_when_the_model_has_no_thinking_level(self, monkeypatch):
        """3 系は thinkingLevel、2.5 系は thinkingBudget。**取り違えると 400。**

        モデル名で見分けると新しい世代のたびに直すことになるので、API が
        「その項目は無い」と言ってきたときだけ言い直す（実測した挙動）。
        """
        bodies = []

        def handler(request):
            import json as _json
            body = _json.loads(request.content)
            bodies.append(body["generationConfig"].get("thinkingConfig"))
            if "thinkingLevel" in (bodies[-1] or {}):
                return httpx.Response(400, json={
                    "error": {"message": "Thinking level is not supported for this model."}
                })
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

        self._stub(monkeypatch, handler)
        config.save_settings({**config.load_settings(), "ai_effort": "high"})
        assert llm.generate("お願い", timeout=30) == "{}"
        assert bodies == [{"thinkingLevel": "high"}, {"thinkingBudget": 16384}]

    def test_sends_no_thinking_config_when_left_at_the_default(self, monkeypatch):
        seen = {}

        def handler(request):
            import json as _json
            seen["config"] = _json.loads(request.content)["generationConfig"]
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

        self._stub(monkeypatch, handler)
        llm.generate("お願い", timeout=30)
        assert "thinkingConfig" not in seen["config"]

    def test_a_rejected_key_says_so(self, monkeypatch):
        self._stub(monkeypatch, lambda r: httpx.Response(
            403, json={"error": {"message": "API key not valid"}}
        ))
        with pytest.raises(llm.LLMError, match="キーが拒否"):
            llm.generate("お願い", timeout=30)

    def test_a_quota_error_says_so(self, monkeypatch):
        self._stub(monkeypatch, lambda r: httpx.Response(
            429, json={"error": {"message": "You exceeded your current quota"}}
        ))
        with pytest.raises(llm.LLMError, match="利用上限"):
            llm.generate("お願い", timeout=30)

    def test_an_empty_answer_is_not_passed_through(self, monkeypatch):
        # 思考だけで本文が空、は実際に起きる。空文字を下流に流さない
        self._stub(monkeypatch, lambda r: httpx.Response(200, json={
            "candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]
        }))
        with pytest.raises(llm.LLMError, match="MAX_TOKENS"):
            llm.generate("お願い", timeout=30)

    def test_model_list_drops_what_cannot_generate_text(self, monkeypatch):
        self._stub(monkeypatch, lambda r: httpx.Response(200, json={"models": [
            {"name": "models/gemini-3.5-flash", "displayName": "Gemini 3.5 Flash",
             "supportedGenerationMethods": ["generateContent"], "thinking": True},
            {"name": "models/gemini-3-pro-image", "displayName": "Nano Banana Pro",
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004", "displayName": "Embedding",
             "supportedGenerationMethods": ["embedContent"]},
        ]}))
        ids = [m["id"] for m in llm.list_gemini_models()]
        assert ids == ["gemini-3.5-flash"]

    def test_without_a_key_nothing_is_sent(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY")
        # 鍵が無ければ通信そのものをしない（conftest が塞いでいるので、
        # ここで本物の httpx が動けば AssertionError になる）
        with pytest.raises(llm.LLMError, match="API キー"):
            llm.generate("お願い", timeout=30)
