"""ファイルを「表示できるテキスト」に変換する。

ここが吸収するのは 3 つ:

* **文字コード** — 青空文庫のテキストは Shift_JIS。UTF-8 決め打ちで読むと全部化ける
* **書式** — epub は XHTML の zip、pdf はページの集合。どちらも中身を取り出す
* **位置** — 初出を「L.42」ではなく「第三章」「p.42」で言えるようにする

``read()`` が返す ``Document`` は、そのまま ``/api/render`` に渡せる形
(``kind`` + ``text``) と、位置を数えるための素のテキスト (``segments``) を持つ。
"""

from __future__ import annotations

import re
import threading
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

from . import render

#: 専用の読み方をする拡張子。ほかは `render.resolve_kind()` の判定に任せる。
#:
#: **「ビューアで開ける拡張子」の一覧をここに置かないこと。** それは
#: `app.CONTENT_SUFFIXES` と `viewer.js` の `OPENABLE` の 2 か所が持っている。
#: ここに 3 つめの一覧を作ると、どれが正なのか分からなくなる（実際、誰も参照して
#: いない一覧が置かれていて `.xhtml` や `.rst` が開けるように読めてしまっていた）。
EPUB_SUFFIXES = (".epub",)
PDF_SUFFIXES = (".pdf",)

#: 日本語の文字コード。UTF-8 で読めなければ順に試す
FALLBACK_ENCODINGS = ("utf-8", "cp932", "euc_jp")

#: 青空文庫のルビ記法。｜ は「ルビの付く範囲の始まり」を示すだけなので消す
_AOZORA_RUBY = re.compile(r"《[^》]*》")
_AOZORA_RUBY_MARK = re.compile(r"[｜|](?=[^\s]*《)")
_AOZORA_NOTE = re.compile(r"［＃[^］]*］")
#: 青空文庫と判定する目印
_AOZORA_HINT = re.compile(r"［＃|《[^》]{1,20}》")
#: 冒頭にある記法の説明ブロック（題・著者のあと、罫線 2 本に挟まれている）。
#: ``.`` を DOTALL で使うと行をまたいで際限なく戻るため、必ず行単位で書く
#: （43,000 字の作品でハングした）
_AOZORA_HEADER = re.compile(r"^-{10,}\n(?:[^\n]*\n){0,40}?-{10,}\n+", re.M)

#: 説明ブロックを探す範囲。作品本文まで巻き込まないよう頭だけを見る
_AOZORA_HEADER_SCAN = 3000


class DocumentError(Exception):
    pass


@dataclass
class Document:
    """表示用のテキストと、位置を数えるための区切り。"""

    kind: str                                   # markdown | text | html
    text: str                                   # /api/render に渡す本文
    segments: list[tuple[str, str]] = field(default_factory=list)  # (位置ラベル, 素のテキスト)
    title: str = ""
    #: 読めずに飛ばした部分 (epub の章など)。**黙って落とさないための報告用**。
    #: 一部だけ欠けた本文は「全部読めている」と区別が付かないので UI に出す
    skipped: list[str] = field(default_factory=list)

    @property
    def plain(self) -> str:
        """照合用の素のテキスト。"""
        return "\n".join(text for _, text in self.segments)

    def locate(self, term: str) -> str:
        """用語が最初に出てくる位置を表示用の文字列で返す。

        章やページの区切りがある文書ではその名前を、無ければ行番号を返す。
        """
        needle = (term or "").casefold()
        if not needle:
            return ""
        for label, text in self.segments:
            if needle in (text or "").casefold():
                if label:
                    return label
                from .ai import locator_of

                return locator_of(text, term)
        return ""


# --------------------------------------------------------------------------- #
# テキスト / 青空文庫
# --------------------------------------------------------------------------- #

def decode(data: bytes) -> str:
    """日本語のテキストファイルを読む。UTF-8 が第一候補、次に Shift_JIS。

    青空文庫からそのまま落としたファイルは cp932 なので、UTF-8 決め打ちだと
    まるごと文字化けする（``errors="replace"`` で読めてしまうぶん気付きにくい）。
    """
    for encoding in FALLBACK_ENCODINGS:
        try:
            return _normalize_newlines(data.decode(encoding))
        except UnicodeDecodeError:
            continue
    return _normalize_newlines(data.decode("utf-8", errors="replace"))


