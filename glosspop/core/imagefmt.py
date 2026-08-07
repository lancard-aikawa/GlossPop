"""画像の**見分け方**と、置いてよい拡張子。

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

#: ラスタ画像。**これが既定で置いてよいもの**（顔・用語ごとの画像）
IMAGE_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg", ".gif")

#: 地図に使える拡張子。**並びは探す順でもある**（`store.map_file()` が上から試す）。
#: 地図は線画で `viewBox` を動かして寄るのが本題なので、SVG を先頭に置く ——
#: ラスタだと**背景だけボケる**（にじむと SVG の意味が無い、と決めてある側と食い違う）
MAP_SUFFIXES = (".svg", *IMAGE_SUFFIXES)

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
