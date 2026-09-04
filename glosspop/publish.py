"""公開ページを書き出す（GitHub Pages 用の 1 枚）。

**`core` には置けない** —— 出力先を知る必要があるから。中身の組み立ては
`core` に任せて（`card` が 1 枚に載せるもの、`booklet` が辞書の 1 枚、
`render` が Markdown → HTML）、ここがやるのは**どこへ何という名前で書くか**だけ。

守っていること 5 つ:

- **既定の出力先を持たない。** 決めていなければ書かない（`config.publish_dir()`
  が ``None`` を返す）。勝手にどこかへ書くと、置いた覚えのないフォルダに
  ファイルが増える —— 「開いただけのフォルダを汚さない」と同じ約束
- **書く前に何が変わるかを出す**（`plan()`）。上書きになるファイルを名前で返す。
  取り込みの `plan()`・統合の下見・控えの `here` と同じ扱いで、
  **「入れ替わります」の一言だけで押させない**
- **名前は組み立てた結果を検査する。** 画面から来た文字列なので、出力先の中に
  収まっていることを最後に必ず確かめる（`sites.py` と `archive._backup_path()`
  と同じ規則）
- **メタ画像の URL は絶対にする。** 相対だと**ページは正しく出るのにカードだけ
  黙って出ない**（プロジェクトサイトは ``/<repo>/`` の下に出るため）。
  基準 URL が無いまま書いたことは、返り値で必ず知らせる
- **commit も push もしない。書くだけ。** 更新が「隣に展開して、そちらを起動して
  くださいと言うだけ」で通してきた線と揃える
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime
from urllib.parse import quote
from pathlib import Path

from . import config
from .core import booklet, imagefmt, render
from .core import card as cardmod
from .core.models import Entry

#: 書き出すファイル。**アンダースコアで始めない** —— GitHub Pages の Jekyll が
#: `_` で始まる名前を無視するので、静かに欠ける
NAME_HTML = "index.html"
NAME_CARD = "card.png"

#: Jekyll を通さない印。**出力先の根**に置く（そこが公開の根とは限らないので
#: 気休めではあるが、`{{ }}` を含む辞書本文が化けるのを防げることがある）
NAME_NOJEKYLL = ".nojekyll"

#: フォルダ名に使えない字。Windows の禁止字と、パスを離れるためのもの
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

#: 名前の上限。長すぎるパスは Windows で書けない
NAME_MAX = 60

#: 断りに名前を並べる上限（`archive.MAX_REPORTED` と同じ考え方で、**切ったことは
#: 件数で分かるようにする**）
MAX_REPORTED = 5

#: クローラが取りに来るページの上限。**X の文書にそう書いてある**
#: （→ [docs/x-cards.md](../docs/x-cards.md)）—— 超えると
#: "Fetching the page failed because the response is too large" でカードが出ない。
#: 本文のページは**辞書をページに埋め込む**ので、長編では届きうる。
#: **切らずに、超えたことを返す**（黙って欠けたものを渡さない、はここでも同じ）
CRAWLER_MAX_BYTES = 2 * 1024 * 1024

#: カードの上限。**X が受け取れるのは 5MB まで**（実測: 25 語の 2 倍描画で 390KB
#: なので十分に余裕がある）。超えるものは貼っても出ないので、書く前に断る
CARD_MAX_BYTES = 5 * 1024 * 1024


#: カードの印の形。**こちらが作ったもの以外は受け付けない**（sha256 の頭）
_STAMP = re.compile(r"[0-9a-f]{1,32}")


class PublishError(Exception):
    """書き出せない理由。**呼ぶ側がそのまま画面に出せる文で投げる。**"""


def safe_stamp(stamp: str) -> str:
    """カードの印を確かめる。**空は空のまま、形が違えば断る。**

    この値は ``og:image`` の URL に**属性として**入る。作っているのはこちら
    (`write_card()` が返す sha256 の頭) だが、**受け取るのはリクエスト本文**なので
    名乗りでしかない —— 形を決め打ちで照合するのがいちばん確か。
    """
    value = (stamp or "").strip()
    if not value:
        return ""
    if not _STAMP.fullmatch(value):
        raise PublishError("カードの印が正しくありません")
    return value


def safe_name(name: str) -> str:
    """フォルダ名にできる形へ落とす。**空になるものは受け付けない。**

    日本語はそのまま通す（URL では percent-encoded になるが配信はできる）。
    落とすのは**パスを離れられる字**と、Windows で書けない字だけ。
    """
    cleaned = _BAD_NAME.sub("", (name or "").strip())
    cleaned = cleaned.replace("..", "").strip(". ").strip()
    cleaned = cleaned.lstrip("_").strip()   # Jekyll が無視する名前を作らない
    cleaned = cleaned[:NAME_MAX].strip()
    if not cleaned:
        raise PublishError("公開するフォルダの名前が空です")
    return cleaned


def site_dir(name: str) -> Path:
    """書き出し先。**組み立てた結果が出力先の中にあることを最後に確かめる。**"""
    root = config.publish_dir()
    if root is None:
        raise PublishError(
            "公開先のフォルダが決まっていません（⚙ の「公開」で決めてください）"
        )
    target = (root / safe_name(name)).resolve()
    if root not in target.parents:
        raise PublishError("公開先の外に出る名前です")
    return target


def _page_url(base: str, name: str, *, file: str = "") -> str:
    """絶対 URL を組む。基準が無ければ空（**相対に落とさない**）。

    **名前は percent-encode する。** フォルダ名に日本語を許しているので、生のまま
    ``og:url`` に載せるとクローラが取り違えうるし、配信されている URL
    （GitHub Pages は encode された形で返す）とも食い違う。
    """
    if not base:
        return ""
    # **ファイル名も encode する。** 本文のページの名前は元のファイル名から作るので
    # 日本語になりうる（`doc_slug()` は禁止文字を落とすだけ）—— 生のまま
    # `og:url` に載せると、配信されている URL と食い違う
    tail = f"/{quote(file, safe='/')}" if file else "/"
    return f"{base.rstrip('/')}/{quote(safe_name(name))}{tail}"


#: `.git/config` の remote から `<user>/<repo>` を取り出す。**GitHub だけ見る**
#: —— Pages の URL の形が分かっているのはここだけで、他所（GitLab 等）は形が違う。
#: https と ssh の両方を受ける
_GIT_REMOTE = re.compile(
    r"url\s*=\s*(?:https://github\.com/|git@github\.com:)([^/\s]+)/([^/\s]+?)(?:\.git)?\s*$",
    re.MULTILINE | re.IGNORECASE,
)

#: `.git` を探して遡る段数。書き出し先はリポジトリの中の 1〜2 段下（`docs/` など）
#: にあることが多い
_GIT_SEARCH_DEPTH = 4


def guess_base_url(target: Path | None = None) -> str:
    """書き出し先から公開先 URL を**読む**。読めなければ空。

    **推測ではなく、置いてあるものを読む** —— `.git/config` の remote と、
    リポジトリ直下の `CNAME`。決め打ちで組み立てると、外したときに
    **ページは出るのにカードだけ黙って出ない**（この repo が 4 度誤診した形）。
    だから**候補として出し、確定するのは人**。

    読めないものははっきりしている: **Pages が有効か、どのブランチ・どのフォルダ
    から配信しているか**は `.git` に書かれていない。GitHub 以外も形が違う。
    """
    try:
        current = (target or config.publish_dir() or Path()).resolve()
    except OSError:
        return ""
    for _ in range(_GIT_SEARCH_DEPTH + 1):
        if (current / ".git").is_dir():
            break
        parent = current.parent
        if parent == current:
            return ""
        current = parent
    else:
        return ""
    # **独自ドメインが最優先。** CNAME があればそれが配信されている名前
    try:
        cname = (current / "CNAME").read_text(encoding="utf-8").strip()
    except OSError:
        cname = ""
    if cname and config.clean_base_url(f"https://{cname}/"):
        return f"https://{cname}/"
    try:
        conf = (current / ".git" / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    found = _GIT_REMOTE.search(conf)
    if not found:
        return ""
    user, repo = found.group(1), found.group(2)
    # **ユーザ / 組織ページはリポジトリ名が付かない**（`<user>.github.io` がそれ）
    if repo.lower() == f"{user.lower()}.github.io":
        url = f"https://{repo}/"
    else:
        url = f"https://{user}.github.io/{repo}/"
    return config.clean_base_url(url)


def plan(name: str) -> dict:
    """書く前の下見。**上書きになるものを名前で返す。**"""
    target = site_dir(name)
    base = config.publish_base_url()
    return {
        "dir": str(target),
        "root": str(config.publish_dir() or ""),
        "base_url": base,
        "url": _page_url(base, name),
        "exists": target.exists(),
        "files": [
            {"name": one, "overwrite": (target / one).exists()}
            for one in (NAME_HTML, NAME_CARD)
        ],
        # 基準 URL が無いとカードだけ出ない。**黙らない**
        "warnings": _warnings(name, base),
    }


def _warnings(name: str, base: str) -> list[str]:
    """**押す前に言っておくこと。** どちらも「黙って効かない」種類の話。"""
    out: list[str] = []
    if not base:
        out.append("公開先の URL が決まっていないので、X などのカードに画像が出ません")
    if not safe_name(name).isascii():
        # **実測**: 生の日本語 URL を素で取りに行くと GitHub Pages は 404 を返す
        # （ブラウザは送る前に percent-encode するので、打ち込むぶんには通る）。
        # 貼った先がどちらの形で取りに来るかは**こちらで決められない**ので、
        # 名前を英数字にできるならそのほうが確実
        out.append(
            "フォルダ名に日本語が入っています。貼った先が URL を encode せずに"
            "取りに行くと 404 になり、カードが出ません（英数字の名前にすると確実です）"
        )
    return out


def _too_big(documents: list[dict]) -> list[str]:
    """クローラの上限を超えたページ。**切らずに、超えたことを言う。**

    2 MB は X の文書に書いてある値（→ docs/x-cards.md）。本文のページは辞書を
    ページに埋め込むので、長編で届きうる —— **ページ自体は正しく読めるのに
    カードだけ出ない**ので、言わないと原因を追えない。
    """
    over = [d for d in documents if d.get("bytes", 0) > CRAWLER_MAX_BYTES]
    if not over:
        return []
    names = "、".join(d["title"] for d in over[:MAX_REPORTED])
    more = f" ほか {len(over) - MAX_REPORTED} 件" if len(over) > MAX_REPORTED else ""
    return [
        f"{len(over)} 件の本文が {CRAWLER_MAX_BYTES // 1024 // 1024} MB を超えました"
        f"（{names}{more}）。ページは読めますが、X などのカードは出ません"
    ]


_TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{note}">
{og}
<style>
:root {{ --bg:#fbfbfa; --fg:#1c1c1a; --dim:#63635e; --line:#e3e1dc; --accent:#0f766e; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#17181a; --fg:#e7e7e4; --dim:#a6a8a5; --line:#2c2f36; --accent:#5eead4; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin:0; padding:0 20px 72px; background:var(--bg); color:var(--fg);
  font-family:"Yu Gothic UI","Hiragino Sans","Noto Sans JP",system-ui,sans-serif;
  line-height:1.8; }}
.wrap {{ max-width:820px; margin:0 auto; }}
header {{ padding:48px 0 24px; border-bottom:1px solid var(--line); }}
h1 {{ margin:0 0 8px; font-size:2rem; }}
.note {{ margin:0; color:var(--dim); }}
.card {{ display:block; width:100%; height:auto; border-radius:12px; margin:24px 0 0; }}
.counts {{ margin:16px 0 0; color:var(--dim); font-size:.95rem; }}
main h2 {{ margin:40px 0 4px; padding-top:12px; border-top:1px solid var(--line);
  font-size:1.3rem; }}
main h3 {{ margin:24px 0 2px; font-size:1.05rem; color:var(--accent); }}
main p {{ margin:.2em 0; }}
footer {{ margin:56px 0 0; padding-top:20px; border-top:1px solid var(--line);
  color:var(--dim); font-size:.88rem; }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>{title}</h1>
  <p class="note">{note}</p>
  <img class="card" src="{card}" alt="{title}" width="1200" height="630">
  <p class="counts">{counts}</p>
</header>
<main>
{docs}
{body}
</main>
<footer><p>{generated} — GlossPop で書き出した用語辞書</p></footer>
</div>
</body>
</html>
"""


