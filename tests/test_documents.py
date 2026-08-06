"""ファイルの読み込み（文字コード・青空文庫・epub・pdf）。"""

from __future__ import annotations

import zipfile

import pytest

from glosspop.core import documents

AOZORA = """銀河鉄道の夜
宮沢賢治

「ではみなさんは、そういうふうに川だと云《い》われたり、
乳《ちち》の流れたあとだと云われたりしていたこのぼんやりと白いものが
ほんとうは何かご承知ですか。」先生は［＃「先生は」に傍点］黒板につるした
大きな黒い星座の図の、上から下へ白くけぶった銀河帯《ぎんがたい》のようなところを指しました。

底本：「銀河鉄道の夜」新潮文庫
入力：もりみつじゅんじ
校正：田中哲郎
"""


class TestEncoding:
    def test_reads_shift_jis(self, tmp_path):
        path = tmp_path / "青空.txt"
        path.write_bytes("ジョバンニは走った。".encode("cp932"))
        assert documents.read_text_file(path) == "ジョバンニは走った。"

    def test_reads_utf8(self, tmp_path):
        path = tmp_path / "utf8.txt"
        path.write_text("カムパネルラ", encoding="utf-8")
        assert documents.read_text_file(path) == "カムパネルラ"

    def test_utf8_wins_when_both_decode(self, tmp_path):
        # cp932 でも「読めてしまう」バイト列があるので、UTF-8 を先に試す
        path = tmp_path / "both.txt"
        path.write_text("銀河鉄道", encoding="utf-8")
        assert documents.read_text_file(path) == "銀河鉄道"


class TestAozora:
    def test_detects_the_notation(self):
        assert documents.looks_like_aozora(AOZORA) is True
        assert documents.looks_like_aozora("ふつうの日本語の文章です。") is False

    def test_strips_ruby_so_terms_still_match(self):
        out = documents.strip_aozora(AOZORA)
        assert "云われたり" in out          # 親字は残る
        assert "《" not in out and "》" not in out
        assert "い》" not in out
        assert "銀河帯のような" in out       # ルビだけ消える

    def test_strips_editor_notes(self):
        assert "［＃" not in documents.strip_aozora(AOZORA)
        assert "先生は黒板" in documents.strip_aozora(AOZORA)

    def test_keeps_the_credits(self):
        # 底本・入力・校正は帰属表示なので残す
        out = documents.strip_aozora(AOZORA)
        assert "入力：もりみつじゅんじ" in out

    def test_read_applies_it_to_files(self, tmp_path):
        path = tmp_path / "銀河.txt"
        path.write_bytes(AOZORA.encode("cp932"))
        doc = documents.read(path)
        assert doc.kind == "text"
        assert "《" not in doc.text
        assert "ジョバンニ" not in doc.text  # このサンプルには出てこない
        assert doc.locate("銀河帯") == "L.7"


