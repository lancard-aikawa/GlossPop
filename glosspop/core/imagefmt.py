"""画像の**見分け方**と、置いてよい拡張子と、**大きさ**。

**名乗りは使わない。** 送られてきたファイル名も Content-Type も自己申告でしかなく、
配る口（`GET /api/persona` `/api/map` `/api/entry-image`）は**中身を検査せずに
そのまま返す**ので、`.png` という名前の HTML を置かれるとそのまま配ってしまう。
だから拡張子は**中身の先頭から決める**。

**ここ 1 か所に置くのは、同じ判断をする場所が 3 つあるから**（語り手の顔・地図の絵・
用語ごとの画像）。写しを作ると、対応形式を足したときに**片方だけ通って、もう片方では
「画像として読めません」になる** —— 画面から入れた絵が場所によって受け付けられたり
拒まれたりするのは、原因の見えない壊れ方をする。

**SVG を通すかは呼ぶ側が決める。** SVG はスクリプトを持てるので既定は通さない。
地図だけが通しているのは、**形式ではなく出し方**で担保しているから
（`<image>` 埋め込みは secure static mode、直接開かれても `CSP: sandbox`）→ `app`。
"""

from __future__ import annotations

import re

#: ラスタ画像。**これが既定で置いてよいもの**（顔・用語ごとの画像）
IMAGE_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg", ".gif")

#: 地図に使える拡張子。**並びは探す順でもある**（`store.map_file()` が上から試す）。
#: 地図は線画で `viewBox` を動かして寄るのが本題なので、SVG を先頭に置く ——
#: ラスタだと**背景だけボケる**（にじむと SVG の意味が無い、と決めてある側と食い違う）
MAP_SUFFIXES = (".svg", *IMAGE_SUFFIXES)

#: 拡張子 → Content-Type。**配る側が名乗る型はここ 1 か所**（`app` の 2 つの表も
#: ここから引く）。どの形式を通すかは呼ぶ側が決めるが、**同じ拡張子に違う型を
#: 名乗る理由は無い** —— 表が散ると、形式を足したときに片方だけ
#: `application/octet-stream` で配られる（画面には出るのに X には拾われない、
#: のような見えにくい壊れ方をする）
MIME_TYPES: dict[str, str] = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}

#: 先頭のバイト列 → 拡張子。**WebP はここに入れられない**（RIFF コンテナなので
#: 先頭 4 バイトが他と同じ。下で別に見る）
_MAGIC: dict[bytes, str] = {
    b"\x89PNG\r\n\x1a\n": ".png",
    b"\xff\xd8\xff": ".jpg",
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
}

#: SVG を探す範囲。**先頭だけ見る** —— 全体を走査すると、画像の中にたまたま
#: 現れたバイト列を SVG と読む余地ができる
_SVG_HEAD = 4096


def sniff(data: bytes, *, allow_svg: bool = False) -> str | None:
    """中身から拡張子を決める。分からなければ ``None``（例外にはしない）。

    **文言は呼ぶ側が持つ。** 顔・地図・用語で通す形式が違うので、「PNG / JPEG /
    GIF / WebP を選んでください」のような案内はここに置かない。

    SVG はテキストなのでマジックバイトほど堅く見分けられないが、**見分けの目的は
    安全ではなく拡張子を決めること**なので、それで足りる（安全は出し方で取る）。
    """
    for magic, suffix in _MAGIC.items():
        if data.startswith(magic):
            return suffix
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if allow_svg:
        head = data[:_SVG_HEAD].lstrip()
        if head.startswith(b"<?xml") or head.startswith(b"<svg") or b"<svg" in head:
            return ".svg"
    return None


# --------------------------------------------------------------------------- #
# 大きさ
#
# **見分け方と同じ理由でここに置く。** 読む場所が増えると、対応形式を足したときに
# 片方だけ通る（`sniff()` が通した絵の大きさが読めない、という食い違いが起きる）。
#
# 使うのは**縦横比**で、地図の点検が「y が絵の下にはみ出していないか」を見るため
# （座標は**絵の幅を 1 とした比**なので、y の上限は絵の縦横比でしか決まらない）。
# `core` は絵の置き場所を知らないので、**バイト列を受け取るだけ**にしてある。
# --------------------------------------------------------------------------- #

#: 大きさを読むときに見る範囲。**先頭だけ。** JPEG は EXIF のサムネイルが挟まると
#: SOF が奥へ行くが、そこは「分からない」で通す（点検が上限を見ないだけに戻る）——
#: 数十 MB の絵を丸ごと読むほうの害が大きい。呼ぶ側が渡す量の目安でもある
SIZE_HEAD = 65536

#: JPEG で大きさが書いてある区切り（SOF0〜SOF15）。**DHT / JPG / DAC は別物**
_JPEG_SOF = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}

