# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

使い方・データ形式・自動リンクの規則は [README.md](README.md) にある。ここには
**README を読んでも分からないこと**（設計の前提、壊しやすい不変条件、開発時の落とし穴）
だけを書く。

## コマンド

```powershell
uv sync --all-groups            # 依存を入れる (dev グループ含む)
uv run glosspop serve           # http://127.0.0.1:8765/
uv run pytest                   # 全テスト
uv run pytest tests/test_linker.py::test_longest_match_wins   # 単体
uv run pytest -k "カテゴリ or category"
```

`glosspop` CLI は辞書操作もできる（`add` / `list` / `show` / `move` / `rm` / `categories`）。
`.claude/skills/gloss-add/SKILL.md` がその使い方をスキルとして持っている。**辞書の
スキーマや CLI の引数を変えたら SKILL.md も直すこと。**

### サーバの止め方（Windows で必ず踏む）

`uv run glosspop serve` の親プロセス（`uv`）を kill しても、**子の python が生き残って
ポートを掴んだまま**になる。新しいサーバは bind に失敗して黙って死に、古いコードが
動き続けるので「直したのに反映されない」という誤診をする。ポートの所有者を落とすこと。

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

`uv sync` が `glosspop.exe` を消せずに失敗するときも、たいていサーバが動いている。

## アーキテクチャ

データの流れは一本道:

```
ソース (ファイル / URL / 貼り付け)
  → render.render_source()      Markdown・テキスト・HTML → HTML
  → Linker.annotate()           テキストにだけ <a class="gloss-link"> を差し込む
  → ブラウザ                     popup.js が data-gloss から辞書を引いて吹き出しを出す
```

`store` が辞書、`categories` がカテゴリマスター、`ai` が Claude CLI 呼び出し。
`app.py` はこれらを繋ぐだけで、ロジックを持たない。

### 壊しやすい不変条件

**ディレクトリ名とファイル名が正。** エントリは `<辞書ルート>/<カテゴリ>/<slug>.md`。
`category` `slug` `scope` は frontmatter に書かない（書いても `_entry_from_file()` が
パスの値で上書きする）。一意キーは `Entry.ref` で、API も URL もこの文字列を使う。
**同じ用語名でもカテゴリが違えば別エントリ**なので、`find_by_surface()` はリストを返す。
単数を期待するコードを書かないこと。

**辞書ルートは 2 つある。** グローバル (`data/glossary/`) と、開いているフォルダの
ローカル (`<フォルダ>/.glosspop/glossary/`)。`store.glossary_dir(scope)` を必ず通すこと。
ref はグローバルが `<カテゴリ>/<slug>`、ローカルが `.local/<カテゴリ>/<slug>`
（カテゴリ名は「.」で始められないので衝突しない）。**グローバル側の形を変えていない**ので、
既存の URL・CLI・スキルはそのまま通る。

ローカル辞書まわりで踏みやすいのは 3 つ:

- `_signature()` に**ローカルのルートパス自体**を入れてある。入れないと、フォルダを
  切り替えてもキャッシュが効いて前のフォルダの辞書が出てくる
- `find_in_category()` はスコープも見る。見ないと「全体の辞書に同名があるので
  このフォルダに登録できない」という誤った衝突になる
- **カテゴリマスターはグローバルのもの。** ローカル保存では `categories.ensure()` を
  呼ばない（小説の「登場人物」がマスターに残ると邪魔になる）。`category_tree()` は
  ローカルぶんをディレクトリから作って `scope` 付きで足している

**Linker はレンダリング済み HTML を文字列として走査する。** DOM は作らない。
`_tokenize()` でタグとテキストに切り、`_runs()` で「読者に見えるテキスト」の
まとまりを作ってから照合する。ここが 2 つの規則を担っている:

- `SKIP_TAGS`（`pre` `a` `script` `style` `textarea`）の内側はリンクしない
- `INLINE_TAGS` の中だけテキストを連結する。ブロック要素と `<br>` は連結しない
  → `**冪**等` は繋がるが `<p>冪</p><p>等</p>` は繋がらない

**`htmlclean.VOID_TAGS` に void 要素を漏れなく入れること。** `DROP_TREES`（中身ごと
捨てるタグ）に void 要素が入っていると、閉じタグを待ち続けて**それ以降の本文が全部
消える**。`<input>` がそうで、**検索ボックスのあるページ（＝たいていの Web ページ）が
空になっていた**。URL 読み込みを触るときは、実際のサイトを 1 つ開いて本文が出ることを
確かめること（`example.com` のような単純なページでは気付けない）。

