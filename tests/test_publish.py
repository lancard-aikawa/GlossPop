"""公開ページの書き出し (`publish`)。

見ているのは**書く前と書いたあとの約束**だけ —— どこにも勝手に書かないこと、
上書きになるものを先に出すこと、カードの URL が絶対であること、そして
**辞書の本文がそのまま HTML として効かないこと**。
"""
from __future__ import annotations

import html as htmllib
import json
import re

import pytest

from glosspop import config, publish
from glosspop.core.models import Entry, Relation

BASE = "https://example.github.io/repo/"


def entry(term: str, **kw) -> Entry:
    kw.setdefault("category", "事件")
    kw.setdefault("slug", term)
    return Entry(term=term, **kw)


@pytest.fixture
def out(tmp_path, monkeypatch):
    target = tmp_path / "pages"
    monkeypatch.setenv("GLOSSPOP_PUBLISH_DIR", str(target))
    monkeypatch.delenv("GLOSSPOP_PUBLISH_BASE_URL", raising=False)
    return target


@pytest.fixture
def based(out, monkeypatch):
    monkeypatch.setenv("GLOSSPOP_PUBLISH_BASE_URL", BASE)
    return out


# --- どこへ書くか -------------------------------------------------------

def test_出力先が決まっていなければ書かない(monkeypatch):
    """**既定を持たない。** 置いた覚えのないフォルダにファイルを増やさない。"""
    monkeypatch.delenv("GLOSSPOP_PUBLISH_DIR", raising=False)
    assert config.publish_dir() is None
    with pytest.raises(publish.PublishError):
        publish.site_dir("辞書")


def test_名前が空になるものは受け付けない(out):
    for bad in ("", "   ", "..", ".", "_"):
        with pytest.raises(publish.PublishError):
            publish.site_dir(bad)


def test_パス区切りは落として根の中に収める(out):
    """escape させない。**落とした結果は `plan()` の `dir` と `url` に出る。**"""
    assert publish.site_dir("../外").parent == out.resolve()
    assert publish.site_dir("a/b").name == "ab"


def test_下見は上書きになるものを返す(out):
    """**「入れ替わります」の一言だけで押させない**（取り込みの `plan()` と同じ）。"""
    before = publish.plan("辞書")
    assert before["exists"] is False
    assert [f["overwrite"] for f in before["files"]] == [False, False]

    publish.write_site([entry("あ")], name="辞書")
    after = publish.plan("辞書")
    assert after["exists"] is True
    assert {f["name"]: f["overwrite"] for f in after["files"]}["index.html"] is True


# --- カードの URL -------------------------------------------------------

def test_基準URLが無ければ画像のタグを書かず理由を返す(out):
    """相対の ``og:image`` は無視される。**半端なタグを書くほうが分かりにくい。**"""
    got = publish.write_site([entry("あ")], name="辞書", card_stamp="abc")
    page = (out / "辞書" / "index.html").read_text(encoding="utf-8")
    assert "og:image" not in page
    assert "twitter:card" not in page
    assert got["warnings"] and "カード" in got["warnings"][0]
    assert publish.plan("辞書")["warnings"]


def test_基準URLがあれば絶対URLでpercent_encodeする(based):
    got = publish.write_site([entry("あ")], name="戦国時代", card_stamp="abc123")
    page = (based / "戦国時代" / "index.html").read_text(encoding="utf-8")
    encoded = "%E6%88%A6%E5%9B%BD%E6%99%82%E4%BB%A3"
    assert f'og:url" content="{BASE}{encoded}/"' in page
    assert f'og:image" content="{BASE}{encoded}/card.png?v=abc123"' in page
    assert 'twitter:card" content="summary_large_image"' in page
    assert got["url"].endswith(f"{encoded}/")


#: 本物の PNG の頭。**見分けは中身でする**ので、偽物は通らない
PNG = b"\x89PNG\r\n\x1a\n"


def test_同じ中身なら印も同じ(based):
    """X はカードを URL ごとに覚える。**変わっていなければ URL も変えない。**"""
    first = publish.write_card("辞書", PNG + b"same")
    second = publish.write_card("辞書", PNG + b"same")
    other = publish.write_card("辞書", PNG + b"other")
    assert first["stamp"] == second["stamp"] != other["stamp"]