def make_epub(path, chapters):
    """最小限の epub を組み立てる（テスト用）。"""
    manifest = "".join(
        f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
        for i in range(len(chapters))
    )
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    opf = (
        '<?xml version="1.0"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>銀河鉄道の夜</dc:title></metadata>"
        f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>"
    )
    container = (
        '<?xml version="1.0"?>'
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        for i, (title, body) in enumerate(chapters):
            zf.writestr(
                f"OEBPS/c{i}.xhtml",
                f"<html><body><h2>{title}</h2><p>{body}</p></body></html>",
            )
    return path


class TestEpub:
    def _sample(self, tmp_path):
        return make_epub(
            tmp_path / "novel.epub",
            [
                ("一、午后の授業", "ジョバンニは席に座っていた。"),
                ("二、活版所", "カムパネルラは笑っていた。"),
            ],
        )

    def test_reads_chapters_in_spine_order(self, tmp_path):
        doc = documents.read(self._sample(tmp_path))
        assert doc.kind == "html"
        assert doc.title == "銀河鉄道の夜"
        assert [label for label, _ in doc.segments] == ["一、午后の授業", "二、活版所"]
        assert "ジョバンニ" in doc.text and "カムパネルラ" in doc.text

    def test_locates_by_chapter(self, tmp_path):
        doc = documents.read(self._sample(tmp_path))
        assert doc.locate("カムパネルラ") == "二、活版所"
        assert doc.locate("居ない人") == ""

    def test_ruby_readings_do_not_leak_into_the_text(self, tmp_path):
        path = make_epub(
            tmp_path / "ruby.epub",
            [("一", "<ruby><rb>銀河</rb><rp>(</rp><rt>ぎんが</rt><rp>)</rp></ruby>を見た。")],
        )
        doc = documents.read(path)
        # 「銀河ぎんが」になると用語照合が壊れる
        assert "銀河" in doc.plain
        assert "ぎんが" not in doc.plain and "ぎんが" not in doc.text
        assert "<rt>" not in doc.text

    def test_broken_file_is_reported(self, tmp_path):
        path = tmp_path / "broken.epub"
        path.write_bytes(b"not a zip")
        with pytest.raises(documents.DocumentError):
            documents.read(path)


class TestPdf:
    def _make_pdf(self, path, pages):
        pypdf = pytest.importorskip("pypdf")
        writer = pypdf.PdfWriter()
        for _ in pages:
            writer.add_blank_page(width=200, height=200)
        with open(path, "wb") as fh:
            writer.write(fh)
        return path

    def test_pdf_without_text_says_so(self, tmp_path):
        # 白紙 = 文字が取れない pdf。スキャン画像の pdf と同じ扱いになる
        path = self._make_pdf(tmp_path / "blank.pdf", [1, 2])
        with pytest.raises(documents.DocumentError) as err:
            documents.read(path)
        assert "画像" in str(err.value)

    def test_pages_become_segments(self, tmp_path, monkeypatch):
        path = self._make_pdf(tmp_path / "novel.pdf", [1, 2, 3])

        import pypdf

        texts = iter(["最初のページ", "ジョバンニが出てくる", "最後"])
        monkeypatch.setattr(pypdf._page.PageObject, "extract_text", lambda self, *a, **k: next(texts))

        doc = documents.read(path)
        assert [label for label, _ in doc.segments] == ["p.1", "p.2", "p.3"]
        assert doc.locate("ジョバンニ") == "p.2"
        assert "【p.2】" in doc.text


def test_crlf_is_normalised(tmp_path):
    """バイト列から decode すると CRLF が素通りする。段落判定が壊れるので揃える。"""
    path = tmp_path / "crlf.txt"
    path.write_bytes("一行目\r\n\r\n二行目\r\n".encode("utf-8"))
    assert documents.read_text_file(path) == "一行目\n\n二行目\n"


AOZORA_WITH_HEADER = """銀河鉄道の夜
宮沢賢治

-------------------------------------------------------
【テキスト中に現れる記号について】

《》：ルビ
（例）云《い》われたり
-------------------------------------------------------

　「ではみなさんは」と先生が云《い》いました。
"""


def test_aozora_notation_header_is_removed():
    """記法の説明は、記法を消したあとに残っていても意味が通らない。"""
    out = documents.strip_aozora(AOZORA_WITH_HEADER)
    assert "テキスト中に現れる記号について" not in out
    assert "-------" not in out
    assert "「ではみなさんは」と先生が云いました。" in out
    # 題名と著者は説明ブロックより前にあるので残す
    assert out.startswith("銀河鉄道の夜\n宮沢賢治")


def test_header_removal_is_fast_on_a_whole_novel():
    """DOTALL の .* で書くと 4 万字でハングする（実際に踏んだ）。"""
    import time

    novel = AOZORA_WITH_HEADER + ("　ジョバンニは走った。\n" * 4000)
    start = time.perf_counter()
    out = documents.strip_aozora(novel)
    assert time.perf_counter() - start < 1.0
    assert "テキスト中に現れる記号について" not in out


def test_header_is_left_alone_in_ordinary_text():
    text = "見出し\n\n-------------------\n本文です。\n"
    assert documents.strip_aozora(text) == text


def test_epub_percent_encoded_hrefs_are_decoded(tmp_path):
    """OPF の href は IRI なので、日本語やスペースは percent-encoded で入る。

    ``zipfile`` は生の名前しか受けないため、復号せずに読むと ``KeyError`` になり
    **その章だけ黙って消える**。日本語ファイル名の epub で必ず踏む。
    """
    path = tmp_path / "encoded.epub"
    files = {
        "第一章.xhtml": "%E7%AC%AC%E4%B8%80%E7%AB%A0.xhtml",
        "ch 2.xhtml": "ch%202.xhtml",
        "plain.xhtml": "plain.xhtml",
    }
    manifest = "".join(
        f'<item id="c{i}" href="{href}" media-type="application/xhtml+xml"/>'
        for i, href in enumerate(files.values())
    )
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(files)))
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<package xmlns="http://www.idpf.org/2007/opf">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>本</dc:title></metadata>'
            f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>",
        )
        for i, name in enumerate(files):
            zf.writestr(
                f"OEBPS/{name}",
                f"<html><body><h2>第{i + 1}章</h2><p>本文{i + 1}</p></body></html>",
            )

    doc = documents.read(path)
    assert [label for label, _ in doc.segments] == ["第1章", "第2章", "第3章"]
    assert doc.locate("本文1") == "第1章"