def _attr(value: str) -> str:
    """属性に入れる値。**組み立てる直前で必ず通す。**

    ここは**人に配るページ**を作るところなので、入れる値は全部「名乗り」として
    扱う（`POST /api/persona` が Content-Type を信じないのと同じ）。素で差し込むと、
    引用符ひとつで**配ったページにタグを足せる**。
    """
    return html.escape(value or "", quote=True)


def _card_size(name: str) -> tuple[int, int] | None:
    """置いてあるカードの大きさ。**実ファイルから読む**（決め打ちしない）。

    読む口は `core.imagefmt.size()` **1 か所**（顔・地図・用語と同じ）。
    まだ置かれていなければ ``None`` —— **書いていない値をタグに出さない**。
    """
    try:
        data = (site_dir(name) / NAME_CARD).read_bytes()
    except (OSError, PublishError):
        return None
    got = imagefmt.size(data)
    return (int(got[0]), int(got[1])) if got else None


def _meta(
    *,
    title: str,
    note: str,
    site: str,
    page_url: str,
    image_url: str,
    size: tuple[int, int] | None = None,
    image_type: str = "image/png",
    imageless_card: str = "",
) -> str:
    """カードのタグ。**基準 URL が無ければ画像のタグを書かない。**

    相対 URL の ``og:image`` は無視されるので、書いても出ない。**書かない**ほうが
    「出ないのはなぜか」を追いやすい（半端なタグは「効いているはず」に見える）。

    辞書の 1 枚 (`_og`) と本文のページ (`_doc_og`) が**同じ組み立てを通る** ——
    写しを作ると、`twitter:*` を足したときのように片方だけ古くなる。

    ``imageless_card`` は**絵が無いときに書く `twitter:card`**。辞書の 1 枚では
    空（＝書かない）。本文のページは ``summary`` を書く —— 絵の無い文書でも
    見出しだけのカードは出したい。
    """
    lines = [
        '<meta property="og:type" content="article">',
        f'<meta property="og:site_name" content="{_attr(site)}">',
        f'<meta property="og:title" content="{_attr(title)}">',
        f'<meta property="og:description" content="{_attr(note)}">',
        # **`twitter:*` も書く。** 仕様上は `og:*` に落ちることになっているが、
        # X の見せ方は何度も変わっていて**こちらから確かめる手段が無い**。
        # 書いておく代金はタグ 3 行で、落ちたときの代金は「カードが出ない」
        f'<meta name="twitter:title" content="{_attr(title)}">',
        f'<meta name="twitter:description" content="{_attr(note)}">',
    ]
    if page_url:
        lines.append(f'<meta property="og:url" content="{_attr(page_url)}">')
    if image_url:
        # **`?v=` で URL を変える。** X はカードを URL ごとに覚えていて、確実に
        # 更新させる手段が無い（旧 Card Validator は廃止）。中身から作った印なので、
        # 変わっていなければ URL も変わらない（無駄なキャッシュ切れを起こさない）
        src = _attr(image_url)
        lines.append(f'<meta property="og:image" content="{src}">')
        lines.append(f'<meta property="og:image:alt" content="{_attr(title)}">')
        lines.append(f'<meta name="twitter:image" content="{src}">')
        if size:
            # **大きさを書く。** 効いたかどうかは確かめられていない（→ CLAUDE.md の
            # 「X に貼ったときの見え方」。1 度これを原因だと誤診した）。害は無いので
            # 残してあるが、**実物から読む** —— 決め打ちにすると、カードの作りを
            # 変えたときに黙ってずれる
            lines.append(f'<meta property="og:image:type" content="{_attr(image_type)}">')
            lines.append(f'<meta property="og:image:width" content="{size[0]}">')
            lines.append(f'<meta property="og:image:height" content="{size[1]}">')
        lines.append(f'<meta name="twitter:image:alt" content="{_attr(title)}">')
        lines.append('<meta name="twitter:card" content="summary_large_image">')
    elif imageless_card:
        # 絵の無い文書でも、見出しだけの小さいカードは出したい
        lines.append(f'<meta name="twitter:card" content="{_attr(imageless_card)}">')
    return "\n".join(lines)