**`MAIN_CANDIDATES`（`main` / `article`）で本文を採るのは最初のひとつだけ。** 見つける
たびに `parts` を差し替える作りにすると、**2 つめの `<article>` が 1 つめを丸ごと
消す**。記事を `<article>` で並べるページ（ブログの一覧、章が並ぶページ）が該当する。
`_main_done` がその番人。これも「黙って本文が消える」系統なので、上の `<input>` と
同じく実際のページで確かめること。

`render.md_to_html()` は `html: False` で生 HTML を通さない。これは安全のためだけで
なく、**Linker のタグ走査が想定外の構造を踏まないため**でもある。外部 HTML を
入れる経路（URL 読み込み）は `htmlclean` の許可制サニタイザを必ず通す。

**辞書本文は保存時に 1 文 1 行へ整形される**（`render.soften_paragraphs()`）。
表示側は `breaks: True` の markdown インスタンスを使う。ソース文書用の `_md` とは
別インスタンスなので、片方だけ設定を変えないこと。

**開いているフォルダは `config.content_dir()` で引く。** ビューアから
`/api/content-root` で切り替えられるようになったので、`config.CONTENT_DIR` は
「既定値」でしかない。直接参照すると、切り替えたあとに一覧・読み出し・パス検査が
別のフォルダを見て、`_safe_content_path()` のディレクトリ脱出チェックが意味を失う。
上書きはプロセス内に残るので、**テストは conftest の fixture でリセットしている**。

任意のフォルダを開ける = 巨大なツリーを掴みうるということでもある。`_iter_content_files()`
が隠しディレクトリと `SKIP_DIRS` に降りず、`MAX_CONTENT_FILES` で打ち切るのはそのため。
打ち切ったことは `truncated` で UI に出す（黙って切らない）。

**凍結した exe の標準出力は UTF-8 ではない。** 日本語 Windows のコンソールでは
CP932 になる（開発時の `uv run` では UTF-8 なので、**この差は exe でしか表面化しない**）。
子プロセスとやりとりする値は必ずバイト列で受け渡すこと — `picker.py` は
`sys.stdout.buffer` へ UTF-8 で書き、親は `text=True` を使わずに自分で復号している。
これを怠って「日本語を含むフォルダを開けない」不具合を出した。CLI もパイプ越しのときだけ
UTF-8 に固定している（コンソールへ直接出すときに UTF-8 で書くと今度は表示が化ける）。

**固定するのは stdin も。** `_use_utf8_when_piped()` が stdout / stderr だけを直していた
ため、`echo '{"term":"冪等"}' | glosspop add --json -` の入力が CP932 で復号され、
**見出し語がサロゲートに化けたまま保存されていた**（`/gloss-add` スキルが使う経路そのもの）。
`uv run` でも凍結後でも起きる。ファイル入力 (`@path`) は最初から UTF-8 決め打ちなので、
`-` だけが穴だった。

**「いま読んでいるもの」はフォルダか URL のどちらか一方。** `config.set_reading_url()`
が立っていれば `sites/` の下を最長一致で探し、そうでなければフォルダを祖先方向に探す。
併用しないのは、小説フォルダを開いたまま Web を読んで登場人物名が無関係なページで
リンクになるのを防ぐため。**URL 側は辞書を勝手に作らない**ので
`local_glossary_dir()` は `None` を返しうる —— `store.glossary_dir()` と
`local_available()` はそれを前提にしている。

**URL のパスをそのままディレクトリ名にしない**（`sites.py`）。`..`・`:`・クエリ・
Windows の禁止文字を潰したうえで、組み立てた結果が `SITES_DIR` の外に出ていないことを
最後に必ず検査する。ホストらしくない入力（空白入り、ドットもポートも無い）は弾く。

**ローカル辞書は祖先方向に探す**（`config.local_root()`）。開いているフォルダから
いちばん近い `.glosspop` を使い、無ければ開いているフォルダに作る。1 巻 2 巻を別
フォルダにしていても作品フォルダに 1 つ置けば共有できる。遠い祖先の辞書が黙って
繋がると驚くので、**親を使っているときは UI にその場所を出す**
（`/api/content` の `local_dir` / `local_is_ancestor`）。

**フォルダ選択ダイアログは必ず別プロセスで開く**（`picker.py`）。tkinter はメイン
スレッドでしか安全に動かず、FastAPI のワーカースレッドから呼ぶと固まる。凍結した
exe には python インタプリタが無いので、自分自身を隠しコマンド `__pick-folder` で
再実行している（`cli.main()` の先頭で引数を横取りしている）。**`glosspop.spec` の
`excludes` に tkinter を戻さないこと** — 戻すと exe 版だけダイアログが開かなくなる。

