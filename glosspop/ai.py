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

#: 段落の切れ目。初出の場面をここで打ち切る
_BLANK_LINE = re.compile(r"\n[ \t]*\n")

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

_SCOPE_FIELD = '  "scope": "global または local (下の「保存先」を参照)",\n'

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


#: 初出の前後をどれだけ渡すか (spoiler="first")
FIRST_LEAD_CHARS = 2000
FIRST_TRAIL_CHARS = 400


def locator_of(text: str, term: str) -> str:
    """初出の行番号を表示用の文字列にする。見つからなければ空文字。

    PDF のページなど別の単位が来ても置き換えられるよう、文字列で持つ。
    """
    index = (text or "").casefold().find((term or "").casefold())
    if index < 0:
        return ""
    return f"L.{text[:index].count(chr(10)) + 1}"


def context_up_to_first(
    text: str, term: str, *, lead: int = FIRST_LEAD_CHARS, trail: int = FIRST_TRAIL_CHARS
) -> str:
    """初出の場面だけを切り出す（それ以降の展開を AI に見せない）。

    前は直前 ``lead`` 文字まで。「初出時点までの全文」にしないのは、小説 1 冊が
    入りきらないうえ、結局そこまでの筋書きを要約させることになるため。

    後ろは **初出を含む段落の終わりまで**（空行で切る）。単純に N 文字取ると、
    初出がファイル末尾に近いときに次の章まで巻き込んでネタバレする。
    """
    haystack = (text or "").casefold()
    index = haystack.find((term or "").casefold())
    if index < 0:
        return ""
    start = max(0, index - lead)
    end = min(len(text), index + len(term) + trail)
    tail = text[index:end]
    para = _BLANK_LINE.search(tail)
    if para:
        end = index + para.start()
    return text[start:end]


def build_scope_block(folder: str) -> str:
    """保存先（全体 / このフォルダだけ）を AI に選ばせるための説明。"""
    where = f"「{folder}」" if folder else "いま開いているフォルダ"
    return "\n".join([
        "## 保存先 (scope)",
        f"この用語を、全体の辞書と {where} だけの辞書のどちらに入れるべきか選んでください。",
        "",
        "- `global` … 分野の一般的な用語。**この文書を離れても同じ意味で通じる**もの"
        "（技術用語、一般名詞、広く使われる略語）",
        "- `local` … **この資料の中でしか通じない固有のもの**"
        "（作品の登場人物・地名・造語、その現場や製品だけの呼び名・社内用語）",
        "",
        "迷ったら「ほかの文書で同じ意味で出てきたら嬉しいか」で決めてください。"
        "嬉しいなら `global`、この資料の中だけの話なら `local` です。",
    ])