def test_epub_reports_unreadable_chapters(tmp_path):
    """zip に無い章は飛ばすが、飛ばしたことは黙っていない。"""
    path = make_epub(tmp_path / "missing.epub", [("一", "本文いち"), ("二", "本文に")])
    with zipfile.ZipFile(path) as src:
        items = {n: src.read(n) for n in src.namelist() if n != "OEBPS/c1.xhtml"}
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in items.items():
            zf.writestr(name, data)

    doc = documents.read(path)
    assert "本文いち" in doc.plain
    assert doc.skipped == ["OEBPS/c1.xhtml"]


def test_epub_chapter_heading_is_not_duplicated(tmp_path):
    """本文に見出しがあるのに足すと、画面に同じ見出しが 2 つ並ぶ。"""
    path = make_epub(tmp_path / "dup.epub", [("一、午后の授業", "本文")])
    doc = documents.read(path)
    assert doc.text.count("一、午后の授業") == 1
    assert [label for label, _ in doc.segments] == ["一、午后の授業"]


def test_epub_without_headings_gets_a_generated_one(tmp_path):
    path = tmp_path / "plain.epub"
    make_epub(path, [("見出し", "本文")])
    # 見出しの無い章に差し替える (zip を作り直す。追記だと同名エントリが二重になる)
    with zipfile.ZipFile(path) as src:
        items = {name: src.read(name) for name in src.namelist()}
    items["OEBPS/c0.xhtml"] = b"<html><body><p>\xe8\xa6\x8b\xe5\x87\xba\xe3\x81\x97\xe3\x81\xae\xe7\x84\xa1\xe3\x81\x84\xe6\x9c\xac\xe6\x96\x87</p></body></html>"
    with zipfile.ZipFile(path, "w") as out:
        for name, data in items.items():
            out.writestr(name, data)
    doc = documents.read(path)
    assert "<h2>第 1 章</h2>" in doc.text
    assert [label for label, _ in doc.segments] == ["第 1 章"]


class TestReadCached:
    """解釈済みの文書を使い回す。

    **これは索引ではない。** 毎回すべてのファイルを見るのは変わらず、変わって
    いないものを読み直さないだけ。だから「取りこぼして無かったことにする」は
    起きない —— そこが崩れていないことを見張る。
    """

    @pytest.fixture(autouse=True)
    def _clean(self):
        documents.invalidate_cache()
        yield
        documents.invalidate_cache()

    def test_the_same_object_comes_back(self, tmp_path):
        path = tmp_path / "銀河.txt"
        path.write_text("ジョバンニ。\n", encoding="utf-8")
        assert documents.read_cached(path) is documents.read_cached(path)

    def test_an_edit_made_outside_is_picked_up(self, tmp_path):
        """**外のエディタで書き換えたら読み直す。** ここが崩れると嘘をつく。"""
        path = tmp_path / "銀河.txt"
        path.write_text("ジョバンニ。\n", encoding="utf-8")
        assert "ジョバンニ" in documents.read_cached(path).plain

        # 中身も長さも変える（mtime だけに頼らない）
        path.write_text("カムパネルラが増えた。\n", encoding="utf-8")
        again = documents.read_cached(path)
        assert "カムパネルラ" in again.plain
        assert "ジョバンニ" not in again.plain

    def test_a_deleted_file_still_raises(self, tmp_path):
        path = tmp_path / "無い.txt"
        with pytest.raises(OSError):
            documents.read_cached(path)

    def test_it_does_not_grow_without_bound(self, tmp_path, monkeypatch):
        monkeypatch.setattr(documents, "CACHE_MAX_FILES", 3)
        paths = []
        for i in range(6):
            p = tmp_path / f"文書{i}.txt"
            p.write_text(f"本文 {i}。\n", encoding="utf-8")
            paths.append(p)
            documents.read_cached(p)
        assert len(documents._cache) == 3
        # 捨てられていても、読み直せば同じものが返る
        assert "本文 0" in documents.read_cached(paths[0]).plain

    def test_the_character_budget_also_evicts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(documents, "CACHE_MAX_CHARS", 100)
        for i in range(4):
            p = tmp_path / f"長い{i}.txt"
            p.write_text("あ" * 200, encoding="utf-8")
            documents.read_cached(p)
        # 上限を超えても**必ず 1 つは残す**（入れた直後に捨てない）
        assert len(documents._cache) == 1