def test_PNGでないものは書かない(based):
    """配る置き場所なので、**名乗りではなく中身**で見分ける（顔の口と同じ）。"""
    with pytest.raises(publish.PublishError):
        publish.write_card("辞書", b"GIF89a not a png")
    with pytest.raises(publish.PublishError):
        publish.write_card("辞書", PNG + b"x" * publish.CARD_MAX_BYTES)


# --- 書いたもの ---------------------------------------------------------

def test_nojekyllを根に置く(out):
    """`{{ }}` を含む辞書本文が Jekyll で化けないように。"""
    publish.write_site([entry("あ")], name="辞書")
    assert (out / ".nojekyll").exists()


def test_辞書本文の生HTMLは通さない(out):
    """**配る口なので、名乗りを信じない。** `md_to_html` の `html: False` に乗る。"""
    danger = entry("罠", definition="<script>alert(1)</script> と <img onerror=x>")
    publish.write_site([danger], name="辞書")
    page = (out / "辞書" / "index.html").read_text(encoding="utf-8")
    body = page.split("<main>", 1)[1].split("</main>", 1)[0]
    # タグとして効いていないこと。**消えていることではない**
    assert "<script" not in body and "<img" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "&lt;img onerror=x&gt;" in body


def test_見出しと数はcoreから来る(out):
    """ページの中身の正は `core`（`card` と `booklet`）。ここで作り直さない。"""
    entries = [
        entry("本能寺の変", when="1582-06-21", relations=[Relation(to="伊賀越え")]),
        entry("伊賀越え", when="1582-06-21"),
    ]
    publish.write_site(entries, name="戦国時代")
    page = (out / "戦国時代" / "index.html").read_text(encoding="utf-8")
    assert "同じ日に、2 つの事件" in page
    assert "2 語 ・ 1 本の関係" in page


# --- API ---------------------------------------------------------------

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from glosspop.app import app
    with TestClient(app) as c:
        yield c


def test_決めていなければ下見も返さない(client, monkeypatch):
    monkeypatch.delenv("GLOSSPOP_PUBLISH_DIR", raising=False)
    got = client.get("/api/publish").json()
    assert got["ready"] is False and got["plan"] is None


def test_公開先を決めるとその場で効く(client, tmp_path, monkeypatch):
    """保存先 (`/api/settings`) と違って**再起動が要らない**。"""
    monkeypatch.delenv("GLOSSPOP_PUBLISH_DIR", raising=False)
    monkeypatch.delenv("GLOSSPOP_PUBLISH_BASE_URL", raising=False)
    target = tmp_path / "pages"
    res = client.put("/api/publish/settings",
                     json={"dir": str(target), "base_url": BASE})
    assert res.status_code == 200
    got = res.json()
    assert got["ready"] is True and got["base_url"] == BASE
    assert client.get("/api/publish").json()["plan"]["dir"].startswith(str(target))


def test_URLはhttpから書かせる(client, tmp_path, monkeypatch):
    monkeypatch.delenv("GLOSSPOP_PUBLISH_DIR", raising=False)
    res = client.put("/api/publish/settings",
                     json={"dir": str(tmp_path / "p"), "base_url": "example.com"})
    assert res.status_code == 400


def test_環境変数が勝つときは書かせない(client, tmp_path, monkeypatch):
    monkeypatch.setenv("GLOSSPOP_PUBLISH_DIR", str(tmp_path / "env"))
    assert client.put("/api/publish/settings", json={"dir": "x"}).status_code == 409
    assert client.get("/api/publish").json()["env_locked"] is True


def test_カードはPNGでなければ断る(client, out):
    res = client.post("/api/publish/card?name=辞書", content=b"GIF89a")
    assert res.status_code == 400


def test_書くとURLが返る(client, based):
    made = client.post("/api/publish/card?name=辞書", content=PNG + b"body").json()
    got = client.post("/api/publish",
                      json={"name": "辞書", "card_stamp": made["stamp"]}).json()
    assert got["url"] == f"{BASE}%E8%BE%9E%E6%9B%B8/"
    assert got["card_url"].endswith(f"?v={made['stamp']}")
    assert (based / "辞書" / "index.html").exists()