def _og(title: str, note: str, name: str, base: str, stamp: str,
        size: tuple[int, int] | None = None) -> str:
    """辞書の 1 枚のタグ。絵は `card.png` **1 枚だけ**。"""
    image = _page_url(base, name, file=NAME_CARD)
    return _meta(
        title=title,
        note=note,
        site=name,
        page_url=_page_url(base, name),
        image_url=f"{image}?v={stamp}" if image else "",
        size=size,
    )


def _doc_og(
    *, title: str, note: str, site: str, base: str, name: str,
    file: str, image: str, stamp: str, size: tuple[int, int] | None,
) -> str:
    """本文のページ 1 枚ぶんのタグ。

    **絵はその文書のもの**（辞書の 1 枚とは別）。無ければ `og:image` を書かず、
    `twitter:card` は ``summary`` に落とす —— **辞書のカードで代用しない**。
    全部の文書が同じ絵になるうえ、貼った相手には「この文書の絵」に見える。
    """
    src = _page_url(base, name, file=f"{DIR_DOCS}/{image}") if image else ""
    return _meta(
        title=title,
        note=note,
        site=site,
        page_url=_page_url(base, name, file=f"{DIR_DOCS}/{file}"),
        image_url=f"{src}?v={stamp}" if src else "",
        size=size,
        image_type=imagefmt.MIME_TYPES.get(Path(image).suffix.lower(), "image/png"),
        imageless_card="summary",
    )


