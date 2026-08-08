"""**どの AI に、どのモデルで、どれだけ考えさせて頼むか**を 1 か所で決める。

プロンプトの中身は ``ai.py`` の仕事で、ここは「文字列を渡して文字列を受け取る」
ところだけを持つ。提供元を増やしたいときに ``ai.py`` を触らずに済ませるため。

持っているのは 2 つ:

- **claude** … Claude Code CLI (``claude -p``) をサブプロセスで叩く。API キーが
  要らない（Claude Code の認証をそのまま使う）ぶん、いちばん手間が無い
- **gemini** … Gemini API を HTTP で叩く。API キーが要る代わりに CLI の導入が要らない

**精度の高い/低いはこちらで決めない。** 使う人がモデルと思考の深さを選べるように
してあり、既定から外れた選択も止めない（安いモデルで数をこなしたい、という使い方が
ある）。選べないのは**壊れる組み合わせだけ**。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from . import config

#: 思考の深さ。Claude CLI の ``--effort`` に合わせてある（値の名前もそのまま）
EFFORTS: dict[str, str] = {
    "": "モデルの既定",
    "low": "浅い（速い・粗い）",
    "medium": "ふつう",
    "high": "深い",
    "xhigh": "とても深い",
    "max": "最大（遅い）",
}

#: Gemini 3 系の ``thinkingLevel``。**2.5 系は受け付けない**（別項目を使う）
_GEMINI_LEVELS = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}

#: Gemini 2.5 系の ``thinkingBudget``（トークン数。-1 はモデルにおまかせ）
_GEMINI_BUDGETS = {
    "low": 0,
    "medium": 4096,
    "high": 16384,
    "xhigh": 24576,
    "max": -1,
}

#: 「思考の深さは指定できません」と API が言ってきたときの目印。
#: これを見て 1 度だけ別の項目で言い直す（モデルごとに項目名が違うため）
_LEVEL_UNSUPPORTED = "thinking level is not supported"

#: 何も選ばなかったときに使うモデル。以前から使っている値をそのまま既定にする
#: （設定を足したせいで、黙って別のモデルに変わることのないように）
DEFAULT_CLAUDE_MODEL = "sonnet"

#: Claude CLI に渡せるモデルの別名。**日付つきの ID を焼き込まない** ——
#: 別名なら CLI 側が現行版に解決するので、版が上がっても直さずに済む
CLAUDE_MODELS = [
    {"id": "", "label": f"既定（{DEFAULT_CLAUDE_MODEL}）"},
    {"id": "haiku", "label": "Haiku（速い・安い）"},
    {"id": "sonnet", "label": "Sonnet（つり合いがよい）"},
    {"id": "opus", "label": "Opus（高精度・遅い）"},
]

#: Gemini のモデル一覧は**API から取る**（焼き込むと必ず古くなる。実際、手元の
#: キーで引いたら知らない世代が並んでいた）。生成に使えないものだけ名前で外す
_GEMINI_SKIP = (
    "tts", "image", "banana", "lyria", "robotics", "embedding",
    "computer-use", "deep-research", "aqa", "veo", "imagen",
)

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"

#: 待てば直る見込みのある応答。**恒久的な失敗（400 / 401 / 403 / 404）は入れない**
_RETRY_STATUS = frozenset({500, 502, 503, 504})

#: 提供元。UI はここを引いて選択肢を出す
PROVIDERS: dict[str, dict] = {
    "claude": {
        "label": "Claude Code CLI",
        "needs_key": False,
        "hint": "claude コマンドの認証をそのまま使います。API キーは要りません。",
    },
    "gemini": {
        "label": "Gemini API",
        "needs_key": True,
        "hint": "Google AI Studio の API キーが要ります。CLI の導入は不要です。",
    },
}

DEFAULT_PROVIDER = "claude"


class LLMError(RuntimeError):
    """AI への依頼が失敗した。**利用者に見せる文言をそのまま入れる。**"""


class TransientError(LLMError):
    """**待てば直る**種類の失敗（高負荷・レート制限）。ここだけ再試行する。

    恒久的な失敗（鍵違い・引数違い・モデル名の誤り）は何度やっても同じなので、
    再試行すると利用者を待たせるだけになる。**分類を緩めないこと。**
    """


# --------------------------------------------------------------------------- #
# 持ち時間と再試行
# --------------------------------------------------------------------------- #

#: 1 件あたりの所要秒の見積もり。**提供元で桁が違う** —— 同じ 19 語・20 本要求で
#: Claude 258.6 秒 / Gemini 72.4 秒（実測）。同じ見積もりを当てると、速いほうで
#: 「駄目だった」と分かるのが遅すぎる
#: 読みは 1 件あたり十数トークンしか書かせないので、関係や抽出よりずっと軽い
SECONDS_PER_ITEM: dict[str, dict[str, int]] = {
    "claude": {"relation": 22, "extract": 10, "reading": 3},
    "gemini": {"relation": 8, "extract": 4, "reading": 1},
}
TIMEOUT_BASE = 60

#: 何回まで引き直すか（1 回目を含む）と、その間隔。
#: 260 秒かけた作業が高負荷の一言で消えるのは割に合わないが、**何度も粘らない**
#: —— 上限は持ち時間の側で頭打ちにする
RETRY_ATTEMPTS = 3
RETRY_WAITS = (3, 12)

#: これより短い時間しか残っていなければ引き直さない（すぐ落ちるだけなので）
MIN_ATTEMPT_SECONDS = 20


def estimate_timeout(kind: str, count: int, provider: str | None = None) -> int:
    """出す件数から持ち時間を見積もる。**上限と、明示された指定は超えない。**

    所要時間は**出力トークン数（思考を含む）にほぼ比例する**。入力の量や起動時間
    ではないので、そこを削っても効かない（実測: 本文 12,000 字の入力は 4.8 秒、
    起動は 5 秒）。``GLOSSPOP_CLAUDE_TIMEOUT`` を大きくしている指定は下回らず、
    ``CLAUDE_TIMEOUT_MAX`` より上には伸ばさない。
    """
    provider = provider or resolve()["provider"]
    each = SECONDS_PER_ITEM.get(provider, SECONDS_PER_ITEM[DEFAULT_PROVIDER])
    want = TIMEOUT_BASE + each.get(kind, each["relation"]) * max(count, 1)
    return max(config.CLAUDE_TIMEOUT, min(want, config.CLAUDE_TIMEOUT_MAX))


# --------------------------------------------------------------------------- #
# 設定の解決
#
# 優先順は 環境変数 > 設定ファイル > 既定（`config` と同じ規則）。
# **保存先の設定と違って、こちらは次の呼び出しから効く** —— `store` のキャッシュや
# 開いているフォルダのような、途中で変わると食い違う状態を持たないため。
# --------------------------------------------------------------------------- #

_ENV = {
    "provider": "GLOSSPOP_AI_PROVIDER",
    "model": "GLOSSPOP_AI_MODEL",
    "effort": "GLOSSPOP_AI_EFFORT",
}
#: ``ai_model`` は UI からは書かない（提供元によらず効かせたい人が設定ファイルに
#: 手で書いたとき用の逃げ道。書かれていれば下の提供元ごとの値より優先される）
_SETTING = {"provider": "ai_provider", "model": "ai_model", "effort": "ai_effort"}

#: モデルは提供元ごとに別々に覚える。claude と gemini で行き来しても、
#: 前に選んだモデル名が相手側に付いて回らないようにするため
MODEL_SETTINGS = {"claude": "ai_model_claude", "gemini": "ai_model_gemini"}


def _pick(field: str, saved: dict) -> tuple[str, str]:
    """(値, どこから来たか) を返す。UI に「環境変数で固定されている」と出すため。"""
    env = (os.environ.get(_ENV[field]) or "").strip()
    if env:
        return env, "env"
    value = str(saved.get(_SETTING[field]) or "").strip()
    return (value, "settings") if value else ("", "default")


def resolve() -> dict:
    """いま使う提供元・モデル・思考の深さ。"""
    saved = config.load_settings()
    provider, provider_source = _pick("provider", saved)
    if provider not in PROVIDERS:
        provider, provider_source = DEFAULT_PROVIDER, "default"

    model, model_source = _pick("model", saved)
    if not model:
        model = str(saved.get(MODEL_SETTINGS[provider]) or "").strip()
        model_source = "settings" if model else "default"

    effort, effort_source = _pick("effort", saved)
    if effort not in EFFORTS:
        effort, effort_source = "", "default"

    return {
        "provider": provider,
        "provider_source": provider_source,
        "model": model,
        "model_source": model_source,
        "effort": effort,
        "effort_source": effort_source,
    }


def gemini_key() -> str:
    """Gemini の API キー。**環境変数が優先。**

    設定ファイルにも置けるようにしてあるのは、exe で使う人に環境変数を触らせない
    ため。``GEMINI_API_KEY`` は Google の道具が使う標準的な名前なので、それも見る。
    """
    for name in ("GLOSSPOP_GEMINI_KEY", "GEMINI_API_KEY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return str(config.load_settings().get("gemini_api_key") or "").strip()


def gemini_key_source() -> str:
    for name in ("GLOSSPOP_GEMINI_KEY", "GEMINI_API_KEY"):
        if (os.environ.get(name) or "").strip():
            return "env"
    return "settings" if gemini_key() else "none"


def available(provider: str | None = None) -> bool:
    """その提供元がいま使えるか（CLI があるか / キーがあるか）。"""
    provider = provider or resolve()["provider"]
    if provider == "gemini":
        return bool(gemini_key())
    return bool(config.CLAUDE_BIN)


def unavailable_reason(provider: str) -> str:
    if provider == "gemini":
        return "Gemini の API キーが設定されていません。⚙ の「AI」タブで登録してください。"
    return "claude CLI が見つかりません。PATH に追加するか GLOSSPOP_CLAUDE_BIN を設定してください。"


def describe() -> dict:
    """UI に出す、いまの状態一式。**キーそのものは返さない。**"""
    current = resolve()
    return {
        **current,
        "providers": [
            {"id": key, **spec, "available": available(key)}
            for key, spec in PROVIDERS.items()
        ],
        "efforts": [{"id": key, "label": label} for key, label in EFFORTS.items()],
        "claude_models": CLAUDE_MODELS,
        "claude_bin": config.CLAUDE_BIN or "",
        "gemini_key_set": bool(gemini_key()),
        "gemini_key_source": gemini_key_source(),
        "available": available(current["provider"]),
        "reason": "" if available(current["provider"]) else unavailable_reason(current["provider"]),
    }


# --------------------------------------------------------------------------- #
# 実行
# --------------------------------------------------------------------------- #

def generate(prompt: str, *, timeout: int, system: str = "") -> str:
    """プロンプトを投げて本文を受け取る。提供元の違いはここで吸収する。

    **一時的な失敗は引き直す。** 高負荷 (503) やレート制限 (429) は待てば直るのに、
    数分かけた下書きがその一言で丸ごと消えるのは割に合わない（関係の下書きを
    Gemini で回して実際に踏んだ）。``timeout`` は**全体の持ち時間**として扱い、
    残り時間を次の試行に配る —— 1 回ごとに与えると、引き直すたびに待ち時間が
    倍々になる。
    """
    current = resolve()
    provider = current["provider"]
    if not available(provider):
        raise LLMError(unavailable_reason(provider))
    run = _run_gemini if provider == "gemini" else _run_claude

    deadline = time.monotonic() + timeout
    last: TransientError | None = None
    for attempt in range(RETRY_ATTEMPTS):
        # **1 回目は言われた持ち時間をそのまま使う。** deadline から引き直すと、
        # ここまでの経過ぶんが `int()` で落ちて 500 秒の指定が 499 秒になる。
        # 手元では丸め込まれて表面化せず、**負荷の高い CI でだけ**タイムアウトの
        # 文言が 1 秒ずれてテストが落ちた（実際にリリースを止めた）。
        # 残りを配り直すのは引き直すときだけでよい
        left = timeout if attempt == 0 else int(deadline - time.monotonic())
        if attempt and left < MIN_ATTEMPT_SECONDS:
            break
        try:
            return run(prompt, current["model"], current["effort"],
                       max(left, MIN_ATTEMPT_SECONDS), system)
        except TransientError as exc:
            last = exc
            wait = RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)]
            if time.monotonic() + wait + MIN_ATTEMPT_SECONDS >= deadline:
                break
            time.sleep(wait)
    raise LLMError(f"{last}（{RETRY_ATTEMPTS} 回まで試しました）")


# ------------------------------------------------------------------ Claude CLI

#: CLI の出力に出たら「待てば直る」と見なす言葉。**増やしすぎないこと** ——
#: 恒久的な失敗を引き直すと、待たせたうえで同じ結果になる
_TRANSIENT_MARKS = (
    "overloaded", "rate limit", "rate_limit", "too many requests",
    "429", "503", "529", "temporarily", "try again",
)


def _looks_transient(detail: str) -> bool:
    """CLI の文言から一時的な失敗かを見分ける。**分からなければ引き直さない。**"""
    low = (detail or "").casefold()
    return any(mark in low for mark in _TRANSIENT_MARKS)


#: 下書き生成はテキスト変換なので、ツールは全部落とす。
#: 許可を出さないとサブプロセスが承認待ちで固まる (実際に踏んだ)。
DISALLOWED_TOOLS = ",".join([
    "Agent", "Bash", "Edit", "Glob", "Grep", "NotebookEdit", "Read",
    "Skill", "Task", "TodoWrite", "WebFetch", "WebSearch", "Write",
])


def _neutral_cwd() -> Path:
    """プロジェクト外の作業ディレクトリ。

    プロジェクト内で実行すると CLAUDE.md や ``gloss-add`` スキルを拾って
    「重複を確認するため CLI を実行したい」と言い出し、承認が取れずに詰まる。
    """
    path = Path(tempfile.gettempdir()) / "glosspop-ai"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_claude(prompt: str, model: str, effort: str, timeout: int, system: str) -> str:
    cmd = [
        config.CLAUDE_BIN,
        "-p", prompt,
        "--output-format", "json",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--disallowed-tools", DISALLOWED_TOOLS,
    ]
    if system:
        cmd += ["--append-system-prompt", system]
    # 追加引数で自分で指定している人のぶんを二重に足さない
    extra = config.CLAUDE_EXTRA_ARGS
    if "--model" not in extra:
        cmd += ["--model", model or DEFAULT_CLAUDE_MODEL]
    if effort and "--effort" not in extra:
        cmd += ["--effort", effort]
    cmd += extra

    # **子のコンソール窓を出さない。** claude はコンソールアプリなので、親に
    # コンソールが無い状態（＝専用ウィンドウで開いたとき。`appwindow.
    # hide_own_console`）で起こすと**下書きのたびに黒い窓が開く**。しかも
    # それを閉じると claude ごと落ちて下書きが失敗する（実際に報告された）。
    # 親のコンソールを間借りしていたぶんは、これまで見えていなかっただけ。
    # `picker.py` と `appwindow.py` も同じ理由で同じ指定をしている
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(_neutral_cwd()),
            **kwargs,
        )
    except subprocess.TimeoutExpired as exc:
        raise LLMError(f"claude CLI が {timeout} 秒でタイムアウトしました") from exc
    except OSError as exc:
        raise LLMError(f"claude CLI を起動できません: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-800:]
        message = f"claude CLI が異常終了しました (exit {proc.returncode}): {detail}"
        if _looks_transient(detail):
            raise TransientError(message)
        raise LLMError(message)

    stdout = (proc.stdout or "").strip()
    if not stdout:
        raise LLMError("claude CLI が何も出力しませんでした")

    # --output-format json の外側エンベロープを剥がす
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    if isinstance(envelope, dict):
        if envelope.get("is_error"):
            raise LLMError(f"claude がエラーを返しました: {envelope.get('result') or envelope}")
        result = envelope.get("result")
        if isinstance(result, str):
            return result
    return stdout


# ------------------------------------------------------------------ Gemini API

def _thinking_config(effort: str, *, use_level: bool) -> dict | None:
    if not effort:
        return None
    if use_level:
        return {"thinkingLevel": _GEMINI_LEVELS[effort]}
    return {"thinkingBudget": _GEMINI_BUDGETS[effort]}


def _gemini_body(prompt: str, effort: str, system: str, *, use_level: bool) -> dict:
    generation: dict = {
        # JSON だけを返させる。フェンスや前置きが混ざると読み取りに失敗する
        "responseMimeType": "application/json",
    }
    thinking = _thinking_config(effort, use_level=use_level)
    if thinking:
        generation["thinkingConfig"] = thinking
    body: dict = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation,
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    return body


def _gemini_text(data: dict) -> str:
    """応答から本文を取り出す。**思考だけで本文が空の応答を素通ししない。**"""
    for candidate in data.get("candidates") or []:
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text") or "" for p in parts).strip()
        if text:
            return text
        reason = candidate.get("finishReason") or ""
        if reason and reason != "STOP":
            raise LLMError(f"Gemini が本文を返しませんでした (finishReason={reason})")
    blocked = (data.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        raise LLMError(f"Gemini が入力を拒否しました (blockReason={blocked})")
    raise LLMError("Gemini が何も返しませんでした")


def _gemini_error(res: httpx.Response) -> str:
    try:
        return str((res.json().get("error") or {}).get("message") or res.text)[:400]
    except ValueError:
        return res.text[:400]


def _run_gemini(prompt: str, model: str, effort: str, timeout: int, system: str) -> str:
    model = model or "gemini-flash-latest"
    url = f"{GEMINI_ENDPOINT}/models/{model}:generateContent"
    headers = {"x-goog-api-key": gemini_key(), "Content-Type": "application/json"}

    # **思考の深さの指定はモデルの世代で項目が違う。** 3 系は thinkingLevel、
    # 2.5 系は thinkingBudget しか受けず、取り違えると 400 で落ちる。
    # モデル名で見分けると新しい世代が出るたびに直すことになるので、
    # API が「その項目は無い」と言ってきたときだけ 1 度言い直す
    for use_level in (True, False):
        body = _gemini_body(prompt, effort, system, use_level=use_level)
        try:
            with httpx.Client(timeout=timeout) as client:
                res = client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise LLMError(f"Gemini API が {timeout} 秒でタイムアウトしました") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Gemini API に繋がりません: {exc}") from exc

        if res.status_code == 200:
            return _gemini_text(res.json())

        detail = _gemini_error(res)
        if use_level and effort and _LEVEL_UNSUPPORTED in detail.casefold():
            continue                      # 古い世代。thinkingBudget で言い直す
        if res.status_code in (401, 403):
            raise LLMError(f"Gemini の API キーが拒否されました: {detail}")
        if res.status_code == 429:
            # レート制限なら待てば直る。1 日ぶんの枠を使い切った場合は直らないが、
            # 区別が付かないので**引き直す側に倒す**（待ち時間は持ち時間で頭打ち）
            raise TransientError(f"Gemini の利用上限に達しました: {detail}")
        if res.status_code in _RETRY_STATUS:
            raise TransientError(f"Gemini API が一時的に応じられませんでした "
                                 f"({res.status_code}): {detail}")
        raise LLMError(f"Gemini API がエラーを返しました ({res.status_code}): {detail}")
    raise LLMError("Gemini API がエラーを返しました")


def list_gemini_models(timeout: int = 30) -> list[dict]:
    """使えるモデルを API から引く。**一覧を焼き込まない**ための口。

    生成に使えないもの（画像・音声・埋め込みなど）は名前で外す。外し漏れても
    UI は手入力を許すので、選べなくなることはない。
    """
    key = gemini_key()
    if not key:
        raise LLMError(unavailable_reason("gemini"))
    try:
        with httpx.Client(timeout=timeout) as client:
            res = client.get(
                f"{GEMINI_ENDPOINT}/models",
                headers={"x-goog-api-key": key},
                params={"pageSize": 200},
            )
    except httpx.HTTPError as exc:
        raise LLMError(f"Gemini API に繋がりません: {exc}") from exc
    if res.status_code != 200:
        raise LLMError(f"モデル一覧を取れませんでした ({res.status_code}): {_gemini_error(res)}")

    out: list[dict] = []
    for item in res.json().get("models") or []:
        name = str(item.get("name") or "").removeprefix("models/")
        if not name or "generateContent" not in (item.get("supportedGenerationMethods") or []):
            continue
        if any(word in name for word in _GEMINI_SKIP):
            continue
        out.append({
            "id": name,
            "label": str(item.get("displayName") or name),
            # 思考の深さを指定できるかどうか。UI で選べるかの判断に使う
            "thinking": bool(item.get("thinking")),
        })
    out.sort(key=lambda m: m["id"])
    return out