# --- 配るページなので、入れる値は全部「名乗り」として扱う -----------------

def test_カードの印は形が違えば断る(based):
    """`card_stamp` は**リクエスト本文から来る**ので、こちらが作った形と照合する。"""
    for bad in ('"><script>alert(1)</script>', "abc XYZ", "../x", "z" * 40):
        with pytest.raises(publish.PublishError):
            publish.write_site([entry("あ")], name="辞書", card_stamp=bad)
    publish.write_site([entry("あ")], name="辞書", card_stamp="6264493d")  # 通る


def test_使えない基準URLは無かったことにする(out, monkeypatch):
    """**書き込みの口を通らない経路**（環境変数・設定ファイル）が素通りしないこと。"""
    monkeypatch.setenv("GLOSSPOP_PUBLISH_BASE_URL", 'https://x/"><script>a</script>')
    assert config.publish_base_url() == ""
    publish.write_site([entry("あ")], name="辞書", card_stamp="abc123")
    page = (out / "辞書" / "index.html").read_text(encoding="utf-8")
    assert "<script>" not in page and "og:image" not in page


def test_見出しに引用符があっても属性から出られない(based):
    """見出しは辞書の中身から作る。**そこも名乗りとして扱う。**"""
    danger = entry('" onload="alert(1)', when="1582", category="事件")
    other = entry("相方", when="1582", category="事件")
    publish.write_site([danger, other], name="辞書", card_stamp="abc123")
    page = (based / "辞書" / "index.html").read_text(encoding="utf-8")
    head = page.split("</head>", 1)[0]
    # 見るのは**属性から抜け出せていないこと**。文字列として残るのは正しい
    assert '" onload="' not in head
    assert "&quot; onload=&quot;" in head


def test_エスケープは二重にかからない(based):
    """先に escape したものを `_og()` へ渡すと `&amp;lt;` になる。"""
    entries = [entry("A&B", when="1582"), entry("C&D", when="1582")]
    publish.write_site(entries, name="辞書", card_stamp="abc123")
    page = (based / "辞書" / "index.html").read_text(encoding="utf-8")
    assert "&amp;amp;" not in page


# --- 本文のページ（サーバ無しで吹き出しが効く 1 枚）-----------------------

def a_page(**kw) -> dict:
    """呼ぶ側（app）が作る形。ここでは最小のものを手で組む。"""
    page = {
        "path": "章.md",
        "title": "第一章",
        "html": '<p><a class="gloss-link" href="/glossary/事件/変" data-gloss="変">変</a></p>',
        "lookup": {"変": {"term": "変", "found": True, "count": 1, "entries": [{
            "term": "変",
            "path": r"C:\Users\me\secret\変.md",
            "url": "/glossary/事件/変",
            "persona_url": "/api/persona?scope=global",
            "definition_html": '<a class="gloss-link" href="/glossary/x">中</a>',
            "summary_html": "",
            "examples_html": [],
            "relations_resolved": [
                {"to": "甲", "label": "続く"},
                {"to": "乙", "label": "実は", "reveal": "第6章"},
            ],
            "backlinks": [{"url": "/glossary/y"}],
        }]}},
    }
    page.update(kw)
    return page


def test_本文のページと共有の部品を書く(out):
    got = publish.write_site([entry("あ")], name="辞書", pages=[a_page()])
    root = out / "辞書"
    assert (root / "assets" / "reader.css").exists()
    assert (root / "assets" / "reader.js").exists()
    assert (root / "docs" / "章.html").exists()
    # 索引から辿れること（**行き先の無いリンクを作らない**、の裏返し）
    index = (root / "index.html").read_text(encoding="utf-8")
    assert 'href="docs/章.html"' in index and "第一章" in index
    assert got["documents"][0]["terms"] == 1


def test_本文が無ければ辞書の1枚だけ(out):
    """貼り付けや URL から読んでいるときは本文を読み直せない。"""
    got = publish.write_site([entry("あ")], name="辞書")
    assert got["documents"] == []
    assert not (out / "辞書" / "docs").exists()
    assert "<section class=\"docs\">" not in (out / "辞書" / "index.html").read_text(encoding="utf-8")