def _doc_list(documents: list[dict]) -> str:
    """本文へのリンク。**1 つも無ければ節ごと出さない**（空の見出しを作らない）。"""
    if not documents:
        return ""
    rows = "\n".join(
        f'<li><a href="{_attr(one["file"])}">{_attr(one["title"])}</a>'
        f' <span class="doc-terms">{one["terms"]} 語</span></li>'
        for one in documents
    )
    return (
        '<section class="docs"><h2>本文</h2>'
        "<p>下線の語にふれると意味が出ます。</p>"
        f"<ul>\n{rows}\n</ul></section>"
    )


def build_page(
    entries: list[Entry],
    *,
    name: str,
    card_stamp: str = "",
    generated: str = "",
    documents: list[dict] | None = None,
) -> str:
    """公開ページ 1 枚ぶんの HTML。**中身の正は `core`。**"""
    made = cardmod.build(entries, name=name)
    day = generated or datetime.now().strftime("%Y-%m-%d")
    # **エスケープは出す直前の 1 回だけ。** 先に escape したものを `_og()` へ渡すと
    # そこでもう一度かかって `&amp;lt;` になる（二重にかけないこと自体が規則）
    return _TEMPLATE.format(
        title=_attr(made.title),
        note=_attr(made.note),
        og=_og(made.title, made.note, name, config.publish_base_url(),
               safe_stamp(card_stamp) or "1", _card_size(name)),
        card=NAME_CARD,
        counts=f"{made.total} 語 ・ {made.links} 本の関係",
        docs=_doc_list(documents or []),
        body=render.md_to_html(booklet.build(entries, title=made.name, generated=day)),
        generated=day,
    )


