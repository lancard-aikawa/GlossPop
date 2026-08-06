"""`samples/` に置いた辞書が、いまのコードでそのまま開けることを確かめる。

**サンプルは放っておくと必ず腐る。** スキーマを変えたときに気付けないと、
「置いて開くだけ」と README に書いてあるものが開けない状態で配られる。
公開しているのは**動く実物**なので、ここが最後の関門になる。

見ているのは 4 つ:

- 読み込める（frontmatter とカテゴリのディレクトリ名が通る）
- **点検 (`doctor`) が黙る** —— 壊れた参照も、本文の抜けも無い
- **別名を含むすべての表記が本文に出てくる** —— 出てこない表記はリンクにならないので、
  「開けばリンクになっている」という約束が嘘になる
- **関係の相手が解決できる** —— 名前で書いてあるので、直した拍子に静かに切れる
"""

from __future__ import annotations

from pathlib import Path

import pytest

from glosspop import config, store
from glosspop.core import documents, doctor, relations
from glosspop.core.linker import Linker

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def sample_folders() -> list[Path]:
    if not SAMPLES.is_dir():
        return []
    return sorted(p for p in SAMPLES.iterdir() if (p / ".glosspop" / "glossary").is_dir())


#: `samples/` はリポジトリにしか無い（配布物には入れない）ので、
#: 無ければ丸ごと飛ばす。**通常のテストを止めないこと**が条件
pytestmark = pytest.mark.skipif(not sample_folders(), reason="samples/ が無い")


@pytest.fixture(params=sample_folders(), ids=lambda p: p.name)
def sample(request):
    """サンプルのフォルダを「開いている」状態にする。

    グローバル側は conftest が tmp_path に差し替えているので、ここで読めるのは
    そのフォルダの辞書だけになる（配布物の 3 語が混ざらない）。
    """
    config.set_content_dir(request.param)
    store.invalidate()
    yield request.param
    config.set_content_dir(None)
    store.invalidate()


def documents_text(folder: Path) -> str:
    """そのフォルダで読めるテキストを全部つないだもの。"""
    parts = []
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in {".txt", ".md", ".html"}:
            parts.append(documents.read(path).text)
    return "\n".join(parts)


def test_the_dictionary_loads(sample):
    entries = store.load_all()
    assert entries, f"{sample.name}: 辞書が空です"
    assert all(e.is_local for e in entries), "フォルダの辞書だけが読めるはず"


def test_the_doctor_is_silent(sample):
    report = doctor.check(store.load_all())
    detail = "\n".join(f"  {i['kind']}: {i.get('entry')} {i.get('detail')}" for i in report["issues"])
    assert not report["issues"], f"{sample.name}: 点検が問題を挙げました\n{detail}"


def test_every_surface_appears_in_the_document(sample):
    """**別名も含めて**本文に出てくること。出てこない表記はリンクにならない。"""
    text = documents_text(sample)
    assert text.strip(), f"{sample.name}: 読める本文がありません"
    missing = [
        f"{e.term}（{surface}）"
        for e in store.load_all()
        for surface in e.surfaces
        if surface not in text
    ]
    assert not missing, f"{sample.name}: 本文に無い表記があります: {missing}"


def test_every_surface_actually_links(sample):
    """照合の規則（`Linker`）で実際にリンクになること。

    「本文に文字列がある」と「リンクになる」は別の話 —— 大文字小文字の区別や
    境界の規則で落ちうるので、素の部分一致では確かめたことにならない。
    """
    text = documents_text(sample)
    entries = store.load_all()
    linked = {e.ref for e in Linker(entries).entries_in(text)}
    missing = [e.term for e in entries if e.ref not in linked]
    assert not missing, f"{sample.name}: リンクにならない用語があります: {missing}"


def test_every_relation_resolves(sample):
    entries = store.load_all()
    broken = [
        f"{e.term} → {rel.to}"
        for e in entries
        for rel in e.relations
        if relations.resolve(rel.to, entries, origin=e).entry is None
    ]
    assert not broken, f"{sample.name}: 行き先の無い関係があります: {broken}"


def test_the_folder_style_is_picked_up(sample):
    """作品ごとの口調が置いてあるなら、そのフォルダを開いた時点で効くこと。"""
    from glosspop import ai

    if not (sample / ".glosspop" / "style.md").is_file():
        pytest.skip("このサンプルには文体の指定が無い")
    assert ai.describe_style()["style_source"] == "folder"