def _normalize_newlines(text: str) -> str:
    """CRLF を LF に揃える。

    ``Path.read_text()`` が暗黙にやっていた変換。バイト列から自前で decode すると
    素通しになり、``\\n\\s*\\n`` で段落を数えている箇所（ネタバレ抑止など）が
    静かに壊れる。
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text_file(path: Path) -> str:
    return decode(path.read_bytes())


def looks_like_aozora(text: str) -> bool:
    return bool(_AOZORA_HINT.search(text or ""))


def strip_aozora(text: str) -> str:
    """青空文庫の記法を落とす。

    ルビ ``《...》`` を残すと ``カムパネルラ`` が ``カムパネルラかむぱねるら`` の
    ような並びになり、自動リンクの照合が壊れる。入力者注 ``［＃...］`` も同じ。
    底本や入力者のクレジット（末尾の記載）は帰属表示なので消さない。
    """
    text = _strip_aozora_header(text or "")
    text = _AOZORA_NOTE.sub("", text)
    text = _AOZORA_RUBY.sub("", text)
    return _AOZORA_RUBY_MARK.sub("", text)


def _strip_aozora_header(text: str) -> str:
    """冒頭の「テキスト中に現れる記号について」を落とす。

    記法の説明なので、記法を消したあとに残っていても意味が通らない
    （``《》：ルビ`` が ``：ルビ`` になる）。罫線 2 本で囲まれた定型。
    題名と著者名はその前にあるので残す。
    """
    head, rest = text[:_AOZORA_HEADER_SCAN], text[_AOZORA_HEADER_SCAN:]
    return _AOZORA_HEADER.sub("", head, count=1) + rest


# --------------------------------------------------------------------------- #
# epub
# --------------------------------------------------------------------------- #

_CONTAINER = "META-INF/container.xml"
_OPF_NS = {"opf": "http://www.idpf.org/2007/opf"}
_CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
_HEADING = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def _opf_path(zf: zipfile.ZipFile) -> str:
    try:
        root = ElementTree.fromstring(zf.read(_CONTAINER))
    except (KeyError, ElementTree.ParseError) as exc:
        raise DocumentError("epub の container.xml を読めません") from exc
    node = root.find(".//c:rootfile", _CONTAINER_NS)
    full_path = node.get("full-path") if node is not None else None
    if not full_path:
        raise DocumentError("epub の rootfile が見つかりません")
    return full_path


def _spine_hrefs(zf: zipfile.ZipFile, opf: str) -> tuple[list[str], str]:
    """spine の順に本文ファイルのパスを返す。あわせて書名も拾う。"""
    try:
        root = ElementTree.fromstring(zf.read(opf))
    except (KeyError, ElementTree.ParseError) as exc:
        raise DocumentError("epub の OPF を読めません") from exc

    base = opf.rpartition("/")[0]
    manifest = {}
    for item in root.iterfind(".//opf:manifest/opf:item", _OPF_NS):
        item_id, href = item.get("id"), item.get("href")
        if item_id and href:
            manifest[item_id] = f"{base}/{href}" if base else href

    hrefs = []
    for ref in root.iterfind(".//opf:spine/opf:itemref", _OPF_NS):
        target = manifest.get(ref.get("idref") or "")
        if target:
            hrefs.append(target)

    title_node = root.find(".//{http://purl.org/dc/elements/1.1/}title")
    title = (title_node.text or "").strip() if title_node is not None else ""
    return hrefs, title


def _read_member(zf: zipfile.ZipFile, href: str) -> bytes | None:
    """manifest の href で zip の中身を読む。無ければ ``None``。

    **OPF の href は IRI なので、日本語やスペースは percent-encoded で入っている。**
    ``zipfile`` は生の名前しか受け付けないので、復号したものを先に試す。これを
    怠ると日本語ファイル名の章が ``KeyError`` になり、**その章だけ黙って消える**
    （章名が英数字の epub では気付けない）。
    """
    for name in dict.fromkeys((unquote(href), href)):
        try:
            return zf.read(name)
        except KeyError:
            continue
    return None


def _chapter_title(html: str, index: int) -> tuple[str, bool]:
    """(章の名前, その見出しが本文中に既にあるか) を返す。

    既にあるなら足さない。足すと画面に同じ見出しが 2 つ並ぶ。
    """
    m = _HEADING.search(html)
    if m:
        text = _TAG.sub("", m.group(1)).strip()
        if text:
            return text[:40], True
    return f"第 {index} 章", False


def read_epub(path: Path) -> Document:
    """epub を章ごとに読む。依存を増やさず zipfile と標準 XML だけで扱う。"""
    from .htmlclean import clean_fragment

    try:
        with zipfile.ZipFile(path) as zf:
            opf = _opf_path(zf)
            hrefs, title = _spine_hrefs(zf, opf)
            parts: list[str] = []
            segments: list[tuple[str, str]] = []
            skipped: list[str] = []
            for i, href in enumerate(hrefs, start=1):
                data = _read_member(zf, href)
                if data is None:
                    # zip に無い章。黙って飛ばすと「全部読めた」と区別が付かない
                    skipped.append(href)
                    continue
                body = clean_fragment(decode(data))
                if not body.strip():
                    continue
                label, has_heading = _chapter_title(body, i)
                parts.append(body if has_heading else f"<h2>{label}</h2>\n{body}")
                segments.append((label, render.to_plain_text(body, kind="html")))
    except zipfile.BadZipFile as exc:
        raise DocumentError("epub として開けません（zip が壊れています）") from exc

    if not segments:
        raise DocumentError("epub に読める本文がありません")
    return Document(
        kind="html", text="\n".join(parts), segments=segments, title=title, skipped=skipped
    )


# --------------------------------------------------------------------------- #
# pdf
# --------------------------------------------------------------------------- #

def read_pdf(path: Path) -> Document:
    """pdf をページごとに読む。スキャン画像の pdf は文字が取れない。"""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - 依存を消した構成向け
        raise DocumentError("pdf を読むには pypdf が必要です") from exc

    try:
        reader = PdfReader(str(path))
        pages = [(f"p.{i}", page.extract_text() or "") for i, page in enumerate(reader.pages, start=1)]
    except Exception as exc:  # pypdf は独自例外を多数投げる
        raise DocumentError(f"pdf を読めません: {exc}") from exc

    segments = [(label, text) for label, text in pages if text.strip()]
    if not segments:
        raise DocumentError(
            "pdf から文字を取り出せません（画像として取り込まれた pdf は読めません）"
        )
    title = ""
    try:
        title = (reader.metadata.title or "").strip() if reader.metadata else ""
    except Exception:
        title = ""
    body = "\n\n".join(f"【{label}】\n{text}" for label, text in segments)
    return Document(kind="text", text=body, segments=segments, title=title)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def read(path: Path) -> Document:
    """拡張子を見て適切な読み方をする。"""
    suffix = path.suffix.lower()
    if suffix in EPUB_SUFFIXES:
        return read_epub(path)
    if suffix in PDF_SUFFIXES:
        return read_pdf(path)

    text = read_text_file(path)
    kind = render.resolve_kind("auto", path.name)
    if kind != "html" and looks_like_aozora(text):
        text = strip_aozora(text)
    plain = render.to_plain_text(text, kind=kind)
    title = render.html_title(text) if kind == "html" else ""
    return Document(kind=kind, text=text, segments=[("", plain)], title=title)


# --------------------------------------------------------------------------- #
# 解釈済みの文書を使い回す
#
# epub と pdf は 1 冊ぶんの解釈が重い（実測: epub 1 冊 9.6 ms。.md は 0.1 ms）。
# 横断検索は**見つからない語**のとき全ファイルを読むので、epub の多いフォルダでは
# そこが丸ごと効く。まとめ登録の初出探しも、同じファイルを語の数だけ読んでいた。
#
# **これは索引ではない。** 毎回すべてのファイルを見るのは変わらず、変わっていない
# ものを読み直さないだけ。索引だと取りこぼしが「その語は無かった」と区別が付かなく
# なるが、この形ならその危険は無い（辞書のキャッシュと同じ考え方）。
# --------------------------------------------------------------------------- #

#: 抱えておく数と総文字数の上限。**どちらも要る** —— 数だけだと長編ばかりの
#: フォルダで数百 MB を抱えうるし、文字数だけだと小さいファイルが際限なく増える
CACHE_MAX_FILES = 64
CACHE_MAX_CHARS = 4_000_000

_cache: "OrderedDict[tuple, Document]" = OrderedDict()
_cache_chars = 0
_cache_lock = threading.Lock()


def _cache_key(path: Path) -> tuple | None:
    """パス + mtime + サイズ。**外のエディタで書き換えられたら別物になる。**"""
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), st.st_mtime_ns, st.st_size)


def invalidate_cache() -> None:
    global _cache_chars
    with _cache_lock:
        _cache.clear()
        _cache_chars = 0


def read_cached(path: Path) -> Document:
    """``read()`` と同じものを返す。**中身が変わっていなければ読み直さない。**

    返す ``Document`` は使い回されるので、**呼び出し側で書き換えないこと**
    （いまはどのメソッドも読むだけ）。読み込み自体はロックの外でやる ——
    中で読むと、重いファイルの解釈で他のリクエストまで直列になる。
    """
    global _cache_chars
    key = _cache_key(path)
    if key is None:
        return read(path)              # stat できないなら素直に読ませて例外を出す

    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit

    doc = read(path)
    size = len(doc.text) + sum(len(t) for _, t in doc.segments)

    with _cache_lock:
        if key not in _cache:
            _cache[key] = doc
            _cache_chars += size
        # 古いものから捨てる。**上限を超えた時点で必ず 1 つは残す**
        while len(_cache) > CACHE_MAX_FILES or (
            _cache_chars > CACHE_MAX_CHARS and len(_cache) > 1
        ):
            _, dropped = _cache.popitem(last=False)
            _cache_chars -= len(dropped.text) + sum(len(t) for _, t in dropped.segments)
    return doc