#: SVG の開きタグ。**先頭だけ見る**（`sniff()` と同じ理由）
_SVG_TAG = re.compile(rb"<svg\b([^>]*)>", re.IGNORECASE | re.DOTALL)
#: 属性の値をそのまま取る（`"…"` / `'…'` / 裸）。**値の中身はここで判断しない** ——
#: 数として読めるかは `_length()` が見る（`width="100%"` の `100` だけを拾って
#: しまう書き方にしないため。実際にそう書いて `%` を素通しさせた）
def _attr(name: bytes) -> re.Pattern[bytes]:
    return re.compile(
        rb"\b" + name + rb"""\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""",
        re.IGNORECASE,
    )


_SVG_WIDTH = _attr(b"width")
_SVG_HEIGHT = _attr(b"height")
_SVG_VIEWBOX = _attr(b"viewBox")

#: `800` / `800px`。**単位付きは px だけ通す** —— `%` や `em` は絵の中の比を
#: 決めないので、そのときは `viewBox` のほうを使う
_SVG_LENGTH = re.compile(rb"^([0-9]*\.?[0-9]+)(?:px)?$", re.IGNORECASE)


def _pair(width: float, height: float) -> tuple[float, float] | None:
    """0 以下は「読めなかった」に倒す（0 で割らせない）。"""
    return (width, height) if width > 0 and height > 0 else None


def size(data: bytes) -> tuple[float, float] | None:
    """画像の ``(幅, 高さ)``。読めなければ ``None``（例外にはしない）。

    **読めないことは異常ではない。** 途中で切れたバイト列も、まだ知らない形式も
    ここへ来る。呼ぶ側は「分からないなら見ない」に倒すこと —— 読めない絵を
    問題として挙げると、地図を使っている辞書で警告が増えるだけになる。
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return _pair(
            int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        )
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return _pair(
            int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")
        )
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_size(data)
    if data.startswith(b"\xff\xd8\xff"):
        return _jpeg_size(data)
    return _svg_size(data)


def _webp_size(data: bytes) -> tuple[float, float] | None:
    """WebP。**中身は 3 通り**（拡張・可逆・非可逆）で、どれも書き方が違う。"""
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:            # 拡張（アニメ・透過など）
        return _pair(
            int.from_bytes(data[24:27], "little") + 1,
            int.from_bytes(data[27:30], "little") + 1,
        )
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:   # 可逆
        bits = int.from_bytes(data[21:25], "little")
        return _pair((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        return _pair(                                    # 非可逆（上位 2 ビットは倍率）
            int.from_bytes(data[26:28], "little") & 0x3FFF,
            int.from_bytes(data[28:30], "little") & 0x3FFF,
        )
    return None


def _jpeg_size(data: bytes) -> tuple[float, float] | None:
    """JPEG。区切りを頭からたどって SOF を探す（**大きさはそこにしかない**）。"""
    i, n = 2, len(data)
    while i + 9 <= n:
        if data[i] != 0xFF:
            i += 1                       # 埋め草。次の区切りまで読み飛ばす
            continue
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1                       # 0xFF の連続も埋め草
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2                       # 長さを持たない区切り
            continue
        if marker in (0xD9, 0xDA):
            return None                  # 画像データに入った（もう SOF は無い）
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if length < 2:
            return None
        if marker in _JPEG_SOF:
            return _pair(
                int.from_bytes(data[i + 7:i + 9], "big"),
                int.from_bytes(data[i + 5:i + 7], "big"),
            )
        i += 2 + length
    return None


def _svg_size(data: bytes) -> tuple[float, float] | None:
    """SVG。``width`` / ``height`` が使えればそれ、駄目なら ``viewBox``。

    **`viewBox` を先に見ないのは、そちらが「中の座標系」だから** —— 両方ある絵で
    比が食い違うときに出るのは `width` / `height` のほう（`preserveAspectRatio` が
    既定の `meet` なので、はみ出さないよう内側に収まる）。
    """
    head = data[:_SVG_HEAD]
    tag = _SVG_TAG.search(head)
    if tag is None:
        return None
    attrs = tag.group(1)
    found = _pair(
        _length(_value(_SVG_WIDTH, attrs)), _length(_value(_SVG_HEIGHT, attrs))
    )
    if found:
        return found
    parts = _value(_SVG_VIEWBOX, attrs).replace(b",", b" ").split()
    if len(parts) != 4:
        return None
    return _pair(_number(parts[2]), _number(parts[3]))


def _value(pattern: re.Pattern[bytes], attrs: bytes) -> bytes:
    """属性の値。**無ければ空**（どの囲み方で書かれていても同じ扱い）。"""
    found = pattern.search(attrs)
    if found is None:
        return b""
    return next((g for g in found.groups() if g is not None), b"")


def _length(raw: bytes) -> float:
    """`800` / `800px` の数。**`%` や `em` は 0**（絵の中の比を決めないため）。"""
    hit = _SVG_LENGTH.match(raw.strip())
    return _number(hit.group(1)) if hit else 0.0


def _number(raw: bytes) -> float:
    """バイト列の数。**読めなければ 0**（`_pair` が「読めなかった」に倒す）。"""
    try:
        return float(raw.decode("ascii"))
    except (UnicodeDecodeError, ValueError):
        return 0.0