def test_辞書ページへのhrefは外す(out):
    """**押せるのに飛び先が無い、が最悪。** Ctrl クリックは別タブで開いてしまう。"""
    publish.write_site([entry("あ")], name="辞書", pages=[a_page()])
    page = (out / "辞書" / "docs" / "章.html").read_text(encoding="utf-8")
    assert 'href="/glossary' not in page
    assert 'tabindex="0"' in page


def embedded(page_text: str) -> dict:
    """ページに埋まっている辞書を読み出す。**属性 → HTML 解除 → JSON。**"""
    found = re.search(r'data-dict="([^"]*)"', page_text)
    assert found, "辞書が埋まっていません"
    return json.loads(htmllib.unescape(found.group(1)))


def test_こちらの環境が漏れる項目を落とす(out):
    """手元のフルパスとサーバの URL は、渡した相手には出ないどころか漏れるだけ。"""
    publish.write_site([entry("あ")], name="辞書", pages=[a_page()])
    text = (out / "辞書" / "docs" / "章.html").read_text(encoding="utf-8")
    got = embedded(text)["変"]["entries"][0]
    for key in publish.STRIP_KEYS:
        assert key not in got
    assert got["backlinks"] == []
    assert "secret" not in text


def test_判明位置つきの関係は伏せる(out):
    """相関図・カードと同じ約束。**伏せた本数は残す。**"""
    publish.write_site([entry("あ")], name="辞書", pages=[a_page()])
    got = embedded((out / "辞書" / "docs" / "章.html").read_text(encoding="utf-8"))
    one = got["変"]["entries"][0]
    assert [r["to"] for r in one["relations_resolved"]] == ["甲"]
    assert one["hidden"] == 1


def test_辞書の本文でスクリプトを閉じさせない(out):
    """人が書くものなので `</script>` は起こりうる。**属性なら切れない。**"""
    page = a_page()
    page["lookup"]["変"]["entries"][0]["summary_html"] = "</script><script>alert(1)"
    publish.write_site([entry("あ")], name="辞書", pages=[page])
    written = (out / "辞書" / "docs" / "章.html").read_text(encoding="utf-8")
    # タグとして効いていないこと。**読み出せば元に戻ること**
    assert "</script><script>alert(1)" not in written
    assert embedded(written)["変"]["entries"][0]["summary_html"] == (
        "</script><script>alert(1)"
    )


def test_名前が重なったら番号で分ける(out):
    """`a/b.md` と `a-b.md` はぶつかる。**黙って上書きしない。**"""
    got = publish.write_site([entry("あ")], name="辞書", pages=[
        a_page(path="a/b.md"), a_page(path="a-b.md"),
    ])
    assert [d["file"] for d in got["documents"]] == ["docs/a-b.html", "docs/a-b-2.html"]


def test_日本語のフォルダ名は断りを出す(based):
    """**実測**: 生の日本語 URL を素で取りに行くと GitHub Pages は 404 を返す。

    ブラウザは送る前に percent-encode するので**打ち込むぶんには通ってしまい**、
    貼った先でだけカードが出ない。どちらの形で取りに来るかは決められないので、
    黙らずに断りを出す。
    """
    warned = publish.plan("戦国時代")["warnings"]
    assert warned and "日本語" in warned[0]
    assert publish.plan("sengoku")["warnings"] == []
    # 書いたあとの返り値も同じ口から出る（下見と食い違わせない）
    got = publish.write_site([entry("あ")], name="戦国時代", card_stamp="abc123")
    assert got["warnings"] == warned


def test_twitterのタグも書く(based):
    """仕様上は `og:*` に落ちるが、**こちらから確かめる手段が無い**。"""
    publish.write_site([entry("あ")], name="sengoku", card_stamp="abc123")
    page = (based / "sengoku" / "index.html").read_text(encoding="utf-8")
    for tag in ("twitter:card", "twitter:title", "twitter:description",
                "twitter:image", "og:image:alt", "og:site_name"):
        assert tag in page
    # og と twitter は同じ URL を指す（別々に組み立てない）
    assert page.count("card.png?v=abc123") == 2