def build_prompt(
    term: str,
    context: str = "",
    *,
    source: str = "",
    spoiler: str = "full",
    scope_folder: str | None = None,
) -> str:
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
        heading = "## 用語が現れた文脈（この文脈での意味を優先する）"
        if spoiler == "first":
            heading = "## 用語が初めて出てくる場面（ここまでしか読んでいない）"
        parts += ["", heading, context.strip()[:4000]]
    if spoiler == "first":
        parts += [
            "",
            "## ネタバレの禁止",
            "**渡した抜粋は初出の場面だけです。それ以降の展開は知らないものとして書いてください。**",
            "後の展開・結末・正体・生死・因果関係の種明かしには触れないこと。",
            "この時点で分かる説明だけを書き、推測で先を語らないこと。",
        ]
    if source.strip():
        parts += ["", f"## 出典\n{source.strip()[:200]}"]
    parts += ["", "## カテゴリ", category_block]

    schema = _SCHEMA_HINT
    if scope_folder is not None:
        parts += ["", build_scope_block(scope_folder)]
        # 保存先を選ばせるときだけスキーマに足す（使わないなら聞かない）
        schema = _SCHEMA_HINT.replace("{\n", "{\n" + _SCOPE_FIELD, 1)

    parts += [
        "",
        "## 出力形式",
        "次の JSON オブジェクトだけを出力してください。前置き・後置きの文章、",
        "コードフェンス以外の説明は一切書かないこと。値は日本語で書く。",
        schema,
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


# --------------------------------------------------------------------------- #
# フォルダ横断
# --------------------------------------------------------------------------- #

#: 1 ファイルあたり / 全体で AI に渡す文字数の上限。
#: 全部渡すとプロンプトが膨らんで候補の質が落ちるので、頭から一定量だけ渡す
PER_FILE_CHARS = 3000
TOTAL_CHARS = 24000


def combine_documents(
    docs: list[tuple[str, str]], *, per_file: int = PER_FILE_CHARS, total: int = TOTAL_CHARS
) -> tuple[str, list[str], list[str]]:
    """複数文書を 1 つのプロンプト本文にまとめる。

    返すのは (まとめた本文, 使ったファイル, 入りきらなかったファイル)。
    切ったことは呼び出し側から UI に出す（黙って切らない）。
    """
    parts: list[str] = []
    used: list[str] = []
    skipped: list[str] = []
    budget = total
    for label, text in docs:
        body = (text or "").strip()
        if not body:
            continue
        if budget <= 0:
            skipped.append(label)
            continue
        chunk = body[: min(per_file, budget)]
        parts.append(f"### {label}\n{chunk}")
        used.append(label)
        budget -= len(chunk)
    return "\n\n".join(parts), used, skipped


def _occurrences(docs: list[tuple[str, str]], term: str) -> tuple[list[str], int]:
    """語が出てくるファイルと総出現数。頻出順に並べるために使う。"""
    needle = term.casefold()
    files: list[str] = []
    count = 0
    for label, text in docs:
        n = (text or "").casefold().count(needle)
        if n:
            files.append(label)
            count += n
    return files, count


def _first_seen(docs: list[tuple[str, str]], term: str) -> tuple[str, str, str]:
    """初出のファイル・位置・その場面の抜粋を返す。

    docs は読む順に並んでいる前提（第 1 章から順に渡す）。
    """
    for label, text in docs:
        if (term or "").casefold() in (text or "").casefold():
            return label, locator_of(text, term), context_up_to_first(text, term)
    return "", "", ""


async def extract_terms_from_documents(
    docs: list[tuple[str, str]], *, limit: int = 20
) -> dict:
    """フォルダ内の複数文書からまとめて候補を挙げる。

    呼び出しは 1 回だけ。ファイル数ぶん呼ぶと数分かかるうえ、同じ語が
    ファイルごとに重複して出てくる。
    """
    if not docs:
        raise AIError("読める文書がありません")
    combined, used, skipped = combine_documents(docs)
    if not combined.strip():
        raise AIError("読める文書がありません")

    exclude = [s for e in store.load_all() for s in e.surfaces]
    prompt = build_extract_prompt(combined, exclude=exclude, limit=limit)
    raw = await to_thread.run_sync(_run_claude, prompt, abandon_on_cancel=True)
    # 照合はプロンプトに載せた範囲ではなく全文に対して行う
    # (頭 3000 字しか渡していなくても、後ろに出てくる語なら採用してよい)
    haystack = "\n".join(text for _, text in docs)
    kept, dropped = filter_candidates(parse_candidates(raw), haystack, limit=limit)

    for item in kept:
        files, count = _occurrences(docs, item["term"])
        first_file, locator, first_context = _first_seen(docs, item["term"])
        item["files"] = files[:20]
        item["file_count"] = len(files)
        item["count"] = count
        item["source"] = first_file
        item["first_file"] = first_file
        item["first_locator"] = locator
        # 初出の場面。ネタバレを避けるとき (spoiler=first) はこれだけを AI に渡す
        item["first_context"] = first_context
    # 複数のファイルに出てくる語ほど辞書化の価値が高い
    kept.sort(key=lambda i: (-i["file_count"], -i["count"]))

    return {
        "candidates": kept,
        "dropped": dropped,
        "files_used": used,
        "files_skipped": skipped,
    }


async def draft_entry(
    term: str,
    context: str = "",
    *,
    source: str = "",
    spoiler: str = "full",
    scope_folder: str | None = None,
) -> EntryDraft:
    """選択テキストから辞書エントリの下書きを作る。保存はしない。

    ``scope_folder`` を渡すと、保存先（全体 / そのフォルダだけ）も選ばせる。
    """
    term = term.strip()
    if not term:
        raise AIError("用語が空です")
    prompt = build_prompt(
        term, context, source=source, spoiler=spoiler, scope_folder=scope_folder
    )
    raw = await to_thread.run_sync(_run_claude, prompt, abandon_on_cancel=True)
    data = parse_draft(raw)
    data.setdefault("term", term)
    if source and not data.get("source"):
        data["source"] = source
    return EntryDraft.model_validate(data)
