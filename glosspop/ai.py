"""Claude Code CLI (``claude -p``) をサブプロセスで叩いて辞書エントリの下書きを作る。

API キー不要 (Claude Code の認証をそのまま流用する) 代わりに 1 回あたり数十秒
かかるので、呼び出しはワーカースレッドに逃がす。
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from anyio import to_thread

from . import config, store
from .models import UNCATEGORIZED, EntryDraft

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

#: 下書き生成はテキスト変換なので、ツールは全部落とす。
#: 許可を出さないとサブプロセスが承認待ちで固まる (実際に踏んだ)。
_DISALLOWED_TOOLS = ",".join([
    "Agent", "Bash", "Edit", "Glob", "Grep", "NotebookEdit", "Read",
    "Skill", "Task", "TodoWrite", "WebFetch", "WebSearch", "Write",
])

_NO_TOOL_SYSTEM = (
    "あなたは JSON を返す変換器として動作します。ツールは一切使わず、"
    "ファイルも読まず、質問もせず、与えられた情報だけで即座に JSON を出力してください。"
)


class AIError(RuntimeError):
    pass


def available() -> bool:
    return bool(config.CLAUDE_BIN)


# --------------------------------------------------------------------------- #
# プロンプト
# --------------------------------------------------------------------------- #

_SCHEMA_HINT = """{
  "term": "見出し語 (選択テキストを正規化したもの)",
  "reading": "日本語の読み (かな)。不要なら空文字",
  "aliases": ["表記ゆれ・略称・英語表記など。本文中で同義に使われる語だけ"],
  "category": "大分類",
  "subcategory": "小分類。不要なら空文字",
  "summary": "吹き出しに出す 1〜2 文の要約 (120 字以内)",
  "definition": "辞書ページ本文。Markdown。3〜6 文で背景と使いどころを説明する。1 文ごとに改行し、話題が変わるところで空行を入れて 2〜3 段落に分ける。1 段落に詰め込まない",
  "examples": ["この語の使用例を 0〜2 件"],
  "tags": ["検索用タグを 0〜4 件"]
}"""


def build_prompt(term: str, context: str = "", *, source: str = "") -> str:
    tree = store.category_tree()
    if tree:
        known = "\n".join(
            "- {}{}".format(
                node["category"],
                "".join(f"\n  - {s['name']}" for s in node["subcategories"] if s["name"]),
            )
            for node in tree
        )
        category_block = (
            "既存のカテゴリ/サブカテゴリ（できるだけ再利用し、"
            "どれにも当てはまらない時だけ新設する）:\n" + known
        )
    else:
        category_block = f"既存カテゴリはまだありません。適切な大分類を新設してください（例: {UNCATEGORIZED} は避ける）。"

    parts = [
        "あなたは用語辞書の編集者です。与えられた用語について辞書エントリを作成してください。",
        "",
        f"## 対象の用語\n{term}",
    ]
    if context.strip():
        parts += ["", "## 用語が現れた文脈（この文脈での意味を優先する）", context.strip()[:4000]]
    if source.strip():
        parts += ["", f"## 出典\n{source.strip()[:200]}"]
    parts += [
        "",
        "## カテゴリ",
        category_block,
        "",
        "## 出力形式",
        "次の JSON オブジェクトだけを出力してください。前置き・後置きの文章、",
        "コードフェンス以外の説明は一切書かないこと。値は日本語で書く。",
        _SCHEMA_HINT,
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# 実行
# --------------------------------------------------------------------------- #

def _neutral_cwd() -> Path:
    """プロジェクト外の作業ディレクトリ。

    プロジェクト内で実行すると CLAUDE.md や ``gloss-add`` スキルを拾って
    「重複を確認するため CLI を実行したい」と言い出し、承認が取れずに詰まる。
    """
    path = Path(tempfile.gettempdir()) / "glosspop-ai"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_claude(prompt: str) -> str:
    if not config.CLAUDE_BIN:
        raise AIError("claude CLI が見つかりません。PATH に追加するか GLOSSPOP_CLAUDE_BIN を設定してください。")
    cmd = [
        config.CLAUDE_BIN,
        "-p", prompt,
        "--output-format", "json",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--disallowed-tools", _DISALLOWED_TOOLS,
        "--append-system-prompt", _NO_TOOL_SYSTEM,
        *config.CLAUDE_EXTRA_ARGS,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.CLAUDE_TIMEOUT,
            cwd=str(_neutral_cwd()),
        )
    except subprocess.TimeoutExpired as exc:
        raise AIError(f"claude CLI が {config.CLAUDE_TIMEOUT} 秒でタイムアウトしました") from exc
    except OSError as exc:
        raise AIError(f"claude CLI を起動できません: {exc}") from exc

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise AIError(f"claude CLI が異常終了しました (exit {proc.returncode}): {detail}")

    stdout = (proc.stdout or "").strip()
    if not stdout:
        raise AIError("claude CLI が何も出力しませんでした")

    # --output-format json の外側エンベロープを剥がす
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    if isinstance(envelope, dict):
        if envelope.get("is_error"):
            raise AIError(f"claude がエラーを返しました: {envelope.get('result') or envelope}")
        result = envelope.get("result")
        if isinstance(result, str):
            return result
    return stdout


def parse_draft(text: str) -> dict:
    """claude の応答テキストから JSON オブジェクトを取り出す。"""
    candidates: list[str] = []
    m = _JSON_BLOCK.search(text)
    if m:
        candidates.append(m.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    candidates.append(text)

    for candidate in candidates:
        try:
            data = json.loads(candidate.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise AIError(f"応答から JSON を取り出せませんでした: {text[:400]}")


async def draft_entry(term: str, context: str = "", *, source: str = "") -> EntryDraft:
    """選択テキストから辞書エントリの下書きを作る。保存はしない。"""
    term = term.strip()
    if not term:
        raise AIError("用語が空です")
    prompt = build_prompt(term, context, source=source)
    raw = await to_thread.run_sync(_run_claude, prompt, abandon_on_cancel=True)
    data = parse_draft(raw)
    data.setdefault("term", term)
    if source and not data.get("source"):
        data["source"] = source
    return EntryDraft.model_validate(data)
