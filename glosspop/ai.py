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
            "登録済みのカテゴリ（インデントはそのカテゴリのサブカテゴリ）:\n" + known + "\n\n"
            "**分野が一致するものがあればそれを使い、無ければ遠慮なく新しいカテゴリ名を書いてください。**\n"
            "無理に既存へ寄せないこと。音楽の用語を「プログラミング」に入れるような分類は誤りです。\n"
            "`subcategory` は、選んだカテゴリの下に並んでいるものだけから選びます。"
            "合うものが無ければ新しい名前を書くか、空文字にしてください"
            "（他のカテゴリのサブカテゴリを流用しないこと）。"
        )
    else:
        category_block = f"登録済みのカテゴリはまだありません。分野に合う大分類を新設してください（{UNCATEGORIZED} は避ける）。"

    category_block += (
        "\n\nカテゴリ名の制約: ディレクトリ名になるので "
        '`< > : " / \\ | ? * # %` と制御文字は使えません。'
        "先頭・末尾に「.」を置かず、40 文字以内にしてください。"
    )

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


_EXTRACT_SCHEMA_HINT = """[
  {
    "term": "文書中に出てくる表記そのまま（活用や助詞を含めない）",
    "reading": "日本語の読み (かな)。不要なら空文字",
    "why": "なぜ辞書化する価値があるかを 20 字程度で",
    "context": "その語が出てくる文を 1 文そのまま抜き出す"
  }
]"""


def build_extract_prompt(
    text: str, *, exclude: list[str] | None = None, limit: int = 12, source: str = ""
) -> str:
    """文書から辞書化する価値のある語を挙げさせるプロンプト。

    下書き (build_prompt) と違い、**1 回の呼び出しで候補だけ**を出させる。
    本文の生成は選ばれた語についてだけ行う（語数 × 数十秒かかるため）。
    """
    parts = [
        "あなたは用語辞書の編集者です。次の文書を読み、"
        "読者がつまずきそうな専門用語を選び出してください。",
        "",
        "## 選ぶ基準",
        "- その分野を知らない読者が意味を取れない語（専門用語・略語・固有名詞）",
        "- 文書の理解に効く語を優先し、多くても " + str(limit) + " 件まで",
        "- 重要な順に並べる",
        "",
        "## 選ばない語",
        "- 一般的な日常語、一般的な動詞・形容詞",
        "- 「これ」「その方法」のような指示語や、その文書だけの言い回し",
        "- 数値・日付・URL・コード片そのもの",
        "",
        "## 表記",
        "`term` は**文書中に現れる表記をそのまま**書いてください。"
        "言い換えたり、単数形・原形に直したりしないこと"
        "（文書に無い表記は登録しても本文中でリンクになりません）。",
    ]
    if exclude:
        listed = "、".join(sorted(set(exclude))[:200])
        parts += [
            "",
            "## すでに辞書にある語（挙げないこと）",
            listed,
        ]
    if source.strip():
        parts += ["", f"## 出典\n{source.strip()[:200]}"]
    parts += [
        "",
        "## 文書",
        (text or "").strip()[:12000],
        "",
        "## 出力形式",
        "次の JSON 配列だけを出力してください。前置き・後置きの文章は書かないこと。",
        "該当する語が無ければ空の配列 [] を返してください。",
        _EXTRACT_SCHEMA_HINT,
    ]
    return "\n".join(parts)


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


def parse_candidates(text: str) -> list[dict]:
    """claude の応答テキストから JSON 配列を取り出す。"""
    candidates: list[str] = []
    m = _JSON_BLOCK.search(text)
    if m:
        candidates.append(m.group(1))
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    candidates.append(text)

    for candidate in candidates:
        try:
            data = json.loads(candidate.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    raise AIError(f"応答から JSON 配列を取り出せませんでした: {text[:400]}")


def filter_candidates(raw: list[dict], text: str, *, limit: int) -> tuple[list[dict], list[dict]]:
    """AI の申告をそのまま信じずに整える。

    返すのは (採用した候補, 落とした候補)。落とした理由も付けて返すのは、
    「なぜこの語が出てこないのか」を UI で説明できるようにするため。
    """
    haystack = (text or "").casefold()
    kept: list[dict] = []
    dropped: list[dict] = []
    seen: set[str] = set()

    for item in raw:
        term = str(item.get("term") or "").strip()
        if not term:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)

        entry = {
            "term": term,
            "reading": str(item.get("reading") or "").strip(),
            "why": str(item.get("why") or "").strip(),
            "context": str(item.get("context") or "").strip()[:400],
        }
        # 文書に無い表記は登録してもリンクにならない (AI の言い換え・原形化を弾く)
        if key not in haystack:
            dropped.append({**entry, "reason": "文書中に見つからない表記"})
            continue
        existing = store.find_by_surface(term)
        if existing:
            dropped.append({
                **entry,
                "reason": "登録済み: " + "、".join(e.path_label for e in existing),
            })
            continue
        if len(kept) >= limit:
            dropped.append({**entry, "reason": f"上限 {limit} 件を超えた"})
            continue
        kept.append(entry)

    return kept, dropped


async def extract_terms(
    text: str, *, source: str = "", limit: int = 12
) -> dict:
    """表示中の文書から辞書化する候補を挙げる。登録はしない。"""
    if not (text or "").strip():
        raise AIError("文書が空です")
    exclude = [s for e in store.load_all() for s in e.surfaces]
    prompt = build_extract_prompt(text, exclude=exclude, limit=limit, source=source)
    raw = await to_thread.run_sync(_run_claude, prompt, abandon_on_cancel=True)
    kept, dropped = filter_candidates(parse_candidates(raw), text, limit=limit)
    return {"candidates": kept, "dropped": dropped}


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
