"""辞書エントリの下書きを AI に作らせる。**プロンプトの組み立てと後処理**が仕事。

どの AI に、どのモデルで、どれだけ考えさせて頼むかは ``llm.py`` が持つ
（Claude Code CLI / Gemini API）。ここはその違いを知らない。

1 回あたり数十秒〜数分かかるので、呼び出しはワーカースレッドに逃がす。
"""

from __future__ import annotations

import json
import os
import re
from functools import partial

from anyio import to_thread

from . import config, llm, relations, store
from .linker import entry_url
from .models import GLOBAL_SCOPE, SCOPES, UNCATEGORIZED, Entry, EntryDraft, Relation

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

#: 段落の切れ目。初出の場面をここで打ち切る
_BLANK_LINE = re.compile(r"\n[ \t]*\n")

_NO_TOOL_SYSTEM = (
    "あなたは JSON を返す変換器として動作します。ツールは一切使わず、"
    "ファイルも読まず、質問もせず、与えられた情報だけで即座に JSON を出力してください。"
)

#: 失敗の型は提供元で分けない（呼ぶ側は「AI に頼めなかった」しか区別しない）
AIError = llm.LLMError


def available() -> bool:
    return llm.available()


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

#: 長い本文を間引くときの窓の数と、窓の切れ目に入れる印。
#: 印を入れるのは、飛んだことを AI に知らせて「連続した話」と読ませないため
SAMPLE_WINDOWS = 8
GAP_MARK = "〔中略〕"
_GAP_JOIN = f"\n\n{GAP_MARK}\n\n"


