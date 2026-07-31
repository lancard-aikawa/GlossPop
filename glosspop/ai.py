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

from . import config, relations, store
from .linker import entry_url
from .models import UNCATEGORIZED, Entry, EntryDraft, Relation

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


def build_scope_block(folder: str, kind: str = "") -> str:
    """保存先（全体 / このフォルダだけ）を AI に選ばせるための説明。

    抽出のときに選ばれた種別 (``kind``) が分かっていれば下敷きとして渡す。
    人物や独自語はほぼローカルなので、毎回ゼロから考えさせる必要がない。
    """
    where = f"「{folder}」" if folder else "いま開いているフォルダ"
    spec = EXTRACT_KINDS.get(kind)
    hint = []
    if spec and spec["scope"]:
        hint = [
            "",
            f"この語は抽出時に「{spec['label']}」として選ばれています。"
            f"通常は `{spec['scope']}` が適切ですが、内容を見て違うと思えば変えてかまいません。",
        ]
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
        *hint,
    ])


def build_prompt(
    term: str,
    context: str = "",
    *,
    source: str = "",
    spoiler: str = "full",
    scope_folder: str | None = None,
    kind: str = "",
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
        parts += ["", build_scope_block(scope_folder, kind)]
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
    "kind": "下の種別コードのいずれか",
    "reading": "日本語の読み (かな)。不要なら空文字",
    "why": "なぜ辞書化する価値があるかを 20 字程度で",
    "context": "その語が出てくる文を 1 文そのまま抜き出す"
  }
]"""


#: 抽出の種別。**何を抜き出すかを先に決めてから候補を挙げさせる**ための枠。
#:
#: 単に「辞書化する価値のある語」と頼むと、AI は語義説明のできる語 —— つまり
#: 専門用語ばかりを挙げ、**登場人物がまるごと落ちる**。人名は「意味が分からない
#: 語」ではないので、その基準に引っかからない。種別ごとに独立した枠を与えて、
#: 枠の振り替えを禁じることでしか埋まらない。
EXTRACT_KINDS: dict[str, dict[str, str]] = {
    "person": {
        "label": "人物・組織",
        "hint": "登場人物・人名・団体・組織・肩書き。**その文書の中で役割を持つ主体**。"
                "語義の説明が要らなくても、誰なのかを覚えておきたい相手なら挙げる",
        "scope": "local",
    },
    "proper": {
        "label": "固有名・独自語",
        "hint": "地名・作品名・製品名・道具の名、その資料の中だけで通じる造語や呼び名。"
                "**この文書を離れたら通じないもの**",
        "scope": "local",
    },
    "term": {
        "label": "専門用語・略語",
        "hint": "その分野を知らない読者が意味を取れない語。技術用語・業界語・略語。"
                "**この文書を離れても同じ意味で通じるもの**",
        "scope": "global",
    },
    "key": {
        "label": "鍵になる語",
        "hint": "この文書の主題そのものを指す語や、繰り返し現れて議論の軸になっている語。"
                "一般的な語でも、この文書では特別な重みを持つなら挙げる",
        "scope": "",
    },
}

#: 既定で抜き出す種別。「鍵になる語」は他と重なりやすいので既定では外す
DEFAULT_KINDS = ("person", "proper", "term")


def plain_hint(kind: str) -> str:
    """種別の説明を UI 用の素の文にする。

    ``hint`` はプロンプトに埋める前提で ``**`` を含んでいる（AI には強調が効く）。
    UI にそのまま出すとアスタリスクが並ぶので、ここで落とす。
    """
    spec = EXTRACT_KINDS.get(kind)
    return spec["hint"].replace("**", "") if spec else ""


def normalize_kinds(kinds: list[str] | tuple[str, ...] | None) -> list[str]:
    """要求された種別を、既知のものだけの順序付きリストにする。空なら既定。"""
    out = [k for k in dict.fromkeys(kinds or ()) if k in EXTRACT_KINDS]
    return out or list(DEFAULT_KINDS)


def allocate_quota(limit: int, kinds: list[str]) -> dict[str, int]:
    """件数の上限を種別ごとに割り振る。**合計は ``limit`` を超えない。**

    合計に対して上限をかけるだけだと、AI が人物を先に並べただけで
    専門用語が全部切られる（順序で切ると種別が丸ごと消える）。

    種別の数より ``limit`` が小さいと 0 件の枠ができるが、それは呼び出し側の
    指定どおりなので勝手に増やさない。実際の抽出経路では
    ``extract_terms()`` が下限を持ち上げている。
    """
    base, extra = divmod(max(limit, 0), len(kinds))
    return {k: base + (1 if i < extra else 0) for i, k in enumerate(kinds)}


def build_extract_prompt(
    text: str,
    *,
    exclude: list[str] | None = None,
    limit: int = 12,
    source: str = "",
    kinds: list[str] | None = None,
) -> str:
    """文書から辞書化する価値のある語を挙げさせるプロンプト。

    下書き (build_prompt) と違い、**1 回の呼び出しで候補だけ**を出させる。
    本文の生成は選ばれた語についてだけ行う（語数 × 数十秒かかるため）。
    """
    kinds = normalize_kinds(kinds)
    quota = allocate_quota(limit, kinds)

    parts = [
        "あなたは用語辞書の編集者です。次の文書を読み、"
        "辞書に登録する価値のある語を**種別ごとに**選び出してください。",
        "",
        "## 抜き出す種別と件数",
    ]
    for key in kinds:
        spec = EXTRACT_KINDS[key]
        parts.append(f"- `{key}` … **{spec['label']}**（最大 {quota[key]} 件）— {spec['hint']}")
    parts += [
        "",
        "**種別ごとに独立して選んでください。** ある種別で件数が集まらなくても、"
        "余った枠を別の種別に振り替えないこと。逆に、ある種別に候補が多くても"
        "他の種別の枠を奪わないこと。",
        "各種別の中では重要な順に並べ、`kind` にその種別コードを必ず入れてください。",
        "",
        "## 選ばない語",
        "- 一般的な日常語、一般的な動詞・形容詞（`key` に該当する場合を除く）",
        "- 「これ」「その方法」のような指示語や、その文書だけの言い回し",
        "- 数値・日付・URL・コード片そのもの",
        "",
        "## 表記",
        "`term` は**文書中に現れる表記をそのまま**書いてください。"
        "言い換えたり、単数形・原形に直したりしないこと"
        "（文書に無い表記は登録しても本文中でリンクになりません）。"
        "人物なら、文書の中でいちばん多く使われている呼び方を選びます。",
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


def kind_label(kind: str) -> str:
    spec = EXTRACT_KINDS.get(kind)
    return spec["label"] if spec else "その他"


def filter_candidates(
    raw: list[dict], text: str, *, limit: int, kinds: list[str] | None = None
) -> tuple[list[dict], list[dict]]:
    """AI の申告をそのまま信じずに整える。

    返すのは (採用した候補, 落とした候補)。落とした理由も付けて返すのは、
    「なぜこの語が出てこないのか」を UI で説明できるようにするため。

    件数の上限は**種別ごと**にかける。全体に対して先頭から切ると、AI が
    ある種別を先に並べただけで別の種別が丸ごと消える。
    """
    kinds = normalize_kinds(kinds)
    quota = allocate_quota(limit, kinds)
    used = {k: 0 for k in kinds}
    order = {k: i for i, k in enumerate(kinds)}

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

        kind = str(item.get("kind") or "").strip()
        if kind not in order:
            kind = ""      # 種別を答えなかった / 知らない種別。空きのある枠に入れる
        entry = {
            "term": term,
            "kind": kind,
            "kind_label": kind_label(kind),
            # 人物や独自語はローカル辞書向き。下書き時の既定として渡す
            "scope_hint": EXTRACT_KINDS.get(kind, {}).get("scope", ""),
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

        bucket = kind or next((k for k in kinds if used[k] < quota[k]), "")
        if not bucket or used[bucket] >= quota[bucket]:
            label = kind_label(bucket or kind)
            dropped.append({**entry, "reason": f"「{label}」の枠（{quota.get(bucket, limit)} 件）を超えた"})
            continue
        used[bucket] += 1
        entry["kind"] = bucket
        entry["kind_label"] = kind_label(bucket)
        entry["scope_hint"] = EXTRACT_KINDS[bucket]["scope"]
        kept.append(entry)

    kept.sort(key=lambda i: order.get(i["kind"], len(order)))
    return kept, dropped


async def extract_terms(
    text: str, *, source: str = "", limit: int = 12, kinds: list[str] | None = None
) -> dict:
    """表示中の文書から辞書化する候補を挙げる。登録はしない。"""
    if not (text or "").strip():
        raise AIError("文書が空です")
    kinds = normalize_kinds(kinds)
    # 種別の数より少ない上限だと 0 件の枠ができる。選んだ種別が黙って
    # 出てこないのがいちばん困るので、下限だけ持ち上げる
    limit = max(limit, len(kinds))
    exclude = [s for e in store.load_all() for s in e.surfaces]
    prompt = build_extract_prompt(
        text, exclude=exclude, limit=limit, source=source, kinds=kinds
    )
    raw = await to_thread.run_sync(_run_claude, prompt, abandon_on_cancel=True)
    kept, dropped = filter_candidates(
        parse_candidates(raw), text, limit=limit, kinds=kinds
    )
    return {"candidates": kept, "dropped": dropped, "kinds": kinds}


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
    docs: list[tuple[str, str]], *, limit: int = 20, kinds: list[str] | None = None
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

    kinds = normalize_kinds(kinds)
    limit = max(limit, len(kinds))
    exclude = [s for e in store.load_all() for s in e.surfaces]
    prompt = build_extract_prompt(combined, exclude=exclude, limit=limit, kinds=kinds)
    raw = await to_thread.run_sync(_run_claude, prompt, abandon_on_cancel=True)
    # 照合はプロンプトに載せた範囲ではなく全文に対して行う
    # (頭 3000 字しか渡していなくても、後ろに出てくる語なら採用してよい)
    haystack = "\n".join(text for _, text in docs)
    kept, dropped = filter_candidates(
        parse_candidates(raw), haystack, limit=limit, kinds=kinds
    )

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
    # 種別のまとまりは崩さず、その中で「多くのファイルに出てくる語」を上に出す
    order = {k: i for i, k in enumerate(kinds)}
    kept.sort(key=lambda i: (order.get(i["kind"], len(order)), -i["file_count"], -i["count"]))

    return {
        "candidates": kept,
        "dropped": dropped,
        "files_used": used,
        "files_skipped": skipped,
        "kinds": kinds,
    }


# --------------------------------------------------------------------------- #
# 関係の下書き
#
# 関係のデータ構造だけあっても、1 本ずつ手で書くことになって図が空のまま終わる。
# **登録済みの用語を渡して、その間の関係だけを 1 回で挙げさせる**のがここ。
# 用語そのものは作らない（それは extract の仕事）。
# --------------------------------------------------------------------------- #

_RELATION_SCHEMA_HINT = """[
  {
    "from": "関係を書く側の用語。**下の一覧にある表記そのまま**",
    "to": "相手の用語。**下の一覧にある表記そのまま**",
    "label": "from から見た to の一言 (10 字程度)",
    "back": "to から見た from の一言。一方的な関係なら空文字",
    "rank": "to が from から見て 上 / 下 / 対等 のいずれか。決められなければ空文字",
    "reveal": "REVEAL_DESC",
    "why": "根拠になる本文を 1 文そのまま抜き出す"
  }
]"""


def build_relations_prompt(
    entries: list[Entry],
    text: str,
    *,
    existing: list[tuple[str, str]] | None = None,
    limit: int = 20,
    spoiler: str = "full",
) -> str:
    """登録済みの用語どうしの関係を挙げさせるプロンプト。"""
    listed = "\n".join(
        f"- {e.term}" + (f" — {e.summary}" if e.summary else "")
        for e in entries
    )
    reveal_desc = (
        "この関係が本文で判明する位置 (「第6章」など)。分からなければ空文字"
        if spoiler != "first"
        else "**必ず空文字にすること**"
    )

    parts = [
        "あなたは用語辞書の編集者です。次の文書を読み、"
        "**すでに登録されている用語どうしの関係**を挙げてください。",
        "",
        "## 対象の用語",
        "**この一覧にあるものだけ**を `from` と `to` に使ってください。"
        "一覧に無い語を勝手に足さないこと（関係だけを作る作業で、用語は作りません）。",
        listed,
        "",
        "## 書き方",
        "すべて **`from` から `to` を見た向き**で書きます。基準を 1 つに固定しているので、"
        "`label` も `back` も `rank` も同じ向きで読めるようにしてください。",
        "",
        "- 相互の関係（互いに認識している）なら `back` にも一言を入れる",
        "- 一方的な関係（片思い、一方が知らない）なら `back` は空文字",
        "- 同じ 2 つの用語の組は **1 回だけ**。向きを変えて 2 行書かないこと",
        "- 本文から読み取れる関係だけを書く。推測で埋めないこと",
        f"- 多くても {limit} 件まで。確かなものを優先する",
    ]
    if existing:
        pairs = "、".join(f"{a}—{b}" for a, b in existing[:200])
        parts += ["", "## すでに書かれている関係（挙げないこと）", pairs]
    if spoiler == "first":
        parts += [
            "",
            "## ネタバレの禁止",
            "**渡した抜粋は各用語が初めて出てくる場面だけです。それ以降の展開は"
            "知らないものとして書いてください。**",
            "後で明かされる関係（正体、血縁、裏切りなど）には触れず、"
            "この時点で分かる関係だけを書くこと。",
        ]
    parts += [
        "",
        "## 文書",
        (text or "").strip()[:16000],
        "",
        "## 出力形式",
        "次の JSON 配列だけを出力してください。前置き・後置きの文章は書かないこと。",
        "確かな関係が無ければ空の配列 [] を返してください。",
        _RELATION_SCHEMA_HINT.replace("REVEAL_DESC", reveal_desc),
    ]
    return "\n".join(parts)


def existing_pairs(entries: list[Entry], scope: list[Entry]) -> list[tuple[str, str]]:
    """すでに関係が書かれている組を**用語名で**返す。プロンプトに載せる用。"""
    out: list[tuple[str, str]] = []
    for entry in scope:
        for rel in entry.relations:
            res = relations.resolve(rel.to, entries, origin=entry)
            other = res.entry.term if res.entry is not None else rel.to
            out.append((entry.term, other))
    return out


def existing_ref_pairs(entries: list[Entry], scope: list[Entry]) -> set[tuple[str, str]]:
    """すでに関係が書かれている組を **ref で**返す。重複の判定用。

    プロンプト用 (``existing_pairs``) と別にしているのは、**照合を用語名でやると
    成立しないため**。同じ用語名がカテゴリ違いで併存できるので名前では一意にならず、
    かたや新しい候補は解決済みの ref で持っている。突き合わせる鍵を揃えないと
    「すでにある関係」を素通しして、同じ組に 2 本目の辺が生える（実際に踏んだ）。
    """
    out: set[tuple[str, str]] = set()
    for entry in scope:
        for rel in entry.relations:
            res = relations.resolve(rel.to, entries, origin=entry)
            if res.entry is not None:
                out.add(_pair_key(entry.ref, res.entry.ref))
    return out


def _pair_key(a: str, b: str) -> tuple[str, str]:
    """向きを無視した組の鍵。同じ組を 2 度書かせないために使う。"""
    return tuple(sorted((a.casefold(), b.casefold())))  # type: ignore[return-value]


def filter_relations(
    raw: list[dict],
    entries: list[Entry],
    *,
    scope: list[Entry],
    limit: int,
    allow_reveal: bool = True,
) -> tuple[list[dict], list[dict]]:
    """AI が挙げた関係をそのまま信じずに整える。

    候補語のときと同じ発想で、**通すと壊れるもの**を先に落とす:

    - 一覧に無い用語（AI は平気で本文中の別の名前を返す）
    - 自分自身への関係（図に描けない）
    - すでに書かれている組（重複した辺になる）
    - 同じ組の 2 度目（向きを変えて 2 行書いてくることがある）

    落とした理由を付けて返すのは、「なぜこの関係が出てこないのか」を UI で
    説明できるようにするため。
    """
    known = existing_ref_pairs(entries, scope)
    seen: set[tuple[str, str]] = set()
    kept: list[dict] = []
    dropped: list[dict] = []

    for item in raw:
        src = str(item.get("from") or "").strip()
        dst = str(item.get("to") or "").strip()
        if not src or not dst:
            continue

        rel = Relation.model_validate({
            "to": dst,
            "label": item.get("label"),
            "back": item.get("back"),
            "rank": item.get("rank"),
            "reveal": item.get("reveal") if allow_reveal else "",
        })
        record = {
            "from": src,
            "to": rel.to,
            "label": rel.label,
            "back": rel.back,
            "rank": rel.rank,
            "reveal": rel.reveal,
            "mutual": rel.mutual,
            "why": str(item.get("why") or "").strip()[:400],
        }

        from_hit = relations.resolve(src, entries)
        if from_hit.entry is None:
            dropped.append({**record, "reason": f"「{src}」は登録された用語ではありません"})
            continue
        to_hit = relations.resolve(rel.to, entries, origin=from_hit.entry)
        if to_hit.entry is None:
            dropped.append({**record, "reason": f"「{rel.to}」は登録された用語ではありません"})
            continue
        if from_hit.entry.ref == to_hit.entry.ref:
            dropped.append({**record, "reason": "自分自身への関係"})
            continue

        key = _pair_key(from_hit.entry.ref, to_hit.entry.ref)
        if key in known:
            dropped.append({**record, "reason": "すでに関係が書かれています"})
            continue
        if key in seen:
            dropped.append({**record, "reason": "同じ組が 2 回挙がりました"})
            continue
        if len(kept) >= limit:
            dropped.append({**record, "reason": f"上限 {limit} 件を超えた"})
            continue

        seen.add(key)
        # 解決済みの ref で持たせる。保存時に名前を引き直さずに済み、
        # 同名がカテゴリ違いで併存していても取り違えない
        record["from_ref"] = from_hit.entry.ref
        record["from_term"] = from_hit.entry.term
        record["from_url"] = entry_url(from_hit.entry)
        record["to_ref"] = to_hit.entry.ref
        record["to_term"] = to_hit.entry.term
        record["to_url"] = entry_url(to_hit.entry)
        kept.append(record)

    return kept, dropped


async def draft_relations(
    entries: list[Entry],
    docs: list[tuple[str, str]],
    *,
    scope: list[Entry] | None = None,
    limit: int = 20,
    spoiler: str = "full",
) -> dict:
    """登録済みの用語どうしの関係を下書きする。保存はしない。

    ``entries`` は辞書全体（参照解決に使う）、``scope`` は関係を探す対象。
    """
    scope = scope if scope is not None else entries
    if len(scope) < 2:
        raise AIError("関係を探すには、同じ範囲に 2 語以上の登録が要ります")

    if spoiler == "first":
        # 各用語の初出の場面だけを繋いで渡す。全文を渡すと、後で明かされる
        # 関係（正体・血縁）が図に出てしまう
        chunks = []
        for entry in scope:
            _, _, context = _first_seen(docs, entry.term)
            if context:
                chunks.append(f"### {entry.term} の初出\n{context}")
        text = "\n\n".join(chunks)
    else:
        text, _, _ = combine_documents(docs)

    if not text.strip():
        raise AIError("読める本文がありません")

    prompt = build_relations_prompt(
        scope,
        text,
        existing=existing_pairs(entries, scope),
        limit=limit,
        spoiler=spoiler,
    )
    raw = await to_thread.run_sync(_run_claude, prompt, abandon_on_cancel=True)
    kept, dropped = filter_relations(
        parse_candidates(raw),
        entries,
        scope=scope,
        limit=limit,
        allow_reveal=spoiler != "first",
    )
    return {"relations": kept, "dropped": dropped}


async def draft_entry(
    term: str,
    context: str = "",
    *,
    source: str = "",
    spoiler: str = "full",
    scope_folder: str | None = None,
    kind: str = "",
) -> EntryDraft:
    """選択テキストから辞書エントリの下書きを作る。保存はしない。

    ``scope_folder`` を渡すと、保存先（全体 / そのフォルダだけ）も選ばせる。
    ``kind`` は抽出時の種別（``EXTRACT_KINDS``）。保存先の下敷きに使う。
    """
    term = term.strip()
    if not term:
        raise AIError("用語が空です")
    prompt = build_prompt(
        term, context, source=source, spoiler=spoiler,
        scope_folder=scope_folder, kind=kind,
    )
    raw = await to_thread.run_sync(_run_claude, prompt, abandon_on_cancel=True)
    data = parse_draft(raw)
    data.setdefault("term", term)
    if source and not data.get("source"):
        data["source"] = source
    return EntryDraft.model_validate(data)