def write_card(name: str, data: bytes) -> dict:
    """メタ画像を置く。**返すのは中身から作った印**（URL の `?v=` に使う）。

    **中身を見分けてから書く**（`core.imagefmt.sniff()` の 1 か所）。送られてくる
    Content-Type は名乗りでしかなく、ここは**そのまま配られる置き場所**なので、
    顔 (`POST /api/persona`) と同じ扱いにする。**PNG だけ**通すのは、カードが
    こちらの生成物だから（人が選んだ画像を置く口ではない）。
    """
    if len(data) > CARD_MAX_BYTES:
        raise PublishError("カードの画像が大きすぎます")
    if imagefmt.sniff(data) != ".png":
        raise PublishError("カードの画像が PNG ではありません")
    target = site_dir(name)
    target.mkdir(parents=True, exist_ok=True)
    (target / NAME_CARD).write_bytes(data)
    return {
        "path": str(target / NAME_CARD),
        "bytes": len(data),
        "stamp": hashlib.sha256(data).hexdigest()[:8],
    }


def write_site(
    entries: list[Entry],
    *,
    name: str,
    card_stamp: str = "",
    pages: list[dict] | None = None,
) -> dict:
    """ページを書く。**画像は別の口**（`write_card`）。

    ``pages`` を渡すと本文のページも書く（見た目と仕掛けは `assets/` に 1 回だけ）。
    渡さなければ辞書の 1 枚だけ —— **貼り付けや URL から読んでいるときは本文を
    読み直せない**ので、そこは呼ぶ側が決める（相関図の `?doc=` と同じ判断）。
    """
    card_stamp = safe_stamp(card_stamp)
    target = site_dir(name)
    target.mkdir(parents=True, exist_ok=True)
    # **本文のページにもカードのタグを書く**ので、基準 URL はここで解決して渡す
    # （`_page_url()` は基準が無ければ空を返す ＝ 画像のタグを書かない）
    base = config.publish_base_url()

    documents: list[dict] = []
    if pages:
        write_assets(name)
        documents = write_documents(name, pages, base=base)

    (target / NAME_HTML).write_text(
        build_page(entries, name=name, card_stamp=card_stamp, documents=documents),
        encoding="utf-8",
        newline="\n",
    )

    root = config.publish_dir()
    marker = (root / NAME_NOJEKYLL) if root else None
    if marker is not None and not marker.exists():
        marker.write_text("", encoding="utf-8")

    return {
        "dir": str(target),
        "html": str(target / NAME_HTML),
        "documents": documents,
        "url": _page_url(base, name),
        "card_url": (
            f"{_page_url(base, name, file=NAME_CARD)}?v={card_stamp}"
            if base and card_stamp else ""
        ),
        "nojekyll": str(marker) if marker else "",
        "warnings": [*_warnings(name, base), *_too_big(documents)],
    }