**ファイルを読むのは `documents.py` の仕事。** `path.read_text()` を直接呼ばないこと。
吸収しているのは 3 つ:

- **文字コード** — UTF-8 → cp932 → euc_jp。青空文庫は Shift_JIS で、UTF-8 決め打ち
  （`errors="replace"`）だと化けたまま「読めてしまう」ので気付きにくい
- **改行** — バイト列から decode すると CRLF が素通りする。`\n\s*\n` で段落を
  数えている箇所（ネタバレ抑止）が静かに壊れるので `decode()` で LF に揃えている
- **位置** — epub は章名、pdf はページ番号、それ以外は行番号。`Document.locate()`

**epub の manifest の href は percent-encoded。** OPF の href は IRI なので、日本語や
スペースを含むファイル名は `%E7%AC%AC...` で入っている。`zipfile` は生の名前しか
受けないので、復号せずに読むと `KeyError` になり**その章だけ黙って消える**
（`_read_member()` が両方を試す）。章名が英数字の epub では気付けない。

**青空文庫の正規表現は行単位で書く。** 冒頭の説明ブロックを `(?:.*\n){0,6}?...` と
DOTALL で書いたら 4 万字の作品でハングした（`.` が行をまたいで際限なく戻る）。
`[^\n]*` を使い、走査範囲も先頭 3000 字に限ること。

**ルビは `rt` / `rp` を中身ごと捨てる**（`htmlclean.DROP_TREES`）。タグだけ落とすと
読みが地の文に残り、`銀河ぎんが` になって照合が壊れる。親字側の `ruby` / `rb` は
`KEEP_TAGS` と `linker.INLINE_TAGS` の両方に入れて、ルビをまたぐ語を 1 語として繋ぐ。

**ビューアで開ける拡張子は 2 か所にある。** `app.CONTENT_SUFFIXES`（一覧と読み出し）と
`viewer.js` の `OPENABLE`（リンクを辿るかの判定）。片方だけ足すと、一覧には出るのに
リンクからは開けない（またはその逆）という食い違いになる。

**ローカル `.html` には base_url が無い。** `htmlclean._safe_url()` は base_url が
空のときだけ相対 href を残す（`allow_relative`）。ここを緩めて `src` も残すと、
配信する経路が無いので壊れた画像が並ぶ。URL 経由では base_url があるので従来どおり
絶対化される。

### AI 下書き（`ai.py`）

`claude -p` をサブプロセスで叩く（API キー不要）。**プロジェクト内で実行すると
CLAUDE.md と `gloss-add` スキルを拾い、「重複を確認するため CLI を実行させて」と
要求して承認待ちで固まる。** そのため:

- cwd は `tempfile.gettempdir()/glosspop-ai`（プロジェクト外）
- `--disallowed-tools` で全ツールを落とし、`--disable-slash-commands` を付ける
- `--bare` は使えない（ANTHROPIC_API_KEY 必須になり、OAuth を読まなくなる）

プロンプトを触るときは、カテゴリの提案が既存へ寄りすぎないか実際に確かめること
（音楽用語を「プログラミング」に入れる、他カテゴリのサブカテゴリを流用する、
といった失敗を実際に踏んだ）。

**ネタバレ対策（`spoiler`）はプロンプトだけでなく、渡すテキスト自体で担保する。**
`position` は AI を呼ばない（`/api/ai/draft` が claude 抜きで返す唯一の経路）。
`first` は `context_up_to_first()` が初出の前後だけを切り出す。**後ろは文字数ではなく
段落の切れ目で打ち切る** — 初出が章末にあると N 文字取るだけで次の章まで入り、
「読んでいないはずの展開」が要約に出る（実際にテストで踏んだ）。プロンプトの禁止文だけに
頼らないこと。

**まとめて登録は「抽出 1 回 + 下書き N 回」。** `extract_terms()` は候補語だけを 1 回で
挙げさせ、本文の生成は選ばれた語について `/api/ai/draft` を語数ぶん呼ぶ（クライアント側で
逐次）。ここを 1 回のプロンプトで N 語ぶんの本文まで作らせる形にすると、出力が長くなって
品質が落ちる。サーバに進捗の状態は持たせていない。

**保存先（`scope`）も AI に選ばせている。** `/api/ai/draft` に `scope: "auto"`
（既定）を渡すと、プロンプトに「保存先」の節とフォルダ名が入り、`scope` を答えさせる。
明示された値が来たときは聞かない（答えより指定が優先）。**カテゴリマスターを触るかは
解決後の `scope` で判断する** — リクエストの値で判断すると、AI がローカルを選んだのに
マスターへ登録してしまう。