def sample_text(text: str, budget: int, *, windows: int = SAMPLE_WINDOWS) -> str:
    """長すぎる本文を、**文書全体に散らした窓**に間引く。

    頭から ``budget`` 文字取るだけだと、長編では冒頭しか AI に見えない
    （345,000 字の小説に 12,000 字の枠なら先頭 3.5%）。後の章で初めて出てくる
    登場人物は候補に挙がりようがなく、関係も引けない —— 実際に
    『吾輩は猫である』で迷亭も寒月も一度も渡っていなかった。

    先頭の窓は必ず 0 から始め、最後の窓は必ず末尾で終わる（書き出しと結びは
    どちらも情報が多い。とくに末尾は、頭から切る実装では絶対に届かなかった）。
    """
    body = text or ""
    if budget <= 0:
        return ""
    if len(body) <= budget:
        return body

    windows = max(1, min(windows, budget // 200 or 1))
    take = budget // windows
    # 窓の頭を [0, 末尾-take] に等間隔で置く。全体を len で割ると最後の窓が
    # 文書の途中で終わり、**結びが一度も届かない**
    step = (len(body) - take) / (windows - 1) if windows > 1 else 0
    parts: list[str] = []
    for i in range(windows):
        start = int(i * step)
        if i:
            # 文の途中から始めない。近くに改行があればそこまで送る
            nl = body.find("\n", start, start + max(take // 4, 1))
            if nl != -1:
                start = nl + 1
        chunk = body[start:start + take].strip()
        if chunk:
            parts.append(chunk)
    return _GAP_JOIN.join(parts)


def _gap_note(text: str) -> list[str]:
    """間引いたことをプロンプトに書き添える行。間引いていなければ空。"""
    if GAP_MARK not in text:
        return []
    return [
        f"（長い文書なので全体から抜粋しています。`{GAP_MARK}` は省略した箇所で、"
        "その前後は連続していません。）",
    ]


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


# --------------------------------------------------------------------------- #
# 文体（口調）
#
# 「広川太一郎風」「司馬遼太郎風」「TRPG のルールブック風」のように、作品や卓に
# 寄せた書き方をさせるための指定。**設定の解決も節の組み立てもここに置く** ——
# 文体は「何を頼むか」であって「誰にどう頼むか」ではないので ``llm.py`` の仕事では
# ない（提供元・モデル・思考の深さと同じ画面に出るだけ）。
#
# **口調は作品につく。** 全体に 1 つだけ持たせると、小説を読むフォルダと社内資料の
# フォルダを行き来するたびに ⚙ を開き直すことになる。なので辞書と同じ置き場所
# (``.glosspop/style.md``) にフォルダぶんを持てるようにし、無ければ全体に落とす。
# --------------------------------------------------------------------------- #

#: 指定を受け付ける長さ。長い指示は本文の枠を食ううえ、出力形式の指示より
#: 目立つようになる（プロンプトの中で相対的に重くなる）
STYLE_MAX_CHARS = 400

STYLE_ENV = "GLOSSPOP_AI_STYLE"
STYLE_SETTING = "ai_style"

#: 画面に出す例。**値そのもの**なので、押せばそのまま入力欄に入る
STYLE_PRESETS: list[dict[str, str]] = [
    {"label": "講談調", "value": "講談・軍記物のような語り口で。歯切れよく、体言止めを混ぜる。"},
    {"label": "司馬遼太郎風", "value": "司馬遼太郎のような史伝の筆致で。俯瞰した視点から、断定を避けずに淡々と書く。"},
    {"label": "広川太一郎風", "value": "広川太一郎の吹き替えのような軽妙な口調で。"
                                      "茶々を入れるような言い回しや語尾の遊びを少し混ぜる。"},
    {"label": "怪盗の予告状風", "value": "怪盗の予告状のように、もったいぶった芝居がかった調子で。"},
    {"label": "TRPG のルールブック風", "value": "TRPG のルールブックの記述のように、"
                                                "卓上でそのまま読み上げられる調子で。世界観の語り口を保つ。"},
    {"label": "です・ます", "value": "です・ます調の、落ち着いた説明文で。"},
]


def _global_style() -> str:
    """全体（どのフォルダでも効く）の指定。設定ファイルに入る。"""
    return str(config.load_settings().get(STYLE_SETTING) or "").strip()


def _folder_style() -> str:
    """いま読んでいるものに効く指定。``.glosspop/style.md` の中身がまるごと値。

    **読むたびに開く。** 覚え込むと、外のエディタで書き換えたぶんを黙って無視する
    ようになる（`store` のキャッシュを署名で作っているのと同じ理由）。AI を呼ぶ
    経路でしか通らないうえ数百字のファイルなので、毎回読んで困らない。
    """
    path = config.local_style_file()
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""                      # 読めない指定で下書きごと止めない


def _style_pick() -> tuple[str, str]:
    """(文体, どこから来たか)。

    **優先順は 環境変数 > 📁 このフォルダ > 全体 > 既定。** 環境変数を最上位に
    するのは既存の規則どおり（テストと一時的な切り替えが、設定ファイルにも
    フォルダにも引きずられないように）。
    """
    env = (os.environ.get(STYLE_ENV) or "").strip()
    if env:
        return env[:STYLE_MAX_CHARS], "env"
    folder = _folder_style()
    if folder:
        return folder[:STYLE_MAX_CHARS], "folder"
    saved = _global_style()
    return (saved[:STYLE_MAX_CHARS], "settings") if saved else ("", "default")


def style() -> str:
    """いま効いている文体の指定。指定が無ければ空文字。

    **基準は「いま読んでいるもの」で、エントリの保存先 (`scope`) ではない。**
    保存先で切り替える形にすると、``scope: "auto"`` は語ごとに行き先が変わるので、
    1 回の「まとめて登録」の中で口調が混ざる。
    """
    return _style_pick()[0]


def describe_style() -> dict:
    """UI に出す文体まわり一式。``/api/ai/settings`` が ``llm.describe()`` に足す。

    **どちらが効いているかを画面に書けるだけの材料を返す。** 全体とフォルダの
    両方に指定できる以上、片方だけ見せると「全体に書いたのに効かない」になる
    （相関図の範囲を必ず画面に出しているのと同じ話）。**祖先の指定が効いている
    ときは場所も出す** —— 黙って遠いフォルダの口調が効くと驚く。
    """
    value, source = _style_pick()
    path = config.local_style_file()
    root = config.local_root()
    return {
        "style": value,
        "style_source": source,
        "style_global": _global_style(),
        "style_folder": _folder_style(),
        "style_folder_path": str(path) if path is not None else "",
        "style_folder_label": root.name if root is not None else "",
        "style_folder_is_ancestor": (
            root is not None and not config.reading_url() and root != config.content_dir()
        ),
        "style_presets": STYLE_PRESETS,
        "style_max": STYLE_MAX_CHARS,
    }


def save_style(scope: str, text: str) -> None:
    """文体を保存する。``scope`` は ``global`` / ``local``。

    **空文字は「消す」。** フォルダ側はファイルごと消す（空のファイルを残すと、
    次に開いた人が「何か指定されている」と読む）。``.glosspop`` は消さない ——
    辞書が入っているかもしれない。

    **保存を押したときだけディレクトリを作る。** 開いただけのフォルダを汚さない、
    というカテゴリマスターと同じ約束。
    """
    if scope not in SCOPES:
        raise AIError(f"不明な保存先です: {scope}")
    text = (text or "").strip()
    if len(text) > STYLE_MAX_CHARS:
        raise AIError(f"文体の指定は {STYLE_MAX_CHARS} 字までです")

    if scope == GLOBAL_SCOPE:
        settings = config.load_settings()
        if text:
            settings[STYLE_SETTING] = text
        else:
            settings.pop(STYLE_SETTING, None)
        config.save_settings(settings)
        return

    path = config.local_style_file()
    if path is None:
        raise AIError("いま読んでいるものに辞書がありません（フォルダを開いてください）")
    if not text:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def build_style_block(value: str, fields: str, keep: str) -> str:
    """文体の指定をプロンプトの 1 節にする。**効く範囲を必ず一緒に書く。**

    文体をそのまま渡すだけにすると、AI は**用語名や関係の相手まで**その口調に
    崩す。ところが `term` は文書中の表記と一致しないと `filter_candidates()` が
    落とし、`from` / `to` は一覧の表記と一致しないと `filter_relations()` が
    落とす —— **頼んだ本人には「なぜか候補が全部消えた」としか見えない**。
    カテゴリはディレクトリ名にもなる。だから範囲は毎回書き添える。

    ``fields`` が文体の効く項目、``keep`` が崩してはいけない項目。
    """
    value = (value or "").strip()[:STYLE_MAX_CHARS]
    if not value:
        return ""
    return "\n".join([
        "## 文体（口調）",
        "次の指定に沿った書き方にしてください。",
        "",
        value,
        "",
        f"**この指定が効くのは {fields} の中身だけです。**",
        f"{keep} は文体に合わせて崩さないこと"
        "（辞書の見出しや相手の指定としてそのまま照合されるので、変えると保存できません）。",
        "JSON の構造・キー名も変えないこと。"
        "**上の「ネタバレの禁止」と、下の「出力形式」の指示のほうが優先します。**",
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

    # 読み手が読む散文だけに効かせる。見出し語・別名・カテゴリを崩されると、
    # 本文でリンクにならない語や、ディレクトリ名に使えないカテゴリが返ってくる
    style_block = build_style_block(
        style(),
        "`summary` `definition` `examples`",
        "`term` `reading` `aliases` `category` `subcategory` `tags`",
    )
    if style_block:
        parts += ["", style_block]

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

def relations_timeout(limit: int) -> int:
    """関係の下書きに許す秒数。**頼んだ本数と、頼む相手から見積もる。**"""
    return llm.estimate_timeout("relation", limit)


def extract_timeout(limit: int) -> int:
    """候補語の抽出に許す秒数。**頼んだ語数と、頼む相手から見積もる。**"""
    return llm.estimate_timeout("extract", limit)


def _generate(prompt: str, *, timeout: int | None = None) -> str:
    """AI に投げて本文を受け取る。**この 1 か所だけが外の頭脳に触る。**

    どの提供元・モデル・思考の深さで動くかは ``llm.resolve()`` が決める。
    テストはここを差し替える（提供元ごとに差し替え先が変わらないように）。
    """
    return llm.generate(prompt, timeout=timeout or config.CLAUDE_TIMEOUT, system=_NO_TOOL_SYSTEM)


_EXTRACT_SCHEMA_HINT = """[
  {
    "term": "文書中に出てくる表記そのまま（活用や助詞を含めない）",
    "kind": "下の種別コードのいずれか",
    "reading": "日本語の読み (かな)。不要なら空文字",
    "alias_of": "登録済みの語と同じものを指す別の呼び方なら、その見出し語。違うなら空文字",
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

#: 抽出のプロンプトに載せる本文の量。超える文書は sample_text() で間引く
EXTRACT_TEXT_CHARS = 12000


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
            "",
            "ただし、**この一覧の語と同じ対象を本文が別の呼び方で指している**とき"
            "（同じ人物の呼び名が場面によって変わる、略称・敬称付きで呼ばれる、など）は、"
            "`term` にその呼び方、`alias_of` に一覧の見出し語を入れて挙げてください。"
            "**別のエントリを立てずに別名として登録します** ——"
            "同じ人物が 2 つに割れると、相関図でも別人として並んでしまいます。",
        ]
    if source.strip():
        parts += ["", f"## 出典\n{source.strip()[:200]}"]
    body = sample_text((text or "").strip(), EXTRACT_TEXT_CHARS)
    parts += [
        "",
        "## 文書",
        *_gap_note(body),
        body,
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


def split_aliases(raw: list[dict], text: str) -> tuple[list[dict], list[dict]]:
    """「すでにある語の別の呼び方」と申告されたものを候補から分ける。

    返すのは (残りの候補, 別名の候補)。**別名は新しいエントリにしない** ——
    『吾輩は猫である』の「主人」と「苦沙弥先生」のように、同じ人物が呼び方ごとに
    別エントリへ割れると、本文のリンク先も相関図のノードも二重になる。

    行き先が 1 つに決まらないとき（同名がカテゴリ違いで併存しているなど）は
    **黙ってどれかに寄せず**、普通の候補として扱う。
    """
    haystack = (text or "").casefold()
    rest: list[dict] = []
    aliases: list[dict] = []
    for item in raw:
        term = str(item.get("term") or "").strip()
        target = str(item.get("alias_of") or "").strip()
        hits = store.find_by_surface(target) if term and target else []
        if len(hits) != 1 or term.casefold() not in haystack:
            rest.append(item)
            continue
        entry = hits[0]
        if term.casefold() in {s.casefold() for s in entry.surfaces}:
            continue                       # すでに別名として入っている
        aliases.append({
            "term": term,
            "alias_of": entry.term,
            "ref": entry.ref,
            "path_label": entry.path_label,
            "summary": entry.summary,
            "why": str(item.get("why") or "").strip(),
            "context": str(item.get("context") or "").strip()[:400],
        })
    return rest, aliases


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
    ask = partial(_generate, timeout=extract_timeout(limit))
    raw = await to_thread.run_sync(ask, prompt, abandon_on_cancel=True)
    parsed, aliases = split_aliases(parse_candidates(raw), text)
    kept, dropped = filter_candidates(parsed, text, limit=limit, kinds=kinds)
    return {"candidates": kept, "aliases": aliases, "dropped": dropped, "kinds": kinds}


# --------------------------------------------------------------------------- #
# フォルダ横断
# --------------------------------------------------------------------------- #

#: 1 ファイルあたり / 全体で AI に渡す文字数の下限と上限。
#: 全部渡すとプロンプトが膨らんで候補の質が落ちるので、全体から間引いて渡す
PER_FILE_CHARS = 3000
TOTAL_CHARS = 24000


def combine_documents(
    docs: list[tuple[str, str]], *, per_file: int = PER_FILE_CHARS, total: int = TOTAL_CHARS
) -> tuple[str, list[str], list[str]]:
    """複数文書を 1 つのプロンプト本文にまとめる。

    返すのは (まとめた本文, 使ったファイル, 入りきらなかったファイル)。
    切ったことは呼び出し側から UI に出す（黙って切らない）。

    ``per_file`` は**下限**として使う。**文書が少ないときは全体の枠を等分する** ——
    固定 3000 字のままだと、長編 1 冊だけのフォルダで冒頭 0.9% しか読めず、
    後の章に出てくる人物の関係は引きようがなかった。1 ファイルの中では
    ``sample_text()`` が全体に散らして採る（頭だけを読まない）。
    """
    bodies = [(label, (text or "").strip()) for label, text in docs]
    bodies = [(label, body) for label, body in bodies if body]
    if bodies:
        per_file = max(per_file, total // len(bodies))

    parts: list[str] = []
    used: list[str] = []
    skipped: list[str] = []
    budget = total
    for label, body in bodies:
        if budget <= 0:
            skipped.append(label)
            continue
        chunk = sample_text(body, min(per_file, budget))
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


def _first_context(docs: list[tuple[str, str]], surfaces: list[str]) -> str:
    """**別名も含めて**いちばん早く現れた表記の初出の場面を返す。

    見出し語だけで探すと、本文がもっぱら別の呼び方をしている人物
    （「主人」に対する「苦沙弥先生」など）で場面が取れない。
    """
    for _, text in docs:
        haystack = (text or "").casefold()
        found = [
            (index, surface)
            for surface in surfaces
            if surface and (index := haystack.find(surface.casefold())) >= 0
        ]
        if found:
            return context_up_to_first(text, min(found)[1])
    return ""


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
    ask = partial(_generate, timeout=extract_timeout(limit))
    raw = await to_thread.run_sync(ask, prompt, abandon_on_cancel=True)
    # 照合はプロンプトに載せた範囲ではなく全文に対して行う
    # (頭 3000 字しか渡していなくても、後ろに出てくる語なら採用してよい)
    haystack = "\n".join(text for _, text in docs)
    parsed, aliases = split_aliases(parse_candidates(raw), haystack)
    kept, dropped = filter_candidates(parsed, haystack, limit=limit, kinds=kinds)

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
        "aliases": aliases,
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

#: 関係を探すときに切り出す窓の大きさ・本数と、プロンプトに載せる上限。
#: 窓は「2 つ以上の登録語が一緒に出てくる」ところにだけ立てる
RELATION_WINDOW = 1500
RELATION_WINDOWS = 12
RELATION_TEXT_CHARS = 20000

def _surface_hits(text: str, entries: list[Entry]) -> list[tuple[int, str]]:
    """本文中の (位置, ref) を全部集める。**別名も数える。**

    「主人」と「苦沙弥先生」が同じ人物だと登録されていても、見出し語だけを
    探すと本文が別名で呼んでいる場面を丸ごと取り落とす。
    """
    haystack = (text or "").casefold()
    hits: list[tuple[int, str]] = []
    for entry in entries:
        for surface in entry.surfaces:
            needle = surface.casefold()
            if not needle:
                continue
            index = haystack.find(needle)
            while index >= 0:
                hits.append((index, entry.ref))
                index = haystack.find(needle, index + len(needle))
    hits.sort()
    return hits


def _densest_window(text: str, entries: list[Entry], width: int) -> str:
    """``text`` の中で**登録語がいちばん多く写っている** ``width`` 字を返す。

    切り詰めるときに頭から取ると、初出窓の前半（その語が出てくる前の場面）ばかりが
    残って関係が読めない。相手の名前が並んでいるところを残す。
    """
    if len(text) <= width:
        return text
    hits = _surface_hits(text, entries)
    if not hits:
        return text[-width:]           # 手掛かりが無ければ初出の直前を残す
    best, score = 0, -1
    for start, _ in hits:
        begin = max(0, min(start - width // 3, len(text) - width))
        seen = {ref for pos, ref in hits if begin <= pos < begin + width}
        if len(seen) > score:
            best, score = begin, len(seen)
    return text[best:best + width]


def first_scene_context(
    docs: list[tuple[str, str]],
    entries: list[Entry],
    *,
    budget: int = RELATION_TEXT_CHARS,
    focus: Entry | None = None,
) -> str:
    """各用語の初出の場面を、**全員ぶん入るように**切り詰めて繋ぐ。

    ``focus`` を渡すと**その語の初出の場面だけ**を、予算を丸ごと使って返す
    （1 語ぶんの下書きでは、他の語の初出を並べても相手が写らないので効かない）。
    見る範囲はその語の初出窓の中だけなので、ネタバレの約束は変わらない。

    前は初出窓（1 語あたり最大 2,400 字）をそのまま連結し、頭から ``budget`` で
    切っていた。19 語なら 41,497 字になり、**52% が黙って落ちて、後ろに並んだ
    主人・吾輩・迷亭・黒 が丸ごと消えていた**（実測）。関係は 2 語が揃って初めて
    書けるので、片方が消えた時点でその関係は絶対に出てこない。

    取り分は等分し、その中では ``_densest_window`` が**相手の名前も写っている
    ところ**を残す。**初出窓の外は見ない**ので、ネタバレの約束は変わらない。
    """
    wanted = [focus] if focus is not None else entries
    scenes = [(e, _first_context(docs, e.surfaces)) for e in wanted]
    scenes = [(e, text) for e, text in scenes if text.strip()]
    if not scenes:
        return ""

    # 見出しのぶんも予算に数える（数えないと上限を超え、最後の語が切られる）
    heads = [f"### {e.term} の初出\n" for e, _ in scenes]
    budget -= sum(len(h) + 2 for h in heads)

    # 短い場面が余らせたぶんを、長い場面に配り直す（等分だと余りが捨てられる）
    share = max(budget // len(scenes), 200)
    spare = sum(share - len(t) for _, t in scenes if len(t) < share)
    long_ones = sum(1 for _, t in scenes if len(t) > share)
    if long_ones:
        share += spare // long_ones

    parts = [
        head + _densest_window(text, entries, share)
        for head, (_, text) in zip(heads, scenes)
    ]
    return "\n\n".join(parts)


def cooccurrence_context(
    docs: list[tuple[str, str]],
    entries: list[Entry],
    *,
    window: int = RELATION_WINDOW,
    limit: int = RELATION_WINDOWS,
    focus: Entry | None = None,
) -> str:
    """登録済みの用語が**一緒に出てくる**ところだけを切り出して繋ぐ。

    関係が書いてあるのは、2 人の名前が近くに並ぶ場面であって文書の冒頭ではない。
    頭から一定量渡す作りだと、長編では関係の書かれた場面が一度も AI に届かず
    「関係が見つかりませんでした」で終わる（それがこの関数を足した理由）。

    窓は**登場する語の種類が多い順**に採り、重なるものは捨てる。同じ場面を
    何度も渡しても新しい関係は出てこないため。

    ``focus`` を渡すと**その語が写っている窓だけ**を採る。1 語ぶんの下書きで
    他人どうしの場面を渡しても、出てくるのは頼んでいない関係になる。
    その語が誰とも並ばないときだけ、1 語しか写っていない窓へ落とす
    （何も渡さないより、その語の場面を見せたほうが読み取れる）。
    """
    spans: list[tuple[int, int, str, int]] = []      # (種類数, -位置, ラベル, 位置)
    hits_by_doc = [(label, text, _surface_hits(text, entries)) for label, text in docs]

    def collect(least: int) -> None:
        spans.clear()
        for order, (label, _, hits) in enumerate(hits_by_doc):
            end = 0
            for i, (start, _) in enumerate(hits):
                while end < len(hits) and hits[end][0] - start <= window:
                    end += 1
                names = {ref for _, ref in hits[i:end]}
                if focus is not None and focus.ref not in names:
                    continue
                if len(names) >= least:
                    spans.append((len(names), -(order * 10**9 + start), label, start))

    collect(2)
    if not spans and focus is not None:
        collect(1)

    picked: list[tuple[str, int]] = []
    for _, _, label, start in sorted(spans, reverse=True):
        if len(picked) >= limit:
            break
        if any(l == label and abs(s - start) < window for l, s in picked):
            continue
        picked.append((label, start))

    by_doc = {label: text for label, text in docs}
    read_order = {label: i for i, (label, _) in enumerate(docs)}
    parts: list[str] = []
    for label, start in sorted(picked, key=lambda p: (read_order.get(p[0], 0), p[1])):
        text = by_doc.get(label, "")
        head = text.rfind("\n", max(0, start - 200), start)
        begin = head + 1 if head >= 0 else start
        parts.append(f"### {label}\n{text[begin:begin + window].strip()}")
    return _GAP_JOIN.join(parts)[:RELATION_TEXT_CHARS]


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
    focus: Entry | None = None,
) -> str:
    """登録済みの用語どうしの関係を挙げさせるプロンプト。

    ``focus`` を渡すと**その語が必ず一方の端になる関係だけ**を頼む。
    頼まずに出力側で落とすだけにすると、上限の大半を他人どうしの関係が食って
    肝心の語の関係が数本しか残らない（`filter_relations` でも落とすが、
    **落とす前に頼む**のが要る）。
    """
    listed = "\n".join(
        f"- {e.term}"
        + (f"（本文での別の呼び方: {'、'.join(e.aliases)}）" if e.aliases else "")
        + (f" — {e.summary}" if e.summary else "")
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
    ]
    if focus is not None:
        parts += [
            "",
            f"## かならず「{focus.term}」の関係にすること",
            f"挙げてよいのは、**`from` か `to` のどちらかが「{focus.term}」である関係だけ**です。"
            "それ以外の組（他の用語どうしの関係）は、本文に書いてあっても挙げないでください。",
            f"向きはどちらでも構いません（「{focus.term}」から見た関係でも、"
            f"相手から「{focus.term}」を見た関係でも、読み取れたほうで書いてください）。",
        ]
    parts += [
        "",
        "## 対象の用語",
        "**この一覧にあるものだけ**を `from` と `to` に使ってください。"
        "一覧に無い語を勝手に足さないこと（関係だけを作る作業で、用語は作りません）。",
        "**別の呼び方が添えてあるものは同じ相手を指します。**"
        "本文がその呼び方をしていても、`from` / `to` には**見出しの表記**を書いてください。",
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
        "- **一覧の語を上から順に見て、組になりうるものを取りこぼさないこと。**"
        "同じ場に居合わせる、話題にする、呼び方が決まっている（飼い主と猫、友人、"
        "師弟、家族、隣人）といった関係は、本文に書いてあれば挙げる",
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
    # 一言 (`label` / `back`) だけに効かせる。`from` / `to` は一覧の表記と
    # 一致しないと `filter_relations()` が落とすので、崩されると 1 本も残らない
    style_block = build_style_block(style(), "`label` `back`", "`from` `to` `rank` `reveal`")
    if style_block:
        parts += [
            "",
            style_block,
            "一言の長さ（10 字程度）は文体を変えても守ること"
            "（相関図の線の上に出るので、長いと畳まれて読めません）。",
        ]
    body = (text or "").strip()[:RELATION_TEXT_CHARS]
    parts += [
        "",
        "## 文書",
        *_gap_note(body),
        body,
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


def _resolve_side(
    name: str, entries: list[Entry], *, origin: Entry | None = None, focus: Entry | None = None
) -> Entry | None:
    """関係の片側 1 件を解決する。**曖昧さの吸収は `relations.resolve` 任せ。**

    足しているのは 1 つだけ —— **``focus`` の表記なら、それは ``focus``**。
    用語ページから頼んだときは、どのエントリの話かが画面で決まっている
    （揺れを吸収する側には分からない情報なので、ここで渡す）。

    これが無いと、**別のエントリが同じ表記を別名に持っているだけで下書きが
    丸ごと落ちた** —— 「寒月」のページから頼んだのに、「水島」が別名として
    「寒月」を持っているせいで `resolve` が決めきれず、その語の関係が 1 本も
    残らなかった（実際に踏んだ。同じ人物が 2 エントリに割れている辞書では普通に起きる）。
    """
    if focus is not None:
        key = (name or "").strip().casefold()
        if key and any(s.casefold() == key for s in focus.surfaces):
            return focus
    return relations.resolve(name, entries, origin=origin).entry


def filter_relations(
    raw: list[dict],
    entries: list[Entry],
    *,
    scope: list[Entry],
    limit: int,
    allow_reveal: bool = True,
    focus: Entry | None = None,
) -> tuple[list[dict], list[dict]]:
    """AI が挙げた関係をそのまま信じずに整える。

    候補語のときと同じ発想で、**通すと壊れるもの**を先に落とす:

    - 一覧に無い用語（AI は平気で本文中の別の名前を返す）
    - 自分自身への関係（図に描けない）
    - すでに書かれている組（重複した辺になる）
    - 同じ組の 2 度目（向きを変えて 2 行書いてくることがある）
    - ``focus`` を指定したのに、その語が端に居ない組（頼んだものと違う）

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

        from_entry = _resolve_side(src, entries, focus=focus)
        if from_entry is None:
            dropped.append({**record, "reason": f"「{src}」は登録された用語ではありません"})
            continue
        to_entry = _resolve_side(rel.to, entries, origin=from_entry, focus=focus)
        if to_entry is None:
            dropped.append({**record, "reason": f"「{rel.to}」は登録された用語ではありません"})
            continue
        if from_entry.ref == to_entry.ref:
            dropped.append({**record, "reason": "自分自身への関係"})
            continue
        if focus is not None and focus.ref not in (from_entry.ref, to_entry.ref):
            dropped.append({**record, "reason": f"「{focus.term}」の関係ではありません"})
            continue

        key = _pair_key(from_entry.ref, to_entry.ref)
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
        record["from_ref"] = from_entry.ref
        record["from_term"] = from_entry.term
        record["from_url"] = entry_url(from_entry)
        record["to_ref"] = to_entry.ref
        record["to_term"] = to_entry.term
        record["to_url"] = entry_url(to_entry)
        kept.append(record)

    return kept, dropped


async def draft_relations(
    entries: list[Entry],
    docs: list[tuple[str, str]],
    *,
    scope: list[Entry] | None = None,
    limit: int = 20,
    spoiler: str = "full",
    focus: Entry | None = None,
) -> dict:
    """登録済みの用語どうしの関係を下書きする。保存はしない。

    ``entries`` は辞書全体（参照解決に使う）、``scope`` は関係を探す対象。
    ``focus`` を渡すと**その語が一方の端になる関係だけ**を探す（相手は
    ``scope`` の中から選ばれる —— 関係は 2 語が揃って初めて書けるので、
    「1 語だけ」を範囲にはできない）。
    """
    scope = scope if scope is not None else entries
    if len(scope) < 2:
        raise AIError("関係を探すには、同じ範囲に 2 語以上の登録が要ります")

    if spoiler == "first":
        # 各用語の初出の場面だけを渡す。全文を渡すと、後で明かされる関係
        # （正体・血縁）が図に出てしまう。**全員ぶんが入るように配分する** ——
        # 頭から切っていたころは中心人物が丸ごと落ちて、関係が出ようが無かった
        text = first_scene_context(docs, scope, focus=focus)
    else:
        # **登録済みの語が一緒に出てくる場面**を探して渡す。関係が書いてあるのは
        # そこであって、文書の冒頭ではない。頭から一定量を渡していたころは、
        # 長編で関係の場面が一度も届かず「見つかりませんでした」で終わっていた
        text = cooccurrence_context(docs, scope, focus=focus)
        # **1 語ぶんのときは頭出しに落とさない。** その語が写っていない冒頭を
        # 渡しても、頼んだ関係は絶対に出てこない（他人どうしの話が返るだけ）
        if not text.strip() and focus is None:
            text, _, _ = combine_documents(docs)

    if not text.strip():
        raise AIError(
            f"「{focus.term}」が本文に見つかりません" if focus is not None
            else "読める本文がありません"
        )

    prompt = build_relations_prompt(
        scope,
        text,
        existing=existing_pairs(entries, scope),
        limit=limit,
        spoiler=spoiler,
        focus=focus,
    )
    # **本数に応じた持ち時間で呼ぶ。** 既定の 180 秒だと 11 本あたりで必ず溢れる
    ask = partial(_generate, timeout=relations_timeout(limit))
    raw = await to_thread.run_sync(ask, prompt, abandon_on_cancel=True)
    kept, dropped = filter_relations(
        parse_candidates(raw),
        entries,
        scope=scope,
        limit=limit,
        allow_reveal=spoiler != "first",
        focus=focus,
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
    raw = await to_thread.run_sync(_generate, prompt, abandon_on_cancel=True)
    data = parse_draft(raw)
    data.setdefault("term", term)
    if source and not data.get("source"):
        data["source"] = source
    return EntryDraft.model_validate(data)