# --------------------------------------------------------------------------- #
# 本文のページ（辞書リンクと吹き出しが、サーバ無しで効く 1 枚）
#
# **GlossPop を持っていない人が読む**ので、通信はしない。辞書はページに埋め込み、
# `base.js` の `api()` 1 か所だけを差し替える —— **popup.js の写しを作らない**ため
# （吹き出しの挙動は本物がそのまま動く）。
#
# **見た目と仕掛けは共有する。** `style.css` は 82KB あるので、文書ごとに埋めると
# 20 文書で 2MB 近く重複する。`assets/` に 1 回だけ書いて全ページから参照する。
# 辞書だけはページごとに違うので、そこだけインラインで持たせる。

#: 外へ出す前に落とす項目。**手元のフルパスとサーバの URL が混ざっている** ——
#: 渡した相手には出ないどころか、こちらの環境が漏れる
STRIP_KEYS = ("path", "url", "persona_url", "image_url", "source", "first_file")

DIR_ASSETS = "assets"
DIR_DOCS = "docs"
NAME_CSS = "reader.css"
NAME_JS = "reader.js"

#: 読み物用の上書き。アプリの枠組み（topbar・パネル）が無いぶんだけ
_READER_CSS = """
/* --- ここから下は公開ページ用の上書き（GlossPop が書き足したもの） --- */
body { margin: 0; }
.reader { max-width: 44rem; margin: 0 auto; padding: 2.5rem 1.25rem 6rem; }
.reader-head { border-bottom: 1px solid var(--border); padding-bottom: .75rem;
  margin-bottom: 2rem; }
.reader-head h1 { margin: 0 0 .35rem; }
.reader-note { color: var(--fg-faint); font-size: var(--fs-12); margin: 0; }
/* **飛び先の無いリンクを出さない。** 辞書ページはこのサイトに無い（冊子が
   リンクを張らなかったのと同じ理由 —— 切れたリンクは「その語が無い」に見える）。
   吹き出しの中の語を辿る道は残っている */
.pop-foot a, .pop-item-link { display: none; }
.pop-foot { justify-content: flex-end; }
"""

#: 埋め込んだ辞書を返すだけの `api()`。**通信はしない**
_SHIM = """// GlossPop が書き出した読み物。**通信はしない。**
// 吹き出しの中身は popup.js（本物）がそのまま描き、辞書だけをここで差し替える。
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// **辞書は属性から読む。** インラインの script に JSON を置くと、辞書の本文に
// `</script>` が現れただけでそこでスクリプトが終わる（人が書くものなので起こる）。
// 属性なら HTML のエスケープ 1 つで済み、そこは `_attr()` に任せてある。
const DICT = JSON.parse(document.getElementById("gloss-data")?.dataset.dict || "{}");
async function api(url) {
  const term = decodeURIComponent((url.split("term=")[1] || ""));
  return DICT[term] || { term, found: false, count: 0, entries: [] };
}
"""


