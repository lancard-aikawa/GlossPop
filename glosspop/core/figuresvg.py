"""**図の SVG**（図形と文字だけ）の形と、削り方。

`archivefmt` が zip の形と安全規則を持っているのと同じ位置づけで、ここが持つのは
**「図の SVG」と呼べるものの形**だけ。辞書の置き場所も出力先も知らない。

**なぜ削るのか。** ここへ来る SVG は AI が書いたもので、**そのままブラウザの DOM に
入れて PNG に焼く**（`graph-export.js` の道）。SVG はスクリプトも外部参照も持てるので、
入れる前にこちらで落とす。`imagefmt` が「SVG はスクリプトを持てるので既定は通さない」
と書いている線を、**通す代わりに中身を削って**成立させているのがこの層。

守っていること 4 つ:

- **許可制。** 知っているタグと属性だけを通し、**それ以外は黙って落とさず数える**
  （`hidden` / `outside` / `tucked` と同じ約束で、呼ぶ側が画面に出せる）。
  禁止する側を並べる形にしないこと —— 数え落としが 1 つあれば素通しになる
- **描けなければ空。** 読めない・寸法が無い・図形が 1 つも残らなかったものは
  ``svg`` を空で返す。`headline.pick()` が言い切れなければ ``None`` を返すのと
  同じ判断で、**外れた図は無いより悪い**（しかも図は、外していても機械で気付けない）
- **参照を持たせない。** `image` `use` `foreignObject` を通さず、`url(...)` を含む
  属性値も落とす。**1 枚で完結していないと、焼いた PNG に何が写るかが実行時に
  決まる**（`graph-export.inlineImages()` が外部画像を data URI に畳んでいるのと
  同じ話を、こちらでは「そもそも持たせない」で済ませている）
- **寸法は `viewBox` で要求する。** 焼く側は `{ root, box }` を必要とし、box は
  `viewBox` からしか作れない。無いものを 300x150 の既定で描くと、**絵は出るので
  画面を見るまで気付けない**（`app._SVG_SIZED` が地図で弾いているのと同じ理由）
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

#: 受け取る長さの上限。**実物の桁から採ってある** —— `samples/戦国時代` の
#: 地図 2 枚が 1,229 字と 1,558 字（rect 1 + path 7〜12）。1 桁の余裕を見てある。
#: 上限を持つのは、**AI が返したものをそのまま解析する**から（長いものを
#: 食わされたときに、パーサの前で断れる場所がここしか無い）
MAX_CHARS = 20_000

#: SVG の名前空間。**出すときはこれだけ**（xlink は通さないので書かない）
SVG_NS = "http://www.w3.org/2000/svg"

#: 通すタグ。**図形と文字だけ**。
#:
#: `defs` `marker` `use` を入れていないのは、**参照で組み立てる形を持たせない**
#: ため（id と `url(...)` が要る ＝ 落とす対象が増える）。矢印が要るなら
#: `path` で書けばよい。`title` `desc` も入れない —— 焼いた先は PNG なので
#: 読み上げには届かず（`alt` を持たないと決めた側の話）、置く意味が無い
TAGS: frozenset[str] = frozenset({
    "svg", "g", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path",
    "text", "tspan",
})

#: どのタグでも通す属性（見た目と位置）。**`style` は入れない** ——
#: 表示は属性で書けるし、`graph-export.bakeStyles()` が焼き込むときに
#: `style` 属性を**上書きする**ので、残しても効かないか、効いたら二重になる。
#: `class` と `id` も入れない（外の CSS は届かず、id はページの id と衝突しうる）
COMMON_ATTRS: frozenset[str] = frozenset({
    "transform",
    "fill", "fill-opacity", "fill-rule",
    "stroke", "stroke-width", "stroke-opacity", "stroke-dasharray",
    "stroke-linecap", "stroke-linejoin",
    "opacity",
})

#: タグごとに追加で通す属性
TAG_ATTRS: dict[str, frozenset[str]] = {
    "svg": frozenset({"viewBox", "width", "height"}),
    "rect": frozenset({"x", "y", "width", "height", "rx", "ry"}),
    "circle": frozenset({"cx", "cy", "r"}),
    "ellipse": frozenset({"cx", "cy", "rx", "ry"}),
    "line": frozenset({"x1", "y1", "x2", "y2"}),
    "polyline": frozenset({"points"}),
    "polygon": frozenset({"points"}),
    "path": frozenset({"d"}),
    "text": frozenset({
        "x", "y", "dx", "dy", "font-size", "font-family", "font-weight",
        "font-style", "text-anchor", "dominant-baseline", "letter-spacing",
    }),
    "tspan": frozenset({
        "x", "y", "dx", "dy", "font-size", "font-weight", "font-style", "text-anchor",
    }),
}

#: 図形と数えるタグ。**1 つも残らなければ「描けなかった」** ——
#: `text` だけの SVG は図ではない（頼んだのは図形と文字であって、文字だけではない）
SHAPES: frozenset[str] = frozenset({
    "rect", "circle", "ellipse", "line", "polyline", "polygon", "path",
})

#: 属性値に現れたら、その属性ごと落とすもの。**外部参照とスクリプト。**
#: 許可制の網をすり抜ける経路はここしかない（通した属性の**中身**）ので、
#: タグの一覧とは別に見る
_BAD_VALUE = re.compile(r"url\s*\(|javascript:|data:|<|&#", re.IGNORECASE)

#: 実体参照の展開で膨らませる攻撃（billion laughs）を、**パーサへ渡す前に**断る。
#: `xml.etree` は外部実体こそ読まないが、内部実体の展開は止めてくれない
_DOCTYPE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)

#: 本文から SVG を取り出す。AI は前後に説明を付けてくることがあるので、
#: **最初の `<svg` から最後の `</svg>` まで**を採る（`_strip_fence` と同じ考え方）
_SVG_SPAN = re.compile(r"<svg[\s>].*</svg\s*>", re.IGNORECASE | re.DOTALL)

#: `viewBox` の 4 つの数。**負の原点も指数も許す**（SVG の数値そのまま）
_VIEWBOX = re.compile(
    r"^\s*(-?[\d.]+(?:e-?\d+)?)[\s,]+(-?[\d.]+(?:e-?\d+)?)"
    r"[\s,]+(-?[\d.]+(?:e-?\d+)?)[\s,]+(-?[\d.]+(?:e-?\d+)?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Figure:
    """削り終えた図 1 枚。

    ``svg`` が空なら**使えなかった**ということで、``why`` にその理由が入る
    （呼ぶ側は「図を作れませんでした」と言って終わる。空の絵を出さない）。
    ``dropped`` は落としたタグ・属性の名前で、**数えて返す**のは黙って欠けた
    ものを出さないため。
    """

    svg: str = ""
    why: str = ""
    box: tuple[float, float, float, float] | None = None
    shapes: int = 0
    texts: int = 0
    dropped: tuple[str, ...] = field(default=())


def extract(text: str) -> str:
    """説明文やコードフェンスに埋もれた SVG を取り出す。無ければ空。"""
    found = _SVG_SPAN.search(text or "")
    return found.group(0) if found else ""


def _local(tag: object) -> str:
    """``{http://…}rect`` → ``rect``。名前空間は捨てて**局所名だけ**で照合する。

    名前空間ごと見ると、``xmlns`` を書き忘れた SVG が丸ごと落ちる（AI は普通に
    書き忘れる）。通すタグは許可制なので、局所名で足りる。
    """
    name = tag if isinstance(tag, str) else ""
    return name.rsplit("}", 1)[-1]


def _allowed_attrs(tag: str) -> frozenset[str]:
    return COMMON_ATTRS | TAG_ATTRS.get(tag, frozenset())


def _clean_attrs(tag: str, attrs: dict, dropped: list[str]) -> dict[str, str]:
    """通す属性だけを残す。**名前空間つきの属性は全部落ちる**（`xlink:href` など）。"""
    allowed = _allowed_attrs(tag)
    out: dict[str, str] = {}
    for raw, value in attrs.items():
        name = raw if isinstance(raw, str) else ""
        if name.startswith("{") or name not in allowed:
            dropped.append(f"{tag}@{_local(name)}")
            continue
        if _BAD_VALUE.search(str(value)):
            dropped.append(f"{tag}@{name}")
            continue
        out[name] = str(value)
    return out


def _copy(node: ET.Element, dropped: list[str], counts: dict[str, int]) -> ET.Element | None:
    """1 要素を写す。**通らないタグは中身ごと捨てる。**

    タグだけ落として子を引き上げる形にしないこと —— `htmlclean` が `rt` / `rp` を
    中身ごと捨てているのと同じ判断で、`script` の中身が図の文字として残る。
    """
    tag = _local(node.tag)
    if tag not in TAGS:
        dropped.append(tag or "?")
        return None
    made = ET.Element(tag, _clean_attrs(tag, dict(node.attrib), dropped))
    made.text = node.text
    if tag in SHAPES:
        counts["shapes"] = counts.get("shapes", 0) + 1
    elif tag == "text":
        counts["texts"] = counts.get("texts", 0) + 1
    for child in list(node):
        got = _copy(child, dropped, counts)
        if got is None:
            # **捨てた要素の tail は連れて行かない。** 連れて行くと、落とした
            # `<script>` の後ろの改行だけが図の文字として残る
            continue
        made.append(got)
    return made


def _box(value: str) -> tuple[float, float, float, float] | None:
    """``viewBox`` を 4 つの数にする。**大きさが 0 以下なら無効。**"""
    found = _VIEWBOX.match(value or "")
    if not found:
        return None
    x, y, w, h = (float(n) for n in found.groups())
    return (x, y, w, h) if w > 0 and h > 0 else None


def clean(text: str) -> Figure:
    """AI が返したものを、**図形と文字だけの SVG** に削る。

    使えなければ ``svg`` を空にして ``why`` を入れる（例外にしない —— 呼ぶ側は
    「作れませんでした」と画面に出すだけで、失敗として扱う必要が無い）。
    """
    # **DTD は取り出す前に見る。** `extract()` は `<svg` より前を捨てるので、
    # 削ったあとで探すと**内部実体の宣言だけが視界から消える**（宣言が消えれば
    # パーサは未定義実体として落とすので通りはしないが、断る理由は「読めなかった」
    # ではなく「受け取らない」であるべき）
    if _DOCTYPE.search(text or ""):
        return Figure(why="DOCTYPE / ENTITY を含む SVG は受け取りません")
    raw = extract(text)
    if not raw:
        return Figure(why="SVG が返ってきませんでした")
    if len(raw) > MAX_CHARS:
        return Figure(why=f"図が大きすぎます（{MAX_CHARS} 字まで）")
    try:
        parsed = ET.fromstring(raw)
    except ET.ParseError:
        return Figure(why="SVG として読めませんでした")
    if _local(parsed.tag) != "svg":
        return Figure(why="SVG として読めませんでした")

    dropped: list[str] = []
    counts: dict[str, int] = {}
    made = _copy(parsed, dropped, counts)
    if made is None:                                  # 起こらないが、型のために
        return Figure(why="SVG として読めませんでした")

    box = _box(made.get("viewBox", ""))
    if box is None:
        return Figure(why="viewBox がありません（縦横比を決められません）")
    if not counts.get("shapes"):
        return Figure(why="図形がありません")

    # **寸法を書き直す。** `viewBox` が正で、`width` / `height` はそこから作る
    # （食い違ったまま残すと、焼いた PNG だけが歪む）
    made.set("xmlns", SVG_NS)
    made.set("width", f"{box[2]:g}")
    made.set("height", f"{box[3]:g}")
    return Figure(
        svg=ET.tostring(made, encoding="unicode"),
        box=box,
        shapes=counts.get("shapes", 0),
        texts=counts.get("texts", 0),
        # 同じ名前が何度も落ちるので**種類で返す**（並びは出た順）
        dropped=tuple(dict.fromkeys(dropped)),
    )