**AI が挙げた候補はそのまま使わない。** `filter_candidates()` が、登録済みの語と
**文書中にその表記で現れない語**を落とす。後者を通すと「登録したのに本文でリンクにならない」
エントリができる（AI は平気で原形や言い換えを返す）。落とした語は理由つきで UI に出す。

## Windows ビルド（`packaging/`）

`.\packaging\build.ps1` で PyInstaller の onedir ビルド（`dist\GlossPop\`）を作る。
手順は README にある。ここには**壊しやすい点**だけ:

**凍結すると `__file__` は一時展開先（`_internal`）を指す。** そのため
`config.DATA_ROOT` は `sys.frozen` のとき `sys.executable` の親を基準にする。
ここを `PACKAGE_DIR` 基準に戻すと、**保存した辞書が起動ごとの一時ディレクトリに
書かれて消える**（サーバは正常に動いて見えるので気付きにくい）。逆に `STATIC_DIR` は
`_internal` 側で正しいので `PACKAGE_DIR` 基準のまま。

**文字列 import は凍結後に解決できない。** `cmd_serve()` は `--reload` のときだけ
`"glosspop.app:app"` を渡し、通常は app オブジェクトを直接渡す。ここを文字列に
戻すと exe が `Could not import module "glosspop.app"` で即死する。

**動的に読むものを足したら spec に足す。** 静的解析で追えないものは
`packaging/glosspop.spec` の `hiddenimports` / `datas` に書く必要がある
（`glosspop.*` は `collect_submodules` でまとめて入れてある）。新しい依存を
`pyproject.toml` に足したときは、**ビルドして exe を起動するところまで確認する**
——`uv run` では動いても凍結すると落ちる、という差が出るのはここだけ。

## フロントエンド

依存ゼロの ES モジュール。ビルド工程は無い。`static/` を編集したら
サーバ再起動は不要（ファイルはディスクから読まれる）。

`/static` と HTML には `Cache-Control: no-cache` を付けている。これが無いと
ブラウザが古い JS を出し、原因不明の壊れ方をする（実際に半日溶かした）。
`RevalidatingStatic` を消さないこと。

- `base.js` — API 呼び出し、DOM ヘルパ、URL 判定
- `select-add.js` — 選択 →「＋ 辞書に登録」。ビューアと辞書ページで共用
- `extract.js` — 「✨ 用語を抽出」→ 候補選択 → 下書き → まとめて保存
- `popup.js` / `editor.js` — 吹き出し・登録ダイアログ。どのページからも使える
- `viewer.js` / `glossary.js` / `entry.js` — 各ページ

**`node.hidden = true` はそれだけでは効かない。** `button` などに `display` を
指定しているので UA スタイルの `[hidden]` が負ける。`style.css` の先頭近くで
`[hidden] { display: none !important; }` を当てて打ち消している。**要素ごとに
`.foo[hidden] { display: none }` を足す書き方を増やさないこと**（この規則より前は
そうなっていて、新しい UI を足すたびに「hidden にしたのに出たまま」を踏む）。

**`prompt()` / `alert()` を入力 UI に使わない。** 一度使って作り直した。値の入力は
インラインの `<input>`/`<select>` にする（`confirm()` は削除確認にだけ使っている）。

## テスト

`tests/conftest.py` の autouse fixture が `config.GLOSSARY_DIR` などを tmp_path に
差し替える。**`store._ready` のリセットも fixture がやっている** ので、
`store.ensure_ready()` の初期化をテストで当てにするなら fixture を確認すること。

フロントエンドの自動テストは無い。UI を変えたら Playwright で実際に動かして確かめる
（このリポジトリの機能は「選択 → 登録 → リンクが生える」まで通して見ないと確認できない）。

## 変更時に一緒に直すもの

| 変えたもの | 一緒に直す |
| --- | --- |
| 辞書のスキーマ / CLI 引数 | `.claude/skills/gloss-add/SKILL.md` |
| 自動リンクの規則 | README.md の「自動リンクの規則」、`content/ようこそ.md` |
| カテゴリ名の制約 | `models.normalize_category()`、README、SKILL.md、`ai.build_prompt()` |
| 依存の追加 / 動的 import・データファイルの追加 | `packaging/glosspop.spec`（ビルドして exe 起動まで確認） |
| バージョン | `pyproject.toml` と `glosspop/__init__.py` の**両方**（タグと不一致だと release ワークフローが落ちる） |