def unlink(html: str) -> str:
    """自動リンクの ``href`` を外す。**押せるのに飛び先が無い、が最悪。**

    素のクリックは `popup.js` が `preventDefault()` するが、**Ctrl / 中クリックは
    「別タブで開く」として通す**（本体では正しい）。書き出したページには辞書
    ページが無いのでそこだけ 404 になる —— `tabindex` に置き換えて、キーボードの
    焦点（`focusin` で吹き出しが出る）は残す。
    """
    return re.sub(r'href="/glossary[^"]*"', 'tabindex="0"', html or "")


def strip_entry(payload: dict) -> dict:
    """外へ出せる形にする。**落とすものと伏せるものが別々にある。**

    - `STRIP_KEYS` は**こちらの環境が漏れる**もの
    - 判明位置つきの関係は**伏せる**（相関図・カードと同じ約束）。伏せた本数は返す
    - `backlinks` は相手側の URL を持つので落とす
    - 吹き出しの中の本文にもリンクが入っている（`definition_html` は annotate
      済み）ので、本文と同じ扱いにする
    """
    out = {k: v for k, v in payload.items() if k not in STRIP_KEYS}
    rels = out.get("relations_resolved") or []
    kept = [r for r in rels if not str(r.get("reveal") or "").strip()]
    out["relations_resolved"] = kept
    out["hidden"] = len(rels) - len(kept)
    out["backlinks"] = []
    out["definition_html"] = unlink(out.get("definition_html", ""))
    out["summary_html"] = unlink(out.get("summary_html", ""))
    out["examples_html"] = [unlink(x) for x in out.get("examples_html", [])]
    return out


def doc_slug(rel: str, taken: set[str]) -> str:
    """文書 1 つぶんのファイル名。**重なったら番号を足す**（黙って上書きしない）。

    フォルダの中の相対パスをそのまま使えないので区切りを潰すが、潰した結果が
    ぶつかりうる（`a/b.md` と `a-b.md`）。名前は画面にも出るので、**消すのではなく
    番号で分ける**。
    """
    stem = Path(rel).with_suffix("").as_posix().replace("/", "-")
    base = _BAD_NAME.sub("", stem).replace("..", "").strip(". ").strip() or "doc"
    base = base[:NAME_MAX]
    name = base
    at = 2
    while f"{name}.html" in taken:
        name = f"{base}-{at}"
        at += 1
    taken.add(f"{name}.html")
    return f"{name}.html"


def write_assets(name: str) -> dict:
    """見た目と仕掛けを 1 回だけ書く。**ページごとに埋め込まない。**"""
    target = site_dir(name) / DIR_ASSETS
    target.mkdir(parents=True, exist_ok=True)
    css = (config.STATIC_DIR / "style.css").read_text(encoding="utf-8")
    (target / NAME_CSS).write_text(css + _READER_CSS, encoding="utf-8", newline="\n")

    popup = (config.STATIC_DIR / "popup.js").read_text(encoding="utf-8")
    # **import を外して素のスクリプトにする。** 差し替えるのは `api` と `esc` の
    # 2 つだけで、吹き出しの描き方は本物のまま（写しを作らない）
    popup = re.sub(r"^import .*?;\s*$", "", popup, count=1, flags=re.M)
    popup = re.sub(r"^export ", "", popup, flags=re.M)
    (target / NAME_JS).write_text(
        f"{_SHIM}\n{popup}\ninstallGlossPopup();\n", encoding="utf-8", newline="\n"
    )
    return {"css": str(target / NAME_CSS), "js": str(target / NAME_JS)}


_READER = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{note}">
{og}
<link rel="stylesheet" href="../{assets}/{css}">
</head>
<body>
<div class="reader">
  <header class="reader-head">
    <h1>{title}</h1>
    <p class="reader-note">{note} — <a href="../">辞書へ</a></p>
  </header>
  <article class="doc">{body}</article>
</div>
<div id="gloss-data" data-dict="{dict}" hidden></div>
<script src="../{assets}/{js}"></script>
</body>
</html>
"""


def build_reader(*, title: str, body_html: str, dictionary: dict, og: str = "") -> str:
    """本文 1 ページぶんの HTML。**辞書はここに埋める**（通信しない）。

    辞書は**属性に置く**（`data-dict`）。インラインの ``<script>`` に JSON を書くと、
    辞書の本文に ``</script>`` が現れただけで**そこでスクリプトが終わる** ——
    辞書の本文は人が書くものなので、実際に起こりうる。属性なら HTML の
    エスケープ 1 つで済み、そこは `_attr()` **1 か所**に任せられる。
    """
    packed = json.dumps(dictionary, ensure_ascii=False)
    return _READER.format(
        title=_attr(title),
        note=_attr(f"下線の語にふれると意味が出ます・{len(dictionary)} 語"),
        # **エスケープは出す直前の 1 回だけ**（`_doc_og()` の中で通してある）
        og=og,
        body=body_html,
        dict=_attr(packed),
        assets=DIR_ASSETS,
        css=NAME_CSS,
        js=NAME_JS,
    )


def _write_doc_image(target: Path, file: str, source: str) -> tuple[str, str, tuple[int, int] | None]:
    """その文書の絵をページの隣に置く。``(ファイル名, 印, 大きさ)``。

    **名前はページと揃える**（`章-一.html` の隣が `章-一.png`）—— ページ名は
    `doc_slug()` が重なりを番号で分けているので、こちらも自動的に一意になる。

    **拡張子は中身から決める**（`sniff()`）。渡されるのは手元のパスだが、
    名乗りを使わないのは配る口と同じ規則。**SVG は通さない** —— X が受け取らない
    （→ docs/x-cards.md）うえ、用語ごとの画像と同じ線。
    """
    if not source:
        return "", "", None
    try:
        data = Path(source).read_bytes()
    except OSError:
        return "", "", None
    suffix = imagefmt.sniff(data)
    if suffix is None:
        return "", "", None
    image = f"{Path(file).stem}{suffix}"
    (target / image).write_bytes(data)
    got = imagefmt.size(data)
    return (
        image,
        hashlib.sha256(data).hexdigest()[:8],
        (int(got[0]), int(got[1])) if got else None,
    )


def write_documents(name: str, pages: list[dict], *, base: str = "") -> list[dict]:
    """本文のページを書く。返すのは**索引に載せるための一覧**。

    ``pages`` は ``{"path", "title", "html", "lookup", "image"}``。中身を作るのは
    呼ぶ側（レンダリングも照合もアプリ側の仕事で、ここは**外へ出す形に整えるだけ**）。
    ``image`` は**手元のファイルのパス** —— 置いてある絵を知っているのは呼ぶ側だけ、
    というのは点検が `maps` を受け取るのと同じ形。

    返り値の ``bytes`` は書いたページの大きさ。**クローラの上限 (2 MB) を超えたら
    呼ぶ側が断りを出す** —— 切らずに、超えたことを言う。
    """
    target = site_dir(name) / DIR_DOCS
    target.mkdir(parents=True, exist_ok=True)
    taken: set[str] = set()
    made: list[dict] = []
    for page in pages:
        file = doc_slug(page["path"], taken)
        title = page.get("title") or Path(page["path"]).stem
        dictionary = {
            surface: {**found, "entries": [strip_entry(e) for e in found.get("entries", [])]}
            for surface, found in (page.get("lookup") or {}).items()
        }
        image, stamp, size = _write_doc_image(target, file, page.get("image") or "")
        html = build_reader(
            title=title,
            body_html=unlink(page.get("html", "")),
            dictionary=dictionary,
            og=_doc_og(
                title=title,
                note=f"下線の語にふれると意味が出ます・{len(dictionary)} 語",
                site=name,
                base=base,
                name=name,
                file=file,
                image=image,
                stamp=stamp,
                size=size,
            ),
        )
        (target / file).write_text(html, encoding="utf-8", newline="\n")
        made.append({
            "path": page["path"],
            "title": title,
            "file": f"{DIR_DOCS}/{file}",
            "terms": len(dictionary),
            "image": f"{DIR_DOCS}/{image}" if image else "",
            "bytes": len(html.encode("utf-8")),
        })
    return made
